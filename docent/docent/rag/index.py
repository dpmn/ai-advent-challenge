"""Построение и запрос RAG-индекса по документации репозитория."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from docent import llm
from docent.config import Config, docent_dir
from docent.rag import store
from docent.rag.chunker import Chunk, chunk_markdown


@dataclass
class IndexStats:
    """Итог индексации: сколько файлов и чанков попало в индекс."""

    files: int
    chunks: int


def _collect_files(root: Path, globs: list[str]) -> list[Path]:
    """Собирает уникальные файлы репозитория по glob-паттернам конфига."""
    seen: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if path.is_file():
                seen.add(path)
    return sorted(seen)


def build(root: Path, config: Config) -> IndexStats:
    """Строит индекс: обход файлов → chunking → эмбеддинги → сохранение.

    Требует ключ API (эмбеддинги считаются через провайдера).
    """
    files = _collect_files(root, config.index_globs)
    chunks: list[Chunk] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(root))
        chunks.extend(chunk_markdown(text, source=rel))

    if not chunks:
        store.save(docent_dir(root), np.zeros((0, 1), dtype=np.float32), [])
        return IndexStats(files=len(files), chunks=0)

    vectors = llm.embed([c.text for c in chunks], config)
    store.save(docent_dir(root), np.asarray(vectors, dtype=np.float32), chunks)
    return IndexStats(files=len(files), chunks=len(chunks))


def query(root: Path, config: Config, question: str) -> list[store.Hit]:
    """Возвращает наиболее релевантные вопросу чанки из индекса."""
    vectors, chunks = store.load(docent_dir(root))
    query_vec = llm.embed([question], config)[0]
    return store.search(query_vec, vectors, chunks, config.top_k)
