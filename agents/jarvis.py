import os
import json
import urllib.request
import urllib.error
import sqlite3
from typing import Optional
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_AGENTS_DIR = Path(__file__).parent.resolve()
_DEFAULT_DB_PATH = str(_AGENTS_DIR / "memory" / "jarvis_history.db")


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
            db_path: Optional[str] = None,
            session_id: Optional[int] = None,
            context_limit: int = 40000,
            compression_enabled: Optional[bool] = None
    ):
        """Инициализация агента с заданными параметрами."""
        self.api_key = api_key or os.getenv('CLOUDRU_SECRET_KEY')
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.context_limit = context_limit

        if not self.api_key:
            raise ValueError("API ключ не найден. Установите переменную окружения CLOUDRU_SECRET_KEY")

        # Инициализация БД
        self._init_db()

        # Режим сжатия по умолчанию (до загрузки сессии)
        self.compression_enabled = compression_enabled if compression_enabled is not None else True

        # Загрузка или создание сессии
        if session_id is not None:
            self.current_session = self._load_session(session_id)
            if not self.current_session:
                raise ValueError(f"Сессия с ID {session_id} не найдена")
        else:
            last_session = self._get_last_session()
            if last_session:
                self.current_session = last_session
            else:
                self.current_session = self.create_session()

        # Переопределяем режим из сохранённого в сессии (если не задан явный параметр)
        if compression_enabled is None:
            saved = self.current_session.get("compression_enabled")
            if saved is not None:
                self.compression_enabled = bool(saved)

        # Загружаем историю текущей сессии
        self.conversation_history = self._load_messages()

        # Счётчики для статистики
        self.total_tokens_used = 0
        self.total_requests = 0

        # Счётчики токенов для последнего запроса и текущей сессии
        self.last_usage: Optional[dict] = None
        
        # Параметры сжатия истории
        self.compression_interval = 5
        self.compression_history: list = self._load_compressed_summaries()
        self.session_prompt_tokens = self.current_session.get("prompt_tokens", 0)
        self.session_completion_tokens = self.current_session.get("completion_tokens", 0)
        self.session_total_tokens = self.current_session.get("total_tokens", 0)

    def _init_db(self):
        """Создаёт таблицы в БД, если их нет."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS compressed_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    source_count INTEGER DEFAULT 0,
                    tokens_before INTEGER DEFAULT 0,
                    tokens_after INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)
            # Добавляем колонки для существующих БД (старых версий)
            for col in ("prompt_tokens", "completion_tokens", "total_tokens"):
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN compression_enabled INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def _get_last_session(self) -> Optional[dict]:
        """Возвращает последнюю активную сессию."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, compression_enabled "
                "FROM sessions ORDER BY last_active_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6]
                }
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        """Загружает сессию по ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, compression_enabled "
                "FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6]
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

    def _load_compressed_summaries(self) -> list:
        """Загружает сжатые summary для текущей сессии."""
        summaries = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT content, source_count, tokens_before, tokens_after "
                "FROM compressed_summaries WHERE session_id = ? ORDER BY created_at ASC",
                (self.current_session["id"],)
            )
            for row in cursor.fetchall():
                summaries.append({
                    "type": "summary",
                    "content": row[0],
                    "source_messages": row[1],
                    "tokens_before": row[2],
                    "tokens_after": row[3]
                })
        return summaries

    def _save_compressed_summary(self, content: str, source_count: int, tokens_before: int, tokens_after: int):
        """Сохраняет одно сжатое summary в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO compressed_summaries (session_id, content, source_count, tokens_before, tokens_after) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.current_session["id"], content, source_count, tokens_before, tokens_after)
            )
            conn.commit()

    def _clear_compressed_summaries(self):
        """Удаляет все сжатые summary для текущей сессии."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM compressed_summaries WHERE session_id = ?",
                (self.current_session["id"],)
            )
            conn.commit()

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
                "INSERT INTO sessions (name, compression_enabled) VALUES (?, ?)",
                (name, int(self.compression_enabled))
            )
            session_id = cursor.lastrowid
            conn.commit()

        session = {"id": session_id, "name": name, "created_at": datetime.now().isoformat(), "compression_enabled": self.compression_enabled}
        self.current_session = session
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})

        self.compression_history = []

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
        self.compression_history = self._load_compressed_summaries()

        self.session_prompt_tokens = session.get("prompt_tokens", 0)
        self.session_completion_tokens = session.get("completion_tokens", 0)
        self.session_total_tokens = session.get("total_tokens", 0)

        saved = session.get("compression_enabled")
        if saved is not None:
            self.compression_enabled = bool(saved)

        status = "вкл" if self.compression_enabled else "выкл"
        print(f"🔄 Переключено на сессию: {session['name']} (ID: {session_id}) | Сжатие: {status}")
        return True

    def list_sessions(self) -> list:
        """Возвращает список всех сессий."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, last_active_at, prompt_tokens, completion_tokens, total_tokens, compression_enabled "
                "FROM sessions ORDER BY last_active_at DESC"
            )
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "last_active": row[3],
                 "prompt_tokens": row[4], "completion_tokens": row[5], "total_tokens": row[6],
                 "compression_enabled": row[7]}
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

        self.conversation_history.append({"role": "user", "content": user_input})
        if self.compression_enabled:
            messages = self.get_compressed_messages()
        else:
            messages = self.get_raw_messages()
        tokens_before = sum(self._count_tokens(m["content"]) for m in messages if m["role"] != "system")

        response = self._call_api(messages)

        if response["success"]:
            assistant_message = response["content"]
            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            self._save_message("user", user_input)
            self._save_message("assistant", assistant_message)

            usage = response.get("usage", {})
            self.last_usage = usage
            self.session_prompt_tokens += usage.get("prompt_tokens", 0)
            self.session_completion_tokens += usage.get("completion_tokens", 0)
            self.session_total_tokens += usage.get("total_tokens", 0)
            self._update_session_tokens()

            total = self.session_prompt_tokens + self.session_completion_tokens
            if total > self.context_limit * 0.8:
                pct = round(total / self.context_limit * 100, 1)
                assistant_message += (
                    f"\n\n⚠️ Внимание: контекст диалога заполнен на {pct}% "
                    f"(~{total} токенов из {self.context_limit}). Рекомендуется начать новую сессию."
                )

            if self.compression_enabled and len([m for m in self.conversation_history if m["role"] != "system"]) % self.compression_interval == 0:
                comp_result = self.compress_history()
                if comp_result:
                    assistant_message += (
                        f"\n\n📦 История сжата: {comp_result['tokens_before']} → {comp_result['tokens_after']} "
                        f"токенов (экономия {comp_result['compression_rate']}%)"
                    )

            return assistant_message
        else:
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
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

        self.compression_history = []
        self._clear_compressed_summaries()

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

        self.compression_history = []
        self._clear_compressed_summaries()

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._update_session_tokens()
        print(f"✂️ История обрезана: удалено {removed} сообщений, оставлено {n}.")

    def _save_compression_mode(self):
        """Сохраняет режим сжатия текущей сессии в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET compression_enabled = ? WHERE id = ?",
                (int(self.compression_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["compression_enabled"] = self.compression_enabled

    def enable_compression(self):
        """Включает сжатие истории для текущей сессии."""
        self.compression_enabled = True
        self._save_compression_mode()
        print("✅ Сжатие истории включено")

    def disable_compression(self):
        """Выключает сжатие истории для текущей сессии."""
        self.compression_enabled = False
        self._save_compression_mode()
        print("✅ Сжатие истории выключено")

    def toggle_compression(self):
        """Переключает режим сжатия истории."""
        if self.compression_enabled:
            self.disable_compression()
        else:
            self.enable_compression()

    def compress_now(self) -> Optional[dict]:
        """Принудительное сжатие истории (без отправки сообщения в API)."""
        messages = [m for m in self.conversation_history if m["role"] != "system"]
        if len(messages) < self.compression_interval:
            print(f"ℹ️ В истории {len(messages)} сообщений, нужно минимум {self.compression_interval} для сжатия.")
            return None
        return self.compress_history()

    def get_raw_messages(self) -> list:
        """Возвращает сырую историю (без сжатия) для отправки в API."""
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend([m for m in self.conversation_history if m["role"] != "system"])
        return result

    def get_compressed_messages(self) -> list:
        """
        Собирает полную историю с учётом сжатых фрагментов.
        Возвращает историю, где каждые N сообщений заменены на их summary.
        """
        result = []
        
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        
        for item in self.compression_history:
            if item["type"] == "summary":
                result.append({"role": "system", "content": f"[АРХИВ: {item['content']}]"})
        
        result.extend([m for m in self.conversation_history if m["role"] != "system"])
        
        return result

    def _count_tokens(self, text: str) -> int:
        """Приближённый подсчёт токенов (1 токен ≈ 4 символа для English, ≈ 3 для Russian)."""
        return max(1, len(text) // 4)

    def compress_history(self) -> Optional[dict]:
        """
        Сжимает историю: берёт первые N сообщений, отправляет на суммаризацию,
        сохраняет summary в отдельную историю сжатия.
        Возвращает summary или None, если сжатие не требуется.
        """
        messages = [m for m in self.conversation_history if m["role"] != "system"]
        
        if len(messages) < self.compression_interval:
            return None
        
        to_compress = messages[:self.compression_interval]
        remaining = messages[self.compression_interval:]
        
        history_text = "\n\n".join([f"{m['role'].upper()}: {m['content']}" for m in to_compress])
        token_count_before = sum(self._count_tokens(m['content']) for m in to_compress)
        
        compress_prompt = (
            "Суммаризируй следующий фрагмент диалога в виде краткого описания основных тем и ключевых фактов. "
            "Сохрани важную информацию, но сократи объём минимум в 5 раз.\n\n"
            f"Диалог:\n{history_text}"
        )
        
        messages_for_compress = [
            {"role": "system", "content": "Ты — эксперт по суммаризации диалогов."},
            {"role": "user", "content": compress_prompt}
        ]
        
        response = self._call_api(messages_for_compress)
        
        if response["success"]:
            summary = response["content"]
            token_count_after = self._count_tokens(summary)
            
            item = {
                "type": "summary",
                "content": summary,
                "source_messages": self.compression_interval,
                "tokens_before": token_count_before,
                "tokens_after": token_count_after
            }
            self.compression_history.append(item)
            self._save_compressed_summary(summary, self.compression_interval, token_count_before, token_count_after)
            
            self.conversation_history = []
            if self.system_prompt:
                self.conversation_history.append({"role": "system", "content": self.system_prompt})
            self.conversation_history.extend(remaining)
            
            comp_rate = round((1 - token_count_after / token_count_before) * 100, 1) if token_count_before > 0 else 0
            
            print(f"📦 История сжата: {len(to_compress)} сообщений → summary (экономия {comp_rate}%, "
                  f"{token_count_before} → {token_count_after} токенов)")
            
            return {
                "summary": summary,
                "tokens_before": token_count_before,
                "tokens_after": token_count_after,
                "compression_rate": comp_rate
            }
        
        return None

    def get_stats(self) -> str:
        """Возвращает статистику использования агента."""
        msg_count = len([m for m in self.conversation_history if m["role"] != "system"])

        comp_status = "вкл" if self.compression_enabled else "выкл"
        lines = [
            "📊 Статистика агента:",
            f"  • Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})",
            f"  • Режим сжатия: {comp_status}",
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

        if self.compression_history:
            compressed = self.compression_history
            total_saved = sum(item["tokens_before"] - item["tokens_after"] for item in compressed)
            lines.extend([
                "",
                f"  📦 Сжатие истории ({len(compressed)} фрагментов):",
                f"     Общая экономия: {total_saved} токенов",
            ])
            for i, item in enumerate(compressed):
                lines.append(
                    f"     Фрагмент {i+1}: {item['source_messages']} сообщений → {item['tokens_after']} токенов "
                    f"(экономия {item['tokens_before'] - item['tokens_after']} токенов)"
                )

        return "\n".join(lines)
