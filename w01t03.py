import os
import json
import urllib.request
import time

from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv('CLOUDRU_SECRET_KEY')
BASE_URL = "https://foundation-models.api.cloud.ru/v1"

TASK = (
    "На стадионе длиной 400 метров два бегуна стартуют одновременно из одной точки "
    "в противоположных направлениях. Скорость первого — 12 км/ч, второго — 8 км/ч. "
    "Через сколько секунд они встретятся в первый раз? "
    "И на каком расстоянии от точки старта (по часовой стрелке) это произойдёт?"
)


def call_llm(messages: list[dict], label: str = "") -> dict:
    """Универсальная функция вызова API с защитой от пустых ответов."""
    payload = {
        "model": "GigaChat/GigaChat-2-Max",
        "max_tokens": 2000,
        "temperature": 0.3,
        "top_p": 0.95,
        "messages": messages,
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

            # Безопасное извлечение контента
            message = result.get("choices", [{}])[0].get("message", {})
            content = message.get("content")

            # Если content пустой или None — проверяем finish_reason
            if not content:
                finish_reason = result.get("choices", [{}])[0].get("finish_reason", "unknown")
                content = f"[ПУСТОЙ ОТВЕТ] finish_reason={finish_reason}. Полный ответ API: {json.dumps(result, ensure_ascii=False)}"

            elapsed = round(time.time() - start, 2)
            return {"label": label, "content": content, "time": elapsed}

    except Exception as e:
        return {"label": label, "content": f"ERROR: {e}", "time": 0}


# ─── Способ 1: Прямой вопрос ────────────────────────────────
r1 = call_llm([
    {"role": "user", "content": TASK}
], label="1. Прямой ответ")

# ─── Способ 2: Chain-of-Thought («решай пошагово») ─────────
r2 = call_llm([
    {"role": "system", "content": "Решай задачу строго пошагово. "
     "Сначала переведи единицы, затем найди относительную скорость, "
     "потом время до встречи, и наконец — расстояние по часовой стрелке. "
     "Показывай все промежуточные вычисления."},
    {"role": "user", "content": TASK}
], label="2. Пошаговое решение")

# ─── Способ 3: Meta-prompting (модель сама пишет промпт) ───
meta_messages = [
    {"role": "system", "content": "Ты — эксперт по промпт-инжинирингу. "
     "Составь оптимальный промпт для решения следующей задачи. "
     "Верни ТОЛЬКО текст промпта, без пояснений."},
    {"role": "user", "content": TASK}
]
generated_prompt = call_llm(meta_messages)["content"].strip()

print(f"[Meta-prompt сгенерирован]:\n{generated_prompt}\n")

r3 = call_llm([
    {"role": "user", "content": generated_prompt + "\n\nЗадача: " + TASK}
], label="3. Meta-prompting")

# ─── Способ 4: Группа экспертов ─────────────────────────────
experts_system = """Ты координируешь группу из трёх экспертов, которые решают задачу совместно.
Каждый эксперт должен дать свою часть решения:

🔹 АНАЛИТИК: переводит единицы, определяет относительную скорость и время до встречи.
🔹 ИНЖЕНЕР: рассчитывает точное расстояние по часовой стрелке от точки старта, проверяет расчёты.
🔹 КРИТИК: ищет возможные ошибки в рассуждениях аналитика и инженера, подтверждает или опровергает итоговый ответ.

Формат ответа:
## Аналитик
...
## Инженер
...
## Критик
...
## Итоговый ответ
..."""

r4 = call_llm([
    {"role": "system", "content": experts_system},
    {"role": "user", "content": TASK}
], label="4. Группа экспертов")

# ─── Вывод результатов ──────────────────────────────────────
results = [r1, r2, r3, r4]

for r in results:
    print("=" * 70)
    print(f"📌 {r['label']}  ⏱ {r['time']} сек")
    print("=" * 70)
    print(r["content"])
    print()

# ─── Сводная таблица ────────────────────────────────────────
print("=" * 70)
print("📊 СВОДНАЯ ТАБЛИЦА")
print("=" * 70)
print(f"{'Способ':<25} {'Время (сек)':<12} {'Длина ответа'}")
print("-" * 55)
for r in results:
    print(f"{r['label']:<25} {r['time']:<12} {len(r['content'])}")