#!/usr/bin/env python3
"""Синтетические прогоны для проверки арифметики отчёта без единого вызова модели.

Схема та же, что в дне 43: берутся настоящие ответы Qwen3-14B из дампа дня 41,
из них детерминированно портится заданная доля полей, а расход проставляется
руками. Дальше `report.py` считает по этим файлам ровно так же, как по живым,
и легко проверить, что проценты, дельты и производные метрики сходятся
с ожидаемыми числами.

Проверять отчёт на живом прогоне неудобно: живой прогон стоит сорок минут,
а ошибку в делении хочется найти раньше.
"""

from __future__ import annotations

import argparse

from common import FIELD_ORDER, RESULTS_DIR, load_big_anchor, load_eval, write_jsonl

# Доля полей, которую портим у каждой руки, и её расход на один товар.
# Числа выбраны так, чтобы порядок рук был заранее известен: mono хуже всех,
# multi лучше всех, а расход растёт вместе с качеством.
ARMS = {
    "mono": {"break_every": 2, "calls": 1, "prompt_tokens": 2500, "gen_tokens": 60, "seconds": 1.5},
    "multi": {"break_every": 5, "calls": 10, "prompt_tokens": 4500, "gen_tokens": 40,
              "seconds": 9.0},
    "multi-lite": {"break_every": 3, "calls": 3, "prompt_tokens": 2600, "gen_tokens": 90,
                   "seconds": 3.0},
    "mono-x4": {"break_every": 2, "calls": 4, "prompt_tokens": 10000, "gen_tokens": 240,
                "seconds": 6.0},
}

BROKEN = "__испорчено__"


def spoil(obj: dict, offset: int, every: int) -> dict:
    """Портит каждое `every`-е поле, отсчитывая от `offset` — детерминированно."""
    spoiled = {}
    for index, field in enumerate(FIELD_ORDER):
        value = obj.get(field) if obj else None
        spoiled[field] = BROKEN if (index + offset) % every == 0 else value
    return spoiled


def main() -> None:
    """CLI: раскладывает синтетические прогоны в results/*_mock.jsonl."""
    parser = argparse.ArgumentParser(description="Синтетические прогоны дня 44")
    parser.add_argument("--limit", type=int, help="сколько примеров взять")
    args = parser.parse_args()

    records = load_eval(args.limit)
    anchor = load_big_anchor()

    for arm, config in ARMS.items():
        rows = []
        for index, record in enumerate(records):
            stage = {"calls": config["calls"], "prompt_tokens": config["prompt_tokens"],
                     "gen_tokens": config["gen_tokens"], "seconds": config["seconds"]}
            rows.append({
                "id": record["id"],
                "name": record["name"],
                "level": record["level"],
                "hard_flags": record["hard_flags"],
                "expected": record["expected"],
                "obj": spoil(anchor.get(record["id"]), index, config["break_every"]),
                "calls": [],
                "format_violations": [],
                "usage": {**stage, "by_stage": {"mock": dict(stage)}},
                "wall_s": config["seconds"],
            })
        write_jsonl(RESULTS_DIR / f"{arm}_mock.jsonl", rows)
        expected_accuracy = 1 - 1 / config["break_every"]
        print(f"{arm}: {len(rows)} записей · испорчено каждое {config['break_every']}-е поле "
              f"(ожидаемая пополевая точность не выше {expected_accuracy * 100:.1f}%) "
              f"→ results/{arm}_mock.jsonl")


if __name__ == "__main__":
    main()
