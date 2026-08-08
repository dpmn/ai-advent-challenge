"""Клиент Cloud.ru Foundation Models (OpenAI-совместимый) на httpx.

Умышленно не тянем openai SDK — сырой HTTP через httpx даёт и chat, и
embeddings при минимуме зависимостей.
"""

from __future__ import annotations

import httpx

from docent.config import API_KEY_ENV, Config, get_api_key, resolve_model


class LLMError(RuntimeError):
    """Ошибка обращения к API провайдера (нет ключа, HTTP-ошибка и т.п.)."""


def _client(config: Config, timeout: float) -> httpx.Client:
    """Создаёт httpx-клиент с базовым URL и авторизацией по ключу из env."""
    key = get_api_key()
    if not key:
        raise LLMError(
            f"Не задан ключ. Установите переменную окружения {API_KEY_ENV}."
        )
    return httpx.Client(
        base_url=config.base_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )


def _post(client: httpx.Client, path: str, payload: dict) -> httpx.Response:
    """POST с преобразованием сетевых ошибок httpx в LLMError.

    Без этого таймаут/обрыв сети пролетает мимо retry/fallback-логики
    вызывающих (они ловят LLMError) и роняет процесс трейсбеком.
    """
    try:
        return client.post(path, json=payload)
    except httpx.HTTPError as err:
        raise LLMError(f"{path}: {type(err).__name__}: {err}") from err


def embed(texts: list[str], config: Config, timeout: float = 60.0) -> list[list[float]]:
    """Возвращает эмбеддинги для списка текстов (embed-модель из конфига)."""
    if not texts:
        return []
    model = resolve_model(config.embed_model)
    with _client(config, timeout) as client:
        resp = _post(client, "/embeddings", {"model": model, "input": texts})
    if resp.status_code != 200:
        raise LLMError(f"embeddings {resp.status_code}: {resp.text[:300]}")
    data = resp.json()["data"]
    # Сортируем по index — провайдер не гарантирует порядок.
    data.sort(key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def chat(
    messages: list[dict],
    config: Config,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> str:
    """Отправляет чат-запрос и возвращает текст ответа ассистента.

    model — логическое имя из реестра или id; по умолчанию берётся из конфига.
    """
    model_id = resolve_model(model or config.model)
    with _client(config, timeout) as client:
        resp = _post(
            client,
            "/chat/completions",
            {
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
            },
        )
    if resp.status_code != 200:
        raise LLMError(f"chat {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def chat_full(
    messages: list[dict],
    config: Config,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> dict:
    """Как chat(), но возвращает всё тело ответа, а не только текст.

    Нужно тем, кому важны метаданные ответа: `finish_reason` и служебное поле
    `gateway` (его добавляет LLM Gateway из дня 48). chat() их отбрасывает, и
    отличить блокировку прокси от обычного ответа модели становится нельзя.
    """
    model_id = resolve_model(model or config.model)
    with _client(config, timeout) as client:
        resp = _post(
            client,
            "/chat/completions",
            {
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
            },
        )
    if resp.status_code != 200:
        raise LLMError(f"chat {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def chat_tools(
    messages: list[dict],
    tools: list[dict],
    config: Config,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 180.0,
) -> dict:
    """Чат-запрос с function calling: возвращает message целиком.

    В отличие от chat(), отдаёт весь объект message (content + tool_calls),
    чтобы агентный цикл мог исполнить запрошенные моделью инструменты.
    tools — список в OpenAI-формате ({"type": "function", "function": {...}}).
    """
    model_id = resolve_model(model or config.model)
    with _client(config, timeout) as client:
        resp = _post(
            client,
            "/chat/completions",
            {
                "model": model_id,
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
            },
        )
    if resp.status_code != 200:
        raise LLMError(f"chat {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]
