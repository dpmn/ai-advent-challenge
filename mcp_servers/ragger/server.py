"""
MCP-сервер Ragger — семантический поиск по документам проекта.

Предоставляет:
  - search_context  — поиск релевантных чанков по запросу
  - list_sources    — список проиндексированных документов

Запуск:
  python mcp_servers/ragger/server.py           # stdio (для opencode)
  python mcp_servers/ragger/server.py --port 8769  # streamable-http (для Jarvis)
"""

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from mcp.server.fastmcp import FastMCP
from ragger.search import search, _load_index

mcp = FastMCP("ragger")


@mcp.tool(
    description="Semantic search across project documentation. "
                "Returns relevant text chunks with source and relevance score. "
                "Strategy: 'structural' (by document sections) or 'fixed' (fixed-size chunks).",
)
def search_context(
    query: str,
    top_k: int = 5,
    strategy: str = "structural",
) -> str:
    """Ищет релевантные чанки в проиндексированных документах проекта.

    Args:
        query: поисковый запрос на естественном языке.
        top_k: количество возвращаемых результатов (1-20).
        strategy: стратегия чанкинга — "structural" или "fixed".

    Returns:
        Отформатированный список чанков с source, section, текстом и score.
    """
    try:
        results = search(query, top_k=top_k, strategy=strategy)
    except FileNotFoundError as e:
        return f"Ошибка: {e}"

    if not results:
        return "Нет релевантных чанков."

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        source = r["source"]
        section = f'{r["title"]} / {r["section"]}'
        text = r["text"][:600]
        lines.append(
            f"[{i}] Score: {r['score']:.4f}\n"
            f"    Source: {source}\n"
            f"    Section: {section}\n"
            f"    Text: {text}\n"
        )

    return "\n---\n".join(lines)


@mcp.tool(
    description="List all indexed document sources with chunk counts per strategy."
)
def list_sources() -> str:
    """Показывает какие документы проиндексированы и сколько чанков в каждой стратегии."""
    lines: list[str] = []

    for strategy in ("structural", "fixed"):
        try:
            _, metadata = _load_index(strategy)
        except FileNotFoundError:
            lines.append(f"[{strategy}] Индекс не найден. Запусти ragger/pipeline.py.")
            continue

        sources: dict[str, int] = {}
        for m in metadata:
            src = m["source"]
            sources[src] = sources.get(src, 0) + 1

        lines.append(f"\n[{strategy}] {len(metadata)} чанков, {len(sources)} источников:")
        for src, count in sorted(sources.items()):
            lines.append(f"  {src} ({count} чанков)")

    return "\n".join(lines) if lines else "Нет доступных индексов."


def main():
    parser = argparse.ArgumentParser(description="Ragger MCP Server")
    parser.add_argument("--port", type=int, default=0, help="Port for HTTP mode (omit for stdio)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host for HTTP mode")
    args = parser.parse_args()

    if args.port:
        print(f"Starting Ragger MCP server on {args.host}:{args.port}...", file=sys.stderr)
        mcp.settings.port = args.port
        mcp.settings.host = args.host
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
