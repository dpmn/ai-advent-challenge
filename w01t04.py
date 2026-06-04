import os
import json
import urllib.request
import time

from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv('CLOUDRU_SECRET_KEY')
BASE_URL = "https://foundation-models.api.cloud.ru/v1"

PROMPT = (
    "Придумай название и краткое описание (2–3 предложения) для нового мобильного "
    "приложения, которое помогает людям сокращать пищевые отходы дома."
)

TEMPERATURES = [0, 0.7, 1.2]


def call_llm(temperature: float) -> dict:
    """Вызов API с заданной температурой."""
    payload = {
        "model": "GigaChat/GigaChat-2-Max",
        "max_tokens": 500,
        "temperature": temperature,
        "top_p": 0.95,
        "messages": [
            {"role": "system", "content": "Ты — креативный продукт-менеджер."},
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

            if not content:
                content = f"[ПУСТОЙ ОТВЕТ] finish_reason={finish_reason}"

            elapsed = round(time.time() - start, 2)
            return {
                "temperature": temperature,
                "content": content.strip(),
                "time": elapsed,
                "length": len(content.strip()),
                "finish_reason": finish_reason
            }
    except Exception as e:
        return {
            "temperature": temperature,
            "content": f"ERROR: {e}",
            "time": 0,
            "length": 0,
            "finish_reason": "error"
        }


# ─── Запуск экспериментов ───────────────────────────────────
results = []
for temp in TEMPERATURES:
    print(f"⏳ Генерация при temperature={temp}...")
    r = call_llm(temp)
    results.append(r)
    # Небольшая пауза между запросами
    time.sleep(1)

# ─── Вывод результатов ──────────────────────────────────────
for r in results:
    print("\n" + "=" * 70)
    print(f"🌡️  TEMPERATURE = {r['temperature']}  |  ⏱ {r['time']}с  |  📏 {r['length']} симв.")
    print("=" * 70)
    print(r["content"])

# ─── Сводная таблица ────────────────────────────────────────
print("\n" + "=" * 70)
print("📊 СВОДНАЯ ТАБЛИЦА")
print("=" * 70)
print(f"{'Temperature':<15} {'Время (с)':<12} {'Длина':<10} {'Finish Reason'}")
print("-" * 55)
for r in results:
    print(f"{r['temperature']:<15} {r['time']:<12} {r['length']:<10} {r['finish_reason']}")