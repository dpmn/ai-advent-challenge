#!/usr/bin/env python3
"""Общее для скриптов дня 42: доступ к схеме дня 41, чтение JSONL, константы.

Схема, промпт и инварианты живут в дне 41 и остаются единственным источником
правды — копировать их сюда нельзя, иначе словари разъедутся и сравнение
«до/после» станет нечестным. Поэтому модуль подкладывает каталог со схемой
в `sys.path` и импортирует её оттуда.

Раскладок две: локально `schema.py` лежит в `day-41/scripts`, а на арендованной
ВМ скрипты копируются плоско в один каталог. Проверяются обе.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DAY_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
_DAY41_SCRIPTS = DAY_DIR.parent / "day-41" / "scripts"

for _candidate in (_HERE, _DAY41_SCRIPTS):
    if (_candidate / "schema.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import schema  # noqa: E402  (импорт возможен только после правки sys.path)

EVAL_PATH = _DAY41_SCRIPTS.parent / "datasets" / "eval.jsonl"
PICKS_PATH = DAY_DIR / "picks50.jsonl"
RESULTS_DIR = DAY_DIR / "results"

# Классы сложности из разметки дня 41, разложенные по трём уровням входов
# из задания: корректные / пограничные / заведомо шумные.
NOISY_FLAGS = ("dirty_unit", "truncated", "multi_volume", "shade_code")
EDGE_FLAGS = ("quoted", "pack", "set_like")

# Тариф арендованной ВМ на immers.cloud, посекундная тарификация.
# День 41 считал по A10 (36,6 ₽/ч), но к дню 42 свободных A10 не нашлось —
# прогон идёт на RTX 4090. Цифра влияет только на отчёт, не на замеры:
# в runs.meta.json пишутся секунды GPU, рубли пересчитываются из них.
GPU_RUB_PER_HOUR = 86.74


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Пишет список записей в JSONL, создавая каталог при необходимости."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def difficulty_level(hard_flags: list[str] | None) -> str:
    """Относит пример к одному из трёх уровней входов задания."""
    flags = set(hard_flags or [])
    if flags & set(NOISY_FLAGS):
        return "шумные"
    if flags & set(EDGE_FLAGS):
        return "пограничные"
    return "корректные"
