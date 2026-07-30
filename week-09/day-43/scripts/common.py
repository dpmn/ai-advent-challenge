#!/usr/bin/env python3
"""Общее для скриптов дня 43: пути, доступ к схеме дня 41 и воротам дня 42.

Схема, промпт и инварианты живут в дне 41, механизмы уверенности — в дне 42.
Копировать их сюда нельзя: разъедутся словари или формула уверенности, и
сравнение с baseline 61,1% / 84,9% станет нечестным. Поэтому модуль
подкладывает оба каталога в `sys.path` и импортирует оттуда.

Раскладок две: локально скрипты лежат по дням, а на арендованной ВМ
копируются плоско в один каталог. Проверяются обе.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

DAY_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
_DAY41_SCRIPTS = DAY_DIR.parent / "day-41" / "scripts"
_DAY42_SCRIPTS = DAY_DIR.parent / "day-42" / "scripts"

for _candidate in (_HERE, _DAY41_SCRIPTS):
    if (_candidate / "schema.py").exists():
        sys.path.insert(0, str(_candidate))
        break

for _candidate in (_HERE, _DAY42_SCRIPTS):
    if (_candidate / "gates.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import schema  # noqa: E402  (импорт возможен только после правки sys.path)
import score  # noqa: E402

EVAL_PATH = DAY_DIR.parent / "day-41" / "datasets" / "eval.jsonl"
PICKS_PATH = DAY_DIR / "picks22.jsonl"
RESULTS_DIR = DAY_DIR / "results"
DAY41_BIG_DUMP = DAY_DIR.parent / "day-41" / "baseline" / "responses100.jsonl"

# Классы сложности из разметки дня 41, разложенные по трём уровням входов.
# Те же границы, что в дне 42, — иначе выборки двух дней несопоставимы.
NOISY_FLAGS = ("dirty_unit", "truncated", "multi_volume", "shade_code")
EDGE_FLAGS = ("quoted", "pack", "set_like")

# Слабая модель: Ollama на ноутбуке (RTX 3060 6 ГБ). Своё железо, поэтому
# в рублях не считается вовсе — только секунды. Придумывать ей цену значило бы
# сравнивать измеренное с выдуманным.
OLLAMA_URL = "http://127.0.0.1:11434"
SMALL_MODEL = "qwen3:0.6b"

# Сильная модель: Qwen3-14B в 4-bit NF4 на арендованной ВМ immers.cloud,
# поднятая как HTTP-сервер (scripts/serve_big.py) и проброшенная ssh-туннелем.
BIG_URL = "http://127.0.0.1:8000"
BIG_MODEL = "qwen3-14b"

# Тариф ВМ на immers.cloud, посекундная тарификация. Та же RTX 4090, что
# в дне 42, — цифра влияет только на отчёт: в мете пишутся секунды GPU.
GPU_RUB_PER_HOUR = 86.74

FIELD_ORDER = schema.FIELD_ORDER


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


def rub(gpu_seconds: float) -> float:
    """Пересчитывает секунды арендованной GPU в рубли."""
    return gpu_seconds / 3600 * GPU_RUB_PER_HOUR
