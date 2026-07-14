"""Оркестрация ответа ассистента: RAG по документации + git-контекст → LLM."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
    "честно скажи об этом, не выдумывай. Отвечай кратко и по делу, на русском."
)


@dataclass
class Answer:
    """Ответ ассистента вместе с источниками, использованными из RAG."""

    text: str
    sources: list[str]


async def _gather_git_context(repo_path: str) -> str:
    """Собирает git-контекст через MCP: ветка и последний коммит."""
    async with McpManager() as manager:
        branch = await manager.call("git_current_branch", {"repo_path": repo_path})
        head = await manager.call("git_head", {"repo_path": repo_path, "ref": "HEAD"})
    return f"Текущая ветка: {branch}\nПоследний коммит: {head}"


def _format_docs(hits: list[Hit]) -> str:
    """Формирует блок документации с указанием источников для промпта."""
    blocks = []
    for i, hit in enumerate(hits, 1):
        loc = hit.chunk.source
        if hit.chunk.heading:
            loc += f" ({hit.chunk.heading})"
        blocks.append(f"[{i}] {loc}\n{hit.chunk.text}")
    return "\n\n".join(blocks)


def ask(root: Path, config: Config, question: str) -> Answer:
    """Отвечает на вопрос о проекте, используя RAG-индекс и git-контекст."""
    hits = index.query(root, config, question)
    docs_block = _format_docs(hits) if hits else "(документация не найдена)"
    git_block = asyncio.run(_gather_git_context(str(root)))

    user_content = (
        f"Вопрос о проекте:\n{question}\n\n"
        f"=== Git-контекст ===\n{git_block}\n\n"
        f"=== Фрагменты документации ===\n{docs_block}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    text = llm.chat(messages, config)
    sources = list(dict.fromkeys(h.chunk.source for h in hits))
    return Answer(text=text, sources=sources)
