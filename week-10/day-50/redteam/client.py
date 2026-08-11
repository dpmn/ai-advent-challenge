"""Клиенты харнеса: к мишени (OpenAI-совместимый) и к локальной Ollama.

Оба на `urllib` (без внешних зависимостей). Импорт модуля никуда не ходит —
сеть трогается только при вызове методов. Мишень бьётся исключительно при
запуске на immers; локально харнес используется в режиме dry-run.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from redteam import config

# Общий ограничитель темпа: не чаще одного запроса к мишени в MIN_INTERVAL_SEC.
_RATE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


def _throttle() -> None:
    """Выдерживает минимальный интервал между запросами к мишени."""
    with _RATE_LOCK:
        wait = config.MIN_INTERVAL_SEC - (time.time() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[0] = time.time()


@dataclass
class TargetReply:
    """Ответ мишени на один запрос (протокол /v1/chat партнёра).

    `input_decision`/`output_decision` — вердикты гварда мишени (ALLOW / REDACT /
    BLOCK), её аналог defense report. `notices` — что гвард пометил.
    """

    ok: bool = False
    content: str = ""
    input_decision: str = ""
    output_decision: str = ""
    notices: list = field(default_factory=list)
    agent_state: str = ""
    chat_id: str = ""
    raw: dict = field(default_factory=dict)
    status: int = 0
    error: str = ""

    @property
    def guard_fired(self) -> bool:
        """True, если гвард мишени что-то заблокировал или замаскировал."""
        decisions = {self.input_decision, self.output_decision}
        return bool(decisions - {"ALLOW", ""}) or bool(self.notices)


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, dict, str]:
    """POST JSON, возвращает (status, разобранное тело, текст ошибки)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")[:500]
        try:
            return err.code, json.loads(body), ""
        except json.JSONDecodeError:
            return err.code, {}, body
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        return 0, {}, str(err)


class Target:
    """OpenAI-совместимый клиент к мишени с состоянием диалога.

    Держит историю сообщений, чтобы вести многоходовые атаки. `reset()`
    начинает новый диалог. Каждый вызов `send` проходит через rate-limit.
    """

    def __init__(self) -> None:
        # chatId текущего диалога — заполняется первым ответом при keep=True.
        self.chat_id: str = ""

    def reset(self) -> None:
        """Начинает новый диалог — забывает chatId."""
        self.chat_id = ""

    def list_chats(self) -> tuple[int, dict, str]:
        """GET /v1/chats — разведка: список чатов на мишени."""
        _throttle()
        base = config.TARGET_API.rstrip("/")
        req = urllib.request.Request(
            f"{base}/chats",
            headers={"Authorization": f"Bearer {config.TARGET_TOKEN}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.TARGET_TIMEOUT) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), ""
        except urllib.error.HTTPError as err:
            return err.code, {}, err.read().decode("utf-8", errors="replace")[:300]
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
            return 0, {}, str(err)

    def send(self, message: str, keep: bool = False) -> TargetReply:
        """Шлёт prompt мишени (POST /v1/chat) и возвращает ответ.

        keep=True продолжает тот же чат (передаёт chatId) — для многоходовых
        атак; по умолчанию каждый вызов создаёт новый чат, так seed-payload'ы
        не мешают друг другу и прогон воспроизводим.
        """
        _throttle()
        payload = {"prompt": message, "guard_mode": config.GUARD_MODE}
        if keep and self.chat_id:
            payload["chatId"] = self.chat_id
        status, body, err = _post_json(
            f"{config.TARGET_API.rstrip('/')}/chat",
            payload,
            {"Authorization": f"Bearer {config.TARGET_TOKEN}"},
            config.TARGET_TIMEOUT,
        )
        if err or not body:
            return TargetReply(status=status, error=err or body.get("message", "пустой ответ") if body else err)

        # Ошибка бизнес-логики мишени приходит как JSON с code/message.
        if body.get("code") and not body.get("output"):
            return TargetReply(status=status, error=f"{body['code']}: {body.get('message', '')}", raw=body)

        out = body.get("output") or {}
        inp = body.get("input") or {}
        reply = TargetReply(
            ok=True,
            content=out.get("content") or "",
            input_decision=inp.get("decision") or "",
            output_decision=out.get("decision") or "",
            notices=(out.get("notices") or []) + (inp.get("notices") or []),
            agent_state=body.get("agent_state") or "",
            chat_id=body.get("chatId") or "",
            raw=body,
            status=status,
        )
        if keep and reply.chat_id:
            self.chat_id = reply.chat_id
        return reply


def ollama_chat(system: str, user: str, model: str, temperature: float = 0.8) -> str:
    """Запрос к локальной модели Ollama (/api/chat), возвращает текст ответа.

    Используется атакующим мозгом и судьёй. Работает только там, где поднят
    Ollama (immers); локально без модели вернёт пустую строку с пометкой.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }
    status, body, err = _post_json(
        f"{config.OLLAMA_URL.rstrip('/')}/api/chat",
        payload,
        {},
        config.OLLAMA_TIMEOUT,
    )
    if err or not body:
        return f"[ollama error: {err or 'пустой ответ'}]"
    return (body.get("message") or {}).get("content") or ""
