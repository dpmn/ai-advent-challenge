#!/usr/bin/env python3
"""Клиент запуска файнтюна через OpenAI-совместимый API.

Автоматизирует цепочку upload file -> create fine-tuning job -> poll status.
Написан на httpx, а не на openai SDK: проект и так ходит в Cloud.ru через
httpx, а три эндпоинта не стоят новой зависимости. Работает с любым
провайдером, который повторяет контракт OpenAI (сам OpenAI, Nebius и т. д.).

ПО УМОЛЧАНИЮ НИЧЕГО НЕ ОТПРАВЛЯЕТ. Задание дня 41 требует подготовить код,
но не запускать тюн, поэтому dry-run — режим по умолчанию, а реальные
запросы включаются только явным --yes.
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
DATASETS = DAY_DIR / "datasets"
PREPARED_DIR = DATASETS / "prepared"

POLL_INTERVAL_S = 30
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class FineTuneClient:
    """Тонкая обёртка над тремя эндпоинтами OpenAI-совместимого API."""

    def __init__(self, base_url: str, api_key: str, dry_run: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(300.0),
        )

    def close(self) -> None:
        """Закрывает HTTP-соединение."""
        self._client.close()

    def _log_intent(self, method: str, path: str, note: str) -> None:
        """Печатает, что было бы отправлено, в режиме dry-run."""
        print(f"  [dry-run] {method} {self.base_url}{path}")
        print(f"            {note}")

    def upload(self, path: Path) -> str:
        """Загружает JSONL с purpose=fine-tune, возвращает id файла."""
        size_kb = path.stat().st_size / 1024
        lines = sum(1 for _ in path.open(encoding="utf-8"))
        if self.dry_run:
            self._log_intent("POST", "/files",
                             f"file={path.name} ({lines} примеров, {size_kb:.0f} КБ), purpose=fine-tune")
            return f"file-DRYRUN-{path.stem}"

        with path.open("rb") as fh:
            response = self._client.post(
                "/files",
                files={"file": (path.name, fh, "application/jsonl")},
                data={"purpose": "fine-tune"},
            )
        response.raise_for_status()
        file_id = response.json()["id"]
        print(f"  загружен {path.name} -> {file_id}")
        return file_id

    def create_job(self, model: str, train_file: str, eval_file: str | None,
                   hyperparameters: dict, suffix: str | None) -> str:
        """Создаёт задачу дообучения, возвращает её id."""
        payload: dict = {
            "model": model,
            "training_file": train_file,
            "hyperparameters": hyperparameters,
        }
        if eval_file:
            payload["validation_file"] = eval_file
        if suffix:
            payload["suffix"] = suffix

        if self.dry_run:
            self._log_intent("POST", "/fine_tuning/jobs",
                             json.dumps(payload, ensure_ascii=False))
            return "ftjob-DRYRUN"

        response = self._client.post("/fine_tuning/jobs", json=payload)
        response.raise_for_status()
        job_id = response.json()["id"]
        print(f"  задача создана -> {job_id}")
        return job_id

    def poll(self, job_id: str, interval: int = POLL_INTERVAL_S) -> dict:
        """Опрашивает статус задачи до терминального, печатая события."""
        if self.dry_run:
            self._log_intent("GET", f"/fine_tuning/jobs/{job_id}",
                             f"опрос каждые {interval} с до статуса из {sorted(TERMINAL_STATUSES)}")
            self._log_intent("GET", f"/fine_tuning/jobs/{job_id}/events",
                             "логи обучения по мере поступления")
            return {"id": job_id, "status": "dry-run", "fine_tuned_model": None}

        seen_events: set[str] = set()
        while True:
            response = self._client.get(f"/fine_tuning/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()

            try:
                events = self._client.get(f"/fine_tuning/jobs/{job_id}/events").json()
                for event in reversed(events.get("data", [])):
                    if event["id"] not in seen_events:
                        seen_events.add(event["id"])
                        print(f"    [{event.get('level', 'info')}] {event.get('message', '')}")
            except httpx.HTTPError:
                pass  # события — необязательная часть контракта, статус важнее

            status = job.get("status")
            print(f"  статус: {status}")
            if status in TERMINAL_STATUSES:
                return job
            time.sleep(interval)


def prepare(source: Path, target: Path) -> Path:
    """Готовит файл к загрузке: оставляет только messages.

    В train.jsonl и eval.jsonl лежат ещё id, group_id и hard_flags — они нужны
    валидатору и скорингу, но в API идти не должны.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with source.open(encoding="utf-8") as src, target.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            record = json.loads(line)
            dst.write(json.dumps({"messages": record["messages"]}, ensure_ascii=False) + "\n")
            kept += 1
    print(f"  подготовлен {target.name}: {kept} примеров (метаданные отброшены)")
    return target


def main() -> None:
    """Точка входа: готовит файлы и проводит цепочку upload -> job -> poll."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("FT_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default="FT_API_KEY")
    parser.add_argument("--model", default="Qwen/Qwen3-14B", help="базовая модель для дообучения")
    parser.add_argument("--suffix", default="mp-attrs", help="метка в имени дообученной модели")
    parser.add_argument("--train", type=Path, default=DATASETS / "train.jsonl")
    parser.add_argument("--eval", type=Path, default=DATASETS / "eval.jsonl")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", default="auto")
    parser.add_argument("--lr-multiplier", default="auto")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_S)
    parser.add_argument("--yes", action="store_true",
                        help="ВЫКЛЮЧИТЬ dry-run и реально отправить запросы")
    args = parser.parse_args()

    if not args.train.exists():
        sys.exit(f"нет обучающего файла: {args.train}")

    dry_run = not args.yes
    api_key = os.getenv(args.api_key_env, "")
    if not dry_run and not api_key:
        sys.exit(f"нет ключа в переменной окружения {args.api_key_env}")

    print("=== ПОДГОТОВКА ФАЙЛОВ ===")
    train_prepared = prepare(args.train, PREPARED_DIR / "train.openai.jsonl")
    eval_prepared = prepare(args.eval, PREPARED_DIR / "eval.openai.jsonl") if args.eval.exists() else None

    mode = "DRY-RUN (ничего не отправляется)" if dry_run else "БОЕВОЙ ЗАПУСК"
    print(f"\n=== {mode} ===")
    print(f"  эндпоинт: {args.base_url}")
    print(f"  базовая модель: {args.model}")

    client = FineTuneClient(args.base_url, api_key, dry_run=dry_run)
    try:
        print("\n1. Загрузка файлов")
        train_id = client.upload(train_prepared)
        eval_id = client.upload(eval_prepared) if eval_prepared else None

        print("\n2. Создание задачи дообучения")
        hyperparameters = {
            "n_epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate_multiplier": args.lr_multiplier,
        }
        job_id = client.create_job(args.model, train_id, eval_id, hyperparameters, args.suffix)

        print("\n3. Опрос статуса")
        job = client.poll(job_id, args.poll_interval)
    finally:
        client.close()

    print("\n=== ИТОГ ===")
    print(f"  задача: {job.get('id')}")
    print(f"  статус: {job.get('status')}")
    if job.get("fine_tuned_model"):
        print(f"  модель: {job['fine_tuned_model']}")
    if dry_run:
        print("\n  Это был dry-run. Для реального запуска добавь --yes и ключ в "
              f"{args.api_key_env}.")


if __name__ == "__main__":
    main()
