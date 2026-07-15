"""Индексация FAQ выдуманного продукта TaskFlow в отдельный FAISS-индекс.

Переиспользует пайплайн ragger: chunk_structural → get_embeddings (Cloud.ru)
→ build_index. Результат — mcp_servers/support_mcp/data/index/structural/,
основной индекс ragger не затрагивается.

Запуск (из корня проекта):
  python3 mcp_servers/support_mcp/build_index.py
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
_ragger_dir = _project_root / "ragger"
for p in (str(_project_root), str(_ragger_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

from chunking import chunk_structural  # noqa: E402
from document_loader import Document  # noqa: E402
from embedder import get_embeddings  # noqa: E402
from indexer import build_index  # noqa: E402

FAQ_DIR = Path(__file__).resolve().parent / "faq"
INDEX_DIR = Path(__file__).resolve().parent / "data" / "index"


def load_faq_documents() -> list[Document]:
    """Читает markdown-файлы FAQ, сохраняя заголовки для structural-чанкинга."""
    docs: list[Document] = []
    for path in sorted(FAQ_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = path.stem
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        docs.append(
            Document(
                source=str(path.relative_to(_project_root)),
                title=title,
                text=text,
            )
        )
    return docs


def main():
    """Полный цикл: FAQ-документы → structural-чанки → эмбеддинги Cloud.ru → FAISS."""
    print("[1/3] Загрузка FAQ TaskFlow...")
    docs = load_faq_documents()
    if not docs:
        print(f"  Нет .md файлов в {FAQ_DIR}")
        sys.exit(1)
    print(f"  {len(docs)} документов")

    print("[2/3] Чанкинг structural...")
    chunks = chunk_structural(docs)
    print(f"  {len(chunks)} чанков")

    print("[3/3] Эмбеддинги (Cloud.ru) и FAISS...")
    embeddings = get_embeddings([c.text for c in chunks])
    build_index(embeddings, chunks, str(INDEX_DIR / "structural"), "structural")

    print(f"\nГотово: {INDEX_DIR / 'structural'}")


if __name__ == "__main__":
    main()
