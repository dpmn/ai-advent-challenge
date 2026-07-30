#!/usr/bin/env python3
"""Клиент слабой модели: `qwen3:0.6b` в Ollama на ноутбуке.

Две вещи, без которых прогон бессмысленен.

**`think: false`.** Шаблон Qwen3 включает размышления по умолчанию, и день 41
на этом потерял baseline: 0% чистого JSON, 2214 символов вместо 162. Флаг
гасится здесь жёстко, а не оставляется на усмотрение вызывающего.

**`logprobs: true`.** Уверенность по каждому полю считается из logprob-ов
того же самого вызова — это единственный механизм дня 42, который окупился
(ноль доп. вызовов). Ollama отдаёт по токену `logprob` и `bytes`; из байтов
восстанавливаются символьные границы токена, чтобы `gates.score_confidence`
дня 42 работал без изменений.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from common import OLLAMA_URL, SMALL_MODEL

DEFAULT_NUM_PREDICT = 256
DEFAULT_TIMEOUT = 300


def token_offsets(entries: list[dict]) -> tuple[list[dict], str]:
    """Переводит logprob-ы Ollama в формат дня 42: `{start, end, logprob}`.

    Границы считаются нарастающим байтовым префиксом, а не сложением длин
    токенов: токенизатор режет по байтам, и кириллический символ может
    оказаться разорван между двумя токенами. Такой токен не даёт нового
    символа, и его logprob иначе просто выпал бы из оценки поля — поэтому
    ему отдаётся тот символ, который он начинает.
    """
    tokens: list[dict] = []
    buffer = b""
    previous = ""
    for entry in entries:
        raw = entry.get("bytes")
        buffer += bytes(raw) if raw else entry.get("token", "").encode()
        current = buffer.decode("utf-8", "ignore")
        start = len(previous)
        tokens.append({
            "start": start,
            "end": max(len(current), start + 1),
            "logprob": round(float(entry["logprob"]), 5),
        })
        previous = current
    return tokens, previous


class OllamaClient:
    """Обёртка над `/api/chat` Ollama с замером времени и logprob-ами."""

    def __init__(self, url: str = OLLAMA_URL, model: str = SMALL_MODEL,
                 num_predict: int = DEFAULT_NUM_PREDICT, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.num_predict = num_predict
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

    def health(self) -> dict:
        """Проверяет, что Ollama отвечает и нужная модель скачана."""
        with urllib.request.urlopen(f"{self.url}/api/tags", timeout=10) as response:
            tags = json.loads(response.read())
        names = [m["name"] for m in tags.get("models", [])]
        if self.model not in names:
            raise RuntimeError(f"модели {self.model} нет в Ollama; есть: {', '.join(names)}")
        details = next(m for m in tags["models"] if m["name"] == self.model)
        return {"model": self.model, "quantization": details["details"].get("quantization_level")}

    def generate(self, system_prompt: str, user_content: str) -> dict:
        """Жадная генерация одного ответа с logprob-ами по каждому токену."""
        started = time.time()
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "think": False,
            "stream": False,
            "options": {"temperature": 0, "num_predict": self.num_predict},
            "logprobs": True,
            "top_logprobs": 1,
        })
        elapsed = time.time() - started

        raw = data.get("message", {}).get("content") or ""
        tokens, rebuilt = token_offsets(data.get("logprobs") or [])
        return {
            "raw": raw,
            "tokens": tokens,
            "latency_s": round(elapsed, 3),
            "gen_tokens": data.get("eval_count"),
            "prompt_tokens": data.get("prompt_eval_count"),
            "done_reason": data.get("done_reason"),
            # Расхождение означало бы, что символьные границы токенов съехали
            # и уверенность по полям считается не по тем токенам.
            "tokens_aligned": rebuilt == raw,
        }
