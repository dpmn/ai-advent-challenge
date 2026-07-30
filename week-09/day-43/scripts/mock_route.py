#!/usr/bin/env python3
"""Синтетический стенд: собирает прогон и якорь без Ollama и без GPU.

Нужен, чтобы арифметика отчёта отлаживалась не на живом прогоне. Считать
шесть политик, случайную эскалацию и свип порога — это код, который ошибается
молча: неверная политика даст правдоподобное число, и на видео это уже не
поймать.

Данные берутся настоящие, из дня 41: ответы `Qwen3-0.6B` из `model_sweep`
играют роль слабой модели, ответы `Qwen3-14B` из `responses100.jsonl` —
сильной. Выдуманы только logprob-ы: в sweep-е их не снимали. Поэтому
**цифры мок-отчёта бессмысленны** — проверяется, что все шесть политик
считаются и отчёт собирается.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

import heuristics
from common import (DAY41_BIG_DUMP, DAY_DIR, PICKS_PATH, RESULTS_DIR, read_jsonl, schema,
                    write_jsonl)

SMALL_DUMP = DAY_DIR.parent / "day-41" / "baseline" / "sweep" / "responses_Qwen_Qwen3-0.6B.jsonl"
CHUNK = 3
SEED = 43
# Правдоподобный разброс: в основном модель уверена, изредка угадывает.
LOGPROB_CHOICES = [-0.01, -0.02, -0.05, -0.1, -0.3, -0.9, -1.8]


def fake_tokens(raw: str, rng: random.Random) -> list[dict]:
    """Режет ответ на псевдотокены и назначает им logprob-ы.

    Границы честные (символьные), значения выдуманные: sweep дня 41 снимался
    без logprob-ов, а без них не сработает ни одна эвристика уверенности.
    """
    tokens = []
    for start in range(0, len(raw), CHUNK):
        tokens.append({
            "start": start,
            "end": min(start + CHUNK, len(raw)),
            "logprob": rng.choice(LOGPROB_CHOICES),
        })
    return tokens


def main() -> None:
    """Точка входа: собирает mock-прогон и mock-якорь."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--picks", type=Path, default=PICKS_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "mock_route.jsonl")
    parser.add_argument("--anchor-out", type=Path, default=RESULTS_DIR / "mock_anchor.jsonl")
    parser.add_argument("--threshold", type=float, default=heuristics.DEFAULT_THRESHOLD)
    parser.add_argument("--metric", choices=("min", "mean"), default=heuristics.DEFAULT_METRIC)
    args = parser.parse_args()

    picks = read_jsonl(args.picks)
    small_dump = {r["id"]: r for r in read_jsonl(SMALL_DUMP)}
    big_dump = {r["id"]: r for r in read_jsonl(DAY41_BIG_DUMP)}
    rng = random.Random(SEED)

    runs, anchors = [], []
    for pick in picks:
        small_raw = (small_dump.get(pick["id"]) or {}).get("raw") or ""
        big_raw = (big_dump.get(pick["id"]) or {}).get("raw") or ""
        tokens = fake_tokens(small_raw, rng)
        suspicion = heuristics.analyze(pick["name"], small_raw, tokens,
                                       threshold=args.threshold, metric=args.metric)
        big_obj, big_clean = schema.parse_model_json(big_raw)
        escalated = suspicion.suspect_fields

        runs.append({
            "id": pick["id"], "name": pick["name"], "url": pick.get("url", ""),
            "hard_flags": pick.get("hard_flags", []), "level": pick["level"],
            "expected": pick["expected"],
            "small": {"model": "mock-qwen3:0.6b", "raw": small_raw, "tokens": tokens,
                      "latency_s": 2.0, "gen_tokens": len(tokens), "done_reason": "stop",
                      "tokens_aligned": True, "obj": suspicion.obj,
                      "clean_json": suspicion.clean_json},
            "confidence": suspicion.confidence,
            "escalated": escalated,
            "heuristics": suspicion.by_heuristic(),
            "reasons": [list(pair) for pair in suspicion.triggered],
            "big": ({"model": "mock-qwen3-14b", "raw": big_raw, "obj": big_obj,
                     "latency_s": 1.3, "gen_tokens": 54} if escalated else None),
            "merged": heuristics.merge(suspicion.obj, big_obj, escalated),
        })
        anchors.append({
            "id": pick["id"], "name": pick["name"], "level": pick["level"],
            "expected": pick["expected"],
            "big": {"model": "mock-qwen3-14b", "raw": big_raw, "obj": big_obj,
                    "clean_json": big_clean, "latency_s": 1.3, "gen_tokens": 54},
        })

    write_jsonl(args.out, runs)
    write_jsonl(args.anchor_out, anchors)

    escalated_items = sum(1 for r in runs if r["escalated"])
    escalated_fields = sum(len(r["escalated"]) for r in runs)
    args.out.with_suffix(".meta.json").write_text(json.dumps({
        "date": "mock",
        "small": {"model": "mock-qwen3:0.6b", "quantization": "mock", "local_seconds": 44.0},
        "big": {"model": "mock-qwen3-14b", "gpu_seconds": round(escalated_items * 1.3, 2),
                "calls": escalated_items},
        "thresholds": {"confidence": args.threshold, "metric": args.metric,
                       "max_chars": heuristics.DEFAULT_MAX_CHARS},
        "items": len(runs), "items_escalated": escalated_items,
        "fields_total": len(runs) * len(schema.FIELD_ORDER),
        "fields_escalated": escalated_fields,
        "mock": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    args.anchor_out.with_suffix(".meta.json").write_text(json.dumps({
        "date": "mock", "model": "mock-qwen3-14b", "quantization": "mock",
        "items": len(anchors), "gpu_seconds": round(len(anchors) * 0.9, 2), "mock": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"mock-прогон: {args.out} ({escalated_items}/{len(runs)} товаров эскалировано, "
          f"{escalated_fields} полей)")
    print(f"mock-якорь:  {args.anchor_out}")
    print("цифры синтетические: logprob-ы выдуманы, проверяется только арифметика отчёта")


if __name__ == "__main__":
    main()
