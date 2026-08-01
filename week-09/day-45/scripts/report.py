#!/usr/bin/env python3
"""Сводит прогоны разных политик в одну таблицу.

Три политики отвечают на один вопрос: стоит ли ставить дешёвую ступень перед
дорогой. «Всё классификатору» — нижняя граница, «всё большой модели» — верхняя,
двухуровневый разбор должен лежать между ними и близко к верхней при заметно
меньшем числе вызовов.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import RESULTS_DIR, read_jsonl, rub

TITLES = {
    "all-small": "всё решает классификатор",
    "two-tier": "два уровня: классификатор + Qwen3-14B",
    "all-big": "всё уходит к Qwen3-14B",
}

ORDER = ("all-small", "two-tier", "all-big")


def load_run(name: str) -> tuple[list[dict], dict] | None:
    """Читает прогон и его мета-файл по имени без расширения."""
    path = RESULTS_DIR / f"{name}.jsonl"
    meta_path = RESULTS_DIR / f"{name}.meta.json"
    if not path.exists() or not meta_path.exists():
        return None
    return read_jsonl(path), json.loads(meta_path.read_text(encoding="utf-8"))


def by_level(records: list[dict]) -> dict[str, float]:
    """Точность по трём уровням сложности входа."""
    buckets: dict[str, list[bool]] = {}
    for record in records:
        buckets.setdefault(record["level"], []).append(record["final"] == record["truth"])
    return {level: sum(hits) / len(hits) for level, hits in sorted(buckets.items())}


def pct(value: float) -> str:
    """Проценты с одним знаком после запятой."""
    return f"{value * 100:.1f}%".replace(".", ",")


def render(runs: dict[str, tuple[list[dict], dict]], suffix: str) -> str:
    """Собирает отчёт в markdown."""
    lines = [
        "# День 45 — классификатор перед большой моделью: сводка",
        "",
        f"Выборка: {len(next(iter(runs.values()))[0])} проверочных примеров дня 41. "
        "Категория товара, 23 варианта.",
        "",
        "| политика | точность | закрыл уровень 1 | вызовов Qwen3-14B | "
        "задержка на товар | время большой модели | ₽ |",
        "|---|---|---|---|---|---|---|",
    ]
    for policy in ORDER:
        if policy not in runs:
            continue
        records, meta = runs[policy]
        summary = meta["summary"]
        lines.append(
            f"| {TITLES[policy]} | **{pct(summary['accuracy'])}** | "
            f"{summary['kept_by_classifier']} | {summary['big_calls']} | "
            f"{summary['avg_latency_total_s']:.2f} с | "
            f"{summary['big_seconds']:.0f} с | {rub(summary['big_seconds']):.2f} |")

    if "two-tier" in runs:
        summary = runs["two-tier"][1]["summary"]
        lines += [
            "",
            "## Что дал второй уровень",
            "",
            f"- отправлено наверх: **{summary['escalated']}** из {summary['total']} "
            f"({summary['escalated'] / summary['total'] * 100:.0f}%)",
            f"- большая модель исправила: **{summary['fixed_by_big']}**, "
            f"испортила: {summary['broken_by_big']}",
            f"- верных среди оставленных классификатору: "
            f"{pct(summary['accuracy_kept'])}",
        ]
        if summary["accuracy_escalated"] is not None:
            lines.append(f"- верных среди ушедших наверх: {pct(summary['accuracy_escalated'])}")

    lines += ["", "## Точность по сложности входа", "",
              "| уровень | " + " | ".join(TITLES[p] for p in ORDER if p in runs) + " |",
              "|---" * (1 + sum(p in runs for p in ORDER)) + "|"]
    levels = sorted({level for records, _ in runs.values() for level in by_level(records)})
    computed = {policy: by_level(records) for policy, (records, _) in runs.items()}
    for level in levels:
        row = f"| {level} | " + " | ".join(
            pct(computed[p].get(level, 0.0)) for p in ORDER if p in runs)
        lines.append(row + " |")

    lines += ["", "## Задержка по ступеням", "",
              "| ступень | среднее время |", "|---|---|"]
    if "two-tier" in runs:
        summary = runs["two-tier"][1]["summary"]
        lines += [
            f"| уровень 1 (классификатор) | {summary['avg_latency_small_ms']:.2f} мс |",
            f"| уровень 2 (Qwen3-14B) | {summary['avg_latency_big_s']:.2f} с |",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    """CLI: собирает отчёт по сохранённым прогонам."""
    parser = argparse.ArgumentParser(description="Отчёт по политикам дня 45")
    parser.add_argument("--suffix", default="", help="суффикс имён прогонов, например _mock")
    parser.add_argument("--out", type=Path, help="куда положить markdown-отчёт")
    args = parser.parse_args()

    runs: dict[str, tuple[list[dict], dict]] = {}
    for policy in ORDER:
        loaded = load_run(f"{policy}{args.suffix}")
        if loaded is None:
            print(f"пропуск: нет прогона {policy}{args.suffix}")
            continue
        runs[policy] = loaded

    if not runs:
        raise SystemExit("нет ни одного прогона")

    text = render(runs, args.suffix)
    out = args.out or RESULTS_DIR / f"report{args.suffix}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
