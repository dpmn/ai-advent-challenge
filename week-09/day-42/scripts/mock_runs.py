#!/usr/bin/env python3
"""Собирает runs.jsonl из готовых ответов дня 41 — для отладки без GPU.

Аренда стоит денег, а ступени 1–2 и вся сборка отчёта к модели не обращаются.
Скрипт переупаковывает `day-41/baseline/responses.jsonl` в формат прогона,
чтобы `gates.py` и `report.py` можно было прогнать локально до аренды.

Logprob-ы у настоящего прогона приходят от модели; здесь они синтезируются
детерминированно из хеша текста токена. Проверять по ним качество нельзя —
только то, что разметка границ и привязка уверенности к полям не разъехались.
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import json

import gates
from common import RESULTS_DIR, difficulty_level, read_jsonl, schema, write_jsonl

DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "day-41" / "baseline" / "responses.jsonl"
CHUNK = 4


def fake_tokens(raw: str, seed: int) -> list[dict]:
    """Нарезает текст на псевдотокены с воспроизводимыми logprob-ами."""
    rng = random.Random(seed)
    tokens = []
    for start in range(0, len(raw), CHUNK):
        tokens.append({
            "start": start,
            "end": min(start + CHUNK, len(raw)),
            "logprob": round(math.log(rng.uniform(0.55, 0.999)), 5),
        })
    return tokens


def fake_samples(obj: dict, count: int, seed: int) -> list[dict]:
    """Строит псевдосэмплы: копии ответа со случайно расшатанным одним полем.

    Нужны, чтобы ступени голосования и самопроверки прогонялись локально.
    Расшатывание грубое и неинформативное — это стенд для кода, не для модели.
    """
    rng = random.Random(seed + 1000)
    samples = []
    for _ in range(count):
        variant = dict(obj)
        if rng.random() < 0.5:
            field = rng.choice(["category", "form", "pack_count", "is_set"])
            if field == "category":
                variant[field] = rng.choice(schema.vocab("category"))
            elif field == "form":
                variant[field] = rng.choice(schema.vocab("form") + [None])
            elif field == "pack_count":
                variant[field] = rng.choice([None, 1, 2])
            else:
                variant[field] = not variant.get(field, False)
        samples.append({
            "raw": json.dumps(variant, ensure_ascii=False),
            "gen_tokens": 60,
            "latency_s": 12.0,
            "tokens": [],
        })
    return samples


def fake_selfcheck(disputed: list[str], seed: int) -> dict:
    """Строит псевдоответ самопроверки для примеров со спорными полями."""
    rng = random.Random(seed + 2000)
    verdict = "reject" if rng.random() < 0.4 else "confirm"
    payload = {"verdict": verdict,
               "bad_fields": disputed[:1] if verdict == "reject" else [],
               "reason": "стенд"}
    return {"raw": json.dumps(payload, ensure_ascii=False), "gen_tokens": 30,
            "latency_s": 11.0, "tokens": []}


def main() -> None:
    """Точка входа: переупаковывает ответы baseline в формат runs.jsonl."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "mock_runs.jsonl")
    parser.add_argument("--no-logprobs", action="store_true",
                        help="не синтезировать logprob-ы (проверка пути без scoring)")
    parser.add_argument("--samples", type=int, default=2,
                        help="сколько псевдосэмплов подложить для ступеней 3 и 4")
    args = parser.parse_args()

    records = []
    for index, source in enumerate(read_jsonl(args.source)):
        raw = source.get("raw") or ""
        constraint = gates.constraint_check(source["name"], raw)
        samples: list[dict] = []
        selfcheck = None
        if constraint.passed and args.samples > 0:
            samples = fake_samples(constraint.obj, args.samples, index)
            sample_objs = [schema.parse_model_json(s["raw"])[0] for s in samples]
            disputed = gates.vote(constraint.obj, sample_objs).disputed
            if disputed:
                selfcheck = fake_selfcheck(disputed, index)
        records.append({
            "id": source["id"],
            "name": source["name"],
            "url": source.get("url", ""),
            "hard_flags": source.get("hard_flags", []),
            "level": difficulty_level(source.get("hard_flags")),
            "expected": source["expected"],
            "base": {
                "raw": raw,
                "gen_tokens": len(raw) // CHUNK,
                "latency_s": source.get("latency_s", 0.0),
                "tokens": [] if args.no_logprobs else fake_tokens(raw, index),
            },
            "samples": samples,
            "selfcheck": selfcheck,
        })

    write_jsonl(args.out, records)
    print(f"{len(records)} записей -> {args.out}")


if __name__ == "__main__":
    main()
