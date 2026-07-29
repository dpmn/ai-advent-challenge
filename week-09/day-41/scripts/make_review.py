#!/usr/bin/env python3
"""Готовит eval к ручной вычитке.

Автопроверка инвариантов ловит только формальный брак: выдуманный бренд,
значение вне словаря, объём, не сходящийся со строкой. Смысловую ошибку
разметчика она пропускает — а на eval такая ошибка стоит дорого: модель
будет наказана за верный ответ, и все метрики поедут.

Отсюда и правило: eval вычитывается целиком, train — выборочно. Каждая
строка идёт со ссылкой на карточку, чтобы спорный бренд можно было
разрешить, открыв товар.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import schema

DAY_DIR = Path(__file__).resolve().parent.parent
DATASETS = DAY_DIR / "datasets"

# Поля, где разметчик ошибается чаще всего: их выносим отдельной строкой.
SPOTLIGHT = ("brand", "line", "shade", "category")


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def render(records: list[dict]) -> str:
    """Собирает markdown для вычитки."""
    lines = [
        "# Вычитка eval",
        "",
        f"Всего примеров: **{len(records)}**. Проверить нужно все —",
        "ошибка в эталоне eval наказывает модель за правильный ответ.",
        "",
        "На что смотреть в первую очередь:",
        "",
        "- **`brand` vs `line` vs `shade`** — главная неоднозначность. Строка в кавычках",
        "  не обязана быть брендом, а сам бренд часто стоит в скобках в конце.",
        "- **`volume`** — объём ОДНОЙ единицы, не суммарный по упаковке.",
        "- **`is_set`** — true только для набора из РАЗНЫХ продуктов.",
        "- **`category`** — если товар не лёг ни в один узел, должно быть `прочее`.",
        "",
        "Нашёл ошибку — правь `datasets/eval.jsonl` в поле `messages[2].content`",
        "и прогони `python3 scripts/validate.py`.",
        "",
        "---",
        "",
    ]

    for idx, record in enumerate(records, 1):
        assistant = next(m["content"] for m in record["messages"] if m["role"] == "assistant")
        obj, _ = schema.parse_model_json(assistant)
        flags = [f for f in record.get("hard_flags", []) if f != "simple"]

        lines.append(f"### {idx}. `{record['id']}`")
        lines.append("")
        lines.append(f"**{record['name']}**")
        lines.append("")
        if record.get("url"):
            lines.append(f"[карточка товара]({record['url']})")
            lines.append("")
        if flags:
            lines.append(f"ловушки: {', '.join(f'`{f}`' for f in flags)}")
            lines.append("")

        spotlight = " · ".join(
            f"{field}=`{obj.get(field)}`" for field in SPOTLIGHT
        )
        lines.append(spotlight)
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(obj, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Точка входа: рендерит файл для ручной вычитки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATASETS / "eval.jsonl")
    parser.add_argument("--out", type=Path, default=DAY_DIR / "EVAL_REVIEW.md")
    args = parser.parse_args()

    records = read_jsonl(args.source)
    args.out.write_text(render(records), encoding="utf-8")
    print(f"на вычитку: {len(records)} примеров -> {args.out}")


if __name__ == "__main__":
    main()
