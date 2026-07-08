"""Семантический поиск по FAISS и RagPipeline — пайплайн поиска с фильтрацией и реранкингом.

RagPipeline — основной класс для RAG-поиска. Поддерживает три режима:
- threshold: только фильтрация по similarity score
- rerank: только LLM-реранкинг
- hybrid: фильтрация + реранкинг

Этапы пайплайна: FAISS search → threshold filter → LLM rerank → top-K slice.
Можно настроить top_k_before, top_k_after, threshold через параметры конструктора.

Поддерживает два набора индексов: облачный (ragger/data/, эмбеддинги Cloud.ru)
и локальный (ragger/data_local/, эмбеддинги nomic-embed-text через Ollama).
Выбор — через параметры data_dir / embed_* у search() и RagPipeline.
Тайминги этапов последнего запроса — в RagPipeline._last_timings.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from ragger.embedder import BASE_URL as CLOUD_BASE_URL
from ragger.embedder import get_embeddings

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR_LOCAL = Path(__file__).resolve().parent / "data_local"

_indexes: dict[tuple[str, str], faiss.Index] = {}
_metadatas: dict[tuple[str, str], list[dict]] = {}


def _load_index(strategy: str, data_dir: Path = DATA_DIR):
    """Загружает FAISS + metadata из data_dir. Кеширует в памяти по ключу (data_dir, strategy)."""
    cache_key = (str(data_dir), strategy)
    if cache_key in _indexes:
        return _indexes[cache_key], _metadatas[cache_key]

    index_path = data_dir / strategy / "index.faiss"
    meta_path = data_dir / strategy / "metadata.json"

    if not index_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"Index '{strategy}' not found at {index_path.parent}. "
            f"Run `python ragger/pipeline.py` first."
        )

    index = faiss.read_index(str(index_path))
    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    _indexes[cache_key] = index
    _metadatas[cache_key] = metadata
    return index, metadata


load_index = _load_index


def search(
    query: str,
    top_k: int = 5,
    strategy: str = "structural",
    data_dir: Path = DATA_DIR,
    embed_api_key: str | None = None,
    embed_model: str = "openai/text-embedding-3-small",
    embed_base_url: str = CLOUD_BASE_URL,
    embed_prefix: str = "",
    timings: dict | None = None,
) -> list[dict]:
    """Семантический поиск по FAISS-индексу. Запрос → эмбеддинг → FAISS search → ранжированные чанки.

    Args:
        query: поисковый запрос (русский или английский).
        top_k: количество возвращаемых чанков.
        strategy: "structural" (по разделам документа) или "fixed" (фиксированный размер).
        data_dir: каталог индексов (DATA_DIR — облачный, DATA_DIR_LOCAL — локальный).
        embed_api_key: ключ API эмбеддера (None → CLOUDRU_SECRET_KEY из env).
        embed_model: модель эмбеддинга запроса (должна совпадать с моделью индекса).
        embed_base_url: base_url API эмбеддера (Cloud.ru или Ollama).
        embed_prefix: task-префикс запроса ('search_query: ' для nomic-embed-text).
        timings: словарь для записи таймингов этапов (embed_s, faiss_s), опционально.

    Returns:
        Список чанков с полями chunk_id, source, title, section, text, score, token_count.
    """
    index, metadata = _load_index(strategy, data_dir)

    t0 = time.monotonic()
    emb = get_embeddings(
        [query],
        api_key=embed_api_key,
        model=embed_model,
        base_url=embed_base_url,
        prefix=embed_prefix,
    )
    t1 = time.monotonic()

    faiss.normalize_L2(emb)
    scores, indices = index.search(emb, top_k)
    t2 = time.monotonic()

    if timings is not None:
        timings["embed_s"] = t1 - t0
        timings["faiss_s"] = t2 - t1

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

    Провайдер выбирается полями data_dir + embed_* (индекс и эмбеддинг запроса
    должны быть от одной модели) и base_url + rerank_model (реранк).
    """

    api_key: str
    top_k_before: int = 10
    top_k_after: int = 5
    threshold: float = 0.2
    mode: str = "hybrid"
    rerank_model: str = "Qwen/Qwen3-30B-A3B"
    base_url: str = CLOUD_BASE_URL
    strategy: str = "structural"
    data_dir: Path = DATA_DIR
    embed_api_key: str | None = None
    embed_model: str = "openai/text-embedding-3-small"
    embed_base_url: str = CLOUD_BASE_URL
    embed_prefix: str = ""
    _last_timings: dict = field(default_factory=dict, repr=False)

    def run(self, query: str) -> list[dict]:
        """Полный пайплайн: search → filter → rerank → slice."""
        timings: dict = {}
        chunks = self._search(query, timings)

        stats = {
            "before_filter": len(chunks),
            "after_filter": 0,
            "after_rerank": 0,
            "final": 0,
            "threshold_cut": 0,
            "rerank_cut": 0,
        }

        t_rerank0 = time.monotonic()

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
                try:
                    chunks = llm_rerank(query, chunks, self.api_key, self.rerank_model, self.base_url)
                except Exception as e:
                    print(f"[RAGPIPELINE] llm_rerank failed, using threshold-filtered chunks: {e}")

        timings["rerank_s"] = time.monotonic() - t_rerank0

        stats["after_filter"] = len(chunks)

        if len(chunks) > self.top_k_after:
            stats["rerank_cut"] = len(chunks) - self.top_k_after
            chunks = chunks[:self.top_k_after]

        stats["after_rerank"] = len(chunks)
        stats["final"] = len(chunks)

        self._last_stats = stats
        self._last_query = query
        self._last_chunks = chunks
        self._last_timings = timings
        return chunks

    def _search(self, query: str, timings: dict | None = None) -> list[dict]:
        """Базовый FAISS-поиск с эмбеддингом запроса по конфигу провайдера."""
        return search(
            query,
            top_k=self.top_k_before,
            strategy=self.strategy,
            data_dir=self.data_dir,
            embed_api_key=self.embed_api_key,
            embed_model=self.embed_model,
            embed_base_url=self.embed_base_url,
            embed_prefix=self.embed_prefix,
            timings=timings,
        )

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
