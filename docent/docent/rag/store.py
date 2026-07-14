"""Хранилище эмбеддингов: numpy-матрица + метаданные чанков в `.docent/`.

Поиск — brute-force косинусная близость. Для документации одного репозитория
(десятки-сотни чанков) этого достаточно и не требует FAISS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from docent.rag.chunker import Chunk

VECTORS_FILE = "index.npy"
CHUNKS_FILE = "chunks.json"


@dataclass
class Hit:
    """Результат поиска: чанк и его косинусная близость к запросу."""

    chunk: Chunk
    score: float


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """Нормирует строки матрицы к единичной длине (для косинуса через dot)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def save(dir_path: Path, vectors: np.ndarray, chunks: list[Chunk]) -> None:
    """Сохраняет матрицу эмбеддингов и метаданные чанков в каталог."""
    dir_path.mkdir(parents=True, exist_ok=True)
    np.save(dir_path / VECTORS_FILE, vectors.astype(np.float32))
    payload = [
        {"source": c.source, "heading": c.heading, "text": c.text} for c in chunks
    ]
    (dir_path / CHUNKS_FILE).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load(dir_path: Path) -> tuple[np.ndarray, list[Chunk]]:
    """Загружает матрицу эмбеддингов и чанки из каталога."""
    vectors = np.load(dir_path / VECTORS_FILE)
    raw = json.loads((dir_path / CHUNKS_FILE).read_text(encoding="utf-8"))
    chunks = [Chunk(source=r["source"], heading=r["heading"], text=r["text"]) for r in raw]
    return vectors, chunks


def search(
    query_vec: list[float], vectors: np.ndarray, chunks: list[Chunk], top_k: int
) -> list[Hit]:
    """Возвращает top_k чанков по косинусной близости к запросу."""
    if len(chunks) == 0:
        return []
    query = np.asarray(query_vec, dtype=np.float32)
    query = query / (np.linalg.norm(query) or 1.0)
    scores = _normalize(vectors.astype(np.float32)) @ query
    top_idx = np.argsort(scores)[::-1][:top_k]
    return [Hit(chunk=chunks[i], score=float(scores[i])) for i in top_idx]
