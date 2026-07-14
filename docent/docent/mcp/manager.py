"""Менеджер MCP-серверов: поднимает включённые серверы и агрегирует инструменты.

Каждый сервер запускается как stdio-подпроцесс (`python -m <module>`), без портов
и ручного управления жизненным циклом. Менеджер строит карту `имя_инструмента →
сессия`, поэтому инструменты из нескольких серверов доступны единообразно.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from docent.mcp.registry import ServerSpec, enabled_servers


class McpManager:
    """Асинхронный менеджер stdio-сессий ко всем включённым MCP-серверам."""

    def __init__(self, servers: list[ServerSpec] | None = None) -> None:
        self._servers = servers if servers is not None else enabled_servers()
        self._stack = AsyncExitStack()
        # tool_name -> ClientSession, владеющая этим инструментом.
        self._tools: dict[str, ClientSession] = {}
        # Служебный лог серверов уводим в devnull, чтобы не шуметь в терминале.
        self._errlog = open(os.devnull, "w")

    async def __aenter__(self) -> "McpManager":
        """Поднимает все серверы, инициализирует сессии и собирает инструменты."""
        for spec in self._servers:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", spec.module]
            )
            read, write = await self._stack.enter_async_context(
                stdio_client(params, errlog=self._errlog)
            )
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
            for tool in listed.tools:
                self._tools[tool.name] = session
        return self

    async def __aexit__(self, *exc) -> None:
        """Закрывает все сессии, подпроцессы и служебный лог."""
        await self._stack.aclose()
        self._errlog.close()

    def tool_names(self) -> list[str]:
        """Возвращает имена всех доступных инструментов."""
        return list(self._tools)

    async def call(self, name: str, arguments: dict) -> str:
        """Вызывает инструмент по имени, возвращает текстовый результат."""
        session = self._tools.get(name)
        if session is None:
            return f"[error] неизвестный инструмент: {name}"
        result = await session.call_tool(name, arguments)
        parts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(parts).strip()
