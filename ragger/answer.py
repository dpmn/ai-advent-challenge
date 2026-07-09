"""Генерация ответа RAG с цитатами, источниками, анти-галлюцинациями и confidence.

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
        llm_metrics: Метрики инференса Ollama по этапам {"verify": {...},
            "generate": {...}} — только при llm_profile с transport="ollama".
    """
    query: str
    answer: str
    sources: list[dict]
    confidence: str  # "high" | "medium" | "low" | "none"
    llm_metrics: dict | None = None


def generate_answer(
    query: str,
    chunks: list[dict],
    api_key: str,
    model: str = "Qwen/Qwen3-Coder-Next",
    base_url: str = "https://foundation-models.api.cloud.ru/v1",
    verify_model: str = "Qwen/Qwen3-30B-A3B",
    llm_profile: dict | None = None,
) -> RagAnswer:
    """Генерирует структурированный ответ на основе RAG-чанков.

    Перед генерацией выполняет pre-verification — проверяет через
    дешёвую LLM, есть ли среди чанков информация, отвечающая на
    запрос. Если нет — confidence="none" без вызова основной модели.

    Args:
        query: Запрос пользователя.
        chunks: Список чанков от RagPipeline (поля chunk_id, source, section, text, score).
        api_key: API-ключ Cloud.ru.
        model: ID модели для генерации ответа.
        base_url: Базовый URL API.
        verify_model: ID дешёвой модели для pre-verification.
        llm_profile: Профиль инференса локального провайдера (day-29).
            None — облачный путь без изменений. Поддерживаемые ключи:
            transport="ollama" (нативный /api/chat вместо /v1),
            keep_alive, gen_options/verify_options (num_ctx, temperature,
            num_predict), chunk_char_limit (обрезка чанков в промпте),
            prompt_style="compact" (короткий промпт под 7B).

    Returns:
        RagAnswer с заполненными answer, sources, confidence
        (+ llm_metrics при transport="ollama").
    """
    profile = llm_profile or {}
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

    # Pre-verification: есть ли среди чанков прямой ответ?
    relevance, verify_metrics = _verify_relevance(
        query, chunks, api_key, verify_model, base_url, profile
    )
    llm_metrics: dict | None = {"verify": verify_metrics} if verify_metrics else None
    if relevance == "no":
        return RagAnswer(
            query=query,
            answer=(
                "Я не знаю ответа на этот вопрос. "
                "В найденных документах нет информации по данной теме."
            ),
            sources=[],
            confidence="none",
            llm_metrics=llm_metrics,
        )

    chunks_text = _format_chunks(chunks, profile.get("chunk_char_limit", 1200))

    if profile.get("prompt_style") == "compact":
        prompt = _build_compact_prompt(query, chunks_text)
    else:
        prompt = _build_default_prompt(query, chunks_text)

    if profile.get("transport") == "ollama":
        from ragger.ollama_client import ollama_chat
        try:
            content, gen_metrics = ollama_chat(
                prompt,
                model=model,
                base_url=base_url,
                options=profile.get("gen_options"),
                keep_alive=profile.get("keep_alive", "30m"),
                timeout=300,
            )
        except Exception as e:
            print(f"[ANSWER] Ollama LLM error: {e}")
            return RagAnswer(
                query=query, answer="", sources=[], confidence="none",
                llm_metrics=llm_metrics,
            )
        answer = _parse_response(content, query)
        answer.llm_metrics = dict(llm_metrics or {}, generate=gen_metrics)
        return answer

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

    # 300s: локальная модель при холодном старте сначала грузится в память
    # (десятки секунд), потом генерирует — 120s не хватало.
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[ANSWER] LLM error: {e}")
        # confidence="none" — сигнал вызывающему коду, что ответа нет:
        # пустой answer с "low" превращался в пустое сообщение пользователю.
        return RagAnswer(
            query=query,
            answer="",
            sources=[],
            confidence="none",
        )

    return _parse_response(content, query)


def _build_default_prompt(query: str, chunks_text: str) -> str:
    """Полный промпт генерации (облачные модели): развёрнутые правила, JSON-схема."""
    return (
        "Твоя задача — ответить на вопрос пользователя, используя ТОЛЬКО "
        "документы из базы знаний ниже.\n\n"
        f"❓ ВОПРОС: {query}\n\n"
        "📄 ДОКУМЕНТЫ:\n"
        + chunks_text
        + "\n\n"
        "⚠️  ПРАВИЛА:\n"
        "1. Если документы содержат информацию по теме вопроса — используй её "
        "для ответа. Если НИ ОДИН документ совсем не про тему вопроса — "
        "напиши 'Я не знаю'. Не используй косвенно связанные документы.\n"
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


def _build_compact_prompt(query: str, chunks_text: str) -> str:
    """Компактный промпт генерации под локальные 7B-модели.

    Та же JSON-схема, что и в полном промпте (парсер общий), но короткие
    правила без эмодзи-разметки — маленькие модели следуют им надёжнее.
    """
    return (
        "Ответь на вопрос, используя ТОЛЬКО документы ниже. Отвечай на русском.\n\n"
        f"ВОПРОС: {query}\n\n"
        "ДОКУМЕНТЫ:\n"
        + chunks_text
        + "\n\n"
        "ПРАВИЛА:\n"
        "- Используй только факты из документов. Если ответа в них нет — "
        'напиши "Я не знаю" и confidence "none".\n'
        "- Для каждого факта укажи источник: source, chunk_id, section и "
        "дословную цитату quote из документа.\n"
        "- confidence: high (полный ответ), medium (частичный), low (слабая связь), "
        "none (ответа нет).\n\n"
        "Верни ТОЛЬКО JSON без markdown и пояснений:\n"
        '{"answer": "...", "sources": [{"source": "...", "chunk_id": "...", '
        '"section": "...", "quote": "..."}], "confidence": "..."}'
    )


def _verify_relevance(
    query: str,
    chunks: list[dict],
    api_key: str,
    model: str = "Qwen/Qwen3-30B-A3B",
    base_url: str = "https://foundation-models.api.cloud.ru/v1",
    llm_profile: dict | None = None,
) -> tuple[str, dict | None]:
    """Проверяет, есть ли среди чанков информация по теме запроса.

    Вызов дешёвой LLM (Qwen3-30B-A3B), ответ — "yes"/"no".
    "no" — только если ни один чанк не связан с темой запроса.
    Даже частичное совпадение = "yes" (основная LLM разберётся).

    Returns:
        (relevance, metrics): "yes"/"no" и метрики Ollama
        (None на облачном пути или при ошибке).
    """
    snippet_chunks = []
    for c in chunks[:8]:
        text = c.get("text", "")[:600]
        src = c.get("source", "?")
        snippet_chunks.append(f'<doc source="{src}">\n{text}\n</doc>')
    snippets = "\n\n".join(snippet_chunks)

    prompt = (
        "Вопрос: {query}\n\n"
        "Документы:\n{snippets}\n\n"
        "Есть ли среди документов хотя бы один, который ХОТЯ БЫ КАСАЕТСЯ "
        "темы вопроса? Если документы про другое — ответь 'no'. "
        "Если хотя бы один про то же самое (даже не полностью) — ответь 'yes'.\n"
        "Ответь строго одним словом: 'yes' или 'no'."
    ).format(query=query, snippets=snippets)

    profile = llm_profile or {}
    if profile.get("transport") == "ollama":
        from ragger.ollama_client import ollama_chat
        try:
            content, metrics = ollama_chat(
                prompt,
                model=model,
                base_url=base_url,
                options=profile.get("verify_options"),
                keep_alive=profile.get("keep_alive", "30m"),
                timeout=300,
            )
            return ("yes" if "yes" in content.strip().lower() else "no"), metrics
        except Exception as e:
            print(f"[ANSWER] Ollama verification error: {e}")
            return "yes", None

    payload = {
        "model": model,
        "max_tokens": 8,
        "temperature": 0.0,
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
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
            return ("yes" if "yes" in content else "no"), None
    except Exception as e:
        print(f"[ANSWER] Verification error: {e}")
        return "yes", None


def _format_chunks(chunks: list[dict], char_limit: int = 1200) -> str:
    """Форматирует чанки с doc ID-разметкой для промпта, обрезая до char_limit символов."""
    parts = []
    for c in chunks:
        source = c.get("source", "?")
        chunk_id = c.get("chunk_id", "?")
        section = c.get("section", "")
        text = c.get("text", "")[:char_limit]
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
