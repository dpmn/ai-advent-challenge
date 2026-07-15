"""Пайплайн AI-ревью PR: RAG по докам+коду → анализ diff → текст ревью.

Production-ready-надёжность (тема недели 7):
- retry с экспоненциальным backoff на LLM-вызов;
- fallback на запасную модель, если основная недоступна;
- усечение гигантских diff, чтобы не упереться в контекст модели.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from docent import llm
from docent.config import Config
from docent.rag import index
from docent.rag.store import Hit

# Верхняя граница размера diff в промпте (символы). Хвост усекаем.
MAX_DIFF_CHARS = 12000
# Сколько символов diff кладём в RAG-запрос (для поиска релевантного контекста).
_QUERY_DIFF_CHARS = 2000
_RETRIES = 3
# Запасная модель на случай отказа основной: дешёвая base-модель.
_FALLBACK_MODEL = "Qwen/Qwen3-30B-A3B"

_SYSTEM_PROMPT = (
    "Ты — старший инженер, делающий ревью pull request. Тебе дают diff "
    "изменений и релевантные фрагменты документации и кода проекта (docstring-и "
    "и сигнатуры). Проанализируй именно изменения из diff, опираясь на контекст "
    "проекта. Отвечай на русском, кратко и по делу, без воды.\n\n"
    "Структура ответа — ровно три секции в markdown:\n"
    "## Потенциальные баги\n"
    "## Архитектурные проблемы\n"
    "## Рекомендации\n\n"
    "В каждой секции — маркированный список. Если по секции замечаний нет — "
    "напиши «Замечаний нет». Не выдумывай проблемы на пустом месте; ссылайся на "
    "конкретные файлы и строки из diff, где это уместно."
)


@dataclass
class ReviewResult:
    """Результат ревью: текст, файлы-контекст из RAG и сработавшая модель."""

    text: str
    sources: list[str] = field(default_factory=list)
    model: str = ""


def _changed_files_from_diff(diff: str) -> list[str]:
    """Вытаскивает список изменённых файлов из заголовков `+++ b/...` diff."""
    files: list[str] = []
    for match in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE):
        name = match.group(1).strip()
        if name and name != "/dev/null":
            files.append(name)
    return list(dict.fromkeys(files))


def _format_context(hits: list[Hit]) -> str:
    """Формирует нумерованный блок RAG-контекста (доки + docstring-и кода)."""
    blocks: list[str] = []
    for i, hit in enumerate(hits, 1):
        loc = hit.chunk.source
        if hit.chunk.heading:
            loc += f" ({hit.chunk.heading})"
        blocks.append(f"[{i}] {loc}\n{hit.chunk.text}")
    return "\n\n".join(blocks)


def _chat_with_retry(messages: list[dict], config: Config) -> tuple[str, str]:
    """Шлёт запрос с ретраями и fallback-моделью. Возвращает (текст, модель).

    Пробуем основную модель с экспоненциальным backoff, затем — запасную.
    Пробрасываем последнюю ошибку, если все попытки исчерпаны.
    """
    last_err: Exception | None = None
    for model in (config.model, _FALLBACK_MODEL):
        for attempt in range(_RETRIES):
            try:
                return llm.chat(messages, config, model=model), model
            except llm.LLMError as err:
                last_err = err
                time.sleep(2 ** attempt)
    assert last_err is not None
    raise last_err


def review(
    root: Path,
    config: Config,
    diff: str,
    changed_files: list[str] | None = None,
) -> ReviewResult:
    """Строит ревью по diff: RAG-контекст + анализ изменений через LLM."""
    diff = diff.strip()
    if not diff:
        return ReviewResult(text="Пустой diff — нечего ревьюить.")

    if changed_files is None:
        changed_files = _changed_files_from_diff(diff)

    truncated = diff[:MAX_DIFF_CHARS]
    trunc_note = "" if len(diff) <= MAX_DIFF_CHARS else "\n\n[diff усечён до лимита]"

    query = "Изменённые файлы: " + ", ".join(changed_files) + "\n" + diff[:_QUERY_DIFF_CHARS]
    hits = index.query(root, config, query)
    context = _format_context(hits) if hits else "(релевантный контекст не найден)"

    files_line = ", ".join(changed_files) if changed_files else "(не определены)"
    user_content = (
        f"=== Изменённые файлы ===\n{files_line}\n\n"
        f"=== Diff ===\n{truncated}{trunc_note}\n\n"
        f"=== Контекст проекта (документация и код) ===\n{context}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    text, model = _chat_with_retry(messages, config)
    sources = list(dict.fromkeys(hit.chunk.source for hit in hits))
    return ReviewResult(text=text, sources=sources, model=model)
