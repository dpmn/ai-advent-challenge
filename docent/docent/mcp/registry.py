"""Реестр MCP-серверов docent.

Каждый сервер — отдельный модуль, запускаемый как stdio-подпроцесс командой
`python -m <module>`. Чтобы добавить новый MCP-сервер, достаточно написать
модуль-сервер (см. `servers/git.py`) и добавить сюда `ServerSpec`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerSpec:
    """Описание MCP-сервера: имя и python-модуль для запуска через `-m`."""

    name: str
    module: str
    enabled: bool = True


# Все известные серверы. Менеджер поднимает только enabled.
SERVERS: list[ServerSpec] = [
    ServerSpec(name="git", module="docent.mcp.servers.git"),
    ServerSpec(name="files", module="docent.mcp.servers.files"),
]


def enabled_servers() -> list[ServerSpec]:
    """Возвращает список включённых серверов."""
    return [s for s in SERVERS if s.enabled]
