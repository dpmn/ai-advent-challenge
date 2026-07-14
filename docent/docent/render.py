"""Рендер markdown-ответа в терминал через rich.

В интерактивном терминале markdown рендерится с форматированием (жирный,
заголовки, списки, таблицы, подсветка кода). При перенаправлении в файл/пайп
печатается сырой текст — чтобы вывод оставался пригодным для скриптов.
"""

from __future__ import annotations

import sys


def render_markdown(text: str) -> None:
    """Печатает markdown-текст: с форматированием в TTY, иначе как есть."""
    if not sys.stdout.isatty():
        print(text)
        return
    # Ленивый импорт: rich нужен только на пути с форматированием.
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.theme import Theme

    # По умолчанию rich рисует инлайн-код на чёрном фоне — плохо читается.
    # Убираем фон, оставляя только цвет (Theme наследует остальные стили).
    theme = Theme({"markdown.code": "bold cyan"})
    Console(theme=theme).print(Markdown(text))
