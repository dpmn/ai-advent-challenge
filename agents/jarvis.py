import os
import json
import urllib.request
import urllib.error
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class JarvisAgent:
    """
    Простой агент для взаимодействия с LLM через API.

    Агент инкапсулирует:
    - Конфигурацию (API ключ, модель, параметры)
    - Историю разговора (контекст)
    - Логику отправки запросов и обработки ответов
    """

    def __init__(
            self,
            api_key: Optional[str] = None,
            base_url: str = "https://foundation-models.api.cloud.ru/v1",
            model: str = "GigaChat/GigaChat-2-Max",
            temperature: float = 0.5,
            max_tokens: int = 1500,
            system_prompt: str = "Ты — полезный AI-ассистент."
    ):
        """Инициализация агента с заданными параметрами."""
        self.api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt

        # История разговора (контекст агента)
        self.conversation_history = []

        # Добавляем системный промпт в начало истории
        if system_prompt:
            self.conversation_history.append({
                "role": "system",
                "content": system_prompt
            })

        # Счётчики для статистики
        self.total_tokens_used = 0
        self.total_requests = 0

        if not self.api_key:
            raise ValueError("API ключ не найден. Установите переменную окружения CLOUDRU_SECRET_KEY")

    def _build_messages(self, user_input: str) -> list:
        """Формирует список сообщений для отправки в API."""
        # Добавляем сообщение пользователя в историю
        self.conversation_history.append({
            "role": "user",
            "content": user_input
        })
        return self.conversation_history

    def _call_api(self, messages: list) -> dict:
        """
        Инкапсулированная логика вызова API.
        Возвращает ответ модели и метаданные.
        """
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))

                # Извлекаем ответ и метаданные
                choice = result.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "")
                usage = result.get("usage", {})

                self.total_requests += 1
                self.total_tokens_used += usage.get("total_tokens", 0)

                return {
                    "success": True,
                    "content": content,
                    "usage": usage,
                    "finish_reason": choice.get("finish_reason", "unknown")
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else "No error body"
            return {
                "success": False,
                "error": f"HTTP {e.code}: {e.reason}",
                "details": error_body
            }
        except urllib.error.URLError as e:
            return {
                "success": False,
                "error": f"URL Error: {e.reason}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }

    def chat(self, user_input: str) -> str:
        """
        Основной метод агента: принимает запрос пользователя,
        отправляет в LLM и возвращает ответ.
        """
        if not user_input or not user_input.strip():
            return "Пожалуйста, введите ваш запрос."

        # Формируем сообщения с учётом истории
        messages = self._build_messages(user_input)

        # Вызываем API
        response = self._call_api(messages)

        if response["success"]:
            assistant_message = response["content"]

            # Добавляем ответ ассистента в историю
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })

            return assistant_message
        else:
            # В случае ошибки удаляем последнее сообщение пользователя из истории
            if self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()

            return f"❌ Ошибка: {response.get('error', 'Неизвестная ошибка')}\n{response.get('details', '')}"

    def reset_conversation(self):
        """Сбрасывает историю разговора."""
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({
                "role": "system",
                "content": self.system_prompt
            })
        print("🔄 История разговора очищена.")

    def get_stats(self) -> str:
        """Возвращает статистику использования агента."""
        return (
            f"📊 Статистика агента:\n"
            f"  • Всего запросов: {self.total_requests}\n"
            f"  • Всего токенов использовано: {self.total_tokens_used}\n"
            f"  • Сообщений в истории: {len(self.conversation_history)}\n"
            f"  • Модель: {self.model}"
        )
