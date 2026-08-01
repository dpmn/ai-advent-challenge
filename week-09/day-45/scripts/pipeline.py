#!/usr/bin/env python3
"""Двухуровневый разбор: классификатор сначала, большая модель по необходимости.

Уровень 1 — классификатор без языковой модели (`classifier.py`). Отвечает
за доли миллисекунды, выдаёт категорию и уверенность.

Уровень 2 — `Qwen3-14B` на арендованной ВМ (сервер `day-43/scripts/serve_big.py`,
порт приходит ssh-туннелем). Спрашивается только тогда, когда уверенность ниже
порога. Запрос к ней — тот же, что в дне 41, без единой правки: она разбирает
товар целиком на девять полей, а категория берётся из её ответа. Урезанный
запрос «верни только категорию» был бы дешевле, но его результат не сравнить
ни с одним прошлым замером.

Скрипт печатает решение по каждому товару по ходу дела — он же идёт в кадр.
"""

from __future__ import annotations

import argparse
import json
import time

import common
from classifier import CategoryClassifier
from common import RESULTS_DIR, load_eval, rub, schema, write_jsonl

BAR = "─" * 78

POLICIES = ("two-tier", "all-big", "all-small")

# Сколько товаров показываем в кадре и как они делятся по сложности.
# Задание просит 20–30; берём 30 поровну, чтобы в прогоне были и лёгкие
# названия, и те, на которых классификатор обязан сдаться.
DEMO_TOTAL = 30
DEMO_SEED = 45


class Style:
    """Цвета для вывода в терминал."""

    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"


def pick_demo(records: list[dict], total: int = DEMO_TOTAL) -> list[dict]:
    """Отбирает товары для показа: поровну простых, пограничных и сложных."""
    import random

    rng = random.Random(DEMO_SEED)
    buckets: dict[str, list[dict]] = {}
    for record in records:
        buckets.setdefault(record["level"], []).append(record)
    for pool in buckets.values():
        rng.shuffle(pool)

    per_level = total // len(buckets)
    picked: list[dict] = []
    for level in sorted(buckets):
        picked.extend(buckets[level][:per_level])
    # Недобор (если уровень мал) добираем из самого большого остатка.
    leftovers = [r for level in sorted(buckets) for r in buckets[level][per_level:]]
    picked.extend(leftovers[:total - len(picked)])
    picked.sort(key=lambda r: r["id"])
    return picked


def category_from_raw(raw: str) -> tuple[str | None, bool]:
    """Достаёт категорию из полного ответа большой модели.

    Возвращает `(категория, разобрался_ли_ответ)`. Значение вне словаря дня 41
    считается отсутствующим: выдуманная категория — это не ответ.
    """
    obj, _ = schema.parse_model_json(raw)
    if not obj:
        return None, False
    value = obj.get("category")
    if value not in common.CATEGORIES:
        return None, True
    return value, True


def verdict(predicted: str | None, truth: str) -> str:
    """Значок сравнения с эталоном."""
    if predicted == truth:
        return f"{Style.GREEN}✔{Style.RESET}"
    return f"{Style.RED}✘{Style.RESET}"


def run(args: argparse.Namespace) -> int:
    """Прогоняет выбранную политику и печатает решения по ходу."""
    classifier = CategoryClassifier.load()
    threshold = args.threshold if args.threshold is not None else classifier.threshold
    classifier.threshold = threshold

    records = load_eval()
    if args.demo:
        records = pick_demo(records, args.limit or DEMO_TOTAL)
    elif args.limit:
        records = records[:args.limit]

    client = None
    if args.policy != "all-small":
        if args.mock:
            from mock_big import MockBigClient
            client = MockBigClient()
        else:
            from big import BigClient, BigModelUnavailable
            client = BigClient()
            try:
                health = client.health()
            except BigModelUnavailable as error:
                print(f"{Style.RED}{error}{Style.RESET}")
                return 1
            print(f"{Style.DIM}большая модель: {health.get('model')} · "
                  f"квантование {health.get('quantization')} · "
                  f"размышления {health.get('thinking')}{Style.RESET}")

    print(f"\n{Style.BOLD}Политика: {args.policy} · товаров: {len(records)} · "
          f"порог уверенности: {threshold:.2f}{Style.RESET}")
    if args.mock:
        print(f"{Style.YELLOW}ВНИМАНИЕ: заглушка вместо живой модели, "
              f"время ответа подставлено{Style.RESET}")
    print(BAR)

    results: list[dict] = []
    for index, record in enumerate(records, 1):
        started = time.time()
        prediction = classifier.predict(record["name"])
        small_seconds = time.time() - started

        escalated = args.policy == "all-big" or (
            args.policy == "two-tier" and prediction["status"] == "UNSURE")

        big_category = None
        big_seconds = 0.0
        big_raw = None
        parsed = None
        if escalated and client is not None:
            answer = client.generate_one(record["name"])
            big_raw = answer["raw"]
            big_seconds = float(answer.get("latency_s") or 0.0)
            big_category, parsed = category_from_raw(big_raw)

        final = big_category if escalated else prediction["category"]
        if escalated and big_category is None:
            final = prediction["category"]  # ответ большой модели не разобран

        print(f"{Style.BOLD}[{index:>3}/{len(records)}] {record['id']}{Style.RESET}  "
              f"{record['name'][:58]}")
        print(f"        уровень 1: {prediction['category']} · уверенность "
              f"{prediction['confidence']:.2f} · {prediction['status']} "
              f"{Style.DIM}({small_seconds * 1000:.1f} мс){Style.RESET}")
        if escalated:
            reason = ("политика all-big" if args.policy == "all-big"
                      else f"уверенность ниже порога {threshold:.2f}")
            print(f"        {Style.YELLOW}→ спрашиваем Qwen3-14B{Style.RESET} ({reason})")
            if big_category is None:
                print(f"        уровень 2: {Style.RED}ответ не разобран{Style.RESET}, "
                      f"остаёмся на уровне 1")
            else:
                print(f"        уровень 2: {big_category} "
                      f"{Style.DIM}({big_seconds:.2f} с){Style.RESET}")
        else:
            print(f"        {Style.GREEN}✓ уверен — большая модель не нужна{Style.RESET}")
        print(f"        эталон: {record['category']}  {verdict(final, record['category'])}")

        results.append({
            "id": record["id"],
            "name": record["name"],
            "level": record["level"],
            "truth": record["category"],
            "small_category": prediction["category"],
            "confidence": prediction["confidence"],
            "status": prediction["status"],
            "top": prediction["top"],
            "escalated": escalated,
            "big_category": big_category,
            "big_raw": big_raw,
            "big_parsed": parsed,
            "final": final,
            "small_s": round(small_seconds, 5),
            "big_s": round(big_seconds, 3),
        })

    print(BAR)
    summary = summarize(results, threshold, args.policy)
    print(render_summary(summary))

    name = args.out or f"{args.policy}{'_demo' if args.demo else ''}{'_mock' if args.mock else ''}"
    write_jsonl(RESULTS_DIR / f"{name}.jsonl", results)
    meta = {
        "policy": args.policy,
        "threshold": threshold,
        "items": len(results),
        "mock": bool(args.mock),
        "demo": bool(args.demo),
        "big_model": common.BIG_MODEL,
        "gpu_seconds": round(getattr(client, "gpu_seconds", 0.0), 3) if client else 0.0,
        "summary": summary,
    }
    (RESULTS_DIR / f"{name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{Style.DIM}результаты: results/{name}.jsonl{Style.RESET}")
    return 0


def summarize(results: list[dict], threshold: float, policy: str) -> dict:
    """Считает метрики, которые требует задание."""
    total = len(results)
    escalated = [r for r in results if r["escalated"]]
    kept = [r for r in results if not r["escalated"]]
    correct = sum(r["final"] == r["truth"] for r in results)
    small_only_correct = sum(r["small_category"] == r["truth"] for r in results)
    kept_correct = sum(r["final"] == r["truth"] for r in kept)
    escalated_correct = sum(r["final"] == r["truth"] for r in escalated)
    # Сколько раз большая модель исправила уровень 1, а сколько испортила.
    fixed = sum(1 for r in escalated
                if r["small_category"] != r["truth"] and r["final"] == r["truth"])
    broken = sum(1 for r in escalated
                 if r["small_category"] == r["truth"] and r["final"] != r["truth"])

    small_seconds = sum(r["small_s"] for r in results)
    big_seconds = sum(r["big_s"] for r in results)

    return {
        "policy": policy,
        "threshold": threshold,
        "total": total,
        "kept_by_classifier": len(kept),
        "escalated": len(escalated),
        "big_calls": len(escalated),
        "accuracy": correct / total if total else 0.0,
        "accuracy_small_only": small_only_correct / total if total else 0.0,
        "accuracy_kept": kept_correct / len(kept) if kept else None,
        "accuracy_escalated": escalated_correct / len(escalated) if escalated else None,
        "fixed_by_big": fixed,
        "broken_by_big": broken,
        "avg_latency_small_ms": small_seconds / total * 1000 if total else 0.0,
        "avg_latency_big_s": big_seconds / len(escalated) if escalated else 0.0,
        "avg_latency_total_s": (small_seconds + big_seconds) / total if total else 0.0,
        "small_seconds": round(small_seconds, 4),
        "big_seconds": round(big_seconds, 2),
    }


def render_summary(s: dict) -> str:
    """Печатает итоговую сводку прогона."""
    lines = [
        f"{Style.BOLD}Итог{Style.RESET}",
        f"  товаров:                         {s['total']}",
        f"  закрыл классификатор:            {s['kept_by_classifier']} "
        f"({s['kept_by_classifier'] / s['total'] * 100:.0f}%)",
        f"  ушло к большой модели:           {s['escalated']} "
        f"({s['escalated'] / s['total'] * 100:.0f}%)",
        f"  вызовов большой модели:          {s['big_calls']}",
        "",
        f"  точность двух уровней:           {s['accuracy'] * 100:.1f}%",
        f"  точность одного классификатора:  {s['accuracy_small_only'] * 100:.1f}%",
    ]
    if s["accuracy_kept"] is not None:
        lines.append(f"  верных среди оставленных:        {s['accuracy_kept'] * 100:.1f}%")
    if s["accuracy_escalated"] is not None:
        lines.append(f"  верных среди ушедших наверх:     {s['accuracy_escalated'] * 100:.1f}%")
    lines += [
        f"  большая модель исправила:        {s['fixed_by_big']}",
        f"  большая модель испортила:        {s['broken_by_big']}",
        "",
        f"  средняя задержка уровня 1:       {s['avg_latency_small_ms']:.2f} мс",
        f"  средняя задержка уровня 2:       {s['avg_latency_big_s']:.2f} с",
        f"  средняя задержка на товар:       {s['avg_latency_total_s']:.2f} с",
        f"  время большой модели всего:      {s['big_seconds']:.1f} с "
        f"({rub(s['big_seconds']):.2f} ₽)",
    ]
    return "\n".join(lines)


def main() -> None:
    """CLI: политика, выборка, заглушка."""
    parser = argparse.ArgumentParser(description="Двухуровневый разбор дня 45")
    parser.add_argument("--policy", choices=POLICIES, default="two-tier",
                        help="two-tier: классификатор с обращением наверх по необходимости; "
                             "all-big: всё большой модели; all-small: всё классификатору")
    parser.add_argument("--demo", action="store_true",
                        help=f"выборка для показа: {DEMO_TOTAL} товаров поровну по сложности")
    parser.add_argument("--limit", type=int, help="сколько товаров взять")
    parser.add_argument("--threshold", type=float,
                        help="порог уверенности; по умолчанию подобранный на train")
    parser.add_argument("--mock", action="store_true",
                        help="заглушка вместо живой модели (проверка конвейера без аренды)")
    parser.add_argument("--out", help="имя файлов результата без расширения")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
