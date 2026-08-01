#!/usr/bin/env python3
"""Общее для скриптов дня 45: пути, доступ к схеме дня 41 и клиенту дня 43.

Схема, словарь категорий, датасеты и судья живут в дне 41, клиент большой
модели — в дне 43. Копировать их сюда нельзя: разъедется словарь или правило
сравнения, и сравнение классификатора с большой моделью станет нечестным.
Поэтому модуль подкладывает оба каталога в `sys.path` и импортирует оттуда.

`big.py` дня 43 берёт адрес сервера из своего `common`, а при таком порядке
путей им оказывается этот модуль — поэтому `BIG_URL` объявлен здесь.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

DAY_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
_DAY41 = DAY_DIR.parent / "day-41"
_DAY43_SCRIPTS = DAY_DIR.parent / "day-43" / "scripts"

sys.path.insert(0, str(_DAY43_SCRIPTS))
sys.path.insert(0, str(_DAY41 / "scripts"))
sys.path.insert(0, str(_HERE))

import schema  # noqa: E402  (импорт возможен только после правки sys.path)
import score  # noqa: E402

TRAIN_PATH = _DAY41 / "datasets" / "train.jsonl"
EVAL_PATH = _DAY41 / "datasets" / "eval.jsonl"
RESULTS_DIR = DAY_DIR / "results"
MODEL_PATH = DAY_DIR / "model" / "classifier.joblib"

# Ответы Qwen3-14B на те же 100 примеров, снятые в дне 41. В этом дне
# используются только заглушкой (`mock_big.py`) для проверки конвейера
# без аренды: основной прогон спрашивает живую модель.
DAY41_BIG_DUMP = _DAY41 / "baseline" / "responses100.jsonl"

# Сервер большой модели на арендованной ВМ, проброшенный ssh-туннелем.
# Тот же адрес и тот же сервер, что в дне 43 (`day-43/scripts/serve_big.py`).
BIG_URL = "http://127.0.0.1:8000"
BIG_MODEL = "qwen3-14b"

# Тариф ВМ на immers.cloud, посекундная тарификация — как в днях 42–44.
GPU_RUB_PER_HOUR = 86.74

# Классы сложности из разметки дня 41. Границы те же, что в днях 42–44.
NOISY_FLAGS = ("dirty_unit", "truncated", "multi_volume", "shade_code")
EDGE_FLAGS = ("quoted", "pack", "set_like")

CATEGORIES = schema.vocab("category")


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


def expected_from_messages(record: dict) -> dict | None:
    """Достаёт эталонный объект из assistant-сообщения обучающего формата."""
    for message in record.get("messages", []):
        if message.get("role") == "assistant":
            obj, _ = schema.parse_model_json(message["content"])
            return obj
    return None


def load_split(path: Path) -> list[dict]:
    """Читает сплит дня 41 в плоские записи `{id, name, category, level}`."""
    records = []
    for raw in read_jsonl(path):
        expected = expected_from_messages(raw)
        if expected is None:
            continue
        records.append({
            "id": raw["id"],
            "name": raw["name"],
            "category": expected["category"],
            "expected": expected,
            "hard_flags": raw.get("hard_flags") or [],
            "level": difficulty_level(raw.get("hard_flags")),
        })
    return records


def load_train() -> list[dict]:
    """400 обучающих примеров дня 41 — на них учится классификатор."""
    return load_split(TRAIN_PATH)


def load_eval() -> list[dict]:
    """100 проверочных примеров дня 41 — на них считаются метрики дня."""
    return load_split(EVAL_PATH)


def rub(seconds: float, rate: float = GPU_RUB_PER_HOUR) -> float:
    """Пересчитывает секунды арендованной ВМ в рубли."""
    return seconds / 3600 * rate
