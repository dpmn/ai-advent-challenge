"""Функции фильтрации (threshold_filter) и LLM-реранкинга (llm_rerank) для RAG-пайплайна."""

import json
import re
import urllib.request


def threshold_filter(chunks: list[dict], threshold: float) -> list[dict]:
    """Отсекает чанки с score ниже порога."""
    return [c for c in chunks if c["score"] >= threshold]


def llm_rerank(
    query: str,
    chunks: list[dict],
    api_key: str,
    model: str = "Qwen/Qwen3-Coder-Next",
    base_url: str = "https://foundation-models.api.cloud.ru/v1",
    llm_profile: dict | None = None,
) -> list[dict]:
    """Реранкинг чанков через LLM: оценивает релевантность каждого чанка к запросу.

    Делает один батч-запрос к LLM, получает оценки для всех чанков,
    сортирует по убыванию оценки.

    Args:
        llm_profile: Профиль инференса локального провайдера (day-29):
            transport="ollama" → нативный /api/chat с rerank_options
            (num_ctx, temperature, num_predict) и keep_alive.
            None — облачный путь без изменений.
    """
    if not chunks:
        return chunks

    chunks_text = "\n\n".join(
        f"[{i}] {c['text'][:800]}"
        for i, c in enumerate(chunks)
    )

    prompt = (
        "Оцени релевантность каждого документа к запросу пользователя.\n\n"
        f"Запрос: {query}\n\n"
        "Документы:\n" + chunks_text + "\n\n"
        "Верни JSON-массив с оценками от 0.0 до 1.0, где 1.0 — идеально релевантен, "
        "0.0 — не релевантен. Индекс элемента в массиве соответствует номеру документа.\n"
        "Формат: [0.1, 0.9, 0.4, ...]"
    )

    profile = llm_profile or {}
    if profile.get("transport") == "ollama":
        from ragger.ollama_client import ollama_chat
        try:
            content, _metrics = ollama_chat(
                prompt,
                model=model,
                base_url=base_url,
                options=profile.get("rerank_options"),
                keep_alive=profile.get("keep_alive", "30m"),
                timeout=300,
            )
        except Exception as e:
            print(f"[RERANKER] Ollama LLM error: {e}")
            return chunks
        return _apply_scores(chunks, content)

    payload = {
        "model": model,
        "max_tokens": 512,
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
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[RERANKER] LLM error: {e}")
        return chunks

    return _apply_scores(chunks, content)


def _apply_scores(chunks: list[dict], content: str) -> list[dict]:
    """Парсит оценки из ответа LLM, проставляет rerank_score и сортирует чанки."""
    scores = _parse_scores(content, len(chunks))

    for i, c in enumerate(chunks):
        c["rerank_score"] = scores[i] if i < len(scores) else 0.0

    chunks.sort(key=lambda c: c["rerank_score"], reverse=True)
    return chunks


def _parse_scores(content: str, expected: int) -> list[float]:
    """Парсит JSON-массив оценок из ответа LLM."""
    try:
        scores = json.loads(content)
        if isinstance(scores, list) and len(scores) == expected:
            return [float(s) for s in scores]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    numbers = re.findall(r"\d+(?:\.\d+)?", content)
    valid = [float(n) for n in numbers if n and n != '.']
    if valid:
        if len(valid) >= expected:
            return valid[:expected]
        return valid + [0.5] * (expected - len(valid))
    return [0.5] * expected
