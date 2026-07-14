"""Оркестрация ответа ассистента: RAG по документации + git-контекст → LLM."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from docent import llm
from docent.config import Config
from docent.mcp.manager import McpManager
from docent.rag import index
from docent.rag.store import Hit

_SYSTEM_PROMPT = (
    "Ты — ассистент разработчика по конкретному репозиторию. Отвечай на вопросы "
    "о структуре и содержании проекта, опираясь на приведённые фрагменты "
    "документации и git-контекст. Если ответа нет в предоставленных данных — "
    "честно скажи об этом, не выдумывай. Отвечай кратко и по делу, на русском.\n\n"
    "В САМОМ КОНЦЕ ответа добавь отдельной строкой список номеров фрагментов "
    "документации, которые ты реально использовал, в формате:\n"
    "SOURCES: 1, 3\n"
    "Если фрагменты документации не использовались (ответ на основе git-контекста "
    "или данных нет) — напиши: SOURCES: none"
)

# Строка-маркер использованных источников, добавляемая моделью в конец ответа.
_SOURCES_RE = re.compile(r"(?im)^[ \t]*(?:SOURCES|ИСТОЧНИКИ)[ \t]*[:：][ \t]*(.*)$")


@dataclass
class Answer:
    """Ответ ассистента: текст, реально использованные источники и MCP-тулзы."""

    text: str
    sources: list[str] = field(default_factory=list)
    mcp_tools: list[str] = field(default_factory=list)


async def _gather_git_context(repo_path: str) -> tuple[str, list[str]]:
    """Собирает git-контекст через MCP. Возвращает (текст, список тулзов).

    В список тулзов попадают только успешно отработавшие инструменты.
    """
    used: list[str] = []
    async with McpManager() as manager:
        branch = await manager.call("git_current_branch", {"repo_path": repo_path})
        if not branch.startswith("[error]"):
            used.append("git_current_branch")
        head = await manager.call("git_head", {"repo_path": repo_path, "ref": "HEAD"})
        if not head.startswith("[error]"):
            used.append("git_head")
    text = f"Текущая ветка: {branch}\nПоследний коммит: {head}"
    return text, used


def _format_docs(hits: list[Hit]) -> str:
    """Формирует нумерованный блок документации с источниками для промпта."""
    blocks = []
    for i, hit in enumerate(hits, 1):
        loc = hit.chunk.source
        if hit.chunk.heading:
            loc += f" ({hit.chunk.heading})"
        blocks.append(f"[{i}] {loc}\n{hit.chunk.text}")
    return "\n\n".join(blocks)


def _extract_sources(text: str, hits: list[Hit]) -> tuple[str, list[str]]:
    """Вырезает маркер SOURCES из ответа, возвращает (чистый_текст, источники).

    Источники — реальные файлы фрагментов, на которые сослалась модель (по
    номерам). Если маркер отсутствует или none — список пустой.
    """
    matches = list(_SOURCES_RE.finditer(text))
    clean = _SOURCES_RE.sub("", text).rstrip()
    if not matches:
        return clean, []
    numbers = re.findall(r"\d+", matches[-1].group(1))
    sources: list[str] = []
    for num in numbers:
        idx = int(num) - 1
        if 0 <= idx < len(hits):
            sources.append(hits[idx].chunk.source)
    # Уникальные с сохранением порядка.
    return clean, list(dict.fromkeys(sources))


def ask(root: Path, config: Config, question: str) -> Answer:
    """Отвечает на вопрос о проекте, используя RAG-индекс и git-контекст."""
    hits = index.query(root, config, question)
    docs_block = _format_docs(hits) if hits else "(документация не найдена)"
    git_block, mcp_tools = asyncio.run(_gather_git_context(str(root)))

    user_content = (
        f"Вопрос о проекте:\n{question}\n\n"
        f"=== Git-контекст ===\n{git_block}\n\n"
        f"=== Фрагменты документации ===\n{docs_block}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw = llm.chat(messages, config)
    text, sources = _extract_sources(raw, hits)
    return Answer(text=text, sources=sources, mcp_tools=mcp_tools)
