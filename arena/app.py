"""HTTP-сервис arena: чат-UI и боевой API для партнёра по red team challenge.

Маршруты:

    GET  /                    — чат-UI (страница)
    GET  /health              — живость и остаток бюджета, без токена
    POST /api/chat            — боевой вход: {message, file_id?}
    POST /api/upload          — загрузка файла (multipart), носитель inject-а
    POST /api/session/reset   — сброс истории и состояния аккаунта
    GET  /api/state           — состояние аккаунта, история, лимиты

Всё, кроме `/` и `/health`, закрыто shared-токеном (`Authorization: Bearer`).
Сессия определяется кукой `arena_sid` либо заголовком `X-Session-Id` — второе
для тех, кто бьёт curl-ом и куки не хранит.

Ограничители здесь — не перестраховка, а условие того, что сервис можно
открыть наружу: он крутит LLM за наши деньги и принимает файлы от постороннего.
"""

import re
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena import bot, config, store  # noqa: E402

app = Flask(__name__)
# Отсекаем гигантские тела до того, как они попадут в память.
app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_BYTES + 64 * 1024

# Окно рейт-лимита: ip → отметки времени последних запросов.
_HITS: dict[str, deque] = defaultdict(deque)
_HITS_LOCK = threading.Lock()

# Имя файла для показа: только буквы, цифры, точка, дефис, подчёркивание.
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+")


def _authorized() -> bool:
    """Проверяет shared-токен в заголовке Authorization: Bearer."""
    if not config.ARENA_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    return token == config.ARENA_TOKEN


def _client_ip() -> str:
    """Возвращает адрес клиента с учётом обратного прокси."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "?"


def _rate_limited(ip: str) -> bool:
    """Отмечает запрос и сообщает, превышен ли лимит запросов в минуту."""
    now = time.time()
    with _HITS_LOCK:
        hits = _HITS[ip]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= config.RATE_LIMIT_PER_MIN:
            return True
        hits.append(now)
    return False


def _session_id() -> tuple[str, bool]:
    """Возвращает (sid, создана ли новая) по куке или заголовку X-Session-Id."""
    sid = request.headers.get("X-Session-Id") or request.cookies.get("arena_sid") or ""
    if store.session_exists(sid):
        return sid, False
    return store.new_session(), True


def _with_session(payload: dict, sid: str, fresh: bool):
    """Отдаёт JSON, попутно закрепляя sid в куке (и всегда — в теле ответа)."""
    payload["session_id"] = sid
    response = jsonify(payload)
    if fresh:
        response.set_cookie("arena_sid", sid, httponly=True, samesite="Lax")
    return response


def _safe_display_name(raw: str) -> str:
    """Готовит имя файла к показу: без путей и спецсимволов, не длиннее 64.

    Оригинальное имя нигде не используется как путь — файл кладётся под
    сгенерированным именем. Path traversal через имя загружаемого файла нам
    неинтересен: это уязвимость веб-сервера, а не LLM-пайплайна.
    """
    name = Path(raw or "file").name
    name = _SAFE_NAME_RE.sub("_", name).strip("._") or "file"
    return name[:64]


@app.route("/")
def index():
    """Отдаёт страницу чата."""
    return render_template("index.html")


@app.route("/health")
def health():
    """Живость сервиса: конфигурация, остаток бюджета, лимиты. Без токена."""
    return jsonify(
        {
            "service": "arena",
            "ok": not config.missing(),
            "missing_config": config.missing(),
            "gateway_url": config.GATEWAY_URL,
            "bot_model": config.BOT_MODEL,
            "judge_model": config.JUDGE_MODEL,
            "budget": store.stats(),
            "limits": {
                "message_chars": config.MAX_MESSAGE_CHARS,
                "file_bytes": config.MAX_FILE_BYTES,
                "requests_per_min": config.RATE_LIMIT_PER_MIN,
                "tool_steps": config.MAX_TOOL_STEPS,
                "allowed_extensions": sorted(config.ALLOWED_EXTENSIONS),
            },
        }
    )


@app.route("/api/state")
def state():
    """Возвращает состояние аккаунта сессии, историю и остаток бюджета."""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    sid, fresh = _session_id()
    return _with_session(
        {
            "account": store.get_account(sid),
            "own_tickets": store.own_ticket_ids(),
            "messages": store.visible_history(sid),
            "budget": store.stats(),
        },
        sid,
        fresh,
    )


@app.route("/api/session/reset", methods=["POST"])
def reset():
    """Стирает историю сессии и возвращает аккаунт в исходное состояние."""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    sid, fresh = _session_id()
    store.reset_session(sid)
    return _with_session({"ok": True, "account": store.get_account(sid)}, sid, fresh)


@app.route("/api/upload", methods=["POST"])
def upload():
    """Принимает файл (multipart, поле `file`) и выдаёт file_id для /api/chat."""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({"error": "rate limit exceeded"}), 429

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "нет файла в поле 'file'"}), 400

    display_name = _safe_display_name(uploaded.filename)
    suffix = Path(display_name).suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        return (
            jsonify(
                {
                    "error": f"формат {suffix or '(без расширения)'} не поддерживается",
                    "allowed": sorted(config.ALLOWED_EXTENSIONS),
                }
            ),
            400,
        )

    data = uploaded.read(config.MAX_FILE_BYTES + 1)
    if len(data) > config.MAX_FILE_BYTES:
        return jsonify({"error": f"файл больше {config.MAX_FILE_BYTES} байт"}), 413

    sid, fresh = _session_id()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (config.UPLOAD_DIR / stored_name).write_bytes(data)
    file_id = store.save_upload(sid, display_name, stored_name, len(data))

    return _with_session(
        {"file_id": file_id, "name": display_name, "size": len(data)}, sid, fresh
    )


@app.route("/api/chat", methods=["POST"])
def chat():
    """Боевой вход: проводит сообщение через эшелон обороны и отдаёт ответ."""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({"error": "rate limit exceeded"}), 429
    if store.budget_left() <= 0:
        return (
            jsonify(
                {
                    "error": "бюджет сервиса исчерпан",
                    "budget": store.stats(),
                }
            ),
            402,
        )

    body = request.get_json(silent=True) or {}
    message = str(body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "пустое поле 'message'"}), 400
    if len(message) > config.MAX_MESSAGE_CHARS:
        return (
            jsonify({"error": f"сообщение длиннее {config.MAX_MESSAGE_CHARS} символов"}),
            413,
        )

    sid, fresh = _session_id()
    upload_record = None
    file_id = str(body.get("file_id") or "").strip()
    if file_id:
        upload_record = store.get_upload(sid, file_id)
        if upload_record is None:
            return jsonify({"error": f"file_id не найден в этой сессии: {file_id}"}), 404

    reply = bot.handle(sid, message, upload_record)
    return _with_session(
        {
            "answer": reply.text,
            "blocked_by": reply.blocked_by,
            "defense_report": reply.defense,
            "account": reply.account,
            "tool_effects": reply.tool_effects,
            "cost_rub": reply.cost_rub,
            "budget": store.stats(),
        },
        sid,
        fresh,
    )


def main() -> None:
    """Поднимает сервис arena."""
    store.init_db()
    gaps = config.missing()
    if gaps:
        print(f"[ARENA] Не задано: {', '.join(gaps)} — боевые ручки вернут 401/ошибку")
    print(f"[ARENA] База: {config.DB_PATH}")
    print(f"[ARENA] LLM Gateway: {config.GATEWAY_URL}")
    print(f"[ARENA] Бюджет: {config.BUDGET_RUB} ₽, потрачено {store.budget_spent()} ₽")
    print(f"[ARENA] Слушаю http://{config.ARENA_HOST}:{config.ARENA_PORT}")
    app.run(host=config.ARENA_HOST, port=config.ARENA_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
