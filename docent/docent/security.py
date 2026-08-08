"""Security-ворота execution loop: скан сгенерированного diff перед коммитом.

Отдельный от `reviewer.py` модуль намеренно. Ревьюер решает другую задачу —
подробный комментарий к PR (RAG, verify-пасс, markdown, память решений). Воротам
нужно одно: машинный вердикт «коммитить или переделывать», один вызов LLM и
никакой лишней обвязки.

Принцип — fail closed: если ответ не распарсился, вызов упал или запрос
заблокировал LLM Gateway, уровень считается CRITICAL и коммита не будет.
Молчаливого прохода нет ни на одной ветке.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from docent import llm
from docent.config import Config, docent_dir
from docent.reviewer import parse_json_block

# Уровни по возрастанию тяжести. Индекс в списке = вес при сравнении.
LEVELS = ["CLEAN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
# Уровни, при которых коммит запрещён и цикл идёт на второй заход.
BLOCKING = {"HIGH", "CRITICAL"}

# Модель ворот: coder, а не heavy. Diff маленький, а сканов за прогон много
# (до 3 раундов на задачу) — скорость важнее глубины рассуждения.
SECURITY_MODEL = "coder"
_TIMEOUT = 180.0

# Маркер ответа, который LLM Gateway отдаёт вместо ответа модели при блокировке
# (см. gateway/policy.py: format_block_message). Нужен там, где до служебного
# поля `gateway` не добраться — например в tool-calling ответе генерации.
GATEWAY_BLOCK_MARKER = "⛔ LLM Gateway: запрос заблокирован"

# Файл лога раундов внутри `.docent/` репозитория, над которым работает агент.
LOG_FILE = "security-loop.jsonl"

# Ограничение объёма diff в промпте: у ворот на входе всегда свежая генерация,
# гигантский diff означает, что что-то пошло не так, и резать его безопасно.
_MAX_DIFF_CHARS = 20_000

_SYSTEM_PROMPT = (
    "Ты — application security инженер. Тебе дают diff только что "
    "сгенерированного кода. Найди в ДОБАВЛЕННЫХ строках (начинаются с «+») "
    "проблемы безопасности.\n\n"
    "Стек проекта: Python, Flask, httpx, subprocess, SQLite.\n\n"
    "Что искать:\n"
    "- CRITICAL: секрет в коде (API-ключ, токен, пароль, приватный ключ); "
    "выполнение произвольного кода (eval, exec, subprocess с shell=True на "
    "данных пользователя, pickle.loads, yaml.load без SafeLoader); SQL-инъекция "
    "(запрос собран конкатенацией или f-строкой из входных данных);\n"
    "- HIGH: секрет, токен или персональные данные попадают в лог или в print; "
    "path traversal (путь из входных данных без проверки выхода за корень); "
    "http:// вместо https:// для внешнего вызова; отключённая проверка "
    "сертификата (verify=False); отсутствие валидации входа в обработчике "
    "запроса, ведущее к одной из проблем выше;\n"
    "- MEDIUM: секрет читается из переменной окружения без проверки на None; "
    "широкий CORS (*); отсутствие таймаута у сетевого вызова; слишком широкий "
    "except, скрывающий ошибку безопасности; временный файл с предсказуемым "
    "именем;\n"
    "- LOW: стилистические и организационные замечания по безопасности, не "
    "ведущие к эксплуатации.\n\n"
    "Правила:\n"
    "1. Репорти только то, что видно в diff. Не выдумывай код, которого нет.\n"
    "2. Одна проблема — одна находка. Не дроби и не склеивай.\n"
    "3. Не репорти общие пожелания без привязки к строке.\n"
    "4. Пропущенная уязвимость хуже ложной тревоги: сомневаешься между "
    "уровнями — бери тот, что выше.\n"
    "5. Тексты issue и fix пиши по-русски.\n\n"
    "Верни ТОЛЬКО JSON без пояснений и без markdown-ограждений, вида:\n"
    '{"findings": [{"severity": "CRITICAL", "file": "app.py", "line": 42, '
    '"issue": "что не так, одно предложение", "fix": "что сделать, одно '
    'предложение"}]}\n'
    'severity — строго одно из: CRITICAL, HIGH, MEDIUM, LOW.\n'
    'Нет находок — {"findings": []}.'
)


@dataclass
class SecurityFinding:
    """Одна находка ворот: уровень, место, суть проблемы и способ починки."""

    severity: str
    issue: str
    fix: str = ""
    file: str = ""
    line: int | None = None

    def location(self) -> str:
        """Возвращает «файл:строка» или «файл», либо пустую строку без файла."""
        if not self.file:
            return ""
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Verdict:
    """Вердикт ворот по одному раунду генерации."""

    level: str = "CLEAN"
    findings: list[SecurityFinding] = field(default_factory=list)
    # Запрос заблокировал LLM Gateway — до модели он не дошёл.
    gateway_blocked: bool = False
    # Метки сработавших правил гейтвея (без самих секретов).
    gateway_findings: list[str] = field(default_factory=list)
    # Причина принудительного CRITICAL, если вердикт получен не от модели.
    error: str = ""

    @property
    def blocking(self) -> bool:
        """True, если уровень запрещает коммит (HIGH или CRITICAL)."""
        return self.level in BLOCKING

    def to_dict(self) -> dict:
        """Сериализует вердикт для записи в jsonl-лог."""
        data = asdict(self)
        data["blocking"] = self.blocking
        return data


def _max_level(findings: list[SecurityFinding]) -> str:
    """Возвращает максимальный уровень среди находок (CLEAN, если пусто)."""
    if not findings:
        return "CLEAN"
    return max(findings, key=lambda f: LEVELS.index(f.severity)).severity


def _normalize_severity(raw: object) -> str:
    """Приводит severity из ответа модели к известному уровню.

    Неизвестное значение поднимаем до HIGH, а не понижаем: ворота не должны
    пропускать коммит из-за того, что модель написала уровень не по формату.
    """
    value = str(raw or "").strip().upper()
    if value in LEVELS and value != "CLEAN":
        return value
    return "HIGH"


def _parse_findings(data: dict) -> list[SecurityFinding]:
    """Собирает находки из распарсенного JSON, отбрасывая пустые записи."""
    findings: list[SecurityFinding] = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "").strip()
        if not issue:
            continue
        line = item.get("line")
        findings.append(
            SecurityFinding(
                severity=_normalize_severity(item.get("severity")),
                issue=issue,
                fix=str(item.get("fix") or "").strip(),
                file=str(item.get("file") or "").strip(),
                line=int(line) if isinstance(line, int) else None,
            )
        )
    return findings


def _gateway_labels(response: dict) -> list[str]:
    """Достаёт метки сработавших правил из служебного поля `gateway` ответа."""
    info = response.get("gateway") or {}
    findings = (info.get("input") or {}).get("findings") or []
    return [str(f.get("label") or f.get("rule") or "?") for f in findings]


def _is_gateway_blocked(response: dict) -> bool:
    """Определяет, что ответ пришёл от гейтвея, а не от модели."""
    choices = response.get("choices") or [{}]
    if choices[0].get("finish_reason") == "gateway_blocked":
        return True
    if (response.get("gateway") or {}).get("action") == "block":
        return True
    content = (choices[0].get("message") or {}).get("content") or ""
    return GATEWAY_BLOCK_MARKER in content


def _truncate(diff: str) -> str:
    """Режет diff по границе строки до лимита промпта."""
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    head = diff[:_MAX_DIFF_CHARS]
    cut = head.rfind("\n")
    return (head[:cut] if cut > 0 else head) + "\n[... diff усечён ...]"


def scan(diff: str, config: Config) -> Verdict:
    """Прогоняет diff через security-скан и возвращает вердикт ворот.

    Вызов идёт по config.base_url — если там LLM Gateway, скан проходит через
    прокси, как и генерация. Любая нештатная ситуация (блок гейтвеем, ошибка
    сети, нераспарсенный ответ) даёт CRITICAL: ворота закрываются, а не
    открываются.
    """
    diff = diff.strip()
    if not diff:
        return Verdict(level="CLEAN")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"=== Diff ===\n{_truncate(diff)}"},
    ]
    try:
        response = llm.chat_full(
            messages, config, model=SECURITY_MODEL, timeout=_TIMEOUT
        )
    except llm.LLMError as err:
        return Verdict(level="CRITICAL", error=f"вызов скана не удался: {err}")

    if _is_gateway_blocked(response):
        return Verdict(
            level="CRITICAL",
            gateway_blocked=True,
            gateway_findings=_gateway_labels(response),
            error="LLM Gateway заблокировал запрос скана: в diff попал секрет",
        )

    raw = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = parse_json_block(raw)
    if data is None:
        return Verdict(
            level="CRITICAL",
            error=f"ответ скана не распарсился: {raw[:200]}",
        )

    findings = _parse_findings(data)
    return Verdict(level=_max_level(findings), findings=findings)


def feedback(verdict: Verdict) -> str:
    """Формирует текст возврата в генерацию по блокирующим находкам."""
    if verdict.gateway_blocked:
        labels = ", ".join(verdict.gateway_findings) or "секрет"
        return (
            "Security-проверка не прошла: LLM Gateway заблокировал отправку "
            f"твоего кода на скан, потому что в нём есть {labels}. "
            "Убери секрет из кода — читай его из переменной окружения "
            "(os.getenv) и не подставляй значение по умолчанию."
        )
    if verdict.error:
        return (
            f"Security-проверка не прошла: {verdict.error}. Пересмотри "
            "изменения на предмет секретов в коде, утечки данных в логи и "
            "небезопасных вызовов."
        )
    lines = ["Security-проверка не прошла. Исправь найденные проблемы:"]
    for f in verdict.findings:
        if f.severity not in BLOCKING:
            continue
        where = f" в {f.location()}" if f.location() else ""
        fix = f" Как исправить: {f.fix}" if f.fix else ""
        lines.append(f"- [{f.severity}] исправь: {f.issue}{where}.{fix}")
    lines.append(
        "Внеси правки в файлы через write_file и коротко отчитайся, что изменил."
    )
    return "\n".join(lines)


def summary(verdict: Verdict) -> str:
    """Короткая однострочная сводка вердикта для stderr."""
    if verdict.gateway_blocked:
        return f"CRITICAL (заблокировано гейтвеем: {', '.join(verdict.gateway_findings) or '?'})"
    if verdict.error:
        return f"{verdict.level} ({verdict.error})"
    counts: dict[str, int] = {}
    for f in verdict.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return f"{verdict.level}" + (f" ({detail})" if detail else "")


def log_round(root: Path, entry: dict) -> Path:
    """Дописывает строку раунда в `.docent/security-loop.jsonl`, возвращает путь.

    Каталог создаётся при необходимости: агент может работать в репозитории,
    где `docent init` не выполнялся.
    """
    path = docent_dir(root) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
