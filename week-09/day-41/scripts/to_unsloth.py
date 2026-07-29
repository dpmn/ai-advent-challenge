#!/usr/bin/env python3
"""Конвертирует датасет под локальный QLoRA-прогон (Unsloth / TRL).

Тюн на арендованной машине идёт не через OpenAI-совместимый API, а через
Unsloth: тот же датасет, другая упаковка. Конвертер держит оба выхода из
одного источника, чтобы train/eval не разъехались между дорожками.

На выходе — ShareGPT-подобный формат `conversations`, который TRL
`SFTTrainer` и Unsloth принимают напрямую.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DAY_DIR = Path(__file__).resolve().parent.parent
DATASETS = DAY_DIR / "datasets"
OUT_DIR = DATASETS / "prepared"

ROLE_MAP = {"system": "system", "user": "human", "assistant": "gpt"}


def convert(source: Path, target: Path, style: str) -> int:
    """Переписывает файл в выбранный формат, возвращает число примеров."""
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with source.open(encoding="utf-8") as src, target.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            messages = json.loads(line)["messages"]
            if style == "sharegpt":
                record = {
                    "conversations": [
                        {"from": ROLE_MAP[m["role"]], "value": m["content"]}
                        for m in messages
                    ]
                }
            else:
                record = {"messages": messages}
            dst.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    """Точка входа: конвертирует train и eval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style", choices=("sharegpt", "messages"), default="sharegpt",
                        help="sharegpt — для Unsloth, messages — для TRL с chat-шаблоном")
    parser.add_argument("--train", type=Path, default=DATASETS / "train.jsonl")
    parser.add_argument("--eval", type=Path, default=DATASETS / "eval.jsonl")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    for source in (args.train, args.eval):
        if not source.exists():
            print(f"пропущен (нет файла): {source}")
            continue
        target = args.out_dir / f"{source.stem}.{args.style}.jsonl"
        count = convert(source, target, args.style)
        print(f"{source.name} -> {target} ({count} примеров, формат {args.style})")


if __name__ == "__main__":
    main()
