import os
import json
import urllib.request
import urllib.error
import sqlite3
from typing import Optional
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


class JarvisAgent:
    """
    Агент для взаимодействия с LLM через API с сохранением контекста в SQLite.

    Поддерживает:
    - Несколько изолированных сессий диалога
    - Переключение между сессиями
    - Автоматическое восстановление истории при перезапуске
    """

    def __init__(
            self,
            api_key: Optional[str] = None,
            base_url: str = "https://foundation-models.api.cloud.ru/v1",
            model: str = "GigaChat/GigaChat-2-Max",
            temperature: float = 0.6,
            max_tokens: int = 2500,
            system_prompt: str = "Ты — полезный AI-ассистент.",
            db_path: str = "memory/jarvis_history.db",
            session_id: Optional[int] = None
    ):
        """Инициализация агента с заданными параметрами."""
        self.api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.db_path = db_path

        if not self.api_key:
            raise ValueError("API ключ не найден. Установите переменную окружения CLOUDRU_SECRET_KEY")

        # Инициализация БД
        self._init_db()

        # Загрузка или создание сессии
        if session_id is not None:
            self.current_session = self._load_session(session_id)
            if not self.current_session:
                raise ValueError(f"Сессия с ID {session_id} не найдена")
        else:
            # По умолчанию загружаем последнюю активную сессию или создаём новую
            last_session = self._get_last_session()
            if last_session:
                self.current_session = last_session
            else:
                self.current_session = self.create_session()

        # Загружаем историю текущей сессии
        self.conversation_history = self._load_messages()

        # Счётчики для статистики
        self.total_tokens_used = 0
        self.total_requests = 0

    def _init_db(self):
        """Создаёт таблицы в БД, если их нет."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)
            conn.commit()

    def _get_last_session(self) -> Optional[dict]:
        """Возвращает последнюю активную сессию."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at FROM sessions "
                "ORDER BY last_active_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "name": row[1], "created_at": row[2]}
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        """Загружает сессию по ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "name": row[1], "created_at": row[2]}
            return None

    def _load_messages(self) -> list:
        """Загружает историю сообщений текущей сессии."""
        history = []
        if self.system_prompt:
            history.append({"role": "system", "content": self.system_prompt})

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = ? ORDER BY timestamp ASC",
                (self.current_session["id"],)
            )
            for row in cursor.fetchall():
                history.append({"role": row[0], "content": row[1]})
        return history

    def _save_message(self, role: str, content: str):
        """Сохраняет одно сообщение в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (self.current_session["id"], role, content)
            )
            conn.execute(
                "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self.current_session["id"],)
            )
            conn.commit()

    def create_session(self, name: Optional[str] = None) -> dict:
        """Создаёт новую сессию."""
        if not name:
            name = f"Сессия от {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (name) VALUES (?)",
                (name,)
            )
            session_id = cursor.lastrowid
            conn.commit()

        session = {"id": session_id, "name": name, "created_at": datetime.now().isoformat()}
        self.current_session = session
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})

        print(f"✨ Создана новая сессия: {name} (ID: {session_id})")
        return session

    def switch_session(self, session_id: int) -> bool:
        """Переключается на другую сессию."""
        session = self._load_session(session_id)
        if not session:
            print(f"❌ Сессия с ID {session_id} не найдена")
            return False

        self.current_session = session
        self.conversation_history = self._load_messages()
        print(f"🔄 Переключено на сессию: {session['name']} (ID: {session_id})")
        return True

    def list_sessions(self) -> list:
        """Возвращает список всех сессий."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, last_active_at FROM sessions "
                "ORDER BY last_active_at DESC"
            )
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "last_active": row[3]}
                for row in cursor.fetchall()
            ]

    def delete_session(self, session_id: int) -> bool:
        """Удаляет сессию и все её сообщения."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            if cursor.rowcount > 0:
                # Если удалили текущую сессию — переключаемся на другую
                if self.current_session["id"] == session_id:
                    last = self._get_last_session()
                    if last:
                        self.switch_session(last["id"])
                    else:
                        self.create_session()
                return True
        return False

    def _build_messages(self, user_input: str) -> list:
        """Формирует список сообщений для отправки в API."""
        self.conversation_history.append({"role": "user", "content": user_input})
        return self.conversation_history

    def _call_api(self, messages: list) -> dict:
        """Инкапсулированная логика вызова API."""
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
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}", "details": error_body}
        except urllib.error.URLError as e:
            return {"success": False, "error": f"URL Error: {e.reason}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    def chat(self, user_input: str) -> str:
        """Основной метод агента: принимает запрос и возвращает ответ."""
        if not user_input or not user_input.strip():
            return "Пожалуйста, введите ваш запрос."

        messages = self._build_messages(user_input)
        response = self._call_api(messages)

        if response["success"]:
            assistant_message = response["content"]
            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            # Сохраняем оба сообщения в БД
            self._save_message("user", user_input)
            self._save_message("assistant", assistant_message)

            return assistant_message
        else:
            if self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            return f"❌ Ошибка: {response.get('error', 'Неизвестная ошибка')}\n{response.get('details', '')}"

    def reset_conversation(self):
        """Очищает историю текущей сессии (но не удаляет саму сессию)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (self.current_session["id"],))
            conn.commit()

        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})
        print(f"🔄 История сессии '{self.current_session['name']}' очищена.")

    def get_stats(self) -> str:
        """Возвращает статистику использования агента."""
        msg_count = len([m for m in self.conversation_history if m["role"] != "system"])
        return (
            f"📊 Статистика агента:\n"
            f"  • Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})\n"
            f"  • Всего запросов: {self.total_requests}\n"
            f"  • Всего токенов использовано: {self.total_tokens_used}\n"
            f"  • Сообщений в текущей сессии: {msg_count}\n"
            f"  • Модель: {self.model}"
        )