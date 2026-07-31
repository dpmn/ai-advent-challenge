#!/usr/bin/env python3
"""Общее для скриптов дня 44: пути, доступ к схеме и скореру дня 41.

Схема, промпт, инварианты и скорер живут в дне 41. Копировать их сюда нельзя:
разъедутся словари или правило сравнения полей, и сравнение декомпозиции
с монолитным baseline станет нечестным — а весь день ровно про это сравнение.
Поэтому модуль подкладывает каталог дня 41 в `sys.path` и импортирует оттуда.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

DAY_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
_DAY41_SCRIPTS = DAY_DIR.parent / "day-41" / "scripts"

for _candidate in (_HERE, _DAY41_SCRIPTS):
    if (_candidate / "schema.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import schema  # noqa: E402  (импорт возможен только после правки sys.path)
import score  # noqa: E402


def _resolve(*candidates: Path) -> Path:
    """Возвращает первый существующий путь, иначе последний из списка.

    Раскладок две: локально скрипты и данные лежат по дням, а на арендованной
    ВМ всё копируется плоско в один каталог. Проверяются обе — как в дне 43.
    """
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


_DAY41 = DAY_DIR.parent / "day-41"

EVAL_PATH = _resolve(_HERE / "eval.jsonl", _DAY41 / "datasets" / "eval.jsonl")
TRAIN_PATH = _resolve(_HERE / "train.jsonl", _DAY41 / "datasets" / "train.jsonl")
RESULTS_DIR = _resolve(_HERE / "results", DAY_DIR / "results")

# Ответы Qwen3-14B на те же 100 примеров, снятые в дне 41 и подтверждённые
# живым прогоном дня 43 (совпадение 100% полей). Потолок качества берётся
# отсюда, а не отдельной арендой большой модели.
DAY41_BIG_DUMP = _resolve(_HERE / "responses100.jsonl",
                          _DAY41 / "baseline" / "responses100.jsonl")

# Классы сложности из разметки дня 41. Границы те же, что в днях 42–43, —
# иначе срезы по уровням несопоставимы между днями.
NOISY_FLAGS = ("dirty_unit", "truncated", "multi_volume", "shade_code")
EDGE_FLAGS = ("quoted", "pack", "set_like")

OLLAMA_URL = "http://127.0.0.1:11434"

# Основная модель дня. Выбрана не за качество, а за его отсутствие: монолитом
# она даёт 61,1% пополевой точности (день 41), и только на такой модели видно,
# даёт ли декомпозиция выигрыш. У 14B монолит уже 84,8% — там мерить нечего.
SMALL_MODEL = "qwen3:0.6b"

# Опциональная модель для смешанной руки: тяжёлый этап отдаётся ей.
MID_MODEL = "qwen3:4b"

# Тариф арендованной ВМ на immers.cloud, посекундная тарификация — тот же,
# что в днях 42–43. Прогон дня 44 сам по себе локальный и бесплатный, но
# ноутбук не тянет 1800 вызовов параллельно с работой, поэтому руки снимаются
# на ВМ. Раз счётчик включён, цена считается и попадает в отчёт: день ровно
# про то, во сколько обходится декомпозиция.
GPU_RUB_PER_HOUR = 86.74

FIELD_ORDER = schema.FIELD_ORDER


def rub(seconds: float, rate: float = GPU_RUB_PER_HOUR) -> float:
    """Пересчитывает секунды арендованной ВМ в рубли."""
    return seconds / 3600 * rate


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


def load_split(path: Path, limit: int | None = None) -> list[dict]:
    """Читает сплит дня 41 и раскладывает в плоские записи `{id, name, expected, level}`."""
    records = []
    for raw in read_jsonl(path):
        expected = expected_from_messages(raw)
        if expected is None:
            continue
        records.append({
            "id": raw["id"],
            "name": raw["name"],
            "expected": expected,
            "hard_flags": raw.get("hard_flags") or [],
            "level": difficulty_level(raw.get("hard_flags")),
        })
    return records[:limit] if limit else records


def load_eval(limit: int | None = None) -> list[dict]:
    """Читает eval дня 41 — все 100 примеров.

    Выборка не урезается до подмножеств дней 42–43: прогон локальный и
    бесплатный, а на 22 товарах корзины дня 43 выходили по 14 наблюдений,
    и величину сигнала читать было нельзя.
    """
    return load_split(EVAL_PATH, limit)


def load_train(limit: int | None = None) -> list[dict]:
    """Читает train дня 41.

    Нужен ровно для одного: отлаживать промпты этапов на train, а не на eval.
    Малые модели цепляются за примеры в промпте (в первом же смоуке 0.6B
    скопировала бренд из примера), правки неизбежны, и делать их по eval
    значило бы подгонять день под метрику, которой он же и меряется.
    """
    return load_split(TRAIN_PATH, limit)


def load_big_anchor() -> dict[str, dict]:
    """Возвращает разобранные ответы 14B из дампа дня 41, ключ — id товара."""
    anchor: dict[str, dict] = {}
    for record in read_jsonl(DAY41_BIG_DUMP):
        obj, _ = schema.parse_model_json(record.get("raw") or "")
        anchor[record["id"]] = obj
    return anchor


class Usage:
    """Копилка расхода: вызовы, токены промпта и генерации, секунды.

    День 43 закончился выводом «тарифицируется вызов, а не поле». Декомпозиция
    меняет один вызов на несколько, поэтому расход считается с самого начала
    и по каждому этапу отдельно — иначе «стало точнее» ничего не значит.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.gen_tokens = 0
        self.seconds = 0.0
        self.by_stage: dict[str, dict] = {}

    def add(self, stage: str, response: dict) -> None:
        """Учитывает один ответ модели в общей копилке и в разрезе этапа."""
        bucket = self.by_stage.setdefault(
            stage, {"calls": 0, "prompt_tokens": 0, "gen_tokens": 0, "seconds": 0.0})
        self.calls += 1
        self.prompt_tokens += response.get("prompt_tokens") or 0
        self.gen_tokens += response.get("gen_tokens") or 0
        self.seconds += response.get("latency_s") or 0.0
        bucket["calls"] += 1
        bucket["prompt_tokens"] += response.get("prompt_tokens") or 0
        bucket["gen_tokens"] += response.get("gen_tokens") or 0
        bucket["seconds"] = round(bucket["seconds"] + (response.get("latency_s") or 0.0), 3)

    def as_dict(self) -> dict:
        """Сериализует копилку для записи в JSONL."""
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "gen_tokens": self.gen_tokens,
            "seconds": round(self.seconds, 3),
            "by_stage": self.by_stage,
        }
