#!/usr/bin/env python3
"""Клиент Ollama для дня 44: короткие вызовы этапов и сэмплирование для контроля.

Почему не берётся `day-43/scripts/small.py` целиком: там `temperature` зашита
в ноль, а руке `mono-x4` (контроль «а не просто ли дело в четырёх вызовах»)
нужны разные сэмплы. Зато logprob-ы здесь не нужны вовсе — гейтов дня 42
и routing-а дня 43 в этом дне нет, эскалацией никто не управляет.

**`think: false`.** Шаблон Qwen3 включает размышления по умолчанию, и день 41
на этом потерял baseline: 0% чистого JSON, 2214 символов вместо 162. Флаг
гасится здесь жёстко, а не оставляется на усмотрение вызывающего: этапы
декомпозиции ждут одну строку ответа, и утечка размышлений ломает их все.
"""

from __future__ import annotations

import json
import time
import urllib.request

from common import OLLAMA_URL, SMALL_MODEL

DEFAULT_NUM_PREDICT = 256
DEFAULT_TIMEOUT = 300


class OllamaClient:
    """Обёртка над `/api/chat` Ollama с замером времени и счётчиками токенов."""

    def __init__(self, url: str = OLLAMA_URL, model: str = SMALL_MODEL,
                 timeout: int = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        """Отправляет JSON-запрос и возвращает разобранный ответ."""
        request = urllib.request.Request(
            f"{self.url}{path}",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def health(self, models: list[str]) -> dict:
        """Проверяет, что Ollama отвечает и все нужные модели скачаны."""
        with urllib.request.urlopen(f"{self.url}/api/tags", timeout=10) as response:
            tags = json.loads(response.read())
        available = {m["name"]: m for m in tags.get("models", [])}
        missing = [m for m in models if m not in available]
        if missing:
            raise RuntimeError(
                f"в Ollama нет моделей: {', '.join(missing)}; есть: {', '.join(available)}")
        return {
            name: available[name]["details"].get("quantization_level") for name in models
        }

    def generate(self, system_prompt: str, user_content: str, model: str | None = None,
                 num_predict: int = DEFAULT_NUM_PREDICT, temperature: float = 0.0,
                 seed: int | None = None) -> dict:
        """Один вызов модели. По умолчанию жадный, размышления выключены."""
        options = {"temperature": temperature, "num_predict": num_predict}
        if seed is not None:
            options["seed"] = seed

        started = time.time()
        data = self._post("/api/chat", {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "think": False,
            "stream": False,
            "options": options,
        })
        elapsed = time.time() - started

        return {
            "raw": (data.get("message", {}).get("content") or "").strip(),
            "latency_s": round(elapsed, 3),
            "gen_tokens": data.get("eval_count"),
            "prompt_tokens": data.get("prompt_eval_count"),
            "done_reason": data.get("done_reason"),
        }
