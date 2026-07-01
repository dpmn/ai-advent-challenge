"""Функции фильтрации и реранкинга для RAG-пайплайна."""

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
    model: str = "Qwen/Qwen3-30B-A3B",
    base_url: str = "https://foundation-models.api.cloud.ru/v1",
) -> list[dict]:
    """Реранкинг чанков через LLM: оценивает релевантность каждого чанка к запросу.

    Делает один батч-запрос к LLM, получает оценки для всех чанков,
    сортирует по убыванию оценки.
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
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[RERANKER] LLM error: {e}")
        return chunks

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
    numbers = re.findall(r"[\d.]+", content)
    if numbers:
        parsed = [float(n) for n in numbers[:expected]]
        return parsed
    return [1.0] * expected
