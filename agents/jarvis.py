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
            model: str = "Qwen/Qwen3-30B-A3B",
            temperature: float = 0.6,
            max_tokens: int = 2500,
            system_prompt: str = "Ты — полезный AI-ассистент.",
            db_path: str = "memory/jarvis_history.db",
            session_id: Optional[int] = None,
            context_limit: int = 40000
    ):
        """Инициализация агента с заданными параметрами."""
        self.api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.db_path = db_path
        self.context_limit = context_limit

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

        # Счётчики токенов для последнего запроса и текущей сессии
        self.last_usage: Optional[dict] = None
        self.session_prompt_tokens = self.current_session.get("prompt_tokens", 0)
        self.session_completion_tokens = self.current_session.get("completion_tokens", 0)
        self.session_total_tokens = self.current_session.get("total_tokens", 0)

    def _init_db(self):
        """Создаёт таблицы в БД, если их нет."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0
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
            # Добавляем колонки для существующих БД (старых версий)
            for col in ("prompt_tokens", "completion_tokens", "total_tokens"):
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def _get_last_session(self) -> Optional[dict]:
        """Возвращает последнюю активную сессию."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens "
                "FROM sessions ORDER BY last_active_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5]
                }
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        """Загружает сессию по ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens "
                "FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5]
                }
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

    def _update_session_tokens(self):
        """Сохраняет счётчики токенов текущей сессии в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET prompt_tokens = ?, completion_tokens = ?, total_tokens = ? WHERE id = ?",
                (self.session_prompt_tokens, self.session_completion_tokens, self.session_total_tokens, self.current_session["id"])
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

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

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

        self.session_prompt_tokens = session.get("prompt_tokens", 0)
        self.session_completion_tokens = session.get("completion_tokens", 0)
        self.session_total_tokens = session.get("total_tokens", 0)

        print(f"🔄 Переключено на сессию: {session['name']} (ID: {session_id})")
        return True

    def list_sessions(self) -> list:
        """Возвращает список всех сессий."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, last_active_at, prompt_tokens, completion_tokens, total_tokens "
                "FROM sessions ORDER BY last_active_at DESC"
            )
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "last_active": row[3],
                 "prompt_tokens": row[4], "completion_tokens": row[5], "total_tokens": row[6]}
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

            # Обновляем счётчики токенов
            usage = response.get("usage", {})
            self.last_usage = usage
            self.session_prompt_tokens += usage.get("prompt_tokens", 0)
            self.session_completion_tokens += usage.get("completion_tokens", 0)
            self.session_total_tokens += usage.get("total_tokens", 0)
            self._update_session_tokens()

            # Предупреждение о приближении к лимиту контекста
            total = self.session_prompt_tokens + self.session_completion_tokens
            if total > self.context_limit * 0.8:
                pct = round(total / self.context_limit * 100, 1)
                assistant_message += (
                    f"\n\n⚠️ Внимание: контекст диалога заполнен на {pct}% "
                    f"(~{total} токенов из {self.context_limit}). Рекомендуется начать новую сессию."
                )

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

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._update_session_tokens()
        print(f"🔄 История сессии '{self.current_session['name']}' очищена.")

    def trim_context(self, n: int = 10):
        """Обрезает историю, оставляя последние N сообщений (без учёта system prompt)."""
        if n < 1:
            self.reset_conversation()
            return

        messages = [m for m in self.conversation_history if m["role"] != "system"]
        if len(messages) <= n:
            print(f"ℹ️ В истории {len(messages)} сообщений, обрезка не требуется.")
            return

        kept = messages[-n:]
        removed = len(messages) - n

        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})
        self.conversation_history.extend(kept)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (self.current_session["id"],))
            for msg in kept:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                    (self.current_session["id"], msg["role"], msg["content"])
                )
            conn.commit()

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._update_session_tokens()
        print(f"✂️ История обрезана: удалено {removed} сообщений, оставлено {n}.")

    def get_stats(self) -> str:
        """Возвращает статистику использования агента."""
        msg_count = len([m for m in self.conversation_history if m["role"] != "system"])

        lines = [
            "📊 Статистика агента:",
            f"  • Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})",
            f"  • Всего запросов: {self.total_requests}",
            f"  • Всего токенов (глобально): {self.total_tokens_used}",
            f"  • Сообщений в текущей сессии: {msg_count}",
            f"  • Модель: {self.model}",
            "",
            f"  📈 Токены текущей сессии:",
            f"     Prompt:     {self.session_prompt_tokens}",
            f"     Completion: {self.session_completion_tokens}",
            f"     Всего:      {self.session_total_tokens}",
        ]

        if self.last_usage:
            lines.extend([
                "",
                "  🎯 Последний запрос:",
                f"     Prompt:     {self.last_usage.get('prompt_tokens', '—')}",
                f"     Completion: {self.last_usage.get('completion_tokens', '—')}",
                f"     Всего:      {self.last_usage.get('total_tokens', '—')}",
            ])

        total = self.session_prompt_tokens + self.session_completion_tokens
        if total > 0:
            pct = round(total / self.context_limit * 100, 1)
            lines.append(f"     Заполнение контекста: {pct}% из {self.context_limit}")

        return "\n".join(lines)
