"""Генерация эмбеддингов: Cloud.ru API (облако) или Ollama (локально).

Модель по умолчанию: openai/text-embedding-3-small (1536-dim) через Cloud.ru.
Для локального режима: base_url Ollama (/v1) + model='nomic-embed-text' (768-dim).
nomic-embed-text требует task-префиксы: 'search_document: ' при индексации,
'search_query: ' при поиске — передаются через параметр prefix.

get_embeddings() принимает список текстов, возвращает numpy array векторов.
Используется в pipeline.py для индексации и в search.py для поискового запроса."""

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
    base_url: str = BASE_URL,
    prefix: str = '',
) -> np.ndarray:
    """Вызывает /v1/embeddings (Cloud.ru или Ollama), возвращает матрицу (N, dim).

    Args:
        texts: Список текстов для векторизации.
        api_key: API-ключ; для Ollama любой непустой (например 'ollama').
        model: ID эмбеддинг-модели.
        batch_size: Размер батча на один HTTP-запрос.
        base_url: Базовый URL OpenAI-совместимого API.
        prefix: Task-префикс, добавляемый к каждому тексту (нужен nomic-embed-text).
    """
    api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
    if not api_key:
        raise ValueError('CLOUDRU_SECRET_KEY не найден')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    if prefix:
        texts = [prefix + t for t in texts]

    all_embeddings: list[list[float]] = []

    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {'model': model, 'input': batch}

            resp = client.post(f'{base_url}/embeddings', json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            batch_emb = [
                d['embedding']
                for d in sorted(data['data'], key=lambda x: x['index'])
            ]
            all_embeddings.extend(batch_emb)

    return np.array(all_embeddings, dtype=np.float32)
