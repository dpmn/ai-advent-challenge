import os
import json
import urllib.request
import urllib.error

from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv('CLOUDRU_SECRET_KEY')
base_url = "https://foundation-models.api.cloud.ru/v1"


def send_request(messages, max_tokens=2500, stop=None, temperature=0.5):
    """Отправляет запрос к API и возвращает текстовый ответ модели."""
    payload = {
        "model": "ai-sage/GigaChat3-10B-A1.8B",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "presence_penalty": 0,
        "top_p": 0.95,
        "messages": messages
    }

    # Добавляем stop-последовательность, если она задана
    if stop:
        payload["stop"] = stop

    url = f"{base_url}/chat/completions"
    data = json.dumps(payload).encode('utf-8')

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')

        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result["choices"][0]["message"]["content"]

    except urllib.error.HTTPError as e:
        return f"HTTP Error: {e.code} - {e.reason}\n{e.read().decode('utf-8')}"
    except urllib.error.URLError as e:
        return f"URL Error: {e.reason}"
    except KeyError as e:
        return f"Unexpected response format. Missing key: {e}"


# === ВАРИАНТ 1: БЕЗ ОГРАНИЧЕНИЙ ===
print("=" * 60)
print("ВАРИАНТ 1: БЕЗ ОГРАНИЧЕНИЙ")
print("=" * 60)

messages_unrestricted = [
    {
        "role": "system",
        "content": "Ты — маленький робот-уборщик, которого оставили одного на заброшенной планете, покрытой мусором. Ты занимаешься уборкой уже много лет."
    },
    {
        "role": "user",
        "content": "Расскажи о своих впечатлениях от жизни на этой планете."
    }
]

response_unrestricted = send_request(messages_unrestricted)
print(response_unrestricted)

# === ВАРИАНТ 2: С ОГРАНИЧЕНИЯМИ ===
print("\n" + "=" * 60)
print("ВАРИАНТ 2: С ОГРАНИЧЕНИЯМИ")
print("Ограничения: формат дневника, ровно 3 записи, стоп-слово 'КОНЕЦ ДНЕВНИКА'")
print("=" * 60)

messages_restricted = [
    {
        "role": "system",
        "content": "Ты — маленький робот-уборщик, которого оставили одного на заброшенной планете, покрытой мусором. Ты занимаешься уборкой уже много лет."
    },
    {
        "role": "user",
        "content": (
            "Веди свой личный дневник. Напиши ровно 3 записи, каждая с указанием дня "
            "(например, 'День 1247'). В каждой записи рассказывай о том, что ты нашёл "
            "и что чувствуешь. После последней записи напиши: КОНЕЦ ДНЕВНИКА"
        )
    }
]

response_restricted = send_request(
    messages_restricted,
    max_tokens=1500,  # ограничение на длину
    stop=["КОНЕЦ ДНЕВНИКА"]  # явное условие завершения
)
print(response_restricted)

# === СРАВНЕНИЕ ===
print("\n" + "=" * 60)
print("СРАВНЕНИЕ")
print("=" * 60)
print(f"Длина ответа без ограничений: {len(response_unrestricted)} символов")
print(f"Длина ответа с ограничениями: {len(response_restricted)} символов")
print(f"Стоп-слово присутствует в ответе: {'КОНЕЦ ДНЕВНИКА' in response_restricted}")
print(f"Количество записей дневника (приблизительно): {response_restricted.count('День ')}")
