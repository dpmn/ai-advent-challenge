"""Пайплайн индексации документов: загрузка → чанкинг → эмбеддинги → FAISS.

Режимы:
  python3 ragger/pipeline.py           — облачные эмбеддинги (Cloud.ru) → ragger/data/
  python3 ragger/pipeline.py --local   — локальные эмбеддинги (Ollama, nomic-embed-text) → ragger/data_local/
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunking import chunk_fixed_size, chunk_structural
from compare import compare_strategies
from document_loader import load_documents
from embedder import get_embeddings
from indexer import build_index

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DATA_DIR_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_local')

OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1')
LOCAL_EMBED_MODEL = 'nomic-embed-text'


def main(local: bool = False):
    """Полный цикл индексации: документы → чанки → эмбеддинги → FAISS-индексы.

    Args:
        local: True — эмбеддинги через Ollama (nomic-embed-text, префикс
            'search_document: '), индексы в data_local/. False — Cloud.ru, data/.
    """
    if local:
        data_dir = DATA_DIR_LOCAL
        embed_kwargs = {
            'api_key': 'ollama',
            'model': LOCAL_EMBED_MODEL,
            'base_url': OLLAMA_BASE_URL,
            'prefix': 'search_document: ',
        }
        provider_label = f'Ollama ({LOCAL_EMBED_MODEL}, {OLLAMA_BASE_URL})'
    else:
        data_dir = DATA_DIR
        embed_kwargs = {}
        provider_label = 'Cloud.ru API'

    print('=' * 60)
    print('  RAGGER — Document Indexing Pipeline')
    print('=' * 60)

    print('\n[1/5] Загрузка документов...')
    docs = load_documents()
    total_chars = sum(len(d.text) for d in docs)
    print(f'  {len(docs)} документов, ~{total_chars} символов (~{total_chars // 4} токенов)')

    print('\n[2/5] Чанкинг fixed-size...')
    fixed = chunk_fixed_size(docs)
    print(f'  {len(fixed)} чанков')

    print('\n[3/5] Чанкинг structural...')
    struct = chunk_structural(docs)
    print(f'  {len(struct)} чанков')

    print(f'\n[4/5] Генерация эмбеддингов через {provider_label}...')
    print('  Fixed-size...')
    emb_fixed = get_embeddings([c.text for c in fixed], **embed_kwargs)
    print(f'  {len(emb_fixed)} эмбеддингов, dim={emb_fixed.shape[1]}')

    print('  Structural...')
    emb_struct = get_embeddings([c.text for c in struct], **embed_kwargs)
    print(f'  {len(emb_struct)} эмбеддингов, dim={emb_struct.shape[1]}')

    print('\n[5/5] Построение FAISS-индексов...')
    build_index(emb_fixed, fixed, os.path.join(data_dir, 'fixed'), 'fixed')
    build_index(emb_struct, struct, os.path.join(data_dir, 'structural'), 'structural')

    if not local:
        print('\n' + '=' * 60)
        print('  СРАВНЕНИЕ СТРАТЕГИЙ ЧАНКИНГА')
        print('=' * 60)
        compare_strategies()

    print(f'\n  Done. Индексы сохранены в {data_dir}/')
    print('=' * 60)


if __name__ == '__main__':
    main(local='--local' in sys.argv)
