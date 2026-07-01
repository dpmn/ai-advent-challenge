"""Семантический поиск по FAISS и RagPipeline — пайплайн поиска с фильтрацией и реранкингом."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from ragger.embedder import get_embeddings

DATA_DIR = Path(__file__).resolve().parent / "data"

_indexes: dict[str, faiss.Index] = {}
_metadatas: dict[str, list[dict]] = {}


def _load_index(strategy: str):
    """Загружает FAISS + metadata. Кеширует в памяти после первого вызова."""
    if strategy in _indexes:
        return _indexes[strategy], _metadatas[strategy]

    index_path = DATA_DIR / strategy / "index.faiss"
    meta_path = DATA_DIR / strategy / "metadata.json"

    if not index_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"Index '{strategy}' not found at {index_path.parent}. "
            f"Run `python ragger/pipeline.py` first."
        )

    index = faiss.read_index(str(index_path))
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    _indexes[strategy] = index
    _metadatas[strategy] = metadata
    return index, metadata


load_index = _load_index


def search(
    query: str,
    top_k: int = 5,
    strategy: str = "structural",
) -> list[dict]:
    """Семантический поиск по проиндексированным документам проекта.

    Args:
        query: поисковый запрос.
        top_k: количество возвращаемых чанков.
        strategy: "structural" (по разделам) или "fixed" (фиксированный размер).

    Returns:
        Список чанков с полями chunk_id, source, title, section, text, score, token_count.
    """
    index, metadata = _load_index(strategy)

    emb = get_embeddings([query])
    faiss.normalize_L2(emb)

    scores, indices = index.search(emb, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        meta = metadata[idx]
        results.append({
            "chunk_id": meta["chunk_id"],
            "source": meta["source"],
            "title": meta["title"],
            "section": meta["section"],
            "text": meta["text"],
            "score": float(score),
            "token_count": meta["token_count"],
        })

    return results


@dataclass
class RagPipeline:
    """Пайплайн RAG-поиска: FAISS search → threshold filter → LLM rerank → top-K slice.

    Режимы:
      "threshold" — только фильтр по similarity score
      "rerank"    — только LLM-реранкинг (без threshold)
      "hybrid"    — threshold-фильтр, затем реранкинг оставшихся
    """

    api_key: str
    top_k_before: int = 10
    top_k_after: int = 5
    threshold: float = 0.5
    mode: str = "hybrid"
    rerank_model: str = "Qwen/Qwen3-30B-A3B"
    base_url: str = "https://foundation-models.api.cloud.ru/v1"
    strategy: str = "structural"

    def run(self, query: str) -> list[dict]:
        """Полный пайплайн: search → filter → rerank → slice."""
        chunks = self._search(query)

        stats = {
            "before_filter": len(chunks),
            "after_filter": 0,
            "after_rerank": 0,
            "final": 0,
            "threshold_cut": 0,
            "rerank_cut": 0,
        }

        if self.mode == "threshold":
            from ragger.reranker import threshold_filter
            before = len(chunks)
            chunks = threshold_filter(chunks, self.threshold)
            stats["threshold_cut"] = before - len(chunks)

        elif self.mode == "rerank":
            if chunks:
                from ragger.reranker import llm_rerank
                chunks = llm_rerank(query, chunks, self.api_key, self.rerank_model, self.base_url)

        elif self.mode == "hybrid":
            from ragger.reranker import threshold_filter, llm_rerank
            before = len(chunks)
            chunks = threshold_filter(chunks, self.threshold)
            stats["threshold_cut"] = before - len(chunks)
            if chunks:
                chunks = llm_rerank(query, chunks, self.api_key, self.rerank_model, self.base_url)

        stats["after_filter"] = len(chunks)

        if len(chunks) > self.top_k_after:
            stats["rerank_cut"] = len(chunks) - self.top_k_after
            chunks = chunks[:self.top_k_after]

        stats["after_rerank"] = len(chunks)
        stats["final"] = len(chunks)

        self._last_stats = stats
        self._last_query = query
        self._last_chunks = chunks
        return chunks

    def _search(self, query: str) -> list[dict]:
        """Базовый FAISS-поиск."""
        return search(query, top_k=self.top_k_before, strategy=self.strategy)

    def compare_modes(self, query: str) -> list[dict]:
        """Прогоняет запрос во всех 3 режимах и возвращает статистику."""
        rows = []
        saved_mode = self.mode

        for mode in ("threshold", "rerank", "hybrid"):
            self.mode = mode
            self.run(query)
            stats = self._last_stats
            avg_score = 0.0
            min_score = 0.0
            max_score = 0.0
            sources = set()
            chunks = getattr(self, "_last_chunks", [])
            if chunks:
                scores = [c["score"] for c in chunks]
                avg_score = sum(scores) / len(scores)
                min_score = min(scores)
                max_score = max(scores)
                sources = {c["source"] for c in chunks}
            rows.append({
                "mode": mode,
                "before_filter": stats["before_filter"],
                "after_filter": stats["after_filter"],
                "threshold_cut": stats["threshold_cut"],
                "rerank_cut": stats["rerank_cut"],
                "final": stats["final"],
                "avg_score": round(avg_score, 4),
                "min_score": round(min_score, 4),
                "max_score": round(max_score, 4),
                "sources": ", ".join(sorted(sources)),
            })

        self.mode = saved_mode
        self._last_compare = rows
        return rows
