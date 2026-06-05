import os
import json
import urllib.request
import time

from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv('CLOUDRU_SECRET_KEY')
BASE_URL = "https://foundation-models.api.cloud.ru/v1"

PROMPT = (
    "Проанализируй плюсы и минусы удалённой работы для IT-компаний. "
    "Представь ответ в виде таблицы с 3 колонками: аспект, плюсы, минусы. "
    "Добавь краткий вывод (2-3 предложения)."
)

MODELS = [
    "ai-sage/GigaChat3-10B-A1.8B",  # Слабая/средняя модель
    "GigaChat/GigaChat-2-Max",      # Средняя/сильная модель
    "Qwen/Qwen3.5-397B-A17B"        # Сильная модель
]


def call_llm(model: str) -> dict:
    """Вызов API с заданной моделью."""
    payload = {
        "model": model,
        "max_tokens": 1500,
        "temperature": 0.5,  # Фиксируем температуру для честного сравнения
        "top_p": 0.95,
        "messages": [
            {"role": "user", "content": PROMPT}
        ]
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            message = result.get("choices", [{}])[0].get("message", {})
            content = message.get("content")
            finish_reason = result["choices"][0].get("finish_reason", "unknown")
            usage = result.get("usage", {})

            if not content:
                content = f"[ПУСТОЙ ОТВЕТ] finish_reason={finish_reason}"

            elapsed = round(time.time() - start, 2)
            return {
                "model": model,
                "content": content.strip(),
                "time": elapsed,
                "length": len(content.strip()),
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "finish_reason": finish_reason
            }
    except Exception as e:
        return {
            "model": model,
            "content": f"ERROR: {e}",
            "time": 0,
            "length": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "finish_reason": "error"
        }


# ─── Запуск экспериментов ───────────────────────────────────
results = []
for model in MODELS:
    print(f"⏳ Генерация на модели: {model}...")
    r = call_llm(model)
    results.append(r)
    time.sleep(1)

# ─── Вывод результатов ──────────────────────────────────────
for r in results:
    print("\n" + "=" * 70)
    print(f"🤖 МОДЕЛЬ: {r['model']}")
    print(f"⏱  Время: {r['time']}с | 📊 Токены: {r['total_tokens']} "
          f"(prompt: {r['prompt_tokens']}, completion: {r['completion_tokens']})")
    print("=" * 70)
    print(r["content"])

# ─── Сводная таблица ────────────────────────────────────────
print("\n" + "=" * 70)
print("📊 СВОДНАЯ ТАБЛИЦА")
print("=" * 70)
print(f"{'Модель':<40} {'Время (с)':<12} {'Всего токенов':<15} {'Длина ответа'}")
print("-" * 80)
for r in results:
    # Укорачиваем имя модели для читаемости
    model_short = r['model'].split('/')[-1][:38]
    print(f"{model_short:<40} {r['time']:<12} {r['total_tokens']:<15} {r['length']}")