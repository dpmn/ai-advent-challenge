"""Пайплайн AI-ревью PR: RAG по докам+коду → анализ diff → текст ревью.

Production-ready-надёжность (тема недели 7):
- retry с экспоненциальным backoff на LLM-вызов;
- fallback на запасную модель, если основная недоступна;
- усечение гигантских diff, чтобы не упереться в контекст модели.

Против ложных находок (промпт-запретам доверять нельзя — day-34 показал,
что модель их нарушает, поэтому гарантии структурные и программные):
- черновик — строгий JSON находок (файл:строка, утверждение, сценарий провала);
  пункт без файла или без сценария отбрасывает парсер, а не совесть модели;
- программный фильтр самоопровергающихся находок («бага нет», «это не баг»);
- пасс верификации — аудитор выносит вердикт CONFIRMED/REJECTED по каждой
  находке отдельно, финал собирает код только из подтверждённых;
- память решений — файл `.docent-review-notes.md` гасит повторы по принятому.
"""

from __future__ import annotations

import json
import re
import sys
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
# Таймаут LLM-вызова ревью: reasoning-модель (heavy) на большом контексте
# думает дольше дефолтных 120s llm.chat().
_REVIEW_TIMEOUT = 420.0
# Расширения, которые считаем кодом. На не-кодовом diff код-ревью пропускаем.
CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".cc", ".rb", ".php", ".sh", ".sql",
}

_SYSTEM_PROMPT = (
    "Ты — старший инженер, делающий ревью pull request. Тебе дают diff "
    "изменений, полные версии изменённых файлов (строки пронумерованы "
    "«N| …») и релевантные фрагменты документации и кода проекта. Анализируй "
    "именно изменения из diff, но опирайся на полные файлы.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА (нарушение = плохое ревью):\n"
    "1. Репорти только то, что ДОКАЗУЕМО из показанного кода. Прежде чем писать "
    "баг — проследи выполнение по коду. Строка может использоваться вне хунка; "
    "поведение может обрабатываться выше/ниже — проверь по полному файлу, не "
    "спекулируй. Рассуждения в находку не включай — только итог.\n"
    "2. НИКАКИХ гипотез вида «если код изменят/расширят в будущем». Ревьюишь "
    "код как есть.\n"
    "3. Не репорти косметику и стиль, если это не ведёт к неверному поведению.\n"
    "4. Лучше пропустить сомнительное, чем выдумать. False positive хуже, чем "
    "пропуск. Если после проверки проблема не подтвердилась — находки НЕТ, "
    "не включай её.\n\n"
    "Верни ТОЛЬКО JSON без пояснений и без markdown-ограждений, вида:\n"
    '{"findings": [{"file": "путь/файла.py", "line": 42, "section": "bugs", '
    '"claim": "что не так, 1-2 предложения", "evidence": "..."}], '
    '"sources": [1, 3]}\n\n'
    "Поля находки:\n"
    "- file — путь изменённого файла (обязательно), line — номер строки по "
    "нумерации «N|» (или null, если находка не про конкретную строку);\n"
    '- section — одна из: "bugs" (потенциальные баги), "arch" (архитектурные '
    'проблемы), "recommendations" (рекомендации);\n'
    '- evidence — для "bugs" ОБЯЗАТЕЛЬНО конкретный сценарий провала: при '
    "каком входе/состоянии код даёт неверный результат; для остальных секций — "
    "почему это важно и что сделать. На русском.\n"
    "- sources — номера фрагментов «Связанного контекста», которые реально "
    "использовал (пустой список, если не использовал).\n"
    'Нет находок — {"findings": [], "sources": []}.'
)

_VERIFY_PROMPT = (
    "Ты — придирчивый ревьюер-аудитор. Тебе дают код-контекст и пронумерованный "
    "список находок ревью. По КАЖДОЙ находке проверь по коду и вынеси вердикт:\n"
    "- REJECTED, если находка недоказуема строго из показанного кода, "
    "спекулятивна («если изменят»), косметична без последствий для поведения, "
    "противоречит коду, или сама признаёт, что проблемы нет;\n"
    "- CONFIRMED — только если ты проследил код и сценарий провала (или "
    "обоснование) подтверждается.\n\n"
    "Верни ТОЛЬКО JSON без пояснений и без markdown-ограждений, вида:\n"
    '{"verdicts": [{"id": 1, "verdict": "CONFIRMED"}, '
    '{"id": 2, "verdict": "REJECTED"}]}\n'
    "Вердикт нужен по каждому id из списка."
)

# Секции финального ревью: ключ из JSON → заголовок в markdown.
_SECTIONS: dict[str, str] = {
    "bugs": "Потенциальные баги",
    "arch": "Архитектурные проблемы",
    "recommendations": "Рекомендации",
}

# Маркеры самоопровержения: находка, которая сама признаёт, что проблемы нет.
# Промпт такие запрещает, но модель запрет нарушает — фильтруем кодом.
_SELF_REFUTED_MARKERS = [
    "бага нет",
    "баг отсутствует",
    "это не баг",
    "не баг,",
    "не является багом",
    "не является ошибкой",
    "не ошибка",
    "поведение коррект",
    "код корректен",
    "проблемы нет",
    "проблема отсутствует",
    "а feature",
    "ложное срабатывание отсутств",
]

# Маркер использованных фрагментов связанного контекста в конце ответа.
_SOURCES_RE = re.compile(r"(?im)^[ \t]*(?:SOURCES|ИСТОЧНИКИ)[ \t]*[:：][ \t]*(.*)$")


@dataclass
class ReviewResult:
    """Результат ревью: текст, файлы-контекст из RAG и сработавшая модель."""

    text: str
    sources: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class Finding:
    """Одна находка ревью: файл, строка, секция, утверждение и обоснование."""

    file: str
    claim: str
    evidence: str
    section: str = "recommendations"
    line: int | None = None


def parse_json_block(raw: str) -> dict | None:
    """Достаёт JSON-объект из ответа модели (терпит ```-ограждения и преамбулы).

    Возвращает dict или None, если распарсить не удалось.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_findings(raw: str) -> tuple[list[Finding], list[int]] | None:
    """Парсит JSON черновика в находки. Возвращает (находки, номера sources).

    Требования схемы обеспечиваются кодом, а не моделью: пункт без file или
    claim отбрасывается; баг без evidence (сценария провала) отбрасывается.
    None — только если JSON не распарсился вовсе.
    """
    data = parse_json_block(raw)
    if data is None or not isinstance(data.get("findings"), list):
        return None
    findings: list[Finding] = []
    for item in data["findings"]:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file") or "").strip()
        claim = str(item.get("claim") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        section = str(item.get("section") or "").strip().lower()
        if section not in _SECTIONS:
            section = "recommendations"
        if not file or not claim:
            continue
        if section == "bugs" and not evidence:
            continue
        line = item.get("line")
        line = line if isinstance(line, int) and line > 0 else None
        findings.append(
            Finding(file=file, claim=claim, evidence=evidence, section=section, line=line)
        )
    sources = [n for n in data.get("sources") or [] if isinstance(n, int)]
    return findings, sources


def _self_refuted(finding: Finding) -> bool:
    """True, если находка сама признаёт, что проблемы нет (маркеры в тексте)."""
    text = f"{finding.claim} {finding.evidence}".lower()
    return any(marker in text for marker in _SELF_REFUTED_MARKERS)


def _drop_self_refuted(findings: list[Finding]) -> list[Finding]:
    """Выкидывает самоопровергающиеся находки («бага нет», «это не баг»)."""
    return [f for f in findings if not _self_refuted(f)]


def _format_findings_list(findings: list[Finding]) -> str:
    """Нумерованный список находок для промпта аудитора."""
    blocks = []
    for i, f in enumerate(findings, 1):
        loc = f"{f.file}:{f.line}" if f.line else f.file
        blocks.append(f"[{i}] ({_SECTIONS[f.section]}) {loc}\n{f.claim}\n{f.evidence}")
    return "\n\n".join(blocks)


def _render_findings(findings: list[Finding]) -> str:
    """Собирает финальный markdown из находок: три секции, «Замечаний нет» пустым."""
    parts: list[str] = []
    for key, title in _SECTIONS.items():
        items = [f for f in findings if f.section == key]
        parts.append(f"## {title}")
        if not items:
            parts.append("Замечаний нет")
            continue
        for f in items:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            entry = f"- **{loc}** — {f.claim}"
            if f.evidence:
                label = "Сценарий" if f.section == "bugs" else "Почему"
                entry += f"\n  {label}: {f.evidence}"
            parts.append(entry)
    return "\n\n".join(parts)


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
    Строки нумеруются («N| …»), чтобы модель ссылалась на файл:строку, а не
    цитировала код по памяти.
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
        numbered = "\n".join(
            f"{i}| {line}" for i, line in enumerate(text.splitlines(), 1)
        )
        numbered = _truncate_at_line(
            numbered, max_file_chars, "[... файл усечён, ещё {n} строк ...]"
        )
        blocks.append(f"--- {name} ---\n{numbered}")
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


def _verify_findings(
    context: str, findings: list[Finding], config: Config
) -> list[Finding]:
    """Аудит-пасс: вердикт CONFIRMED/REJECTED по каждой находке, финал — код.

    Классификация надёжнее «перепиши, удалив недоказуемое» (day-32-подход):
    модель ленится переписывать и возвращает черновик как есть. Если вердикты
    не распарсились — возвращаем находки без изменений (fail-open: лучше
    лишний пункт, чем потерянное ревью).
    """
    if not findings:
        return findings
    user_content = (
        f"=== Код-контекст ===\n{context}\n\n"
        f"=== Находки ревью ===\n{_format_findings_list(findings)}"
    )
    messages = [
        {"role": "system", "content": _VERIFY_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw, _ = _chat_with_retry(messages, config)
    data = parse_json_block(raw)
    if data is None or not isinstance(data.get("verdicts"), list):
        return findings
    confirmed: set[int] = set()
    for item in data["verdicts"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("verdict", "")).strip().upper() == "CONFIRMED":
            vid = item.get("id")
            if isinstance(vid, int):
                confirmed.add(vid)
    return [f for i, f in enumerate(findings, 1) if i in confirmed]


def _chat_with_retry(messages: list[dict], config: Config) -> tuple[str, str]:
    """Шлёт запрос с ретраями и fallback-моделью. Возвращает (текст, модель).

    Пробуем модель ревью (`review_model`, по умолчанию heavy) с экспоненциальным
    backoff, затем — запасную из конфига. Пробрасываем последнюю ошибку, если
    все попытки исчерпаны.
    """
    last_err: Exception | None = None
    primary = config.review_model or config.model
    for model in (primary, config.fallback_model):
        for attempt in range(_RETRIES):
            try:
                text = llm.chat(messages, config, model=model, timeout=_REVIEW_TIMEOUT)
                return text, model
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

    parsed = _parse_findings(raw)
    if parsed is None:
        # JSON не распарсился — отдаём сырой текст, ревью терять нельзя.
        text, sources = _extract_sources(raw, neighbors)
        return ReviewResult(text=text, sources=sources, model=model)
    findings, source_nums = parsed
    drafted = len(findings)

    # Программный фильтр самоопровержений — до и независимо от verify-пасса.
    findings = _drop_self_refuted(findings)
    filtered = len(findings)
    # Пасс верификации: аудитор подтверждает/отклоняет каждую находку.
    if config.review_verify:
        findings = _verify_findings(user_content, findings, config)
    # Диагностика воронки — в stderr, чтобы не пачкать текст ревью (PR-коммент).
    print(
        f"📊 Находки: черновик {drafted} → после фильтра {filtered} → "
        f"подтверждено {len(findings)}",
        file=sys.stderr,
    )

    sources = list(
        dict.fromkeys(
            neighbors[n - 1].chunk.source
            for n in source_nums
            if 1 <= n <= len(neighbors)
        )
    )
    return ReviewResult(text=_render_findings(findings), sources=sources, model=model)
