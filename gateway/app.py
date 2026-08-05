"""
LLM Gateway — HTTP-прокси между клиентом и провайдером LLM (день 48).

Зачем отдельный процесс, а не ещё один модуль внутри агента: в проекте вызовы
LLM разбросаны по семи местам (`jarvis.py`, `jarvis_memory.py`, `jarvis_session.py`,
`state_machine.py`, `ragger/reranker.py` и т.д.). Проверка, встроенная в одно из
них, остальные не покрывает. Прокси даёт одну точку, через которую проходит весь
трафик: клиенту достаточно поменять base_url, его код не меняется вообще.

Маршруты:
  POST /v1/chat/completions — основной путь: input guard → апстрим → output guard
  GET  /health              — живость, версия прайса, адреса апстримов
  GET  /stats               — счётчики запросов, токенов, стоимости, срабатываний
  GET  /audit?limit=N       — хвост аудит-лога

Апстрим выбирается заголовком `X-Upstream: cloud|local` — по белому списку, а не
произвольным URL: принимать адрес апстрима от клиента значит превратить гейтвей
в открытый релей для запросов куда угодно.

Ключ API гейтвей у себя не хранит: заголовок Authorization клиента уходит наверх
как есть. Если клиент его не прислал, берётся CLOUDRU_SECRET_KEY из .env — это
нужно для проверки через curl.

Стриминг не поддерживается: ответ надо увидеть целиком, чтобы проверить его до
отдачи клиенту. Поле "stream" в теле запроса принудительно снимается.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import policy
from gateway.audit import GatewayAudit
from gateway.cost import Pricing, estimate

load_dotenv()

CLOUD_BASE_URL = os.getenv("CLOUD_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

UPSTREAMS = {
    "cloud": CLOUD_BASE_URL,
    "local": OLLAMA_BASE_URL,
}

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "5001"))
UPSTREAM_TIMEOUT = int(os.getenv("GATEWAY_TIMEOUT", "120"))

app = Flask(__name__)
audit = GatewayAudit()
pricing = Pricing()

STATS = {
    "requests": 0,
    "blocked": 0,
    "masked": 0,
    "passed": 0,
    "errors": 0,
    "output_flagged": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cost_rub": 0.0,
    "findings": {},
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
}


def _count_findings(findings: list[dict]) -> None:
    """Копит счётчик срабатываний по правилам для /stats."""
    for f in findings:
        rule = f.get("rule", "?")
        STATS["findings"][rule] = STATS["findings"].get(rule, 0) + 1


def _system_prompt(messages: list) -> str:
    """Собирает текст системных сообщений запроса — база для проверки утечки промпта."""
    parts = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def _last_user_text(messages: list) -> str:
    """Возвращает текст последнего пользовательского сообщения."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
    return ""


def _blocked_response(model: str, verdict: policy.InputVerdict, request_id: str) -> dict:
    """Строит ответ в формате OpenAI для заблокированного запроса.

    Формат намеренно обычный: любой OpenAI-совместимый клиент покажет текст
    предупреждения как сообщение ассистента и не сломается. Что ответ пришёл от
    гейтвея, а не от модели, видно по finish_reason, полю gateway и заголовку
    X-Gateway-Action.
    """
    return {
        "id": f"gateway-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": policy.format_block_message(verdict),
            },
            "finish_reason": "gateway_blocked",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "gateway": {
            "action": policy.BLOCKED,
            "input": verdict.to_dict(),
            "output": {"action": "pass", "findings": []},
            "cost": {"cost_rub": 0.0, "price_source": "запрос не отправлен"},
            "request_id": request_id,
        },
    }


def _call_upstream(base_url: str, payload: dict, api_key: str) -> tuple[int, dict]:
    """Проксирует запрос в апстрим. Возвращает (HTTP-код, тело ответа)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"error": {"message": body or e.reason}}
        return e.code, parsed
    except urllib.error.URLError as e:
        return 502, {"error": {"message": f"upstream unreachable: {e.reason}"}}


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """Основной маршрут: проверяет запрос, проксирует его и проверяет ответ."""
    started = time.time()
    request_id = uuid.uuid4().hex[:12]
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")

    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    model = body.get("model", "")

    upstream_name = request.headers.get("X-Upstream", "cloud").lower()
    if upstream_name not in UPSTREAMS:
        upstream_name = "cloud"
    base_url = UPSTREAMS[upstream_name]

    auth = request.headers.get("Authorization", "")
    api_key = auth[7:] if auth.lower().startswith("bearer ") else os.getenv("CLOUDRU_SECRET_KEY", "")

    STATS["requests"] += 1

    # ── Input Guard ──────────────────────────────────────────────
    verdict = policy.scan_input(messages)
    _count_findings([f.to_dict() for f in verdict.findings])

    if verdict.action == policy.BLOCKED:
        STATS["blocked"] += 1
        response = _blocked_response(model, verdict, request_id)
        # В лог идёт вычищенный текст: заблокированный запрос содержит ключ,
        # и в файле аудита ему не место.
        masked_prompt = policy.redact_for_log(_last_user_text(messages))
        audit.log(
            request_id=request_id,
            client_ip=client_ip,
            model=model,
            upstream=upstream_name,
            action=policy.BLOCKED,
            input_verdict=verdict.to_dict(),
            prompt_preview=masked_prompt,
            response_preview="(в модель не отправлено)",
            duration_ms=int((time.time() - started) * 1000),
        )
        return jsonify(response), 200, {"X-Gateway-Action": policy.BLOCKED}

    if verdict.action == policy.MASKED:
        STATS["masked"] += 1
    else:
        STATS["passed"] += 1

    # ── Апстрим ──────────────────────────────────────────────────
    payload = {**body, "messages": verdict.messages}
    payload.pop("stream", None)
    status, upstream_body = _call_upstream(base_url, payload, api_key)

    if status >= 400:
        STATS["errors"] += 1
        error_text = json.dumps(upstream_body, ensure_ascii=False)[:500]
        audit.log(
            request_id=request_id,
            client_ip=client_ip,
            model=model,
            upstream=upstream_name,
            action="upstream_error",
            input_verdict=verdict.to_dict(),
            prompt_preview=policy.redact_for_log(_last_user_text(verdict.messages)),
            duration_ms=int((time.time() - started) * 1000),
            upstream_status=status,
            error=error_text,
        )
        upstream_body.setdefault("gateway", {})
        upstream_body["gateway"] = {
            "action": "upstream_error",
            "input": verdict.to_dict(),
            "request_id": request_id,
        }
        return jsonify(upstream_body), status

    # ── Output Guard ─────────────────────────────────────────────
    choices = upstream_body.get("choices") or [{}]
    message = choices[0].get("message") or {}
    answer = message.get("content") or ""

    out_verdict = policy.scan_output(answer, _system_prompt(messages))
    _count_findings([f.to_dict() for f in out_verdict.findings])

    final_answer = out_verdict.text
    if out_verdict.findings:
        STATS["output_flagged"] += 1
        final_answer = policy.format_output_warning(out_verdict) + "\n\n" + final_answer
        upstream_body["choices"][0]["message"]["content"] = final_answer

    usage = upstream_body.get("usage") or {}
    cost = estimate(pricing, model, usage, upstream=upstream_name)
    STATS["prompt_tokens"] += cost["prompt_tokens"]
    STATS["completion_tokens"] += cost["completion_tokens"]
    STATS["cost_rub"] = round(STATS["cost_rub"] + cost["cost_rub"], 6)

    action = verdict.action if not out_verdict.findings else f"{verdict.action}+output_{out_verdict.action}"
    upstream_body["gateway"] = {
        "action": action,
        "input": verdict.to_dict(),
        "output": out_verdict.to_dict(),
        "cost": cost,
        "request_id": request_id,
        "upstream": upstream_name,
    }

    duration_ms = int((time.time() - started) * 1000)
    audit.log(
        request_id=request_id,
        client_ip=client_ip,
        model=model,
        upstream=upstream_name,
        action=action,
        input_verdict=verdict.to_dict(),
        output_verdict=out_verdict.to_dict(),
        prompt_preview=policy.redact_for_log(_last_user_text(verdict.messages)),
        response_preview=policy.redact_for_log(final_answer),
        usage=cost,
        duration_ms=duration_ms,
        upstream_status=status,
    )

    headers = {"X-Gateway-Action": action}
    return jsonify(upstream_body), 200, headers


@app.route("/health", methods=["GET"])
def health():
    """Возвращает состояние гейтвея: апстримы и источник прайса."""
    return jsonify({
        "status": "ok",
        "upstreams": UPSTREAMS,
        "pricing": {
            "source": pricing.source,
            "fetched_at": pricing.fetched_at,
            "models": len(pricing.models),
        },
        "started_at": STATS["started_at"],
    })


@app.route("/stats", methods=["GET"])
def stats():
    """Возвращает счётчики запросов, срабатываний, токенов и стоимости."""
    return jsonify(STATS)


@app.route("/audit", methods=["GET"])
def audit_tail():
    """Возвращает последние записи аудит-лога."""
    limit = request.args.get("limit", default=20, type=int)
    return jsonify({"records": audit.tail(limit=max(1, min(limit, 200)))})


def _init_pricing() -> None:
    """Готовит прайс: сначала живые цены из API, при неудаче — кеш на диске."""
    key = os.getenv("CLOUDRU_SECRET_KEY", "")
    if pricing.refresh(CLOUD_BASE_URL, key):
        print(f"[GATEWAY] Прайс получен из API: моделей {len(pricing.models)}")
        return
    if pricing.load_cached():
        print(f"[GATEWAY] Прайс из кеша {Path(pricing.path).name}: "
              f"моделей {len(pricing.models)}, снят {pricing.fetched_at}")
        return
    print("[GATEWAY] Прайс недоступен — стоимость считаться не будет, только токены")


if __name__ == "__main__":
    _init_pricing()
    print(f"[GATEWAY] Слушаю http://{GATEWAY_HOST}:{GATEWAY_PORT}/v1")
    print(f"[GATEWAY] Апстримы: {UPSTREAMS}")
    app.run(host=GATEWAY_HOST, port=GATEWAY_PORT, debug=False)
