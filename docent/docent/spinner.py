"""Терминальная крутилка на время долгих операций (индексация, генерация).

Пишет в stderr, поэтому не мешает полезному выводу в stdout. Активна только в
интерактивном терминале (TTY); при перенаправлении в файл/пайп молчит.
"""

from __future__ import annotations

import itertools
import sys
import threading
from types import TracebackType
from typing import TextIO

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """Контекст-менеджер: крутит анимацию с подписью, пока идёт работа."""

    def __init__(self, text: str = "Доцент думает", stream: TextIO | None = None) -> None:
        self._text = text
        self._stream = stream or sys.stderr
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = self._stream.isatty()

    def __enter__(self) -> "Spinner":
        if self._enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        if not self._enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join()
        # Стираем строку крутилки.
        self._stream.write("\r\033[K")
        self._stream.flush()

    def _spin(self) -> None:
        """Цикл анимации до сигнала остановки."""
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                return
            self._stream.write(f"\r{frame} {self._text}…")
            self._stream.flush()
            self._stop.wait(0.08)
