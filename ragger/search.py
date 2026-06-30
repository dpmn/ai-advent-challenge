import json
import sys
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
