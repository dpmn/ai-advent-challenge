#!/usr/bin/env python3
"""Считает routing против якорей. Работает офлайн, GPU не нужен.

Одна цифра сама по себе бессмысленна: «routing дал 74% пополевой точности» —
это хорошо или плохо? Поэтому всё сравнивается с якорями:

  **1. всё на слабой**      что было бы без routing-а вообще (нижняя граница)
  **2. routing по полям**   фактический прогон: эскалированные поля от 14B
  **3. routing по ответу**  при сомнении берём ответ 14B целиком, а не поля
  **4. всё на сильной**     потолок качества и цены (верхняя граница)
  **5. случайная эскалация** та же доля полей, выбранная слепо
  **6. статичный раздел**   category/pack_count/form/purpose всегда к 14B

Якорь 5 — главный контроль, которого не хватило в дне 42. Он отвечает на
вопрос «эвристика выбрала правильные поля или просто выбрала много?»:
если слепой выбор той же доли даёт то же качество, сигнала нет. Считается
двумя сотнями перестановок, чтобы вместо одного шумного числа получить
диапазон.

Якорь 6 — бесплатный конкурент: раздел полей выводится из таблицы дня 41
заранее, без единого сигнала. Эвристика, которая его не обгоняет, дорого
переоткрывает уже известное.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import statistics

import heuristics
from common import (DAY41_BIG_DUMP, FIELD_ORDER, RESULTS_DIR, read_jsonl, rub, schema, score)

SWEEP = [0.0, 0.3, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95, 0.99]
RANDOM_TRIALS = 200
RANDOM_SEED = 43

# Поля, где 0.6B систематически слаба по sweep-у дня 41: category 31%,
# pack_count 34%, form 57%, purpose 56%. Политика «эти всегда к сильной»
# не требует ни logprob-ов, ни кода — только таблицы, которая уже есть.
STATIC_FIELDS = ("category", "pack_count", "form", "purpose")


def field_ok(expected: dict, obj: dict | None, name: str) -> bool:
    """Совпадает ли одно поле с эталоном."""
    if not isinstance(obj, dict):
        return False
    return score.fields_equal(name, expected.get(name), obj.get(name))


def big_obj_for(record: dict, anchor: dict[str, dict]) -> dict | None:
    """Ответ сильной модели по товару: живой, если он был, иначе якорный.

    Живой ответ приоритетнее — именно он попал в гибрид на прогоне. Якорный
    нужен для полей, которые эскалированы не были: без них ни одну
    альтернативную политику не пересобрать.
    """
    live = (record.get("big") or {}).get("obj")
    if isinstance(live, dict):
        return live
    return (anchor.get(record["id"]) or {}).get("obj")


def evaluate(records: list[dict], sources: list[list[str]], big_objs: list[dict | None],
             per_call_seconds: float, measured_seconds: float | None = None) -> dict:
    """Считает метрики одной политики.

    `sources[i]` — поля, которые в этой политике берутся у сильной модели для
    i-го товара. Стоимость считается по вызовам, а не по полям: сильную модель
    просят ответить целиком, поэтому один товар с одним сомнительным полем
    стоит ровно столько же, сколько товар с девятью.
    """
    fields_correct = exact = calls = escalated_fields = 0
    by_level: dict[str, dict] = {}
    by_field = {name: {"correct": 0, "escalated": 0} for name in FIELD_ORDER}

    for record, fields, big_obj in zip(records, sources, big_objs):
        expected = record["expected"]
        small_obj = record["small"]["obj"]
        merged = heuristics.merge(small_obj, big_obj, fields)
        correct = [field_ok(expected, merged, name) for name in FIELD_ORDER]

        fields_correct += sum(correct)
        exact += int(all(correct))
        calls += int(bool(fields))
        escalated_fields += len(fields)

        bucket = by_level.setdefault(record["level"], {"n": 0, "correct": 0, "fields": 0,
                                                       "calls": 0, "escalated": 0})
        bucket["n"] += 1
        bucket["correct"] += sum(correct)
        bucket["fields"] += len(FIELD_ORDER)
        bucket["calls"] += int(bool(fields))
        bucket["escalated"] += len(fields)

        for name, ok in zip(FIELD_ORDER, correct):
            by_field[name]["correct"] += int(ok)
            by_field[name]["escalated"] += int(name in fields)

    total_fields = len(records) * len(FIELD_ORDER)
    gpu = measured_seconds if measured_seconds is not None else calls * per_call_seconds
    return {
        "items": len(records),
        "fields_total": total_fields,
        "fields_correct": fields_correct,
        "field_accuracy": fields_correct / total_fields if total_fields else 0.0,
        "exact": exact,
        "exact_accuracy": exact / len(records) if records else 0.0,
        "calls": calls,
        "escalated_fields": escalated_fields,
        "escalated_share": escalated_fields / total_fields if total_fields else 0.0,
        "gpu_seconds": round(gpu, 2),
        "gpu_measured": measured_seconds is not None,
        "rub": round(rub(gpu), 3),
        "by_level": by_level,
        "by_field": by_field,
    }


def random_anchor(records: list[dict], big_objs: list[dict | None], k: int,
                  per_call_seconds: float, trials: int = RANDOM_TRIALS) -> dict:
    """Якорь «случайная эскалация того же объёма».

    Выбор слепой: k пар (товар, поле) из всех, равновероятно. Одна выборка —
    шумное число, поэтому берётся распределение по `trials` перестановкам:
    сравнивать фактический routing надо с диапазоном, а не с одним значением.
    """
    rng = random.Random(RANDOM_SEED)
    pairs = [(i, name) for i in range(len(records)) for name in FIELD_ORDER]
    accuracies, calls_list = [], []
    for _ in range(trials):
        chosen = rng.sample(pairs, min(k, len(pairs)))
        sources: list[list[str]] = [[] for _ in records]
        for index, name in chosen:
            sources[index].append(name)
        stats = evaluate(records, sources, big_objs, per_call_seconds)
        accuracies.append(stats["field_accuracy"])
        calls_list.append(stats["calls"])
    accuracies.sort()

    def percentile(values: list[float], q: float) -> float:
        """Значение квантиля по отсортированному списку."""
        return values[min(len(values) - 1, int(q * len(values)))]

    return {
        "trials": trials,
        "escalated_fields": k,
        "field_accuracy": statistics.median(accuracies),
        "p05": percentile(accuracies, 0.05),
        "p95": percentile(accuracies, 0.95),
        "calls": statistics.median(calls_list),
        "gpu_seconds": round(statistics.median(calls_list) * per_call_seconds, 2),
        "rub": round(rub(statistics.median(calls_list) * per_call_seconds), 3),
    }


def suspicion_fields(record: dict, threshold: float, metric: str, max_chars: int) -> list[str]:
    """Пересобирает решение эвристик по записанным данным при другом пороге."""
    small = record["small"]
    return heuristics.analyze(
        record["name"], small["raw"], small["tokens"],
        threshold=threshold, metric=metric, max_chars=max_chars,
        done_reason=small.get("done_reason"),
    ).suspect_fields


def heuristic_breakdown(records: list[dict], big_objs: list[dict | None]) -> dict:
    """Раскладывает каждое эскалированное поле по исходу: помогло или нет.

    Это и есть проверка эвристики. Поле, отправленное наверх, могло быть
    исправлено, испорчено или не измениться по существу — и без этой
    таблицы «эскалировали 38% полей» не значит ничего.
    """
    stats: dict[str, dict] = {}
    missed = {"fields": 0, "items": 0}

    for record, big_obj in zip(records, big_objs):
        expected = record["expected"]
        small_obj = record["small"]["obj"]
        escalated = record["escalated"]
        by_heuristic = record.get("heuristics") or {}

        item_missed = 0
        for name in FIELD_ORDER:
            small_ok = field_ok(expected, small_obj, name)
            big_ok = field_ok(expected, big_obj, name)
            if name not in escalated:
                if big_ok and not small_ok:
                    missed["fields"] += 1
                    item_missed += 1
                continue
            if big_ok and not small_ok:
                outcome = "исправлено"
            elif small_ok and not big_ok:
                outcome = "испорчено"
            elif small_ok:
                outcome = "оба верны"
            else:
                outcome = "оба неверны"
            for heuristic, fields in by_heuristic.items():
                if name in fields:
                    bucket = stats.setdefault(heuristic, {"полей": 0, "исправлено": 0,
                                                          "испорчено": 0, "оба верны": 0,
                                                          "оба неверны": 0})
                    bucket["полей"] += 1
                    bucket[outcome] += 1
        missed["items"] += int(bool(item_missed))

    return {"by_heuristic": stats, "missed": missed}


def dump_crosscheck(records: list[dict], anchor: dict[str, dict]) -> dict | None:
    """Сверяет живые ответы 14B с дампом дня 41 на тех же товарах.

    Дамп снят месяцем раньше, на другой машине, батчем — но той же моделью,
    в том же квантовании и по тому же промпту. Совпадение означает, что
    цифры дня 41 воспроизводимы; расхождение — что сравнивать с ними
    напрямую нельзя, и это надо знать до того, как выводы написаны.
    """
    if not DAY41_BIG_DUMP.exists() or not anchor:
        return None
    dump = {r["id"]: r for r in read_jsonl(DAY41_BIG_DUMP)}
    agree = compared = dump_correct = anchor_correct = 0
    for record in records:
        row = dump.get(record["id"])
        anchor_obj = (anchor.get(record["id"]) or {}).get("obj")
        if row is None or not isinstance(anchor_obj, dict):
            continue
        dump_obj, _ = schema.parse_model_json(row.get("raw") or "")
        expected = record["expected"]
        for name in FIELD_ORDER:
            compared += 1
            dump_ok = field_ok(expected, dump_obj, name)
            anchor_ok = field_ok(expected, anchor_obj, name)
            dump_correct += int(dump_ok)
            anchor_correct += int(anchor_ok)
            if isinstance(dump_obj, dict):
                agree += int(score.fields_equal(name, dump_obj.get(name), anchor_obj.get(name)))
    if not compared:
        return None
    return {
        "fields": compared,
        "agreement": agree / compared,
        "dump_accuracy": dump_correct / compared,
        "anchor_accuracy": anchor_correct / compared,
    }


def pct(value: float | None) -> str:
    """Проценты для таблиц."""
    return "—" if value is None else f"{value * 100:.1f}%"


def render(data: dict) -> str:
    """Собирает markdown-отчёт."""
    meta = data["meta"]
    policies = data["policies"]
    rnd = data["random"]
    order = [
        ("small", "1 — всё на слабой"),
        ("routing_fields", "2 — routing по полям"),
        ("routing_item", "3 — routing по ответу"),
        ("big", "4 — всё на сильной"),
        (None, "5 — случайная эскалация"),
        ("static", "6 — статичный раздел полей"),
    ]

    lines = [
        "# День 43 — routing между моделями",
        "",
        f"Слабая: `{meta['small_model']}` ({meta.get('small_quant', '?')}, Ollama, ноутбук). "
        f"Сильная: `{meta['big_model']}` (4-bit NF4, immers). "
        f"Товаров: **{meta['items']}**, полей: **{meta['items'] * len(FIELD_ORDER)}**. "
        f"Порог уверенности: **{meta['threshold']:g}** по метрике `{meta['metric']}`."
        + (" Порог отличается от того, с которым шёл прогон, поэтому политика 2 "
           "пересобрана офлайн из записанных ответов, а не измерена."
           if meta.get("simulated") else ""),
        "",
        "## Routing против якорей",
        "",
        "| политика | пополевая точность | все 9 полей | эскалировано полей | вызовов 14B | GPU, с | ₽ |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, title in order:
        if key is None:
            lines.append(
                f"| {title} | {pct(rnd['field_accuracy'])} "
                f"({pct(rnd['p05'])}…{pct(rnd['p95'])}) | — | {rnd['escalated_fields']} | "
                f"{rnd['calls']:.0f} | ~{rnd['gpu_seconds']:.0f} | ~{rnd['rub']:.2f} |"
            )
            continue
        stats = policies[key]
        mark = "" if stats["gpu_measured"] else "~"
        lines.append(
            f"| {title} | **{pct(stats['field_accuracy'])}** | {pct(stats['exact_accuracy'])} | "
            f"{stats['escalated_fields']} ({pct(stats['escalated_share'])}) | {stats['calls']} | "
            f"{mark}{stats['gpu_seconds']:.0f} | {mark}{stats['rub']:.2f} |"
        )
    lines += [
        "",
        f"Случайная эскалация — медиана и диапазон 5–95% по {rnd['trials']} перестановкам "
        "того же объёма. `~` — время оценено по средней стоимости вызова, а не измерено.",
        "",
    ]

    breakdown = data["breakdown"]
    lines += ["## Что дала эскалация по эвристикам", "",
              "| эвристика | полей | исправлено | испорчено | оба верны | оба неверны |",
              "|---|---|---|---|---|---|"]
    for name in sorted(breakdown["by_heuristic"]):
        bucket = breakdown["by_heuristic"][name]
        lines.append(f"| {name} | {bucket['полей']} | **{bucket['исправлено']}** | "
                     f"{bucket['испорчено']} | {bucket['оба верны']} | {bucket['оба неверны']} |")
    missed = breakdown["missed"]
    lines += ["", f"Пропущено эвристиками: **{missed['fields']} полей** "
                  f"(в {missed['items']} товарах) слабая модель ответила неверно, сильная верно, "
                  "но поле не эскалировано.", ""]

    lines += ["## Порог уверенности", "",
              "| порог | эскалировано полей | вызовов 14B | пополевая точность | все 9 | ₽ |",
              "|---|---|---|---|---|---|"]
    for row in data["sweep"]:
        mark = " ←" if row["threshold"] == meta["threshold"] else ""
        lines.append(f"| {row['threshold']:g}{mark} | {row['escalated_fields']} "
                     f"({pct(row['escalated_share'])}) | {row['calls']} | "
                     f"{pct(row['field_accuracy'])} | {pct(row['exact_accuracy'])} | "
                     f"~{row['rub']:.2f} |")
    lines += ["", "Порог 0 — это только эвристика #1 (constraint) и #3 (length): "
                  "уверенность ниже нуля не бывает, поэтому #2 не срабатывает.", ""]

    lines += ["## По уровням входов", "",
              "| уровень | товаров | слабая | routing | сильная | эскалировано полей |",
              "|---|---|---|---|---|---|"]
    for level in ("корректные", "пограничные", "шумные"):
        small_bucket = policies["small"]["by_level"].get(level)
        if not small_bucket:
            continue
        route_bucket = policies["routing_fields"]["by_level"][level]
        big_bucket = policies["big"]["by_level"][level]
        lines.append(
            f"| {level} | {small_bucket['n']} | "
            f"{pct(small_bucket['correct'] / small_bucket['fields'])} | "
            f"{pct(route_bucket['correct'] / route_bucket['fields'])} | "
            f"{pct(big_bucket['correct'] / big_bucket['fields'])} | "
            f"{route_bucket['escalated']} |"
        )

    lines += ["", "## По полям", "",
              "| поле | слабая | routing | сильная | эскалировано |", "|---|---|---|---|---|"]
    items = meta["items"]
    for name in FIELD_ORDER:
        small_field = policies["small"]["by_field"][name]
        route_field = policies["routing_fields"]["by_field"][name]
        big_field = policies["big"]["by_field"][name]
        lines.append(f"| `{name}` | {pct(small_field['correct'] / items)} | "
                     f"{pct(route_field['correct'] / items)} | "
                     f"{pct(big_field['correct'] / items)} | {route_field['escalated']} |")

    check = data.get("crosscheck")
    if check:
        lines += ["", "## Сверка с дампом дня 41", "",
                  f"Живые ответы 14B против `responses100.jsonl`, снятого месяцем раньше на "
                  f"другой машине: совпадение по полям **{pct(check['agreement'])}**, "
                  f"пополевая точность дампа {pct(check['dump_accuracy'])} против "
                  f"{pct(check['anchor_accuracy'])} у живого прогона."]

    if meta.get("local_seconds"):
        lines += ["", "## Стоимость прогона", "",
                  f"Слабая модель: {meta['local_seconds']:.0f} с на ноутбуке (своё железо, "
                  "в рублях не считаем). "
                  f"Сильная: {meta.get('gpu_seconds', 0):.0f} с GPU ≈ "
                  f"{rub(meta.get('gpu_seconds', 0)):.2f} ₽ на живом прогоне плюс "
                  f"{meta.get('anchor_gpu_seconds', 0):.0f} с ≈ "
                  f"{rub(meta.get('anchor_gpu_seconds', 0)):.2f} ₽ на якорь «всё на сильной»."]

    return "\n".join(lines) + "\n"


def main() -> None:
    """Точка входа: считает все политики и пишет отчёт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=RESULTS_DIR / "route.jsonl")
    parser.add_argument("--anchor", type=Path, default=RESULTS_DIR / "anchor_big.jsonl")
    parser.add_argument("--out", type=Path, default=None,
                        help="по умолчанию results/report.md, а при другом пороге — "
                             "results/report_thrX.md, чтобы не затирать основной отчёт")
    parser.add_argument("--threshold", type=float,
                        help="порог для таблиц (по умолчанию тот, с которым шёл прогон)")
    args = parser.parse_args()

    records = read_jsonl(args.runs)
    anchor_rows = read_jsonl(args.anchor) if args.anchor.exists() else []
    anchor = {r["id"]: r["big"] for r in anchor_rows}
    if not anchor:
        raise SystemExit(f"нет якоря «всё на сильной»: {args.anchor}. Сначала anchor_big.py")

    run_meta_path = args.runs.with_suffix(".meta.json")
    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.exists() else {}
    anchor_meta_path = args.anchor.with_suffix(".meta.json")
    anchor_meta = (json.loads(anchor_meta_path.read_text(encoding="utf-8"))
                   if anchor_meta_path.exists() else {})

    thresholds = run_meta.get("thresholds") or {}
    threshold = args.threshold if args.threshold is not None else thresholds.get("confidence", 0.75)
    metric = thresholds.get("metric", heuristics.DEFAULT_METRIC)
    max_chars = thresholds.get("max_chars", heuristics.DEFAULT_MAX_CHARS)

    if args.out is None:
        suffix = f"{threshold:g}".replace(".", "")
        args.out = RESULTS_DIR / ("report.md" if args.threshold is None
                                  else f"report_thr{suffix}.md")

    big_objs = [big_obj_for(record, anchor) for record in records]

    # Средняя цена одного онлайнового вызова сильной модели — из живого прогона,
    # где батча нет. Якорный проход шёл батчами и на товар дешевле; смешивать
    # эти две цены в одной таблице нельзя.
    live_calls = [r["big"]["latency_s"] for r in records if r.get("big")]
    per_call = (sum(live_calls) / len(live_calls)) if live_calls else (
        (anchor_meta.get("gpu_seconds") or 0.0) / max(1, len(records)))

    # Политика 2 — это факт прогона. Но если в отчёте запрошен другой порог,
    # честно пересобрать её из записанных данных, а не оставить фактическую
    # строку под чужим заголовком: иначе таблица врёт о том, что показывает.
    simulated = abs(threshold - thresholds.get("confidence", threshold)) > 1e-9
    live_fields = ([suspicion_fields(r, threshold, metric, max_chars) for r in records]
                   if simulated else [r["escalated"] for r in records])
    all_fields = [list(FIELD_ORDER) for _ in records]
    item_fields = [list(FIELD_ORDER) if r["escalated"] else [] for r in records]
    static_fields = [[f for f in STATIC_FIELDS] for _ in records]
    none_fields: list[list[str]] = [[] for _ in records]

    live_gpu = (run_meta.get("big") or {}).get("gpu_seconds")
    policies = {
        "small": evaluate(records, none_fields, big_objs, per_call, measured_seconds=0.0),
        "routing_fields": evaluate(records, live_fields, big_objs, per_call,
                                   measured_seconds=None if simulated else live_gpu),
        "routing_item": evaluate(records, item_fields, big_objs, per_call),
        "big": evaluate(records, all_fields, big_objs, per_call,
                        measured_seconds=anchor_meta.get("gpu_seconds")),
        "static": evaluate(records, static_fields, big_objs, per_call),
    }

    escalated_total = sum(len(f) for f in live_fields)
    rnd = random_anchor(records, big_objs, escalated_total, per_call)

    sweep = []
    for value in SWEEP:
        sources = [suspicion_fields(r, value, metric, max_chars) for r in records]
        stats = evaluate(records, sources, big_objs, per_call)
        sweep.append({"threshold": value, **{k: stats[k] for k in
                                             ("escalated_fields", "escalated_share", "calls",
                                              "field_accuracy", "exact_accuracy", "rub")}})

    data = {
        "meta": {
            "items": len(records),
            "small_model": (run_meta.get("small") or {}).get("model", "qwen3:0.6b"),
            "small_quant": (run_meta.get("small") or {}).get("quantization"),
            "big_model": (run_meta.get("big") or {}).get("model", "qwen3-14b"),
            "threshold": threshold,
            "metric": metric,
            "simulated": simulated,
            "local_seconds": (run_meta.get("small") or {}).get("local_seconds"),
            "gpu_seconds": (run_meta.get("big") or {}).get("gpu_seconds", 0.0),
            "anchor_gpu_seconds": anchor_meta.get("gpu_seconds", 0.0),
        },
        "policies": policies,
        "random": rnd,
        "breakdown": heuristic_breakdown(records, big_objs),
        "sweep": sweep,
        "crosscheck": dump_crosscheck(records, anchor),
    }

    report = render(data)
    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    args.out.with_suffix(".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"отчёт: {args.out}")


if __name__ == "__main__":
    main()
