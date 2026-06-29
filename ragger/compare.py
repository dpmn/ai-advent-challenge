from chunking import chunk_fixed_size, chunk_structural
from document_loader import load_documents


def compare_strategies():
    """"""
    docs = load_documents()
    fixed = chunk_fixed_size(docs)
    struct = chunk_structural(docs)

    def _stats(chunks, label):
        tokens = [c.token_count for c in chunks]
        print(f'\n--- {label} ---')
        print(f'  Чанков:              {len(chunks)}')
        print(f'  Средняя длина:       {sum(tokens) / len(tokens):.0f} токенов')
        print(f'  Мин / Макс:          {min(tokens)} / {max(tokens)}')
        print(f'  Всего токенов:       {sum(tokens)}')
        print(f'  Источников покрыто:  {len({c.source for c in chunks})}')

    _stats(fixed, 'Fixed-size chunking')
    _stats(struct, 'Structural chunking')

    print(f'\n{"=" * 54}')
    print(f'{"Метрика":<30} {"Fixed":<12} {"Structural":<12}')
    print(f'{"=" * 54}')
    print(f'{"Количество чанков":<30} {len(fixed):<12} {len(struct):<12}')
    if fixed and struct:
        f_avg = sum(c.token_count for c in fixed) / len(fixed)
        s_avg = sum(c.token_count for c in struct) / len(struct)
        print(f'{"Средняя длина (токенов)":<30} {f_avg:<12.0f} {s_avg:<12.0f}')
        f_min = min(c.token_count for c in fixed)
        s_min = min(c.token_count for c in struct)
        print(f'{"Мин длина (токенов)":<30} {f_min:<12} {s_min:<12}')
        f_max = max(c.token_count for c in fixed)
        s_max = max(c.token_count for c in struct)
        print(f'{"Макс длина (токенов)":<30} {f_max:<12} {s_max:<12}')
    print(f'{"=" * 54}')
