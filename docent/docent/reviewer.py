"""Пайплайн AI-ревью PR: RAG по докам+коду → анализ diff → текст ревью.

Production-ready-надёжность (тема недели 7):
- retry с экспоненциальным backoff на LLM-вызов;
- fallback на запасную модель, если основная недоступна;
- усечение гигантских diff, чтобы не упереться в контекст модели.

Против ложных находок:
- жёсткий промпт (доказуемость, обязательный сценарий провала, без гипотез);
- пасс верификации — второй LLM-вызов отсеивает недоказуемое;
- память решений — файл `.docent-review-notes.md` гасит повторы по принятому.
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

# Сколько символов diff кладём в RAG-запрос (для поиска релевантного контекста).
# Лимиты объёма контекста (diff/файлы/число файлов) — в Config, см. config.py.
_QUERY_DIFF_CHARS = 2000
_RETRIES = 3
# Расширения, которые считаем кодом. На не-кодовом diff код-ревью пропускаем.
CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".cc", ".rb", ".php", ".sh", ".sql",
}

_SYSTEM_PROMPT = (
    "Ты — старший инженер, делающий ревью pull request. Тебе дают diff "
    "изменений, полные версии изменённых файлов и релевантные фрагменты "
    "документации и кода проекта. Анализируй именно изменения из diff, но "
    "опирайся на полные файлы. Отвечай на русском, кратко и по делу, без воды.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА (нарушение = плохое ревью):\n"
    "1. Репорти только то, что ДОКАЗУЕМО из показанного кода. Прежде чем писать "
    "баг — мысленно проследи выполнение по коду. Строка может использоваться "
    "вне хунка; поведение может обрабатываться выше/ниже — проверь по полному "
    "файлу, не спекулируй.\n"
    "2. Каждый пункт в «Потенциальные баги» ОБЯЗАН содержать конкретный сценарий "
    "провала: при каком входе/состоянии код даёт неверный результат. Нет "
    "сценария — не баг, не пиши.\n"
    "3. НИКАКИХ гипотез вида «если код изменят/расширят в будущем». Ревьюишь "
    "код как есть.\n"
    "4. Не репорти косметику и стиль, если это не ведёт к неверному поведению.\n"
    "5. Лучше пропустить сомнительное, чем выдумать. False positive хуже, чем "
    "пропуск.\n"
    "6. НЕ перечисляй то, что проверил и счёл нормальным. Фразы «это не баг», "
    "«поведение корректно», «намеренно», «значит, бага нет» ЗАПРЕЩЕНЫ — если "
    "находки нет, просто не пиши пункт. Пиши только подтверждённые проблемы.\n\n"
    "Структура ответа — ровно три секции в markdown:\n"
    "## Потенциальные баги\n"
    "## Архитектурные проблемы\n"
    "## Рекомендации\n\n"
    "В каждой секции — маркированный список. Если по секции замечаний нет — "
    "напиши «Замечаний нет».\n\n"
    "В САМОМ КОНЦЕ ответа добавь отдельной строкой номера фрагментов «Связанного "
    "контекста», которые реально использовал, в формате:\n"
    "SOURCES: 1, 3\n"
    "Если связанный контекст не использовался — напиши: SOURCES: none"
)

_VERIFY_PROMPT = (
    "Ты — придирчивый ревьюер-аудитор. Тебе дают код-контекст и ЧЕРНОВИК ревью. "
    "Твоя задача — вычистить черновик от недоказуемого:\n"
    "- по каждой находке проверь по коду, доказуема ли она строго из показанного;\n"
    "- УДАЛИ пункт ЦЕЛИКОМ, если он недоказуем/спекулятивен/гипотетичен «если "
    "изменят»/косметика без последствий/противоречит коду. НЕ оставляй его с "
    "пометкой «это не баг» и НЕ поясняй, почему удалил;\n"
    "- доказуемые находки оставь ДОСЛОВНО, не переписывай и не добавляй новых;\n"
    "- ЗАПРЕЩЕНЫ мета-комментарии и фразы «это не баг», «поведение корректно», "
    "«намеренно», «значит, бага нет». В финале — только подтверждённые находки;\n"
    "- сохрани формат: три секции и строку SOURCES в конце. Пустая секция — "
    "строка «Замечаний нет» и ничего больше.\n"
    "Верни только очищенное ревью, без комментариев о своей работе."
)

# Маркер использованных фрагментов связанного контекста в конце ответа.
_SOURCES_RE = re.compile(r"(?im)^[ \t]*(?:SOURCES|ИСТОЧНИКИ)[ \t]*[:：][ \t]*(.*)$")


@dataclass
class ReviewResult:
    """Результат ревью: текст, файлы-контекст из RAG и сработавшая модель."""

    text: str
    sources: list[str] = field(default_factory=list)
    model: str = ""


def _has_code(changed_files: list[str]) -> bool:
    """True, если среди изменённых файлов есть хотя бы один файл с кодом."""
    return any(Path(name).suffix.lower() in CODE_EXTS for name in changed_files)


def _changed_files_from_diff(diff: str) -> list[str]:
    """Вытаскивает список изменённых файлов из заголовков `+++ b/...` diff."""
    files: list[str] = []
    for match in re.finditer(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE):
        name = match.group(1).strip()
        if name and name != "/dev/null":
            files.append(name)
    return list(dict.fromkeys(files))


def _truncate_at_line(text: str, max_chars: int, marker: str) -> str:
    """Усекает текст по границе строки до `max_chars`, вставляя видимый маркер.

    `marker` содержит `{n}` — число пропущенных строк.
    """
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    newline = head.rfind("\n")
    if newline > 0:
        head = head[:newline]
    omitted = text[len(head):].count("\n")
    return f"{head}\n{marker.format(n=omitted)}"


def _read_changed_files(
    root: Path, changed_files: list[str], max_file_chars: int, max_files: int
) -> str:
    """Читает полное содержимое изменённых файлов из working tree.

    Каждый файл усекается до `max_file_chars` (по границе строки), всего не
    больше `max_files`. Отсутствующие (удалённые) и нечитаемые — пропускаются.
    """
    blocks: list[str] = []
    for name in changed_files[:max_files]:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _truncate_at_line(text, max_file_chars, "[... файл усечён, ещё {n} строк ...]")
        blocks.append(f"--- {name} ---\n{text}")
    return "\n\n".join(blocks)


def _format_context(hits: list[Hit]) -> str:
    """Формирует нумерованный блок RAG-контекста (доки + docstring-и кода)."""
    blocks: list[str] = []
    for i, hit in enumerate(hits, 1):
        loc = hit.chunk.source
        if hit.chunk.heading:
            loc += f" ({hit.chunk.heading})"
        blocks.append(f"[{i}] {loc}\n{hit.chunk.text}")
    return "\n\n".join(blocks)


def _extract_sources(text: str, hits: list[Hit]) -> tuple[str, list[str]]:
    """Вырезает маркер SOURCES из конца ответа, возвращает (текст, источники).

    Маркер учитывается ТОЛЬКО если он на последней непустой строке — упоминание
    «SOURCES» в теле рекомендаций не считается и не вырезается. Источники —
    файлы фрагментов связанного контекста по номерам. Нет маркера — пустой
    список, текст не тронут.
    """
    lines = text.rstrip().splitlines()
    if not lines:
        return text.rstrip(), []
    match = _SOURCES_RE.match(lines[-1])
    if not match:
        return "\n".join(lines).rstrip(), []
    sources: list[str] = []
    for num in re.findall(r"\d+", match.group(1)):
        idx = int(num) - 1
        if 0 <= idx < len(hits):
            sources.append(hits[idx].chunk.source)
    clean = "\n".join(lines[:-1]).rstrip()
    return clean, list(dict.fromkeys(sources))


def _load_review_notes(root: Path, config: Config) -> str:
    """Читает файл осознанных решений проекта, если он есть; иначе пустая строка.

    Эти решения подмешиваются в промпт, чтобы модель не репортила уже принятое.
    """
    if not config.review_notes:
        return ""
    path = root / config.review_notes
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _verify(context: str, draft: str, config: Config) -> str:
    """Аудит-пасс: вторым LLM-вызовом отсеивает недоказуемые находки черновика."""
    user_content = (
        f"=== Код-контекст ===\n{context}\n\n"
        f"=== Черновик ревью ===\n{draft}"
    )
    messages = [
        {"role": "system", "content": _VERIFY_PROMPT},
        {"role": "user", "content": user_content},
    ]
    verified, _ = _chat_with_retry(messages, config)
    return verified


def _chat_with_retry(messages: list[dict], config: Config) -> tuple[str, str]:
    """Шлёт запрос с ретраями и fallback-моделью. Возвращает (текст, модель).

    Пробуем основную модель с экспоненциальным backoff, затем — запасную из
    конфига. Пробрасываем последнюю ошибку, если все попытки исчерпаны.
    """
    last_err: Exception | None = None
    for model in (config.model, config.fallback_model):
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

    # Не гоняем LLM на чисто «не-кодовом» diff (только доки/конфиги).
    if changed_files and not _has_code(changed_files):
        return ReviewResult(text="В diff нет изменений кода — код-ревью пропущено.")

    diff_block = _truncate_at_line(diff, config.max_diff_chars, "[... усечено {n} строк diff ...]")
    files_block = _read_changed_files(
        root, changed_files, config.max_file_chars, config.max_context_files
    )
    if not files_block:
        files_block = "(содержимое изменённых файлов недоступно)"

    query = "Изменённые файлы: " + ", ".join(changed_files) + "\n" + diff[:_QUERY_DIFF_CHARS]
    hits = index.query(root, config, query)
    # Соседний контекст: RAG-хиты по другим файлам (сами изменённые уже даны
    # целиком выше — не дублируем).
    changed_set = set(changed_files)
    neighbors = [hit for hit in hits if hit.chunk.source not in changed_set]
    context = _format_context(neighbors) if neighbors else "(связанный контекст не найден)"

    files_line = ", ".join(changed_files) if changed_files else "(не определены)"
    notes = _load_review_notes(root, config)
    notes_block = (
        f"=== Осознанные решения проекта (НЕ репорти уже принятое) ===\n{notes}\n\n"
        if notes else ""
    )
    user_content = (
        f"=== Изменённые файлы ===\n{files_line}\n\n"
        f"{notes_block}"
        f"=== Diff ===\n{diff_block}\n\n"
        f"=== Полные версии изменённых файлов ===\n{files_block}\n\n"
        f"=== Связанный контекст проекта (RAG) ===\n{context}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw, model = _chat_with_retry(messages, config)
    # Пасс верификации: отсеиваем недоказуемые находки вторым вызовом.
    if config.review_verify:
        raw = _verify(user_content, raw, config)
    text, sources = _extract_sources(raw, neighbors)
    return ReviewResult(text=text, sources=sources, model=model)
