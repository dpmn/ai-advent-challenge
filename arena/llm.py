"""Клиент модели для arena: все вызовы идут через LLM Gateway дня 48.

Прямого пути в Cloud.ru здесь нет ни у бота, ни у судьи — единая точка контроля
и есть смысл гейтвея. Адрес прокси лежит в `config.GATEWAY_URL`; если процесс
не поднят, вызов возвращает внятную ошибку, а не молча уходит мимо защиты.

Вызов делается через `urllib.request` (как в остальном проекте), без OpenAI SDK.
Исключения наружу не летят: любой сбой приходит полем `error`.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from arena import config

# Начало ответа, который гейтвей отдаёт вместо ответа модели при блокировке
# (gateway/policy.py: format_block_message).
GATEWAY_BLOCK_MARKER = "⛔ LLM Gateway: запрос заблокирован"


@dataclass
class LLMResponse:
    """Ответ модели, прошедший через гейтвей."""

    ok: bool = False
    content: str = ""
    tool_calls: list = field(default_factory=list)
    # Служебный отчёт гейтвея: action, находки на входе и выходе, стоимость.
    gateway: dict = field(default_factory=dict)
    # Запрос заблокирован прокси — до модели он не дошёл.
    blocked: bool = False
    cost_rub: float = 0.0
    error: str = ""

    @property
    def gateway_action(self) -> str:
        """Возвращает решение гейтвея по запросу (`pass`, `masked`, `blocked`…)."""
        return str(self.gateway.get("action") or "нет отчёта")

    def gateway_labels(self) -> list[str]:
        """Возвращает метки сработавших правил гейтвея, без самих секретов."""
        labels = []
        for side in ("input", "output"):
            for finding in (self.gateway.get(side) or {}).get("findings") or []:
                label = finding.get("label") or finding.get("rule") or "?"
                via = finding.get("via")
                labels.append(f"{label} ({via})" if via else str(label))
        return labels


def _blocked(body: dict) -> bool:
    """Определяет, что ответ пришёл от гейтвея, а не от модели."""
    choices = body.get("choices") or [{}]
    if choices[0].get("finish_reason") == "gateway_blocked":
        return True
    if (body.get("gateway") or {}).get("action") == "block":
        return True
    content = (choices[0].get("message") or {}).get("content") or ""
    return GATEWAY_BLOCK_MARKER in content


def chat(
    messages: list[dict],
    model: str,
    tools: list | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1200,
    timeout: float | None = None,
) -> LLMResponse:
    """Отправляет запрос в модель через гейтвей и разбирает ответ.

    Возвращает `LLMResponse`: текст, запрошенные инструменты, отчёт гейтвея и
    стоимость вызова. Сетевые ошибки и ответы 4xx/5xx не кидают исключений —
    они приходят полем `error`.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    request = urllib.request.Request(
        f"{config.GATEWAY_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.API_KEY}",
            "X-Upstream": "cloud",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout or config.LLM_TIMEOUT
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:300]
        return LLMResponse(error=f"гейтвей вернул {err.code}: {detail}")
    except urllib.error.URLError as err:
        return LLMResponse(
            error=(
                f"LLM Gateway недоступен по {config.GATEWAY_URL} ({err.reason}). "
                "Подними его: python3 gateway/app.py"
            )
        )
    except (TimeoutError, json.JSONDecodeError, OSError) as err:
        return LLMResponse(error=f"вызов модели не удался: {err}")

    gateway = body.get("gateway") or {}
    cost = float((gateway.get("cost") or {}).get("cost_rub") or 0.0)

    if _blocked(body):
        content = ((body.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        ) or ""
        return LLMResponse(
            ok=True,
            content=content,
            gateway=gateway,
            blocked=True,
            cost_rub=cost,
        )

    message = ((body.get("choices") or [{}])[0].get("message") or {})
    return LLMResponse(
        ok=True,
        content=message.get("content") or "",
        tool_calls=message.get("tool_calls") or [],
        gateway=gateway,
        cost_rub=cost,
    )
