#!/usr/bin/env python3
"""Отбирает 50 примеров из eval дня 41 под три уровня входов из задания.

Случайная полусотня из eval дала бы почти сплошные простые случаи: в eval
49 примеров помечены `simple`. Контроль качества на них ничего не покажет —
гейт нечего отклонять. Поэтому выборка стратифицирована по уже имеющейся
разметке классов сложности, а не пересобирается заново.

Уровни (см. `common.difficulty_level`):
  шумные      — dirty_unit, truncated, multi_volume, shade_code
  пограничные — quoted, pack, set_like
  корректные  — всё остальное
"""

from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

from common import EVAL_PATH, PICKS_PATH, difficulty_level, read_jsonl, schema, write_jsonl

DEFAULT_SEED = 42
TARGET_TOTAL = 50
# Шумных берём столько, сколько есть, но не больше — иначе выборка перестанет
# отвечать на вопрос «а как гейт ведёт себя на нормальных входах».
TARGET_BY_LEVEL = {"шумные": 22, "пограничные": 15, "корректные": 13}

# Внутри шумного уровня эти классы забираем целиком: на них baseline дня 41
# показал 0% точности по `volume`, ради них выборка и стратифицируется.
PRIORITY_FLAGS = ("dirty_unit", "truncated")


def expected_from_messages(record: dict) -> dict | None:
    """Достаёт эталонный объект из assistant-сообщения обучающего формата."""
    for message in record.get("messages", []):
        if message.get("role") == "assistant":
            obj, _ = schema.parse_model_json(message["content"])
            return obj
    return None


def stratified_pick(evalset: list[dict], seed: int, total: int) -> list[dict]:
    """Набирает выборку по уровням, добирая недостачу из соседних уровней."""
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {level: [] for level in TARGET_BY_LEVEL}
    for record in evalset:
        buckets[difficulty_level(record.get("hard_flags"))].append(record)
    for pool in buckets.values():
        rng.shuffle(pool)
    # Приоритетные классы поднимаем в начало шумного пула — иначе усечение
    # до квоты выбрасывает часть тех самых примеров, ради которых всё затеяно.
    buckets["шумные"].sort(
        key=lambda r: not set(r.get("hard_flags") or []) & set(PRIORITY_FLAGS)
    )

    picked: list[dict] = []
    for level, target in TARGET_BY_LEVEL.items():
        take = buckets[level][:target]
        buckets[level] = buckets[level][target:]
        picked.extend(take)

    # Недобор в одном уровне добираем остатками других: важнее сохранить
    # объём выборки, чем идеальные пропорции.
    if len(picked) < total:
        leftovers = [r for pool in buckets.values() for r in pool]
        rng.shuffle(leftovers)
        picked.extend(leftovers[: total - len(picked)])

    return picked[:total]


def main() -> None:
    """Точка входа: отбирает выборку и выгружает её в JSONL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", type=Path, default=EVAL_PATH)
    parser.add_argument("--out", type=Path, default=PICKS_PATH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--total", type=int, default=TARGET_TOTAL)
    args = parser.parse_args()

    evalset = read_jsonl(args.eval)
    picked = stratified_pick(evalset, args.seed, args.total)

    records = []
    skipped = 0
    for record in picked:
        expected = expected_from_messages(record)
        if expected is None:
            skipped += 1
            continue
        records.append({
            "id": record["id"],
            "name": record["name"],
            "url": record.get("url", ""),
            "hard_flags": record.get("hard_flags", []),
            "level": difficulty_level(record.get("hard_flags")),
            "expected": expected,
        })

    write_jsonl(args.out, records)

    levels = Counter(r["level"] for r in records)
    flags = Counter(f for r in records for f in r["hard_flags"] or ["(без флагов)"])
    print(f"выборка: {len(records)} примеров -> {args.out}")
    if skipped:
        print(f"пропущено без эталона: {skipped}")
    print("\nпо уровням:")
    for level in ("корректные", "пограничные", "шумные"):
        print(f"  {level:12s} {levels.get(level, 0):3d}")
    print("\nпо классам сложности:")
    for flag, count in flags.most_common():
        print(f"  {flag:14s} {count:3d}")


if __name__ == "__main__":
    main()
