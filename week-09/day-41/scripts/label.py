#!/usr/bin/env python3
"""Черновая разметка кандидатов тяжёлой моделью через Cloud.ru.

Дистилляция: большая модель размечает, программные инварианты отсеивают
галлюцинации, человек вычитывает eval. Размечающая модель видит ровно тот
же system-промпт, который потом пойдёт в обучающие примеры и в baseline.

Пишет инкрементально и умеет докатывать: уже размеченные id пропускаются,
так что оборванный прогон продолжается с места остановки.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv

import schema

load_dotenv()

DAY_DIR = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = DAY_DIR / "datasets" / "candidates.jsonl"
OUT_PATH = DAY_DIR / "datasets" / "labeled.jsonl"

BASE_URL = "https://foundation-models.api.cloud.ru/v1"
DEFAULT_MODEL = "MiniMaxAI/MiniMax-M2.5"
MAX_RETRIES = 4
CARRY_FIELDS = ("id", "name", "url", "source", "group_id", "final_probe", "hard_flags")

_write_lock = threading.Lock()


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл, возвращает пустой список, если файла нет."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def label_one(client: httpx.Client, model: str, system_prompt: str, name: str) -> str:
    """Размечает одно название, возвращает сырой текст ответа модели."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": name},
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }
    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.post("/chat/completions", json=payload)
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            choice = response.json()["choices"][0]
            message = choice["message"]
            # MiniMax — reasoning-модель: на части запросов content приходит null,
            # а текст оседает в reasoning_content. Пустой ответ считаем сбоем и повторяем.
            content = message.get("content") or message.get("reasoning_content") or ""
            if content.strip():
                return content
            last_error = f"пустой content (finish_reason={choice.get('finish_reason')})"
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt)
    raise RuntimeError(f"не удалось разметить после {MAX_RETRIES} попыток: {last_error}")


def process(record: dict, client: httpx.Client, model: str, system_prompt: str,
            out_handle, counters: dict) -> None:
    """Размечает одну запись, прогоняет инварианты и дописывает результат."""
    try:
        raw = label_one(client, model, system_prompt, record["name"])
        error = None
    except RuntimeError as exc:
        raw, error = "", str(exc)

    obj, clean_json = schema.parse_model_json(raw) if raw else (None, False)
    problems = schema.check_invariants(record["name"], obj) if obj else ["ответ не разобран в JSON"]

    result = {field: record[field] for field in CARRY_FIELDS}
    result.update({
        "obj": obj,
        "clean_json": clean_json,
        "problems": problems,
        "raw": raw if obj is None else "",
        "error": error,
    })

    with _write_lock:
        out_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        out_handle.flush()
        counters["done"] += 1
        if problems:
            counters["with_problems"] += 1
        if counters["done"] % 25 == 0:
            print(f"  размечено {counters['done']}/{counters['total']}, "
                  f"с замечаниями {counters['with_problems']}", flush=True)


def main() -> None:
    """Точка входа: размечает кандидатов и печатает сводку по инвариантам."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, help="разметить только первые N (для прогонки)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--restart", action="store_true", help="разметить заново, не докатывать")
    args = parser.parse_args()

    api_key = os.getenv("CLOUDRU_SECRET_KEY")
    if not api_key:
        sys.exit("нет CLOUDRU_SECRET_KEY в окружении (положи в .env)")

    candidates = read_jsonl(args.candidates)
    if not candidates:
        sys.exit(f"нет кандидатов: {args.candidates}")

    if args.restart and args.out.exists():
        args.out.unlink()
    done_ids = {rec["id"] for rec in read_jsonl(args.out) if rec.get("obj") is not None}
    todo = [rec for rec in candidates if rec["id"] not in done_ids]
    if args.limit:
        todo = todo[: args.limit]

    print(f"кандидатов {len(candidates)}, уже размечено {len(done_ids)}, к разметке {len(todo)}")
    if not todo:
        print("нечего делать")
        return

    system_prompt = schema.build_system_prompt()
    counters = {"done": 0, "with_problems": 0, "total": len(todo)}
    started = time.time()

    with args.out.open("a", encoding="utf-8") as out_handle:
        with httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(180.0),
        ) as client:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for record in todo:
                    pool.submit(process, record, client, args.model,
                                system_prompt, out_handle, counters)

    elapsed = time.time() - started
    print(f"\nготово за {elapsed:.0f} с")
    print(f"размечено: {counters['done']}, с замечаниями инвариантов: {counters['with_problems']}")
    print(f"результат: {args.out}")


if __name__ == "__main__":
    main()
