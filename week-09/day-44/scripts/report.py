#!/usr/bin/env python3
"""Сводит руки эксперимента дня 44 в один отчёт.

Меряется не «стало точнее», а точность против расхода: декомпозиция меняет
один вызов на несколько, и день 43 уже показал, что тарифицируется вызов,
а не поле. Поэтому рядом с качеством всегда стоят вызовы, токены и секунды,
а также производные — точность на 1000 сгенерированных токенов и на секунду.

Считается по всем рукам одним скорером дня 41 (`score.fields_equal`) на одном
эталоне, иначе сравнение бессмысленно.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import common
from common import (FIELD_ORDER, GPU_RUB_PER_HOUR, RESULTS_DIR, load_big_anchor, read_jsonl,
                    rub, schema, score)

ARM_TITLES = {
    "mono": "A — монолит (1 вызов)",
    "multi": "B — декомпозиция (10 вызовов)",
    "multi-lite": "B-lite — декомпозиция (3 вызова)",
    "mono-x4": "контроль — монолит ×4 + majority",
}

STAGE_TITLES = {
    "normalize": "этап 1 · нормализация",
    "category": "этап 2 · category",
    "form": "этап 2 · form",
    "purpose": "этап 2 · purpose",
    "is_set": "этап 2 · is_set",
    "classify_all": "этап 2 · всё одним вызовом",
    "brand": "этап 3 · brand",
    "line": "этап 3 · line",
    "shade": "этап 3 · shade",
    "volume": "этап 3 · volume",
    "pack_count": "этап 3 · pack_count",
    "extract_all": "этап 3 · всё одним вызовом",
    "mono": "монолит",
}


def field_ok(expected: dict, obj: dict | None, field: str) -> bool:
    """Верно ли одно поле предсказания."""
    if not obj:
        return False
    return score.fields_equal(field, expected.get(field), obj.get(field))


def evaluate(records: list[dict]) -> dict:
    """Считает качество, расход и производные метрики одной руки."""
    per_field = {field: 0 for field in FIELD_ORDER}
    per_level: dict[str, list[int]] = {}
    full_ok = 0
    parsed = 0
    invariant_clean = 0
    violations: dict[str, int] = {}

    for record in records:
        obj = record.get("obj")
        flags = [field_ok(record["expected"], obj, field) for field in FIELD_ORDER]
        for field, ok in zip(FIELD_ORDER, flags):
            per_field[field] += ok
        full_ok += all(flags)
        parsed += bool(obj)
        if obj and not schema.check_invariants(record["name"], obj):
            invariant_clean += 1
        bucket = per_level.setdefault(record["level"], [0, 0])
        bucket[0] += sum(flags)
        bucket[1] += len(FIELD_ORDER)
        for field in record.get("format_violations") or []:
            violations[field] = violations.get(field, 0) + 1

    total_fields = len(records) * len(FIELD_ORDER)
    usage = {
        "calls": sum(r["usage"]["calls"] for r in records),
        "prompt_tokens": sum(r["usage"]["prompt_tokens"] for r in records),
        "gen_tokens": sum(r["usage"]["gen_tokens"] for r in records),
        "seconds": round(sum(r["usage"]["seconds"] for r in records), 1),
    }
    correct = sum(per_field.values())

    by_stage: dict[str, dict] = {}
    for record in records:
        for stage, stats in record["usage"]["by_stage"].items():
            bucket = by_stage.setdefault(
                stage, {"calls": 0, "prompt_tokens": 0, "gen_tokens": 0, "seconds": 0.0})
            for key in ("calls", "prompt_tokens", "gen_tokens"):
                bucket[key] += stats[key]
            bucket["seconds"] = round(bucket["seconds"] + stats["seconds"], 1)

    return {
        "items": len(records),
        "field_accuracy": correct / total_fields if total_fields else 0.0,
        "full_ok": full_ok,
        "parsed": parsed,
        "invariant_clean": invariant_clean,
        "per_field": {f: per_field[f] / len(records) for f in FIELD_ORDER},
        "per_level": {level: hits / total for level, (hits, total) in sorted(per_level.items())},
        "violations": dict(sorted(violations.items(), key=lambda kv: -kv[1])),
        "usage": usage,
        "wall_seconds": sum(r.get("wall_s") or 0.0 for r in records),
        "by_stage": by_stage,
        "acc_per_1k_gen": correct / usage["gen_tokens"] * 1000 if usage["gen_tokens"] else 0.0,
        "fields_per_second": correct / usage["seconds"] if usage["seconds"] else 0.0,
    }


def evaluate_anchor(records: list[dict], anchor: dict[str, dict]) -> dict:
    """Считает потолок: ответы Qwen3-14B из дампа дня 41 на тех же примерах."""
    per_field = {field: 0 for field in FIELD_ORDER}
    full_ok = 0
    covered = 0
    for record in records:
        obj = anchor.get(record["id"])
        if record["id"] not in anchor:
            continue
        covered += 1
        flags = [field_ok(record["expected"], obj, field) for field in FIELD_ORDER]
        for field, ok in zip(FIELD_ORDER, flags):
            per_field[field] += ok
        full_ok += all(flags)
    total = covered * len(FIELD_ORDER)
    return {
        "items": covered,
        "field_accuracy": sum(per_field.values()) / total if total else 0.0,
        "full_ok": full_ok,
        "per_field": {f: per_field[f] / covered for f in FIELD_ORDER} if covered else {},
    }


def cascade_damage(records: list[dict]) -> dict | None:
    """Считает, сколько ошибок пришло из этапа 1.

    Нормализация — единственный этап, чей выход виден всем остальным. Оригинал
    названия идёт рядом с разметкой, поэтому испортить вход она не может
    полностью, но искажение всё равно попадает в контекст. Считаются случаи,
    когда в «чистом» названии пропал кусок, содержащий верный ответ.
    """
    damaged = 0
    lost_fields = 0
    checked = 0
    for record in records:
        marks = record.get("marks")
        if not marks:
            continue
        checked += 1
        clean = marks.get("clean") or ""
        haystack = schema.normalize_for_match(clean)
        lost = []
        for field in ("brand", "line", "shade"):
            expected = record["expected"].get(field)
            if not isinstance(expected, str) or not expected.strip():
                continue
            if schema.normalize_for_match(expected) not in haystack:
                lost.append(field)
        if lost:
            damaged += 1
            lost_fields += len(lost)
    if not checked:
        return None
    return {"checked": checked, "items_damaged": damaged, "fields_lost": lost_fields}


def pct(value: float) -> str:
    """Проценты с одним знаком, запятая как разделитель."""
    return f"{value * 100:.1f}%".replace(".", ",")


def render(data: dict) -> str:
    """Собирает отчёт в markdown."""
    arms = data["arms"]
    anchor = data["anchor"]
    baseline = arms.get("mono", {}).get("field_accuracy")

    lines = [
        "# День 44 — декомпозиция инференса: сводка",
        "",
        f"Выборка: {data['items']} примеров ({data['split']} дня 41) · модель {data['model']} · "
        f"скорер и эталон дня 41.",
        "",
        "## Качество против расхода",
        "",
        "| рука | пополевая | все 9 | вызовов | ток. промпта | ток. генерации | секунд | "
        + ("₽ | " if data.get("rate") else "")
        + "полей / 1000 ток. | полей / с |",
        "|---" * (9 + bool(data.get("rate"))) + "|",
    ]
    for arm, stats in arms.items():
        usage = stats["usage"]
        lines.append(
            f"| {ARM_TITLES.get(arm, arm)} | **{pct(stats['field_accuracy'])}** | "
            f"{stats['full_ok']} | {usage['calls']} | {usage['prompt_tokens']} | "
            f"{usage['gen_tokens']} | {usage['seconds']:.0f} | "
            + (f"{rub(stats['wall_seconds'], data['rate']):.2f} | " if data.get("rate") else "")
            + f"{stats['acc_per_1k_gen']:.1f} | {stats['fields_per_second']:.2f} |")
    if anchor:
        lines.append(
            f"| потолок — Qwen3-14B (дамп дня 41) | {pct(anchor['field_accuracy'])} | "
            f"{anchor['full_ok']} |" + " — |" * (6 + bool(data.get("rate"))))

    if baseline:
        lines += ["", "Дельта к монолиту:", ""]
        for arm, stats in arms.items():
            if arm == "mono":
                continue
            delta = (stats["field_accuracy"] - baseline) * 100
            calls = stats["usage"]["calls"] / max(arms["mono"]["usage"]["calls"], 1)
            tokens = stats["usage"]["prompt_tokens"] / max(arms["mono"]["usage"]["prompt_tokens"], 1)
            seconds = stats["usage"]["seconds"] / max(arms["mono"]["usage"]["seconds"], 1)
            lines.append(
                f"- **{ARM_TITLES.get(arm, arm)}**: {delta:+.1f} п.п. "
                f"за ×{calls:.1f} вызовов, ×{tokens:.1f} токенов промпта, ×{seconds:.1f} времени")

    lines += ["", "## Точность по полям", "",
              "| поле | " + " | ".join(ARM_TITLES.get(a, a) for a in arms)
              + (" | 14B |" if anchor else " |"),
              "|---" * (len(arms) + 1 + bool(anchor)) + "|"]
    for field in FIELD_ORDER:
        row = f"| `{field}` | " + " | ".join(pct(arms[a]["per_field"][field]) for a in arms)
        if anchor:
            row += f" | {pct(anchor['per_field'][field])}"
        lines.append(row + " |")

    lines += ["", "## Точность по уровням входа", "",
              "| уровень | " + " | ".join(ARM_TITLES.get(a, a) for a in arms) + " |",
              "|---" * (len(arms) + 1) + "|"]
    levels = sorted({lvl for stats in arms.values() for lvl in stats["per_level"]})
    for level in levels:
        lines.append(f"| {level} | "
                     + " | ".join(pct(arms[a]["per_level"].get(level, 0.0)) for a in arms) + " |")

    lines += ["", "## Соблюдение формата", "",
              "| рука | разобран ответ | прошёл инварианты дня 41 | нарушений формата по этапам |",
              "|---|---|---|---|"]
    for arm, stats in arms.items():
        bad = ", ".join(f"{k} {v}" for k, v in list(stats["violations"].items())[:5]) or "—"
        lines.append(f"| {ARM_TITLES.get(arm, arm)} | {stats['parsed']}/{stats['items']} | "
                     f"{stats['invariant_clean']}/{stats['items']} | {bad} |")

    for arm, stats in arms.items():
        if len(stats["by_stage"]) < 2:
            continue
        lines += ["", f"## Расход по этапам: {ARM_TITLES.get(arm, arm)}", "",
                  "| этап | вызовов | ток. промпта | ток. генерации | секунд |",
                  "|---|---|---|---|---|"]
        for stage, usage in stats["by_stage"].items():
            lines.append(f"| {STAGE_TITLES.get(stage, stage)} | {usage['calls']} | "
                         f"{usage['prompt_tokens']} | {usage['gen_tokens']} | "
                         f"{usage['seconds']:.0f} |")

    if data.get("cascade"):
        lines += ["", "## Каскад: вред нормализации", ""]
        for arm, damage in data["cascade"].items():
            lines.append(
                f"- **{ARM_TITLES.get(arm, arm)}**: у {damage['items_damaged']} товаров "
                f"из {damage['checked']} «чистое» название потеряло кусок, где стоял верный "
                f"ответ ({damage['fields_lost']} полей). Оригинал названия при этом уходил "
                f"на все этапы рядом с разметкой.")

    return "\n".join(lines) + "\n"


def main() -> None:
    """CLI: собирает отчёт по файлам прогонов в results/."""
    parser = argparse.ArgumentParser(description="Отчёт по рукам дня 44")
    parser.add_argument("--suffix", default="", help="суффикс файлов прогонов")
    parser.add_argument("--arms", default="mono,multi,multi-lite,mono-x4",
                        help="какие руки включить в отчёт, через запятую")
    parser.add_argument("--out", type=Path, help="куда положить markdown-отчёт")
    parser.add_argument("--rub-per-hour", type=float, default=0.0,
                        help=f"тариф арендованной ВМ; для immers.cloud {GPU_RUB_PER_HOUR}. "
                             "Ноль (по умолчанию) — прогон на своём железе, цены в отчёте нет")
    parser.add_argument("--no-anchor", action="store_true",
                        help="без потолка 14B (например, для прогона по train)")
    args = parser.parse_args()

    arms: dict[str, dict] = {}
    cascade: dict[str, dict] = {}
    records_for_anchor: list[dict] = []
    model = "—"
    split = "eval"

    for arm in args.arms.split(","):
        path = RESULTS_DIR / f"{arm}{args.suffix}.jsonl"
        if not path.exists():
            print(f"пропуск: нет {path.name}")
            continue
        records = read_jsonl(path)
        arms[arm] = evaluate(records)
        damage = cascade_damage(records)
        if damage:
            cascade[arm] = damage
        records_for_anchor = records_for_anchor or records
        meta_path = RESULTS_DIR / f"{arm}{args.suffix}.meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            model = meta["models"]["mono"]
            split = meta.get("split", split)

    if not arms:
        raise SystemExit("нет ни одного файла прогона")

    anchor = None
    if not args.no_anchor:
        anchor = evaluate_anchor(records_for_anchor, load_big_anchor())

    data = {
        "items": next(iter(arms.values()))["items"],
        "model": model,
        "split": split,
        "rate": args.rub_per_hour,
        "arms": arms,
        "anchor": anchor,
        "cascade": cascade,
    }

    text = render(data)
    out = args.out or RESULTS_DIR / f"report{args.suffix}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
