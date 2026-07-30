#!/usr/bin/env python3
"""Клиент сильной модели: `Qwen3-14B` на арендованной ВМ через ssh-туннель.

Сервер — `scripts/serve_big.py`, поднятый на ВМ и слушающий её loopback.
На ноутбуке порт появляется туннелем (см. RUNBOOK), поэтому по умолчанию
клиент стучится в `127.0.0.1`.

Промпт клиент не строит: он живёт на сервере, собирается из схемы дня 41 и
одинаков для обеих моделей. Иначе эскалация мерила бы разницу промптов,
а не разницу моделей.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from common import BIG_URL

DEFAULT_TIMEOUT = 600


class BigModelUnavailable(RuntimeError):
    """Сервер сильной модели не отвечает: ВМ погашена или туннель не поднят."""


class BigClient:
    """Обёртка над HTTP-API сильной модели с накоплением GPU-времени."""

    def __init__(self, url: str = BIG_URL, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.gpu_seconds = 0.0
        self.calls = 0

    def _request(self, path: str, payload: dict | None = None) -> dict:
        """Отправляет запрос к серверу, переводя сетевые ошибки в понятную."""
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        request = urllib.request.Request(f"{self.url}{path}", data, headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.URLError as error:
            raise BigModelUnavailable(
                f"сильная модель на {self.url} недоступна ({error}); "
                "проверь, что ВМ жива и ssh-туннель поднят"
            ) from error

    def health(self) -> dict:
        """Состояние сервера: какая модель, сколько GPU-времени уже потрачено."""
        return self._request("/health")

    def generate(self, names: list[str]) -> list[dict]:
        """Запрашивает полные ответы модели на список названий товаров.

        Ответ всегда полный, по тому же промпту, что у слабой модели, — даже
        когда эскалировано одно поле. Урезанный промпт «верни только form»
        был бы дешевле по токенам, но его результат не сравнить ни с baseline
        дня 41, ни с якорем «всё на сильной»: это был бы третий промпт.
        """
        started = time.time()
        data = self._request("/generate", {"names": names})
        wall = time.time() - started
        self.gpu_seconds += float(data.get("gpu_seconds") or 0.0)
        self.calls += len(names)
        items = data.get("items") or []
        for item in items:
            item["wall_s"] = round(wall / max(1, len(items)), 3)
        return items

    def generate_one(self, name: str) -> dict:
        """Один товар — один ответ."""
        items = self.generate([name])
        if not items:
            raise BigModelUnavailable("сервер вернул пустой список ответов")
        return items[0]
