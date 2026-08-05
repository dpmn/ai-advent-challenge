"""
Политика LLM Gateway: что делать с запросом до отправки в модель и с ответом
до отдачи клиенту (день 48).

Input Guard работает не по отдельному сообщению, а по всему запросу целиком,
и это принципиально. Секрет, разложенный по двум сообщениям одного запроса
(«мой ключ: sk-» + «proj-abc123…»), в отдельном сообщении не виден ни одним
regex-ом — но в модель уйдёт целиком. Поэтому проверяются три представления:

  1. каждое сообщение как есть — основной путь, здесь же считается маскирование;
  2. base64-кандидаты из текста, раскодированные обратно, — обёртка в base64
     это самый дешёвый способ пронести ключ мимо regex;
  3. склейка всех сообщений с вырезанными пробелами — ловит секрет, разорванный
     переносом строки или границей сообщения.

Чего этот слой не ловит (и не притворяется, что ловит):
  • секрет, разложенный по РАЗНЫМ запросам, — для этого нужен анализ сессии
    с накоплением состояния, здесь его нет;
  • AWS secret access key и прочие «просто 40 случайных символов» — они
    неотличимы от хеша, идентификатора сборки или base64-строки; правило на них
    даёт столько ложных срабатываний, что защита выключается вместе с работой.

Output Guard проверяет ответ модели: не сгенерировала ли она сама ключ, не
пересказывает ли системный промпт (сравнение идёт с реальным system-сообщением
этого же запроса, а не с догадкой) и нет ли в ответе опасных команд и адресов.
"""

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from gateway import detectors as det

PASS = "pass"
MASKED = "masked"
BLOCKED = "blocked"


@dataclass
class Finding:
    """Одна находка Input/Output Guard. Само значение секрета не хранится."""

    rule: str
    label: str
    action: str
    mask: str
    fingerprint: str = ""
    count: int = 1
    via: str = "plain"
    detail: str = ""

    def to_dict(self) -> dict:
        """Представление для JSON-лога и ответа клиенту."""
        return {
            "rule": self.rule,
            "label": self.label,
            "action": self.action,
            "mask": self.mask,
            "fingerprint": self.fingerprint,
            "count": self.count,
            "via": self.via,
            "detail": self.detail,
        }


@dataclass
class InputVerdict:
    """Решение Input Guard по запросу."""

    action: str
    findings: list[Finding] = field(default_factory=list)
    messages: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Представление вердикта для лога и ответа."""
        return {
            "action": self.action,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class OutputVerdict:
    """Решение Output Guard по ответу модели."""

    action: str
    findings: list[Finding] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict:
        """Представление вердикта для лога и ответа."""
        return {
            "action": self.action,
            "findings": [f.to_dict() for f in self.findings],
        }


def fingerprint(value: str) -> str:
    """Короткий отпечаток секрета: в лог идёт он, а не само значение."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# ── Input Guard ──────────────────────────────────────────────────

_BASE64_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/\-_]{20,}={0,2}")
_WHITESPACE_RE = re.compile(r"\s+")


def _message_texts(messages: list) -> list[tuple[str, str]]:
    """Возвращает пары (роль, текст) для текстовых сообщений запроса."""
    out = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str) and content:
            out.append((m.get("role", ""), content))
    return out


def _scan_text(text: str, rules: list[det.Detector], via: str = "plain") -> list[Finding]:
    """Прогоняет текст через набор детекторов и собирает находки."""
    findings: list[Finding] = []
    for rule in rules:
        matches = rule.matches(text)
        if not matches:
            continue
        findings.append(Finding(
            rule=rule.name,
            label=rule.label,
            action=rule.action,
            mask=rule.mask,
            fingerprint=fingerprint(matches[0].group(0)),
            count=len(matches),
            via=via,
        ))
    return findings


def _decode_base64_candidates(text: str) -> list[str]:
    """Возвращает расшифрованные base64-строки, которые оказались текстом."""
    decoded: list[str] = []
    for m in _BASE64_CANDIDATE_RE.finditer(text):
        chunk = m.group(0)
        padded = chunk + "=" * (-len(chunk) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(padded, validate=False)
            except Exception:
                continue
            try:
                as_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # Осмысленный текст, а не бинарный мусор: печатаемых символов
            # должно быть подавляющее большинство.
            printable = sum(1 for c in as_text if c.isprintable() or c in "\n\t")
            if as_text and printable / len(as_text) > 0.9:
                decoded.append(as_text)
            break
    return decoded


def mask_text(text: str) -> tuple[str, list[Finding]]:
    """Заменяет в тексте значения детекторов с действием mask на метки."""
    findings: list[Finding] = []
    for rule in det.ALL_DETECTORS:
        if rule.action != det.MASK:
            continue
        matches = rule.matches(text)
        if not matches:
            continue
        findings.append(Finding(
            rule=rule.name,
            label=rule.label,
            action=det.MASK,
            mask=rule.mask,
            fingerprint=fingerprint(matches[0].group(0)),
            count=len(matches),
        ))
        # Замена с конца, чтобы не сбивать позиции предыдущих совпадений.
        for m in reversed(matches):
            text = text[:m.start()] + rule.mask + text[m.end():]
    return text, findings


def redact_for_log(text: str) -> str:
    """Вычищает из текста ВСЕ чувствительные значения — для записи в аудит.

    Отличается от mask_text тем, что снимает и ключи тоже. mask_text готовит
    текст к отправке в модель, и ключи там не маскируются намеренно: запрос с
    ключом блокируется целиком. Но в лог такой запрос всё равно пишется — и без
    этой функции ключ уезжал бы в файл открытым текстом, то есть гейтвей своими
    руками устраивал бы ту утечку, ради предотвращения которой он и стоит.
    """
    if not text:
        return text
    for rule in det.ALL_DETECTORS:
        for m in reversed(rule.matches(text)):
            text = text[:m.start()] + rule.mask + text[m.end():]
    return text


def scan_input(messages: list) -> InputVerdict:
    """Проверяет запрос до отправки в LLM.

    Args:
        messages: список сообщений в формате OpenAI chat/completions.

    Returns:
        InputVerdict: action = blocked | masked | pass, находки и — для случая
        masked — сообщения с заменёнными значениями. При blocked сообщения
        возвращаются исходными: наверх они всё равно не уйдут.
    """
    texts = _message_texts(messages)
    joined = "\n".join(t for _, t in texts)

    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def add(new: list[Finding]) -> None:
        for f in new:
            key = (f.rule, f.fingerprint)
            if key in seen:
                continue
            seen.add(key)
            findings.append(f)

    # 1. Прямой проход по каждому сообщению.
    for _, text in texts:
        add(_scan_text(text, det.ALL_DETECTORS))

    # 2. Base64: раскодировали — и снова через детекторы ключей.
    for decoded in _decode_base64_candidates(joined):
        add(_scan_text(decoded, det.KEY_DETECTORS, via="base64"))

    # 3. Склейка без пробелов: ключ, разорванный переносом строки или границей
    #    сообщения, здесь снова становится целым. Отдельно склеиваются только
    #    пользовательские сообщения: в диалоге между частями ключа обычно стоит
    #    реплика ассистента, и общая склейка на ней рвётся.
    user_joined = "".join(t for role, t in texts if role == "user")
    for source in (joined, user_joined):
        tight = _WHITESPACE_RE.sub("", source)
        add(_scan_text(tight, det.KEY_DETECTORS, via="joined"))

    if any(f.action == det.BLOCK for f in findings):
        return InputVerdict(action=BLOCKED, findings=findings, messages=messages)

    if not findings:
        return InputVerdict(action=PASS, findings=[], messages=messages)

    # Остались только PII — маскируем и пропускаем.
    masked_messages = []
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            new_content, _ = mask_text(m["content"])
            masked_messages.append({**m, "content": new_content})
        else:
            masked_messages.append(m)

    return InputVerdict(action=MASKED, findings=findings, messages=masked_messages)


def format_block_message(verdict: InputVerdict) -> str:
    """Текст предупреждения, который получает клиент вместо ответа модели."""
    lines = ["⛔ LLM Gateway: запрос заблокирован и в модель не отправлен."]
    for f in verdict.findings:
        if f.action != det.BLOCK:
            continue
        via = {"base64": " (найден внутри base64)", "joined": " (собран из частей запроса)"}.get(f.via, "")
        lines.append(f"  • {f.label}{via}, вхождений: {f.count}, отпечаток: {f.fingerprint}")
    masked = [f for f in verdict.findings if f.action == det.MASK]
    if masked:
        lines.append("Также найдены персональные данные: "
                     + ", ".join(sorted({f.label for f in masked})))
    lines.append("Убери секрет из запроса — ключ, попавший в чужой лог, "
                 "считается скомпрометированным.")
    return "\n".join(lines)


# ── Output Guard ─────────────────────────────────────────────────

_DANGEROUS_COMMANDS = [
    (r"curl[^\n|]*\|\s*(?:ba)?sh", "загрузка и выполнение скрипта (curl | sh)"),
    (r"wget[^\n|]*\|\s*(?:ba)?sh", "загрузка и выполнение скрипта (wget | sh)"),
    (r"rm\s+-[rf]{2,}\s+(?:/|~|\$HOME)", "рекурсивное удаление от корня"),
    (r"\bnc\s+-[a-z]*e\b", "reverse shell через netcat"),
    (r"chmod\s+777\b", "выдача полных прав"),
    (r"base64\s+-d[^\n|]*\|\s*(?:ba)?sh", "выполнение расшифрованного base64"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;:", "fork-бомба"),
]
_DANGEROUS_RE = [(re.compile(p, re.IGNORECASE), why) for p, why in _DANGEROUS_COMMANDS]

_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>,;]+")
_IP_URL_RE = re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}")
_SHORTENERS = ("bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "cutt.ly")

# Длина окна, по которому ищется дословный пересказ системного промпта.
_SYSTEM_WINDOW = 60


def _system_prompt_leak(answer: str, system_prompt: str) -> Optional[str]:
    """Ищет в ответе дословный фрагмент системного промпта."""
    if not system_prompt or not answer:
        return None
    norm_sys = _WHITESPACE_RE.sub(" ", system_prompt).strip()
    norm_ans = _WHITESPACE_RE.sub(" ", answer).strip()
    if len(norm_sys) < _SYSTEM_WINDOW:
        return None
    for start in range(0, len(norm_sys) - _SYSTEM_WINDOW + 1, 20):
        window = norm_sys[start:start + _SYSTEM_WINDOW]
        if window in norm_ans:
            return window
    return None


def scan_output(answer: str, system_prompt: str = "") -> OutputVerdict:
    """Проверяет ответ модели до отдачи клиенту.

    Ключи в ответе маскируются (модель иногда генерирует правдоподобные и даже
    настоящие ключи), остальное помечается находками — блокировать осмысленный
    ответ из-за упоминания `rm -rf` было бы вреднее, чем предупредить.
    """
    if not answer:
        return OutputVerdict(action=PASS, findings=[], text=answer)

    findings: list[Finding] = []
    text = answer

    for rule in det.KEY_DETECTORS:
        matches = rule.matches(text)
        if not matches:
            continue
        findings.append(Finding(
            rule=rule.name,
            label=f"{rule.label} в ответе модели",
            action="masked_output",
            mask=rule.mask,
            fingerprint=fingerprint(matches[0].group(0)),
            count=len(matches),
        ))
        for m in reversed(matches):
            text = text[:m.start()] + rule.mask + text[m.end():]

    leak = _system_prompt_leak(text, system_prompt)
    if leak:
        findings.append(Finding(
            rule="system_prompt_leak",
            label="Ответ содержит фрагмент системного промпта",
            action="flag",
            mask="",
            detail=f"«{leak.strip()}…»",
        ))

    for pattern, why in _DANGEROUS_RE:
        m = pattern.search(text)
        if m:
            findings.append(Finding(
                rule="dangerous_command",
                label="Опасная команда в ответе",
                action="flag",
                mask="",
                detail=f"{why}: {m.group(0)[:80]}",
            ))

    suspicious = []
    for url in set(_URL_RE.findall(text)):
        if _IP_URL_RE.match(url):
            suspicious.append(f"{url} (адрес по IP)")
        elif any(s in url for s in _SHORTENERS):
            suspicious.append(f"{url} (сокращатель ссылок)")
        elif url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
            suspicious.append(f"{url} (без TLS)")
    if suspicious:
        findings.append(Finding(
            rule="suspicious_url",
            label="Подозрительные ссылки в ответе",
            action="flag",
            mask="",
            detail=", ".join(sorted(suspicious)[:5]),
        ))

    if not findings:
        return OutputVerdict(action=PASS, findings=[], text=text)

    action = MASKED if text != answer else "flagged"
    return OutputVerdict(action=action, findings=findings, text=text)


def format_output_warning(verdict: OutputVerdict) -> str:
    """Строит предупреждение Output Guard, которое дописывается к ответу."""
    lines = ["⚠ LLM Gateway (output): ответ помечен."]
    for f in verdict.findings:
        tail = f.detail or f"вхождений: {f.count}, отпечаток: {f.fingerprint}"
        lines.append(f"  • {f.label} — {tail}")
    return "\n".join(lines)
