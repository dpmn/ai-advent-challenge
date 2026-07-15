"""
MCP-сервер Support — ассистент поддержки пользователей продукта TaskFlow.

Источники данных:
  - data/users.json, data/tickets.json — «CRM»: пользователи и тикеты
  - data/index/ — FAISS-индекс FAQ/документации TaskFlow (строится build_index.py)

Предоставляет:
  - get_user          — профиль пользователя по email
  - list_user_tickets — тикеты пользователя
  - get_ticket        — тикет с историей сообщений
  - search_faq        — RAG-поиск по FAQ и документации TaskFlow

Запуск:
  python3 mcp_servers/support_mcp/server.py --port 8770  # streamable-http (для Jarvis)
  python3 mcp_servers/support_mcp/server.py              # stdio
"""

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from mcp.server.fastmcp import FastMCP
from ragger.search import search

_DATA_DIR = Path(__file__).resolve().parent / "data"
_INDEX_DIR = _DATA_DIR / "index"

mcp = FastMCP("support")


def _load_json(name: str) -> list[dict]:
    """Читает JSON-файл из data/ (users.json / tickets.json)."""
    with open(_DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _format_ticket(t: dict, with_messages: bool = True) -> str:
    """Форматирует тикет в читаемый текст; with_messages=False — без истории."""
    lines = [
        f"Ticket {t['ticket_id']}: {t['subject']}",
        f"  Статус: {t['status']}, приоритет: {t['priority']}, создан: {t['created']}",
    ]
    if with_messages:
        for m in t["messages"]:
            lines.append(f"  [{m['at']}] {m['from']}: {m['text']}")
    return "\n".join(lines)


@mcp.tool(
    description="Support CRM: get TaskFlow user profile by email. "
                "Call this FIRST when a user introduces themselves in a support conversation — "
                "the profile (plan, role) and their tickets are needed to answer correctly."
)
def get_user(email: str) -> str:
    """Возвращает профиль пользователя TaskFlow по email.

    Args:
        email: email пользователя (например ivanov@example.com).
    """
    for u in _load_json("users.json"):
        if u["email"].lower() == email.strip().lower():
            return (
                f"{u['user_id']}: {u['name']} <{u['email']}>\n"
                f"  Тариф: {u['plan']}, роль: {u['role']}, зарегистрирован: {u['registered']}\n"
                f"  Заметки: {u['notes']}"
            )
    return f"Пользователь с email {email} не найден."


@mcp.tool(
    description="Support CRM: list all tickets of a TaskFlow user by user_id (e.g. U-001). "
                "Call after get_user to see what the user has already reported — "
                "their question is usually about an open ticket."
)
def list_user_tickets(user_id: str) -> str:
    """Возвращает список тикетов пользователя (без историй сообщений).

    Args:
        user_id: ID пользователя (например U-001).
    """
    tickets = [t for t in _load_json("tickets.json") if t["user_id"] == user_id.strip()]
    if not tickets:
        return f"У пользователя {user_id} нет тикетов."
    return "\n\n".join(_format_ticket(t, with_messages=False) for t in tickets)


@mcp.tool(
    description="Support CRM: get full TaskFlow ticket with message history by ticket_id (e.g. TK-101). "
                "Use to understand the details of the user's problem before answering."
)
def get_ticket(ticket_id: str) -> str:
    """Возвращает тикет целиком, включая историю сообщений.

    Args:
        ticket_id: ID тикета (например TK-101).
    """
    for t in _load_json("tickets.json"):
        if t["ticket_id"] == ticket_id.strip().upper():
            return _format_ticket(t)
    return f"Тикет {ticket_id} не найден."


@mcp.tool(
    description="RAG search over TaskFlow product FAQ and documentation (auth, tokens, billing, "
                "plans, limits, integrations). ALWAYS ground your support answer in these chunks — "
                "quote the solution from the docs, don't invent product behavior."
)
def search_faq(query: str, top_k: int = 4) -> str:
    """Семантический поиск по FAQ и документации TaskFlow.

    Args:
        query: вопрос или описание проблемы на естественном языке.
        top_k: количество возвращаемых чанков (1-10).
    """
    try:
        results = search(query, top_k=min(max(top_k, 1), 10), data_dir=_INDEX_DIR)
    except FileNotFoundError:
        return "Ошибка: индекс FAQ не построен. Запусти mcp_servers/support_mcp/build_index.py."

    if not results:
        return "Нет релевантных разделов FAQ."

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] Score: {r['score']:.4f}\n"
            f"    Source: {r['source']} / {r['section']}\n"
            f"    Text: {r['text']}\n"
        )
    return "\n---\n".join(lines)


def main():
    """Точка входа: streamable-http при --port, иначе stdio."""
    parser = argparse.ArgumentParser(description="Support MCP Server (TaskFlow)")
    parser.add_argument("--port", type=int, default=0, help="Port for HTTP mode (omit for stdio)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host for HTTP mode")
    args = parser.parse_args()

    if args.port:
        print(f"Starting Support MCP server on {args.host}:{args.port}...", file=sys.stderr)
        mcp.settings.port = args.port
        mcp.settings.host = args.host
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
