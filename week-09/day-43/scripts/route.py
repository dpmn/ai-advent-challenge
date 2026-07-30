#!/usr/bin/env python3
"""Живой routing: слабая модель на ноутбуке, эскалация полей на сильную.

Это главный скрипт дня и тот, который снимается на видео. По каждому товару:

1. `qwen3:0.6b` в Ollama отвечает целиком (обязательный вызов, он же якорь
   «всё на слабой»);
2. три эвристики решают, какие **поля** сомнительны — бесплатно, из того же
   ответа и его logprob-ов;
3. если сомнительные поля есть, `Qwen3-14B` на арендованной ВМ отвечает по
   тому же промпту, и из его ответа берутся только эти поля.

В лог по каждому товару выводится, какая эвристика на что сработала и что
изменилось после эскалации. Сравнение с эталоном печатается сразу: иначе
из кадра не видно, эскалация исправила поле или испортила.

Сухой прогон без GPU: `--no-escalate`. Эвристики отрабатывают полностью,
эскалация не делается — так проверяется доля эскалации до аренды ВМ.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import heuristics
from big import BigClient, BigModelUnavailable
from common import (BIG_MODEL, BIG_URL, FIELD_ORDER, OLLAMA_URL, PICKS_PATH, RESULTS_DIR,
                    SMALL_MODEL, read_jsonl, rub, schema, score, write_jsonl)
from small import OllamaClient

BAR = "─" * 78


class Style:
    """ANSI-раскраска лога. Гасится вне терминала и флагом `--no-color`."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        """Оборачивает текст в ANSI-код, если раскраска включена."""
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        """Жирный."""
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        """Приглушённый."""
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        """Красный: испорчено или неверно."""
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        """Зелёный: верно или исправлено."""
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        """Жёлтый: сработавшая эвристика."""
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        """Голубой: модель."""
        return self._wrap("36", text)


def plural(count: int, one: str, few: str, many: str) -> str:
    """Согласует существительное с числом: 1 поле, 2 поля, 5 полей."""
    tail_100, tail_10 = count % 100, count % 10
    if 11 <= tail_100 <= 14:
        return many
    if tail_10 == 1:
        return one
    if 2 <= tail_10 <= 4:
        return few
    return many


def value_repr(value) -> str:
    """Компактное представление значения поля для лога."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def confidence_line(suspicion: heuristics.Suspicion, metric: str, threshold: float) -> list[str]:
    """Раскладывает пополевую уверенность в две строки лога."""
    cells = []
    for name in FIELD_ORDER:
        value = heuristics.field_confidence(suspicion.confidence, name, metric)
        if value is None:
            cells.append(f"{name} —")
        else:
            mark = "↓" if value < threshold else " "
            cells.append(f"{name} {value:.2f}{mark}")
    half = (len(cells) + 1) // 2
    return ["  ".join(cells[:half]), "  ".join(cells[half:])]


def field_verdict(expected: dict, obj: dict | None, name: str) -> bool:
    """Совпадает ли одно поле с эталоном."""
    if not isinstance(obj, dict):
        return False
    return score.fields_equal(name, expected.get(name), obj.get(name))


def run(args: argparse.Namespace) -> int:
    """Прогоняет выборку через routing и пишет результат. Возвращает код выхода."""
    style = Style(args.color)
    picks = read_jsonl(args.picks)
    if args.limit:
        picks = picks[: args.limit]

    small = OllamaClient(args.ollama_url, args.small_model)
    small_info = small.health()
    print(style.bold(f"слабая: {small_info['model']} ({small_info['quantization']}, "
                     f"ollama, ноутбук)"))

    big: BigClient | None = None
    if not args.no_escalate:
        big = BigClient(args.big_url)
        try:
            big_info = big.health()
        except BigModelUnavailable as error:
            print(style.red(f"сильная модель недоступна: {error}"))
            return 2
        print(style.bold(f"сильная: {big_info['model']} ({big_info['quantization']}, immers)"))
    else:
        print(style.dim("сухой прогон: эскалация выключена (--no-escalate)"))

    print(style.dim(f"порог уверенности: {args.threshold:g} по метрике {args.metric}; "
                    f"товаров: {len(picks)}"))

    system_prompt = schema.build_system_prompt()
    records: list[dict] = []
    local_seconds = 0.0
    fields_total = fields_escalated = 0
    items_escalated = 0
    by_heuristic: dict[str, int] = {}

    for index, pick in enumerate(picks, start=1):
        expected = pick["expected"]
        print()
        print(BAR)
        print(f"{style.bold(f'[{index:02d}/{len(picks)}]')} {pick['id']}  "
              f"{style.bold(pick['name'][:60])}  {style.dim(pick['level'])}")

        answer = small.generate(system_prompt, pick["name"])
        local_seconds += answer["latency_s"]
        suspicion = heuristics.analyze(
            pick["name"], answer["raw"], answer["tokens"],
            threshold=args.threshold, metric=args.metric,
            max_chars=args.max_chars, done_reason=answer["done_reason"],
        )
        small_obj = suspicion.obj

        print(f"  {style.cyan(f'{args.small_model} (ollama, ноутбук)')}  "
              f"{answer['latency_s']:.2f} c  {answer['gen_tokens']} ток.  "
              f"JSON: {'чистый' if suspicion.clean_json else 'с обёрткой' if small_obj else 'не разобран'}")
        for line in confidence_line(suspicion, args.metric, args.threshold):
            print(f"    {style.dim(line)}")

        for heuristic, reason in suspicion.triggered:
            print(f"  {style.yellow('⚠ ' + heuristic)} : {reason}")
            by_heuristic[heuristic] = by_heuristic.get(heuristic, 0)

        escalated = suspicion.suspect_fields
        fields_total += len(FIELD_ORDER)
        fields_escalated += len(escalated)
        items_escalated += int(bool(escalated))
        for heuristic, fields in suspicion.by_heuristic().items():
            by_heuristic[heuristic] = by_heuristic.get(heuristic, 0) + len(fields)

        big_answer = None
        merged = small_obj
        if not escalated:
            print(f"  {style.green('✓ сомнительных полей нет — остаётся на ' + args.small_model)}")
        elif big is None:
            fields_word = plural(len(escalated), "поля", "полей", "полей")
            print(f"  {style.dim('→ к эскалации ' + str(len(escalated)) + ' ' + fields_word + ': ' + ', '.join(escalated))}"
                  f" {style.dim('(сухой прогон, вызова нет)')}")
        else:
            print(f"  → эскалация {style.bold(str(len(escalated)))} "
                  f"{plural(len(escalated), 'поля', 'полей', 'полей')}: "
                  f"{style.bold(', '.join(escalated))}")
            try:
                big_answer = big.generate_one(pick["name"])
            except BigModelUnavailable as error:
                print(style.red(f"  ! эскалация не удалась: {error}"))
                return 2
            big_obj, _ = schema.parse_model_json(big_answer["raw"])
            big_answer["obj"] = big_obj
            merged = heuristics.merge(small_obj, big_obj, escalated)
            print(f"  {style.cyan(f'{BIG_MODEL} (immers)')}  "
                  f"{big_answer['latency_s']:.2f} c  {big_answer['gen_tokens']} ток.")
            for name in escalated:
                was = field_verdict(expected, small_obj, name)
                now = field_verdict(expected, merged, name)
                old_value = value_repr(small_obj.get(name)) if isinstance(small_obj, dict) else "—"
                new_value = value_repr(merged.get(name)) if isinstance(merged, dict) else "—"
                if now and not was:
                    verdict = style.green("✔ исправлено")
                elif was and not now:
                    verdict = style.red("✘ испорчено")
                elif now:
                    verdict = style.dim("= оба верны")
                else:
                    verdict = style.red("= оба неверны")
                print(f"    {name}: {old_value} → {new_value}   {verdict}")

        small_ok = sum(field_verdict(expected, small_obj, f) for f in FIELD_ORDER)
        merged_ok = sum(field_verdict(expected, merged, f) for f in FIELD_ORDER)
        summary = (f"  итог: {len(FIELD_ORDER) - len(escalated)} полей от {args.small_model}, "
                   f"{len(escalated)} от {BIG_MODEL} · верных полей "
                   f"{small_ok} → {merged_ok} из {len(FIELD_ORDER)}")
        print(style.green(summary) if merged_ok > small_ok
              else style.red(summary) if merged_ok < small_ok else style.dim(summary))

        if not answer["tokens_aligned"]:
            print(style.red("  ! символьные границы токенов съехали — уверенность"
                            " по полям считается не по тем токенам"))

        records.append({
            "id": pick["id"],
            "name": pick["name"],
            "url": pick.get("url", ""),
            "hard_flags": pick.get("hard_flags", []),
            "level": pick["level"],
            "expected": expected,
            "small": {"model": args.small_model, **answer, "obj": small_obj,
                      "clean_json": suspicion.clean_json},
            "confidence": suspicion.confidence,
            "escalated": escalated,
            "heuristics": suspicion.by_heuristic(),
            "reasons": [list(pair) for pair in suspicion.triggered],
            "big": ({"model": BIG_MODEL, **big_answer} if big_answer else None),
            "merged": merged,
        })
        if args.pause:
            time.sleep(args.pause)

    gpu_seconds = big.gpu_seconds if big else 0.0
    print()
    print(BAR)
    print(style.bold("итог прогона"))
    print(f"  товаров: {len(records)} · эскалировано хотя бы одно поле: {items_escalated} · "
          f"осталось целиком на {args.small_model}: {len(records) - items_escalated}")
    print(f"  полей всего: {fields_total} · эскалировано: {fields_escalated} "
          f"({fields_escalated / max(1, fields_total) * 100:.1f}%)")
    if by_heuristic:
        print("  по эвристикам (полей): " + " · ".join(
            f"{name} {count}" for name, count in sorted(by_heuristic.items())))
    print(f"  {args.small_model} на ноутбуке: {local_seconds:.1f} c (своё железо, ₽ не считаем)")
    if big:
        print(f"  {BIG_MODEL} на immers: {gpu_seconds:.1f} c GPU ≈ {rub(gpu_seconds):.2f} ₽ "
              f"за {big.calls} {plural(big.calls, 'вызов', 'вызова', 'вызовов')}")

    write_jsonl(args.out, records)
    meta = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "small": {**small_info, "local_seconds": round(local_seconds, 2)},
        "big": ({"model": BIG_MODEL, "gpu_seconds": round(gpu_seconds, 2),
                 "calls": big.calls, "rub": round(rub(gpu_seconds), 3)} if big else None),
        "thresholds": {"confidence": args.threshold, "metric": args.metric,
                       "max_chars": args.max_chars},
        "items": len(records),
        "items_escalated": items_escalated,
        "fields_total": fields_total,
        "fields_escalated": fields_escalated,
        "by_heuristic": by_heuristic,
        "dry_run": bool(args.no_escalate),
    }
    args.out.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nпрогон: {args.out}")
    return 0


def main() -> None:
    """Точка входа: разбирает аргументы и запускает прогон."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--picks", type=Path, default=PICKS_PATH)
    parser.add_argument("--out", type=Path, default=None,
                        help="по умолчанию results/route.jsonl, "
                             "а при --no-escalate results/dry_route.jsonl")
    parser.add_argument("--threshold", type=float, default=heuristics.DEFAULT_THRESHOLD)
    parser.add_argument("--metric", choices=("min", "mean"), default=heuristics.DEFAULT_METRIC)
    parser.add_argument("--max-chars", type=int, default=heuristics.DEFAULT_MAX_CHARS)
    parser.add_argument("--limit", type=int, help="взять только первые N товаров")
    parser.add_argument("--no-escalate", action="store_true",
                        help="сухой прогон: эвристики считаются, сильная модель не вызывается")
    parser.add_argument("--ollama-url", default=OLLAMA_URL)
    parser.add_argument("--small-model", default=SMALL_MODEL)
    parser.add_argument("--big-url", default=BIG_URL)
    parser.add_argument("--pause", type=float, default=0.0,
                        help="пауза между товарами, чтобы лог читался на видео")
    parser.add_argument("--no-color", dest="color", action="store_false",
                        help="без ANSI-раскраски")
    parser.set_defaults(color=sys.stdout.isatty())
    args = parser.parse_args()

    if args.out is None:
        args.out = RESULTS_DIR / ("dry_route.jsonl" if args.no_escalate else "route.jsonl")

    sys.exit(run(args))


if __name__ == "__main__":
    main()
