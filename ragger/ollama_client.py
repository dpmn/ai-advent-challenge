"""Нативный клиент Ollama (/api/chat) для локального RAG-пути.

Зачем не OpenAI-совместимый /v1: тот не принимает options (num_ctx и др.)
и не возвращает метрики инференса. Нативный /api/chat даёт per-request:
  - options: num_ctx, temperature, num_predict — без правки серверных env;
  - keep_alive: модель не выгружается между запросами (лечит холодный старт);
  - метрики: load_duration, prompt_eval_*, eval_* → tokens/sec.

Важно: num_ctx — load-time параметр. Разный num_ctx в соседних запросах
перегружает модель (десятки секунд), поэтому все этапы RAG (rerank, verify,
generate) должны использовать одно значение.
"""

import json
import urllib.request


def api_root(base_url: str) -> str:
    """Возвращает корень Ollama-сервера, срезая OpenAI-совместимый суффикс /v1."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")].rstrip("/")
    return root


def ollama_chat(
    prompt: str,
    model: str,
    base_url: str,
    options: dict | None = None,
    keep_alive: str = "30m",
    timeout: int = 300,
) -> tuple[str, dict]:
    """Один запрос к нативному /api/chat Ollama.

    Args:
        prompt: Текст user-сообщения.
        model: Имя модели Ollama (например "qwen2.5-coder:7b").
        base_url: Адрес сервера; допустим и с суффиксом /v1 (будет срезан).
        options: Ollama options (num_ctx, temperature, num_predict, ...).
        keep_alive: Сколько держать модель в памяти после запроса.
        timeout: Таймаут запроса, секунды.

    Returns:
        (content, metrics): текст ответа и метрики инференса
        (load_s, prompt_eval_count, prompt_eval_s, eval_count, eval_s,
        tok_s, total_s). Сетевые ошибки не глотает — обработка на вызывающем.
    """
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": keep_alive,
    }
    if options:
        payload["options"] = options

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{api_root(base_url)}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    content = result.get("message", {}).get("content", "")
    return content, _extract_metrics(result)


def _extract_metrics(result: dict) -> dict:
    """Достаёт метрики инференса из ответа /api/chat (наносекунды → секунды)."""
    def _s(key: str) -> float:
        return round(result.get(key, 0) / 1e9, 2)

    eval_count = result.get("eval_count", 0)
    eval_s = result.get("eval_duration", 0) / 1e9
    return {
        "load_s": _s("load_duration"),
        "prompt_eval_count": result.get("prompt_eval_count", 0),
        "prompt_eval_s": _s("prompt_eval_duration"),
        "eval_count": eval_count,
        "eval_s": round(eval_s, 2),
        "tok_s": round(eval_count / eval_s, 1) if eval_s > 0 else 0.0,
        "total_s": _s("total_duration"),
    }
