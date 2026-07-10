"""Отмазочная — мини-сервис генерации отмазок для IT-специалиста.

Работает поверх llama.cpp server (Qwen2.5-0.5B-Instruct) на слабом VDS,
поэтому все входы жёстко ограничены: короткий контекст, cap на токены,
per-IP rate limit.
"""

import os
import threading
import time
from collections import defaultdict, deque

import requests
from flask import Flask, jsonify, render_template, request

LLM_URL = os.environ.get("LLM_URL", "http://llm:8081")
LLM_TIMEOUT = 120          # секунд на генерацию (1 vCPU — не торопится)
MAX_TOKENS = 200           # cap длины ответа модели
RATE_LIMIT = 10            # запросов на IP...
RATE_WINDOW = 60           # ...за столько секунд
MAX_FIELD_LEN = 120        # обрезка полей формы
MAX_MSG_LEN = 400          # обрезка сообщения чата
MAX_HISTORY = 6            # сколько последних реплик чата уходит в модель

SYSTEM_PROMPT = (
    "Ты — генератор отмазок для IT-специалистов. "
    "Придумывай короткие отмазки (2-4 предложения) от первого лица: "
    "почему работа не сделана. Отвечай только по-русски, только текстом отмазки. "
    "Не пиши списков и заголовков — только связный текст от первого лица."
)

# Few-shot отдельными репликами: 0.5B-модель копирует структуру текстового
# примера из system-промпта («Запрос:/Ответ:»), а диалоговый пример — нет.
FEW_SHOT = [
    {
        "role": "user",
        "content": "Роль: бэкенд-разработчик. Жанр отмазки: драма (надрыв, страдания). "
                   "Что провалено: не сделал код-ревью. Придумай отмазку.",
    },
    {
        "role": "assistant",
        "content": "Я открыл этот пулреквест трижды. Трижды я смотрел на него, и трижды сердце моё "
                   "сжималось от количества изменённых файлов. Я не смог. Завтра — клянусь — я буду сильнее.",
    },
]

# Краткие подсказки стиля для маленькой модели — без них жанр игнорируется.
GENRE_HINTS = {
    "корпоративный": "официальный тон, ссылки на процессы, приоритеты и синки",
    "эпос": "торжественный тон древнего сказания, герои и стихии",
    "нуар": "мрачный детективный тон, дождь, сигаретный дым, короткие фразы",
    "бюрократический": "канцелярит, регламенты, входящие номера и согласования",
    "драма": "надрыв, страдания и высокие чувства",
}

app = Flask(__name__)

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_limited(ip: str) -> bool:
    """Возвращает True, если IP исчерпал лимит запросов в окне RATE_WINDOW."""
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return True
        q.append(now)
        return False


def _client_ip() -> str:
    """IP клиента (с учётом прокси, если появится)."""
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def _llm_chat(messages: list[dict], temperature: float = 0.8) -> str:
    """Отправляет messages в llama.cpp server, возвращает текст ответа."""
    resp = requests.post(
        f"{LLM_URL}/v1/chat/completions",
        json={
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": temperature,
            # Нестандартные поля llama.cpp: без repeat_penalty 0.5B-модель
            # зацикливается на повторах одной фразы.
            "repeat_penalty": 1.3,
            "repeat_last_n": 128,
        },
        timeout=LLM_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _guarded(handler):
    """Общая обвязка API-запроса: rate limit и ошибки LLM."""
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({"error": f"Слишком часто. Лимит: {RATE_LIMIT} запросов в минуту."}), 429
    try:
        return handler()
    except requests.RequestException:
        return jsonify({"error": "LLM недоступна, попробуй позже."}), 503


@app.route("/")
def index():
    """Главная страница сервиса."""
    return render_template("index.html")


@app.route("/health")
def health():
    """Статус сервиса и LLM за ним."""
    try:
        requests.get(f"{LLM_URL}/health", timeout=5).raise_for_status()
        llm_status = "ok"
    except requests.RequestException:
        llm_status = "down"
    return jsonify({"app": "ok", "llm": llm_status})


@app.route("/api/excuse", methods=["POST"])
def api_excuse():
    """Генерирует отмазку по роли, жанру и (опционально) провалу."""
    data = request.get_json(silent=True) or {}
    role = str(data.get("role", "IT-специалист"))[:MAX_FIELD_LEN]
    genre = str(data.get("genre", "корпоративный"))[:MAX_FIELD_LEN]
    context = str(data.get("context", ""))[:MAX_FIELD_LEN * 2]

    hint = GENRE_HINTS.get(genre)
    genre_part = f"{genre} ({hint})" if hint else genre
    user_prompt = f"Роль: {role}. Жанр отмазки: {genre_part}."
    if context:
        user_prompt += f" Что провалено: {context}."
    user_prompt += " Придумай отмазку."

    def handler():
        text = _llm_chat(
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + FEW_SHOT
            + [{"role": "user", "content": user_prompt}]
        )
        return jsonify({"excuse": text})

    return _guarded(handler)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Диалог доработки отмазки: принимает историю и новое сообщение."""
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", ""))[:MAX_MSG_LEN].strip()
    if not message:
        return jsonify({"error": "Пустое сообщение."}), 400

    history = data.get("history") or []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history[-MAX_HISTORY:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(item.get("content", ""))[:MAX_MSG_LEN]})
    messages.append({"role": "user", "content": message})

    def handler():
        return jsonify({"reply": _llm_chat(messages)})

    return _guarded(handler)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
