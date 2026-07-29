#!/usr/bin/env python3
"""Снимает baseline: ответы БАЗОВОЙ модели на 10 примерах из eval.

Точка отсчёта для сравнения «до и после тюна». Работает по
OpenAI-совместимому API, так что одинаково годится и для vLLM на
арендованной машине, и для любого провайдера.

Десятка не случайная: пять случайных плюс пять целевых на классы ловушек.
Случайная выборка на этом распределении с высокой вероятностью не поймает
ни грязных единиц, ни кодов оттенков — и baseline ничего не покажет.

Модель видит ровно тот же system-промпт, что зашит в обучающие примеры.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

import schema

load_dotenv()

DAY_DIR = Path(__file__).resolve().parent.parent
EVAL_PATH = DAY_DIR / "datasets" / "eval.jsonl"
OUT_PATH = DAY_DIR / "baseline" / "responses.jsonl"

# Цель тюна — Qwen3-4B, выбрана замером 29.07.2026 (см. baseline/model_sweep.md).
# Qwen3-14B снята там же и остаётся точкой отсчёта «сколько даёт размер».
DEFAULT_MODEL = "Qwen/Qwen3-4B"
DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"

# По одному представителю на класс ловушки — в порядке важности для задачи.
TRAP_PRIORITY = ("dirty_unit", "multi_volume", "shade_code", "pack", "truncated", "junk_prefix", "quoted")
N_TRAPS = 5
N_RANDOM = 5


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def pick_examples(evalset: list[dict], seed: int) -> list[dict]:
    """Отбирает десятку: сначала по одному на класс ловушки, затем случайные."""
    import random

    rng = random.Random(seed)
    chosen: dict[str, dict] = {}

    for flag in TRAP_PRIORITY:
        if len(chosen) >= N_TRAPS:
            break
        pool = [r for r in evalset
                if flag in r.get("hard_flags", []) and r["id"] not in chosen]
        if pool:
            pick = rng.choice(pool)
            pick["picked_for"] = flag
            chosen[pick["id"]] = pick

    rest = [r for r in evalset if r["id"] not in chosen]
    rng.shuffle(rest)
    for record in rest[:N_RANDOM]:
        record["picked_for"] = "random"
        chosen[record["id"]] = record

    return list(chosen.values())


def ask(client: httpx.Client, model: str, system_prompt: str, name: str) -> str:
    """Отправляет один запрос базовой модели, возвращает сырой текст ответа."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": name},
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }
    response = client.post("/chat/completions", json=payload)
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    return message.get("content") or message.get("reasoning_content") or ""


def main() -> None:
    """Точка входа: отбирает десятку, опрашивает базовую модель, пишет ответы."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="OpenAI-совместимый эндпоинт базовой модели")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default="BASELINE_API_KEY",
                        help="имя переменной окружения с ключом (для vLLM обычно не нужен)")
    parser.add_argument("--eval", type=Path, default=EVAL_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--dry-run", action="store_true",
                        help="показать отобранную десятку и выйти, не обращаясь к модели")
    parser.add_argument("--dump-picks", type=Path,
                        help="выгрузить отобранную десятку в JSONL (для прогона на ВМ через baseline_hf.py)")
    args = parser.parse_args()

    if not args.eval.exists():
        sys.exit(f"нет eval-файла: {args.eval}")

    evalset = read_jsonl(args.eval)
    picked = pick_examples(evalset, args.seed)

    print(f"=== ОТОБРАНО {len(picked)} ПРИМЕРОВ ===")
    for record in picked:
        print(f"  [{record['picked_for']:12s}] {record['name'][:78]}")

    if args.dump_picks:
        args.dump_picks.parent.mkdir(parents=True, exist_ok=True)
        with args.dump_picks.open("w", encoding="utf-8") as fh:
            for record in picked:
                expected, _ = schema.parse_model_json(
                    next(m["content"] for m in record["messages"] if m["role"] == "assistant")
                )
                fh.write(json.dumps({
                    "id": record["id"],
                    "name": record["name"],
                    "url": record.get("url", ""),
                    "picked_for": record["picked_for"],
                    "hard_flags": record.get("hard_flags", []),
                    "expected": expected,
                }, ensure_ascii=False) + "\n")
        print(f"\nдесятка выгружена: {args.dump_picks}")

    if args.dry_run:
        print("\n--dry-run: к модели не обращаемся")
        return

    system_prompt = schema.build_system_prompt()
    api_key = os.getenv(args.api_key_env, "not-needed")
    results = []

    print(f"\nопрашиваю {args.model} на {args.base_url}")
    with httpx.Client(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(300.0),
    ) as client:
        for record in picked:
            started = time.time()
            try:
                raw = ask(client, args.model, system_prompt, record["name"])
                error = None
            except httpx.HTTPError as exc:
                raw, error = "", f"{type(exc).__name__}: {exc}"

            expected, _ = schema.parse_model_json(
                next(m["content"] for m in record["messages"] if m["role"] == "assistant")
            )
            results.append({
                "id": record["id"],
                "name": record["name"],
                "url": record.get("url", ""),
                "picked_for": record["picked_for"],
                "hard_flags": record.get("hard_flags", []),
                "expected": expected,
                "raw": raw,
                "error": error,
                "latency_s": round(time.time() - started, 2),
            })
            status = "ошибка" if error else f"{len(raw)} симв"
            print(f"  [{record['picked_for']:12s}] {status:12s} {record['name'][:52]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")

    failed = sum(1 for r in results if r["error"])
    print(f"\nготово: {len(results)} ответов, ошибок {failed}")
    print(f"записано: {args.out}")
    print(f"метрики: python3 scripts/score.py --predictions {args.out} "
          f"--out baseline/report.md --title 'Baseline: {args.model} без тюна'")


if __name__ == "__main__":
    main()
