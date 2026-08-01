#!/usr/bin/env python3
"""Заглушка большой модели: отдаёт ответы из дампа дня 41 вместо живых вызовов.

Нужна ровно для одного — прогнать конвейер целиком до аренды ВМ и убедиться,
что вывод, подсчёты и запись результатов работают. Ответы настоящие (те же
100 примеров, снятые в дне 41 и подтверждённые живым прогоном дня 43), но
задержка выдуманная, поэтому мерить по заглушке ничего нельзя — только
проверять, что скрипт цел.

Интерфейс совпадает с `BigClient` дня 43, чтобы конвейер не знал, с кем
разговаривает.
"""

from __future__ import annotations

from common import DAY41_BIG_DUMP, read_jsonl

# Выдуманное время ответа. Взято близким к живым замерам дня 43 (~1,3 с
# на товар), но это подстановка, а не измерение.
FAKE_LATENCY_S = 1.3


class MockBigClient:
    """Возвращает ответы 14B из дампа дня 41 по названию товара."""

    def __init__(self) -> None:
        self.gpu_seconds = 0.0
        self.calls = 0
        self._by_name = {r["name"]: r for r in read_jsonl(DAY41_BIG_DUMP)}

    def health(self) -> dict:
        """Сообщает, что это заглушка, а не живой сервер."""
        return {"ok": True, "mock": True, "model": "дамп дня 41",
                "items": len(self._by_name)}

    def generate_one(self, name: str) -> dict:
        """Один товар — один ответ из дампа."""
        record = self._by_name.get(name)
        if record is None:
            raise KeyError(f"в дампе дня 41 нет товара {name!r}")
        self.calls += 1
        self.gpu_seconds += FAKE_LATENCY_S
        return {"raw": record.get("raw") or "", "latency_s": FAKE_LATENCY_S,
                "gen_tokens": None, "wall_s": FAKE_LATENCY_S}
