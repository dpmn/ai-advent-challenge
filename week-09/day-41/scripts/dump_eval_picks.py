#!/usr/bin/env python3
"""Выгружает весь eval в формат picks, пригодный для `baseline_hf.py`.

Зачем отдельный скрипт: `baseline.py --dump-picks` отбирает десятку по ловушкам,
как требует задание дня 41. Но после пересъёмки baseline 29.07.2026 четыре
метрики из семи упёрлись в 100%, и остаток измеряется единицами процентов —
на десяти примерах такой сдвиг неотличим от шума. Чтобы «до тюна» и «после»
можно было сравнивать, нужна точка отсчёта на всех 100 примерах eval.

Отбора здесь нет: берутся все записи подряд, в порядке файла. Поле `picked_for`
заполняется классом сложности — так `score.py` продолжает считать разрез
по классам, а `baseline_hf.py` печатает осмысленную метку в прогрессе.

Запускать локально, GPU не нужен.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import schema

_HERE = Path(__file__).resolve().parent
DEFAULT_EVAL = _HERE.parent / "datasets" / "eval.jsonl"
DEFAULT_OUT = _HERE.parent / "baseline" / "picks100.jsonl"


def main() -> None:
    """Точка входа: eval.jsonl -> picks100.jsonl."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    records = [json.loads(line) for line in args.eval.open(encoding="utf-8") if line.strip()]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = []
    with args.out.open("w", encoding="utf-8") as fh:
        for record in records:
            assistant = next(
                (m["content"] for m in record["messages"] if m["role"] == "assistant"), None
            )
            expected, _ = schema.parse_model_json(assistant or "")
            if expected is None:
                # Эталон, который сам не разбирается, сделал бы метрику
                # бессмысленной: модель наказывалась бы за верный ответ.
                skipped.append(record["id"])
                continue
            flags = record.get("hard_flags") or []
            fh.write(json.dumps({
                "id": record["id"],
                "name": record["name"],
                "url": record.get("url", ""),
                "picked_for": flags[0] if flags else "simple",
                "hard_flags": flags,
                "expected": expected,
            }, ensure_ascii=False) + "\n")
            written += 1

    print(f"выгружено: {written} из {len(records)} -> {args.out}")
    if skipped:
        print(f"пропущено (эталон не разобрался): {len(skipped)} — {', '.join(skipped)}")


if __name__ == "__main__":
    main()
