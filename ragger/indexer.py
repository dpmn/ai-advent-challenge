import json
import os

import faiss
import numpy as np

from chunking import Chunk


def build_index(
    embeddings: np.ndarray,
    chunks: list[Chunk],
    output_dir: str,
    strategy: str,
):
    """Нормализует векторы, строит FAISS IndexFlatIP, сохраняет индекс + метаданные."""
    os.makedirs(output_dir, exist_ok=True)

    faiss.normalize_L2(embeddings)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    faiss.write_index(index, os.path.join(output_dir, 'index.faiss'))

    metadata = [
        {
            'faiss_id': i,
            'chunk_id': c.chunk_id,
            'source': c.source,
            'title': c.title,
            'section': c.section,
            'text': c.text,
            'strategy': c.strategy,
            'token_count': c.token_count,
        }
        for i, c in enumerate(chunks)
    ]

    with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f'  [{strategy}] {len(chunks)} чанков, размерность {dim}')
