import os
import json
import urllib.request
import urllib.error

from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv('CLOUDRU_SECRET_KEY')
base_url = "https://foundation-models.api.cloud.ru/v1"

payload = {
    "model": "ai-sage/GigaChat3-10B-A1.8B",
    "max_tokens": 2500,
    "temperature": 0.5,
    "presence_penalty": 0,
    "top_p": 0.95,
    "messages": [
        {
            "role": "system",
            "content": "Ты — опытный Data Engineer. Отвечай кратко и по делу."
        },
        {
            "role": "user",
            "content": "Какие существуют архитектуры данных?"
        }
    ]
}

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
        print(result["choices"][0]["message"]["content"])

except urllib.error.HTTPError as e:
    print(f"HTTP Error: {e.code} - {e.reason}")
    print(e.read().decode('utf-8'))
except urllib.error.URLError as e:
    print(f"URL Error: {e.reason}")
except KeyError as e:
    print(f"Unexpected response format. Missing key: {e}")
    print(result)
