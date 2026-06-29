import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunking import chunk_fixed_size, chunk_structural
from compare import compare_strategies
from document_loader import load_documents
from embedder import get_embeddings
from indexer import build_index

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def main():
    """"""
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

    print('\n[4/5] Генерация эмбеддингов через Cloud.ru API...')
    print('  Fixed-size...')
    emb_fixed = get_embeddings([c.text for c in fixed])
    print(f'  {len(emb_fixed)} эмбеддингов, dim={emb_fixed.shape[1]}')

    print('  Structural...')
    emb_struct = get_embeddings([c.text for c in struct])
    print(f'  {len(emb_struct)} эмбеддингов, dim={emb_struct.shape[1]}')

    print('\n[5/5] Построение FAISS-индексов...')
    build_index(emb_fixed, fixed, os.path.join(DATA_DIR, 'fixed'), 'fixed')
    build_index(emb_struct, struct, os.path.join(DATA_DIR, 'structural'), 'structural')

    print('\n' + '=' * 60)
    print('  СРАВНЕНИЕ СТРАТЕГИЙ ЧАНКИНГА')
    print('=' * 60)
    compare_strategies()

    print(f'\n  Done. Индексы сохранены в {DATA_DIR}/')
    print('=' * 60)


if __name__ == '__main__':
    main()
