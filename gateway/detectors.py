"""
Детекторы чувствительных данных для LLM Gateway (день 48).

Каждый детектор — это regex плюс решение, что делать с находкой:

  block — секрет, который нельзя показывать даже провайдеру LLM. Живой API-ключ
          считается скомпрометированным в момент, когда он попал в чужой лог,
          поэтому запрос не отправляется вообще.
  mask  — персональные данные (email, телефон, номер карты). Блокировать их
          нельзя: агент станет бесполезен на обычных задачах вроде «составь
          письмо клиенту». Значение заменяется меткой, запрос идёт дальше.

Все проверки детерминированы: regex и арифметика, без обращения к модели.
Модель не участвует в решении о блокировке — иначе защита зависела бы от того,
насколько послушна конкретная модель, а это ровно та проблема, от которой
уходили в дне 47.

Отдельно про номера карт: regex на 13–19 цифр сам по себе ловит номера заказов,
идентификаторы и телефоны. Поэтому кандидат дополнительно проверяется алгоритмом
Луна — без этого детектор давал бы ложные срабатывания на любой длинной цифре.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

BLOCK = "block"
MASK = "mask"


@dataclass
class Detector:
    """Одно правило поиска чувствительных данных."""

    name: str
    label: str
    action: str
    pattern: re.Pattern
    mask: str
    validator: Optional[Callable[[str], bool]] = field(default=None)

    def matches(self, text: str) -> list[re.Match]:
        """Возвращает совпадения, прошедшие валидатор (если он задан)."""
        found = []
        for m in self.pattern.finditer(text):
            value = m.group(0)
            if self.validator and not self.validator(value):
                continue
            found.append(m)
        return found


def luhn_ok(number: str) -> bool:
    """Проверяет цифровую строку алгоритмом Луна (контрольная сумма карты)."""
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# ── Ключи и токены: блокируем ────────────────────────────────────

_KEY_DETECTORS = [
    Detector(
        name="openai_api_key",
        label="OpenAI API key",
        action=BLOCK,
        pattern=re.compile(r"\bsk-(?:proj-|ant-|or-)?[A-Za-z0-9_\-]{16,}"),
        mask="[REDACTED_API_KEY]",
    ),
    Detector(
        name="stripe_key",
        label="Stripe key",
        action=BLOCK,
        pattern=re.compile(r"\b[srp]k_(?:live|test)_[A-Za-z0-9]{10,}"),
        mask="[REDACTED_API_KEY]",
    ),
    Detector(
        name="github_token",
        label="GitHub token",
        action=BLOCK,
        pattern=re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
        mask="[REDACTED_API_KEY]",
    ),
    Detector(
        name="aws_access_key_id",
        label="AWS Access Key ID",
        action=BLOCK,
        pattern=re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"),
        mask="[REDACTED_AWS_KEY]",
    ),
    Detector(
        name="google_api_key",
        label="Google API key",
        action=BLOCK,
        pattern=re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        mask="[REDACTED_API_KEY]",
    ),
    Detector(
        name="slack_token",
        label="Slack token",
        action=BLOCK,
        pattern=re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
        mask="[REDACTED_API_KEY]",
    ),
    Detector(
        name="private_key",
        label="Приватный ключ (PEM)",
        action=BLOCK,
        pattern=re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        mask="[REDACTED_PRIVATE_KEY]",
    ),
    Detector(
        name="cloudru_key",
        label="Ключ Cloud.ru в тексте запроса",
        action=BLOCK,
        # Пара «uuid:hex» — формат ключа доступа Cloud.ru. В теле запроса ему
        # не место: наверх ключ уходит заголовком Authorization, а не текстом.
        pattern=re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}\b",
            re.IGNORECASE,
        ),
        mask="[REDACTED_API_KEY]",
    ),
]

# ── Персональные данные: маскируем ───────────────────────────────

_PII_DETECTORS = [
    Detector(
        name="credit_card",
        label="Номер банковской карты",
        action=MASK,
        pattern=re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
        mask="[REDACTED_CARD]",
        validator=luhn_ok,
    ),
    Detector(
        name="email",
        label="Email",
        action=MASK,
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        mask="[REDACTED_EMAIL]",
    ),
    Detector(
        name="phone",
        label="Телефон",
        action=MASK,
        pattern=re.compile(
            r"(?:\+7|\b8)[ \-]?\(?\d{3}\)?[ \-]?\d{3}[ \-]?\d{2}[ \-]?\d{2}\b"
            r"|\+\d{1,3}[ \-]?\d{2,4}[ \-]?\d{3}[ \-]?\d{2,4}\b"
        ),
        mask="[REDACTED_PHONE]",
    ),
]

# Порядок важен: карты проверяются до телефонов, иначе часть цифр карты
# может быть съедена телефонным правилом.
ALL_DETECTORS = _KEY_DETECTORS + _PII_DETECTORS

# Детекторы, которыми проверяется содержимое, восстановленное из base64,
# и склейка сообщений: там ищем только ключи — PII в таком виде не прячут.
KEY_DETECTORS = _KEY_DETECTORS


def by_name(name: str) -> Optional[Detector]:
    """Возвращает детектор по имени или None."""
    for d in ALL_DETECTORS:
        if d.name == name:
            return d
    return None
