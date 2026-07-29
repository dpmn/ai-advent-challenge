#!/usr/bin/env python3
"""Сводит baseline нескольких моделей в одну таблицу — выбор кандидата под тюн.

Метрики считает `score.score()`, тот же код, что мерил Qwen3-14B. Ничего
не пересчитывается по-своему, иначе сравнение поехало бы.

Главное, что видно в сводке и не видно в отдельных отчётах: где проходит
граница между «модель не тянет задачу» и «модель не тянет формат». Второе
лечится тюном в первую очередь, первое — не лечится вовсе, и путать их
при выборе кандидата дорого.

Запускать локально, GPU не нужен.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import score as scorer

_HERE = Path(__file__).resolve().parent
DEFAULT_DIR = _HERE.parent / "baseline" / "sweep"

# Порядок вывода — по возрастанию размера, чтобы тренд читался сверху вниз.
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)B", re.IGNORECASE)


def model_size(name: str) -> float:
    """Достаёт число миллиардов параметров из имени файла ответов."""
    match = _SIZE_RE.search(name)
    return float(match.group(1)) if match else 999.0


def pretty_name(path: Path) -> str:
    """responses_Qwen_Qwen3-4B.jsonl -> Qwen3-4B."""
    stem = path.stem.replace("responses_", "").replace("Qwen_", "")
    return stem.replace("_", "/")


def main() -> None:
    """Точка входа: собирает все responses_*.jsonl и печатает сводку."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                        help="каталог с responses_*.jsonl со свипа")
    parser.add_argument("--baseline", type=Path,
                        default=_HERE.parent / "baseline" / "responses100.jsonl",
                        help="уже снятый baseline Qwen3-14B")
    parser.add_argument("--out", type=Path,
                        default=_HERE.parent / "baseline" / "model_sweep.md")
    args = parser.parse_args()

    runs: list[tuple[str, dict, list[dict]]] = []

    paths = sorted(args.dir.glob("responses_*.jsonl")) if args.dir.exists() else []
    if args.baseline.exists():
        paths.append(args.baseline)

    if not paths:
        raise SystemExit(f"не нашёл ответов ни в {args.dir}, ни в {args.baseline}")

    for path in paths:
        records = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
        if not records:
            print(f"пропускаю пустой файл: {path.name}")
            continue
        name = "Qwen3-14B" if path == args.baseline else pretty_name(path)
        runs.append((name, scorer.score(records), records))

    runs.sort(key=lambda r: model_size(r[0]))

    def pct(value) -> str:
        return "—" if value is None else f"{value * 100:.1f}%"

    lines = [
        "# Выбор модели под файнтюн: baseline нескольких размеров",
        "",
        f"Одна выборка ({runs[0][1]['total']} примеров), один промпт, одно квантование "
        "(4-bit NF4), один батч. Отличается только модель.",
        "",
        "| модель | чистый JSON | schema | vocab | per-field | unit norm | галлюц. | длина | с/ответ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, metrics, records in runs:
        latency = sum(r.get("latency_s", 0) for r in records) / len(records)
        lines.append(
            f"| **{name}** | {pct(metrics['json_valid_clean'])} "
            f"| {pct(metrics['schema_compliance'])} "
            f"| {pct(metrics['vocab_compliance'])} "
            f"| **{pct(metrics['field_exact_mean'])}** "
            f"| {pct(metrics['unit_normalization'])} "
            f"| {pct(metrics['hallucination_rate'])} "
            f"| {metrics['avg_output_chars']:.0f} "
            f"| {latency:.2f} |"
        )

    lines += ["", "## Пополевая точность", "",
              "| модель | " + " | ".join(f"`{f}`" for f in scorer.schema.FIELD_ORDER) + " |",
              "|---|" + "---|" * len(scorer.schema.FIELD_ORDER)]
    for name, metrics, _ in runs:
        cells = " | ".join(pct(metrics["field_exact"][f]) for f in scorer.schema.FIELD_ORDER)
        lines.append(f"| **{name}** | {cells} |")

    lines += [
        "",
        "## Как это читать",
        "",
        "- **Чистый JSON и schema низкие, per-field высокий** — модель понимает задачу,",
        "  но не держит формат. Это первое, чему учит тюн; кандидата не отбрасывать.",
        "- **Per-field низкий при высоком schema** — формат держит, а задачу не понимает.",
        "  Тюн на 400 примерах такое чинит плохо.",
        "- **`form` и `purpose`** — закрытые словари, слабое место и у 14B (67% и 71%).",
        "  Если у младшей модели они не сильно хуже, разница в размере не окупается.",
        "- **с/ответ** — при батче 8 на RTX 4090. Для промышленного прогона каталога",
        "  это главная цифра: на миллионе карточек секунда разницы стоит 280 часов GPU.",
        "",
    ]

    report = "\n".join(lines)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"сводка: {args.out}")


if __name__ == "__main__":
    main()
