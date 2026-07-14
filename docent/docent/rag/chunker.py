"""Разбиение markdown-документов на чанки для индексации.

Стратегия: сначала режем по заголовкам (`#`), сохраняя путь заголовков как
контекст, затем длинные секции окном по символам с перекрытием.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    """Фрагмент документа: текст, источник и путь заголовков над ним."""

    source: str
    heading: str
    text: str


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Режет markdown по заголовкам, возвращая пары (путь_заголовков, текст)."""
    sections: list[tuple[str, str]] = []
    # Стек заголовков по уровням для построения пути ("H1 > H2 > H3").
    heading_stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def heading_path() -> str:
        return " > ".join(title for _, title in heading_stack)

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((heading_path(), body))
        buf.clear()

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            if title:
                flush()
                # Сбрасываем заголовки уровнем глубже или равным текущему.
                heading_stack[:] = [h for h in heading_stack if h[0] < level]
                heading_stack.append((level, title))
                continue
        buf.append(line)
    flush()
    return sections


def _window(text: str, max_chars: int, overlap: int) -> list[str]:
    """Режет длинный текст окном по символам с перекрытием."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        pieces.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return pieces


def chunk_markdown(
    text: str, source: str, max_chars: int = 1200, overlap: int = 150
) -> list[Chunk]:
    """Возвращает список чанков для одного markdown-документа."""
    chunks: list[Chunk] = []
    for heading, body in _split_sections(text):
        for piece in _window(body, max_chars, overlap):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(source=source, heading=heading, text=piece))
    return chunks
