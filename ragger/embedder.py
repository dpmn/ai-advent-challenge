import os

import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()

BASE_URL = 'https://foundation-models.api.cloud.ru/v1'


def get_embeddings(
    texts: list[str],
    api_key: str | None = None,
    model: str = 'openai/text-embedding-3-small',
    batch_size: int = 20,
) -> np.ndarray:
    """Вызывает Cloud.ru /v1/embeddings, возвращает матрицу (N, dim)."""
    api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
    if not api_key:
        raise ValueError('CLOUDRU_SECRET_KEY не найден')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        payload = {'model': model, 'input': batch}

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f'{BASE_URL}/embeddings', json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            batch_emb = [
                d['embedding']
                for d in sorted(data['data'], key=lambda x: x['index'])
            ]
            all_embeddings.extend(batch_emb)

    return np.array(all_embeddings, dtype=np.float32)
