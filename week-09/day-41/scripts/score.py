#!/usr/bin/env python3
"""Считает метрики качества извлечения по семи критериям.

Один и тот же скрипт меряет базовую модель сегодня и зафайнтюненную потом —
иначе цифры «до» и «после» будут несопоставимы.

Вход: JSONL с полями id, name, expected (эталон), raw (сырой ответ модели).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import schema

DAY_DIR = Path(__file__).resolve().parent.parent

# Классы сложности, по которым считаем отдельный срез: на них виден
# основной выигрыш тюна, а в общем среднем они тонут.
UNIT_CLASSES = ("dirty_unit", "multi_volume")


def fields_equal(field: str, expected, actual) -> bool:
    """Сравнивает одно поле эталона и предсказания. null == null считается совпадением."""
    if field == "volume":
        if expected is None or actual is None:
            return expected is None and actual is None
        if not isinstance(actual, dict) or set(actual) != {"value", "unit"}:
            return False
        return (round(float(expected["value"]), 3) == round(float(actual.get("value", -1) or -1), 3)
                and expected["unit"] == actual.get("unit"))
    if field == "purpose":
        if not isinstance(actual, list):
            return False
        return set(expected or []) == set(actual)
    return expected == actual


def schema_compliant(obj: dict) -> bool:
    """Все девять ключей на месте и типы допустимые."""
    if set(obj) != set(schema.FIELD_ORDER):
        return False
    if not isinstance(obj.get("is_set"), bool):
        return False
    if not isinstance(obj.get("purpose"), list):
        return False
    pack = obj.get("pack_count")
    if pack is not None and (not isinstance(pack, int) or isinstance(pack, bool) or pack < 1):
        return False
    volume = obj.get("volume")
    if volume is not None and (not isinstance(volume, dict) or set(volume) != {"value", "unit"}):
        return False
    for field in ("brand", "line", "shade", "category", "form"):
        value = obj.get(field)
        if value is not None and not isinstance(value, str):
            return False
    return True


def vocab_compliant(obj: dict) -> bool:
    """category и form лежат в закрытых словарях."""
    if obj.get("category") not in schema.vocab("category"):
        return False
    form = obj.get("form")
    if form is not None and form not in schema.vocab("form"):
        return False
    return all(p in schema.vocab("purpose") for p in obj.get("purpose") or [])


def hallucinated_fields(name: str, obj: dict) -> list[str]:
    """Поля, заполненные значением, которого нет во входной строке."""
    haystack = schema.normalize_for_match(name)
    bad = []
    for field in ("brand", "line", "shade"):
        value = obj.get(field)
        if isinstance(value, str) and value.strip():
            if schema.normalize_for_match(value) not in haystack:
                bad.append(field)
    return bad


def score(records: list[dict]) -> dict:
    """Считает все метрики по списку предсказаний."""
    total = len(records)
    parsed_clean = 0
    parsed_any = 0
    schema_ok = 0
    vocab_ok = 0
    hallucinated = 0
    lengths: list[int] = []
    field_hits: Counter = Counter()
    field_total: Counter = Counter()
    unit_hits = unit_total = 0
    by_class: dict[str, list[bool]] = defaultdict(list)

    for record in records:
        raw = record.get("raw") or ""
        lengths.append(len(raw))
        expected = record["expected"]
        obj, clean = schema.parse_model_json(raw)

        if obj is not None:
            parsed_any += 1
            if clean:
                parsed_clean += 1
            if schema_compliant(obj):
                schema_ok += 1
            if vocab_compliant(obj):
                vocab_ok += 1
            if hallucinated_fields(record["name"], obj):
                hallucinated += 1

        for field in schema.FIELD_ORDER:
            field_total[field] += 1
            hit = obj is not None and fields_equal(field, expected.get(field), obj.get(field))
            if hit:
                field_hits[field] += 1
            if field == "volume":
                flags = record.get("hard_flags") or []
                if any(f in flags for f in UNIT_CLASSES):
                    unit_total += 1
                    unit_hits += int(hit)
                for flag in flags:
                    by_class[flag].append(hit)

    field_rates = {f: field_hits[f] / field_total[f] for f in schema.FIELD_ORDER}
    return {
        "total": total,
        "json_valid_clean": parsed_clean / total if total else 0.0,
        "json_valid_any": parsed_any / total if total else 0.0,
        "schema_compliance": schema_ok / total if total else 0.0,
        "vocab_compliance": vocab_ok / total if total else 0.0,
        "hallucination_rate": hallucinated / total if total else 0.0,
        "avg_output_chars": sum(lengths) / total if total else 0.0,
        "field_exact": field_rates,
        "field_exact_mean": sum(field_rates.values()) / len(field_rates),
        "unit_normalization": unit_hits / unit_total if unit_total else None,
        "unit_sample": unit_total,
        "volume_by_class": {k: sum(v) / len(v) for k, v in sorted(by_class.items()) if v},
    }


def render(metrics: dict, title: str) -> str:
    """Собирает markdown-отчёт по метрикам."""
    pct = lambda x: f"{x * 100:.1f}%"
    lines = [
        f"# {title}",
        "",
        f"Примеров: **{metrics['total']}**",
        "",
        "| # | метрика | значение |",
        "|---|---|---|",
        f"| 1 | JSON-валидность (чистый ответ) | {pct(metrics['json_valid_clean'])} |",
        f"|   | JSON-валидность (с извлечением из обёрток) | {pct(metrics['json_valid_any'])} |",
        f"| 2 | Schema compliance | {pct(metrics['schema_compliance'])} |",
        f"| 3 | Vocab compliance | {pct(metrics['vocab_compliance'])} |",
        f"| 4 | Per-field exact match (среднее) | {pct(metrics['field_exact_mean'])} |",
        f"| 5 | Unit normalization (n={metrics['unit_sample']}) | "
        f"{pct(metrics['unit_normalization']) if metrics['unit_normalization'] is not None else 'нет выборки'} |",
        f"| 6 | Hallucination rate | {pct(metrics['hallucination_rate'])} |",
        f"| 7 | Средняя длина ответа | {metrics['avg_output_chars']:.0f} символов |",
        "",
        "## Per-field exact match по полям",
        "",
        "| поле | точность |",
        "|---|---|",
    ]
    for field, rate in sorted(metrics["field_exact"].items(), key=lambda kv: kv[1]):
        lines.append(f"| `{field}` | {pct(rate)} |")

    if metrics["volume_by_class"]:
        lines += ["", "## Точность `volume` по классам сложности", "",
                  "| класс | точность |", "|---|---|"]
        for cls, rate in sorted(metrics["volume_by_class"].items(), key=lambda kv: kv[1]):
            lines.append(f"| `{cls}` | {pct(rate)} |")

    return "\n".join(lines) + "\n"


def main() -> None:
    """Точка входа: считает метрики и печатает/сохраняет отчёт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True, help="JSONL с id/name/expected/raw")
    parser.add_argument("--out", type=Path, help="куда записать markdown-отчёт")
    parser.add_argument("--title", default="Метрики")
    args = parser.parse_args()

    with args.predictions.open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]

    metrics = score(records)
    report = render(metrics, args.title)
    print(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        metrics_path = args.out.with_suffix(".json")
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"отчёт: {args.out}\nметрики: {metrics_path}")


if __name__ == "__main__":
    main()
