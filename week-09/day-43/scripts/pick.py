#!/usr/bin/env python3
"""Отбирает 22 примера из eval дня 41 под живой прогон routing-а.

Почему 22, а не 50 дня 42 и не все 100: прогон снимается на видео, и его
длина складывается из локальных вызовов `qwen3:0.6b` (~2 с) и эскалаций
к 14B (~1,3 с). Двадцать два товара — это около двух минут, при которых
в кадре ещё видно каждое решение.

Почему пропорции другие, чем в дне 42 (там 13/15/22): день 42 проверял гейт,
и выборку сознательно перекосили в шумные — гейту нужно что отклонять.
Routing-у нужен смешанный поток, иначе нечего показать в графе «осталось
на маленькой модели».

Уровни (см. `common.difficulty_level`):
  шумные      — dirty_unit, truncated, multi_volume, shade_code
  пограничные — quoted, pack, set_like
  корректные  — всё остальное
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import random

from common import EVAL_PATH, PICKS_PATH, difficulty_level, read_jsonl, schema, write_jsonl

DEFAULT_SEED = 43
TARGET_TOTAL = 22
TARGET_BY_LEVEL = {"шумные": 8, "пограничные": 6, "корректные": 8}

# Внутри шумного уровня эти классы поднимаем в начало: на них baseline дня 41
# дал 0% точности по `volume`, то есть эскалация обязана срабатывать. Если она
# и здесь промолчит, эвристика не работает.
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
    buckets["шумные"].sort(
        key=lambda r: not set(r.get("hard_flags") or []) & set(PRIORITY_FLAGS)
    )

    picked: list[dict] = []
    for level, target in TARGET_BY_LEVEL.items():
        take = buckets[level][:target]
        buckets[level] = buckets[level][target:]
        picked.extend(take)

    if len(picked) < total:
        leftovers = [r for pool in buckets.values() for r in pool]
        rng.shuffle(leftovers)
        picked.extend(leftovers[: total - len(picked)])

    # Порядок в кадре: перемешиваем уровни между собой, чтобы прогон не шёл
    # блоками «сначала все простые, потом все грязные» — иначе на видео
    # непонятно, реагирует ли routing на вход или просто устал.
    rng.shuffle(picked)
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
