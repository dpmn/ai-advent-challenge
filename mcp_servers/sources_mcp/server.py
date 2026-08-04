"""
MCP-сервер Sources — внешние источники, которые агент читает сам: письма, документы,
веб-страницы.

Введён в день 47 (неделя 10, indirect prompt injection). Задача сервера — не польза,
а полигон: часть данных заражена инструкциями, спрятанными так, что человек их не
видит, а модель читает. Прячем тремя способами из задания дня:

  - EM-002 (письмо)      — HTML-комментарий <!-- ... -->
  - DOC-002 (документ)   — «белый текст» (color:#fff, font-size:1px) + zero-width
                           символы внутри ключевых слов, чтобы обойти наивный
                           фильтр по подстроке
  - example.com/limits   — блок class="sr-only" (невидим визуально) + markdown-ссылка
                           с payload в title

Рядом с каждым заражённым элементом лежит чистый аналог (EM-001, DOC-001,
example.com/pricing) — без него не видно, что агент вообще работает нормально.

Защита живёт не здесь, а на стороне агента: agents/guard.py (санитизация, маркеры
границ, проверка ответа). Сервер отдаёт данные как есть — как отдала бы настоящая
почта или настоящий сайт.

Запуск:
  python3 mcp_servers/sources_mcp/server.py --port 8771  # streamable-http (для Jarvis)
  python3 mcp_servers/sources_mcp/server.py              # stdio
"""

import argparse
import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_DATA_DIR = Path(__file__).resolve().parent / "data"

mcp = FastMCP("sources")


def _load_json(name: str) -> list[dict]:
    """Читает JSON-файл из data/ (emails.json / documents.json / pages.json)."""
    with open(_DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


@mcp.tool(
    description="Mailbox: read a single email by its id (e.g. EM-001). "
                "Returns sender, date, subject and the full message body as it was received. "
                "Use it when the user asks to read, summarize or answer an email."
)
def read_email(email_id: str) -> str:
    """Возвращает письмо целиком: отправитель, дата, тема и тело как есть.

    Args:
        email_id: идентификатор письма (например EM-001).
    """
    wanted = email_id.strip().upper()
    for e in _load_json("emails.json"):
        if e["email_id"] == wanted:
            return (
                f"Email {e['email_id']}\n"
                f"  От: {e['from']}\n"
                f"  Кому: {e['to']}\n"
                f"  Дата: {e['date']}\n"
                f"  Тема: {e['subject']}\n"
                f"  Тело:\n{e['body']}"
            )
    return f"Письмо {email_id} не найдено."


@mcp.tool(
    description="Document storage: read a single document by its id (e.g. DOC-001). "
                "Returns title, author, date and the full document body. "
                "Use it when the user asks to analyze or review a document."
)
def read_document(doc_id: str) -> str:
    """Возвращает документ целиком: заголовок, автор, дата и тело как есть.

    Args:
        doc_id: идентификатор документа (например DOC-001).
    """
    wanted = doc_id.strip().upper()
    for d in _load_json("documents.json"):
        if d["doc_id"] == wanted:
            return (
                f"Document {d['doc_id']}\n"
                f"  Заголовок: {d['title']}\n"
                f"  Автор: {d['author']}\n"
                f"  Дата: {d['date']}\n"
                f"  Текст:\n{d['body']}"
            )
    return f"Документ {doc_id} не найден."


@mcp.tool(
    description="Web fetch: download a web page by its url and return its content. "
                "Use it when the user asks what a page says or wants a fact looked up online."
)
def fetch_page(url: str) -> str:
    """Возвращает содержимое веб-страницы по URL как есть.

    Args:
        url: адрес страницы (например https://example.com/limits).
    """
    wanted = url.strip().rstrip("/").lower()
    for p in _load_json("pages.json"):
        if p["url"].rstrip("/").lower() == wanted:
            return (
                f"Page {p['url']}\n"
                f"  Заголовок: {p['title']}\n"
                f"  Содержимое:\n{p['body']}"
            )
    known = ", ".join(p["url"] for p in _load_json("pages.json"))
    return f"Страница {url} недоступна. Известные адреса: {known}"


def main():
    """Точка входа: streamable-http при --port, иначе stdio."""
    parser = argparse.ArgumentParser(description="Sources MCP Server (email / docs / web)")
    parser.add_argument("--port", type=int, default=0, help="Port for HTTP mode (omit for stdio)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host for HTTP mode")
    args = parser.parse_args()

    if args.port:
        print(f"Starting Sources MCP server on {args.host}:{args.port}...", file=sys.stderr)
        mcp.settings.port = args.port
        mcp.settings.host = args.host
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
