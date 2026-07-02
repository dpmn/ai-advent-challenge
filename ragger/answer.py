"""Генерация ответа с цитатами, источниками и анти-галлюцинациями.

Форсирует LLM возвращать структурированный JSON с полями:
  - answer: текст ответа
  - sources: список {source, chunk_id, section, quote}
  - confidence: high|medium|low|none
"""

import json
import re
import urllib.request
from dataclasses import dataclass, asdict


@dataclass
class RagAnswer:
    """Структурированный ответ RAG с обязательными источниками и цитатами.

    Attributes:
        query: Исходный запрос пользователя.
        answer: Текст ответа (может содержать "не знаю" при confidence=none).
        sources: Список словарей {source, chunk_id, section, quote}.
        confidence: Уровень уверенности (high|medium|low|none).
    """
    query: str
    answer: str
    sources: list[dict]
    confidence: str  # "high" | "medium" | "low" | "none"


def generate_answer(
    query: str,
    chunks: list[dict],
    api_key: str,
    model: str = "Qwen/Qwen3-Coder-Next",
    base_url: str = "https://foundation-models.api.cloud.ru/v1",
) -> RagAnswer:
    """Генерирует структурированный ответ на основе RAG-чанков.

    Если чанки пусты — возвращает RagAnswer с confidence="none"
    (режим "не знаю"). Иначе вызывает LLM со строгим JSON-форматом,
    парсит ответ и возвращает RagAnswer.

    Args:
        query: Запрос пользователя.
        chunks: Список чанков от RagPipeline (поля chunk_id, source, section, text, score).
        api_key: API-ключ Cloud.ru.
        model: ID модели.
        base_url: Базовый URL API.

    Returns:
        RagAnswer с заполненными answer, sources, confidence.
    """
    if not chunks:
        return RagAnswer(
            query=query,
            answer=(
                "Я не знаю ответа на этот вопрос. "
                "Пожалуйста, уточните запрос — возможно, я смогу найти информацию "
                "по другим ключевым словам."
            ),
            sources=[],
            confidence="none",
        )

    chunks_text = _format_chunks(chunks)

    prompt = (
        "Твоя задача — ответить на вопрос пользователя, используя ТОЛЬКО "
        "документы из базы знаний ниже.\n\n"
        "📄 ДОКУМЕНТЫ:\n"
        + chunks_text
        + "\n\n"
        "⚠️  ПРАВИЛА:\n"
        "1. Если информации в документах недостаточно для ответа — "
        "напиши 'Я не знаю' и попроси уточнить.\n"
        "2. Если знаешь — дай развёрнутый ответ и ОБЯЗАТЕЛЬНО укажи "
        "ИСТОЧНИКИ и ЦИТАТЫ для каждого факта.\n"
        "3. В поле sources перечисли использованные документы: "
        "source (имя файла/URL), chunk_id, section (раздел), "
        "quote (дословная цитата фрагмента текста, подтверждающего ответ).\n"
        "4. confidence: high — полное совпадение, medium — частичное, "
        "low — слабая связь, none — ответа нет в документах.\n\n"
        "Ответ верни СТРОГО в виде JSON (без markdown-обёртки, без пояснений):\n"
        "{\n"
        '  "answer": "текст ответа",\n'
        '  "sources": [\n'
        '    {\n'
        '      "source": "docs/example.md",\n'
        '      "chunk_id": "struct_00042",\n'
        '      "section": "Название раздела",\n'
        '      "quote": "дословная цитата"\n'
        '    }\n'
        "  ],\n"
        '  "confidence": "high|medium|low|none"\n'
        "}"
    )

    payload = {
        "model": model,
        "max_tokens": 2048,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[ANSWER] LLM error: {e}")
        return RagAnswer(
            query=query,
            answer="",
            sources=[],
            confidence="low",
        )

    return _parse_response(content, query)


def _format_chunks(chunks: list[dict]) -> str:
    """Форматирует чанки с doc ID-разметкой для промпта."""
    parts = []
    for c in chunks:
        source = c.get("source", "?")
        chunk_id = c.get("chunk_id", "?")
        section = c.get("section", "")
        text = c.get("text", "")[:1200]
        parts.append(
            f'<doc id="{chunk_id}" source="{source}" section="{section}">\n'
            f"{text}\n"
            f"</doc>"
        )
    return "\n\n".join(parts)


def _parse_response(content: str, query: str) -> RagAnswer:
    """Парсит JSON-ответ от LLM с fallback-стратегией.

    Пытается извлечь JSON разными паттернами, при полной неудаче
    возвращает ответ как plain text с confidence="low".
    """
    data = _extract_json(content)
    if data:
        return RagAnswer(
            query=query,
            answer=data.get("answer", content.strip()),
            sources=_normalize_sources(data.get("sources", [])),
            confidence=data.get("confidence", "low"),
        )

    return RagAnswer(
        query=query,
        answer=content.strip(),
        sources=[],
        confidence="low",
    )


def _extract_json(content: str) -> dict | None:
    """Извлекает JSON-объект из ответа LLM.

    Пробует: прямой парсинг → поиск { } с "answer" → поиск ```json блоков.
    """
    # Попытка 1: прямой парсинг всего ответа
    content = content.strip()
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "answer" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Попытка 2: поиск ```json ... ``` блока
    json_block = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL
    )
    if json_block:
        try:
            data = json.loads(json_block.group(1).strip())
            if isinstance(data, dict) and "answer" in data:
                return data
        except json.JSONDecodeError:
            pass

    # Попытка 3: поиск { ... } верхнего уровня с полем "answer"
    match = re.search(
        r'\{\s*"[a-zA-Z]+".*?"answer"\s*:\s*".*?"[\s\S]*?\}',
        content,
        re.DOTALL,
    )
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "answer" in data:
                return data
        except json.JSONDecodeError:
            pass

    # Попытка 4: поиск более свободного JSON
    match = re.search(r"(\{[\s\S]*\})", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _normalize_sources(sources: list) -> list[dict]:
    """Приводит источники к единому формату {source, chunk_id, section, quote}."""
    result = []
    for s in sources:
        if isinstance(s, dict):
            result.append({
                "source": str(s.get("source", s.get("name", ""))),
                "chunk_id": str(s.get("chunk_id", s.get("id", ""))),
                "section": str(s.get("section", s.get("heading", ""))),
                "quote": str(s.get("quote", s.get("text", ""))),
            })
        elif isinstance(s, str):
            result.append({
                "source": s,
                "chunk_id": "",
                "section": "",
                "quote": "",
            })
    return result
