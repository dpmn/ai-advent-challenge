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
    - Стратегии управления контекстом:
      • sliding_window — только последние 5 сообщений
      • sticky_facts — ключевые факты + последние 5 сообщений
      • branching — ветки диалога от checkpoint
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

        self._init_db()

        self.compression_enabled = compression_enabled if compression_enabled is not None else True

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

        if compression_enabled is None:
            saved = self.current_session.get("compression_enabled")
            if saved is not None:
                self.compression_enabled = bool(saved)

        # ─── Стратегии управления контекстом ──────────────────────
        saved_strategy = self.current_session.get("context_strategy")
        self.context_strategy: Optional[str] = saved_strategy if saved_strategy else None
        # sticky_facts for key-value memory
        saved_facts = self.current_session.get("sticky_facts")
        self.sticky_facts: dict = json.loads(saved_facts) if saved_facts and saved_facts != "{}" else {}
        # branching state
        self.branches: dict = {}  # {branch_id: {"name": str, "messages": [msg, ...]}}
        self.current_branch_id: int = 0
        self._next_branch_id: int = 1
        self.checkpoint_index: Optional[int] = None

        self.conversation_history = self._load_messages()

        # Если стратегия branching, но ветки не инициализированы (загрузка из БД) — инициализируем
        if self.context_strategy == "branching" and not self.branches:
            self._init_branches()

        self.total_tokens_used = 0
        self.total_requests = 0
        self.last_usage: Optional[dict] = None

        self.compression_interval = 5
        self.compression_history: list = self._load_compressed_summaries()
        self.session_prompt_tokens = self.current_session.get("prompt_tokens", 0)
        self.session_completion_tokens = self.current_session.get("completion_tokens", 0)
        self.session_total_tokens = self.current_session.get("total_tokens", 0)

    # ─────────────── БД: инициализация ────────────────────────────

    def _init_db(self):
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS branches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    parent_branch_id INTEGER DEFAULT 0,
                    checkpoint_message_index INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)
            for col in ("prompt_tokens", "completion_tokens", "total_tokens"):
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
            for col_migration in [
                ("compression_enabled", "INTEGER DEFAULT 1"),
                ("context_strategy", "TEXT DEFAULT NULL"),
                ("sticky_facts", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_migration[0]} {col_migration[1]}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    # ─────────────── Сессии ───────────────────────────────────────

    def _get_last_session(self) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts "
                "FROM sessions ORDER BY last_active_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6], "context_strategy": row[7], "sticky_facts": row[8]
                }
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts "
                "FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6], "context_strategy": row[7], "sticky_facts": row[8]
                }
            return None

    def _load_messages(self) -> list:
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

    def _delete_old_messages(self, keep_count: int):
        """Удаляет все сообщения из БД, кроме последних keep_count (для sliding window)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM messages WHERE id NOT IN (
                    SELECT id FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ) AND session_id = ?
            """, (self.current_session["id"], keep_count, self.current_session["id"]))
            conn.commit()

    def _update_session_tokens(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET prompt_tokens = ?, completion_tokens = ?, total_tokens = ? WHERE id = ?",
                (self.session_prompt_tokens, self.session_completion_tokens, self.session_total_tokens, self.current_session["id"])
            )
            conn.commit()

    def create_session(self, name: Optional[str] = None) -> dict:
        if not name:
            name = f"Сессия от {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (name, compression_enabled) VALUES (?, ?)",
                (name, int(self.compression_enabled))
            )
            session_id = cursor.lastrowid
            conn.commit()

        session = {
            "id": session_id, "name": name, "created_at": datetime.now().isoformat(),
            "compression_enabled": self.compression_enabled,
            "context_strategy": None, "sticky_facts": "{}"
        }
        self.current_session = session
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})

        self.compression_history = []
        self.context_strategy = None
        self.sticky_facts = {}
        self.branches = {}
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

        print(f"✨ Создана новая сессия: {name} (ID: {session_id})")
        return session

    def switch_session(self, session_id: int) -> bool:
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

        # Загружаем стратегию
        saved_strategy = session.get("context_strategy")
        self.context_strategy = saved_strategy if saved_strategy else None
        saved_facts = session.get("sticky_facts")
        self.sticky_facts = json.loads(saved_facts) if saved_facts and saved_facts != "{}" else {}
        self.branches = {}
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None

        if self.context_strategy == "branching" and not self.branches:
            self._init_branches()

        status = "вкл" if self.compression_enabled else "выкл"
        strat = f" | Стратегия: {self.context_strategy}" if self.context_strategy else ""
        print(f"🔄 Переключено на сессию: {session['name']} (ID: {session_id}) | Сжатие: {status}{strat}")
        return True

    def list_sessions(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, last_active_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy "
                "FROM sessions ORDER BY last_active_at DESC"
            )
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "last_active": row[3],
                 "prompt_tokens": row[4], "completion_tokens": row[5], "total_tokens": row[6],
                 "compression_enabled": row[7], "context_strategy": row[8]}
                for row in cursor.fetchall()
            ]

    def delete_session(self, session_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            if cursor.rowcount > 0:
                if self.current_session["id"] == session_id:
                    last = self._get_last_session()
                    if last:
                        self.switch_session(last["id"])
                    else:
                        self.create_session()
                return True
        return False

    # ─────────────── API ──────────────────────────────────────────

    def _build_messages(self, user_input: str) -> list:
        self.conversation_history.append({"role": "user", "content": user_input})
        return self.conversation_history

    def _call_api(self, messages: list) -> dict:
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

    # ═══════════════════════════════════════════════════════════════
    # СТРАТЕГИИ УПРАВЛЕНИЯ КОНТЕКСТОМ
    # ═══════════════════════════════════════════════════════════════

    def _save_strategy_state(self):
        """Сохраняет стратегию и sticky_facts в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET context_strategy = ?, sticky_facts = ? WHERE id = ?",
                (self.context_strategy, json.dumps(self.sticky_facts, ensure_ascii=False),
                 self.current_session["id"])
            )
            conn.commit()
        self.current_session["context_strategy"] = self.context_strategy
        self.current_session["sticky_facts"] = json.dumps(self.sticky_facts, ensure_ascii=False)

    def set_strategy(self, strategy: Optional[str]) -> str:
        """
        Устанавливает стратегию управления контекстом.
        strategy: None / "sliding_window" / "sticky_facts" / "branching"
        При установке стратегии сжатие автоматически отключается.
        """
        valid = {None, "sliding_window", "sticky_facts", "branching"}
        if strategy not in valid:
            return f"❌ Неизвестная стратегия. Допустимые: {[s for s in valid if s is not None]}"

        old_strategy = self.context_strategy
        self.context_strategy = strategy

        # Отключаем сжатие при включении стратегии
        if strategy is not None and self.compression_enabled:
            self.disable_compression()

        # При переходе на sticky_facts — извлекаем факты из истории
        if strategy == "sticky_facts" and old_strategy != "sticky_facts":
            if not self.sticky_facts:
                self._extract_facts_initial()

        # При переходе на branching — сбрасываем ветки
        if strategy == "branching" and old_strategy != "branching":
            self._init_branches()

        # При выключении стратегии — чистим ветки
        if strategy is None and old_strategy == "branching":
            self.branches = {}
            self.current_branch_id = 0
            self._next_branch_id = 1
            self.checkpoint_index = None

        self._save_strategy_state()

        strat_name = strategy if strategy else "выкл"
        print(f"🔀 Стратегия управления контекстом: {strat_name}")
        return f"✅ Стратегия установлена: {strat_name}"

    # ── Sliding Window ────────────────────────────────────────────

    def _get_sliding_window_messages(self, window_size: int = 5) -> list:
        """Возвращает только последние window_size сообщений."""
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        # После добавления user-сообщения в chat() — берём последние N
        result.extend(non_system[-window_size:])
        return result

    def _apply_sliding_window(self, window_size: int = 5):
        """Обрезает conversation_history и БД до последних window_size сообщений."""
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        if len(non_system) <= window_size:
            return

        # Оставляем последние window_size
        kept = non_system[-window_size:]
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})
        self.conversation_history.extend(kept)

        # Синхронизируем БД
        self._delete_old_messages(window_size)

    # ── Sticky Facts ──────────────────────────────────────────────

    def _get_sticky_facts_messages(self, window_size: int = 5) -> list:
        """Возвращает факты + последние window_size сообщений."""
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        if self.sticky_facts:
            facts_lines = []
            for k, v in self.sticky_facts.items():
                facts_lines.append(f"  • {k}: {v}")
            facts_str = "📌 Ключевые факты диалога:\n" + "\n".join(facts_lines)
            result.append({"role": "system", "content": facts_str})
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        result.extend(non_system[-window_size:])
        return result

    def _extract_facts_initial(self):
        """Извлекает факты из всей имеющейся истории при первом включении sticky_facts."""
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        if len(non_system) < 2:
            return

        dialog_text = "\n\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in non_system
        )
        prompt = (
            "Извлеки ключевые факты из диалога ниже. "
            "Верни результат в формате JSON: {\"факты\": [\"факт1\", \"факт2\", ...]}. "
            "Только JSON, без пояснений.\n\nДиалог:\n" + dialog_text
        )

        fact_messages = [
            {"role": "system", "content": "Ты — анализатор фактов. Отвечай только JSON."},
            {"role": "user", "content": prompt}
        ]
        response = self._call_api(fact_messages)
        if response["success"]:
            self._parse_and_store_facts(response["content"])

    def _extract_facts(self, current_user_msg: str, current_assistant_msg: str):
        """Обновляет sticky_facts на основе последнего обмена."""
        old_facts_str = json.dumps(self.sticky_facts, ensure_ascii=False, indent=2)

        prompt = (
            "У меня есть текущие факты диалога:\n"
            f"{old_facts_str}\n\n"
            "Последний обмен:\n"
            f"USER: {current_user_msg}\n"
            f"ASSISTANT: {current_assistant_msg}\n\n"
            "Обнови список фактов: добавь новые важные сведения, "
            "удали устаревшие, обобщи при необходимости. "
            "Факты — это любые важные данные: цель, ограничения, "
            "предпочтения, принятые решения, договорённости, имена, сроки. "
            "Верни результат в формате JSON: {\"факты\": [\"факт1\", \"факт2\", ...]}. "
            "Только JSON, без пояснений."
        )

        fact_messages = [
            {"role": "system", "content": "Ты — анализатор фактов. Отвечай только JSON."},
            {"role": "user", "content": prompt}
        ]
        response = self._call_api(fact_messages)
        if response["success"]:
            self._parse_and_store_facts(response["content"])
            self._save_strategy_state()

    def _parse_and_store_facts(self, raw: str):
        """Парсит JSON-ответ и обновляет self.sticky_facts."""
        try:
            # Ищем JSON в ответе
            json_start = raw.find('{')
            json_end = raw.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                raw = raw[json_start:json_end]
            data = json.loads(raw)
            facts_list = data.get("факты", data.get("facts", []))
            if facts_list:
                new_facts = {}
                for i, fact in enumerate(facts_list):
                    key = f"Факт {i + 1}"
                    new_facts[key] = fact
                self.sticky_facts = new_facts
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Branching ─────────────────────────────────────────────────

    def _init_branches(self):
        """Инициализирует ветвление: создаёт главную ветку (branch 0)."""
        self.branches = {
            0: {
                "name": "main",
                "messages": self.conversation_history.copy() if self.conversation_history else [],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        }
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None
        # Сбрасываем сессионные счётчики — каждый branch считает сам
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._update_session_tokens()
        # Восстанавливаем историю из главной ветки
        if 0 in self.branches:
            self.conversation_history = self.branches[0]["messages"].copy()

    def _get_branching_messages(self) -> list:
        """Возвращает сообщения текущей ветки."""
        return self.conversation_history

    def save_checkpoint(self) -> str:
        """Сохраняет checkpoint в текущей ветке."""
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен. Используйте: /strategy branching"

        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        self.checkpoint_index = len(non_system)
        print(f"📍 Checkpoint сохранён на сообщении #{self.checkpoint_index} в ветке '{self.branches[self.current_branch_id]['name']}'")
        return f"✅ Checkpoint сохранён. Следующая ветка начнётся отсюда (сообщение #{self.checkpoint_index})."

    def create_branch(self, name: str) -> str:
        """Создаёт новую ветку от checkpoint."""
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен."
        if self.checkpoint_index is None:
            return "❌ Сначала сохраните checkpoint: /checkpoint"
        if any(b["name"] == name for b in self.branches.values()):
            return f"❌ Ветка с именем '{name}' уже существует. Используйте другое имя."

        # Сохраняем текущую ветку
        self.branches[self.current_branch_id]["messages"] = self.conversation_history.copy()

        # Создаём новую ветку с историей до checkpoint
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        checkpoint_msgs = non_system[:self.checkpoint_index]

        branch_id = self._next_branch_id
        self._next_branch_id += 1

        new_history = []
        if self.system_prompt:
            new_history.append({"role": "system", "content": self.system_prompt})
        new_history.extend(checkpoint_msgs)

        self.branches[branch_id] = {
            "name": name,
            "messages": new_history,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        # Переключаемся на новую ветку
        self.current_branch_id = branch_id
        self.conversation_history = new_history.copy()

        # Сохраняем в БД информацию о ветке
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO branches (session_id, name, parent_branch_id, checkpoint_message_index) "
                "VALUES (?, ?, ?, ?)",
                (self.current_session["id"], name, self.current_branch_id, self.checkpoint_index)
            )
            conn.commit()

        print(f"🌿 Создана ветка '{name}' (ID: {branch_id}) от checkpoint #{self.checkpoint_index}")
        return f"✅ Ветка '{name}' создана. Вы переключены на неё."

    def list_branches(self) -> list:
        """Возвращает список всех веток."""
        if self.context_strategy != "branching":
            return []
        result = []
        for bid, branch in self.branches.items():
            msg_count = len([m for m in branch["messages"] if m["role"] != "system"])
            current = " 👈 (текущая)" if bid == self.current_branch_id else ""
            result.append({
                "id": bid,
                "name": branch["name"],
                "messages": msg_count,
                "current": current
            })
        return result

    def switch_branch(self, branch_id: int) -> str:
        """Переключается на другую ветку."""
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен."
        if branch_id not in self.branches:
            return f"❌ Ветка с ID {branch_id} не найдена. Используйте: /branches"

        # Сохраняем текущую ветку (сообщения + токены)
        cur = self.branches[self.current_branch_id]
        cur["messages"] = self.conversation_history.copy()
        cur["prompt_tokens"] = self.session_prompt_tokens
        cur["completion_tokens"] = self.session_completion_tokens
        cur["total_tokens"] = self.session_total_tokens

        # Загружаем новую ветку
        self.current_branch_id = branch_id
        nxt = self.branches[branch_id]
        self.conversation_history = nxt["messages"].copy()
        self.session_prompt_tokens = nxt["prompt_tokens"]
        self.session_completion_tokens = nxt["completion_tokens"]
        self.session_total_tokens = nxt["total_tokens"]
        self._update_session_tokens()

        branch_name = nxt["name"]
        print(f"🔄 Переключено на ветку '{branch_name}' (ID: {branch_id}) | токенов: {self.session_total_tokens}")
        return f"✅ Переключено на ветку '{branch_name}'."

    # ── Команды ───────────────────────────────────────────────────

    def _handle_command(self, cmd_input: str) -> str:
        parts = cmd_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "/help":
            return (
                "📋 Доступные команды:\n\n"
                "  /help         — показать этот список\n"
                "  /stats        — статистика агента\n"
                "  /sessions     — список сессий\n"
                "  /session <id> — переключиться на сессию\n"
                "  /new [name]   — создать новую сессию\n"
                "  /clear        — очистить историю\n"
                "  /model [name] — показать/сменить модель\n"
                "  /temp [value] — показать/сменить температуру\n"
                "  /strategy [type] — показать/сменить стратегию\n"
                "  /compression [on|off|toggle] — управление сжатием\n"
                "  /context      — информация о контексте\n"
                "  /checkpoint   — сохранить checkpoint (branching)\n"
                "  /branch <name> — создать ветку\n"
                "  /branches     — список веток\n"
                "  /switch <id>  — переключить ветку"
            )

        if cmd == "/stats":
            return self.get_stats()

        if cmd == "/sessions":
            sessions = self.list_sessions()
            if not sessions:
                return "Нет сессий."
            lines = ["📂 Сессии:"]
            for s in sessions:
                marker = " 👈" if s["id"] == self.current_session["id"] else ""
                strategy = f" | {s['context_strategy']}" if s.get("context_strategy") else ""
                lines.append(f"  ID {s['id']}: {s['name']}{marker}{strategy}")
            return "\n".join(lines)

        if cmd == "/session":
            if not arg:
                return f"Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})"
            try:
                sid = int(arg.strip())
                if self.switch_session(sid):
                    return f"✅ Переключено на сессию: {self.current_session['name']}"
                return f"❌ Сессия с ID {sid} не найдена."
            except ValueError:
                return "❌ Укажите числовой ID сессии."

        if cmd == "/new":
            name = arg.strip() if arg else None
            self.create_session(name)
            return f"✅ Создана новая сессия: {self.current_session['name']}"

        if cmd == "/clear":
            self.reset_conversation()
            return "✅ История диалога очищена."

        if cmd == "/model":
            if not arg:
                return f"Текущая модель: {self.model}"
            new_model = arg.strip()
            self.model = new_model
            return f"✅ Модель изменена: {new_model}"

        if cmd == "/temp":
            if not arg:
                return f"Текущая температура: {self.temperature}"
            try:
                val = float(arg.strip())
                if 0 <= val <= 2:
                    self.temperature = val
                    return f"✅ Температура изменена: {val}"
                return "❌ Температура должна быть от 0 до 2."
            except ValueError:
                return "❌ Укажите числовое значение (0.0 – 2.0)."

        if cmd == "/strategy":
            if not arg:
                cur = self.context_strategy or "выкл"
                return f"Текущая стратегия: {cur}"
            strategy_map = {
                "off": None, "выкл": None,
                "sliding_window": "sliding_window", "sliding": "sliding_window",
                "sticky_facts": "sticky_facts", "sticky": "sticky_facts",
                "branching": "branching", "branch": "branching",
            }
            arg_lower = arg.strip().lower()
            if arg_lower in strategy_map:
                return self.set_strategy(strategy_map[arg_lower])
            return "❌ Допустимые стратегии: off, sliding_window, sticky_facts, branching"

        if cmd == "/compression":
            if not arg:
                status = "вкл" if self.compression_enabled else "выкл"
                return f"Сжатие истории: {status}"
            arg_lower = arg.strip().lower()
            if arg_lower in ("on", "вкл", "1"):
                self.enable_compression()
                return "✅ Сжатие истории включено"
            if arg_lower in ("off", "выкл", "0"):
                self.disable_compression()
                return "✅ Сжатие истории выключено"
            if arg_lower in ("toggle", "switch"):
                self.toggle_compression()
                status = "вкл" if self.compression_enabled else "выкл"
                return f"✅ Сжатие истории: {status}"
            return "❌ Используйте: /compression [on|off|toggle]"

        if cmd == "/context":
            lines = ["📐 Контекст диалога:"]
            total = self.session_prompt_tokens + self.session_completion_tokens
            pct = round(total / self.context_limit * 100, 1) if self.context_limit > 0 else 0
            lines.append(f"  Токенов в сессии: {total} / {self.context_limit} ({pct}%)")
            non_system = [m for m in self.conversation_history if m["role"] != "system"]
            lines.append(f"  Сообщений: {len(non_system)}")
            lines.append(f"  Модель: {self.model}")
            lines.append(f"  Стратегия: {self.context_strategy or 'выкл'}")
            lines.append(f"  Сжатие: {'вкл' if self.compression_enabled else 'выкл'}")
            if self.context_strategy == "sliding_window":
                lines.append("  Окно: последние 5 сообщений")
            elif self.context_strategy == "sticky_facts":
                lines.append(f"  Фактов: {len(self.sticky_facts)}")
            elif self.context_strategy == "branching":
                lines.append(f"  Веток: {len(self.branches)}")
                cur = self.branches.get(self.current_branch_id, {})
                lines.append(f"  Текущая ветка: '{cur.get('name', '?')}'")
            return "\n".join(lines)

        if cmd == "/checkpoint":
            return self.save_checkpoint()

        if cmd == "/branch":
            if not arg:
                return "❌ Укажите имя ветки: /branch <name>"
            return self.create_branch(arg.strip())

        if cmd == "/branches":
            branches = self.list_branches()
            if not branches:
                return "Ветвление не активно. Используйте: /strategy branching"
            lines = ["🌿 Ветки:"]
            for b in branches:
                lines.append(f"  ID {b['id']}: '{b['name']}' — {b['messages']} сообщений{b['current']}")
            return "\n".join(lines)

        if cmd == "/switch":
            if not arg:
                return "❌ Укажите ID ветки: /switch <id>"
            try:
                bid = int(arg.strip())
                return self.switch_branch(bid)
            except ValueError:
                return "❌ Укажите числовой ID ветки."

        return f"❌ Неизвестная команда: {cmd}. Используйте /help для списка команд."

    # ── Основной метод ────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """Основной метод агента: принимает запрос и возвращает ответ."""
        if not user_input or not user_input.strip():
            return "Пожалуйста, введите ваш запрос."

        self.conversation_history.append({"role": "user", "content": user_input})
        self._save_message("user", user_input)

        # ── Команды ──────────────────────────────────────────
        if user_input.startswith("/"):
            resp = self._handle_command(user_input)
            if resp:
                self.conversation_history.append({"role": "command", "content": resp})
                self._save_message("command", resp)
            return resp

        # ── Обычный чат ──────────────────────────────────────

        # Выбираем стратегию построения сообщений
        if self.context_strategy == "sliding_window":
            messages = self._get_sliding_window_messages()
        elif self.context_strategy == "sticky_facts":
            messages = self._get_sticky_facts_messages()
        elif self.context_strategy == "branching":
            messages = self._get_branching_messages()
        else:
            if self.compression_enabled:
                messages = self.get_compressed_messages()
            else:
                messages = self.get_raw_messages()

        tokens_before = sum(self._count_tokens(m["content"]) for m in messages if m["role"] != "system")

        response = self._call_api(messages)

        if response["success"]:
            assistant_message = response["content"]
            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            self._save_message("assistant", assistant_message)

            usage = response.get("usage", {})
            self.last_usage = usage
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            tt = usage.get("total_tokens", 0)

            if self.context_strategy == "branching":
                branch = self.branches[self.current_branch_id]
                branch["prompt_tokens"] += pt
                branch["completion_tokens"] += ct
                branch["total_tokens"] += tt
                self.session_prompt_tokens = branch["prompt_tokens"]
                self.session_completion_tokens = branch["completion_tokens"]
                self.session_total_tokens = branch["total_tokens"]
            else:
                self.session_prompt_tokens += pt
                self.session_completion_tokens += ct
                self.session_total_tokens += tt
            self._update_session_tokens()

            total = self.session_prompt_tokens + self.session_completion_tokens
            if total > self.context_limit * 0.8:
                pct = round(total / self.context_limit * 100, 1)
                assistant_message += (
                    f"\n\n⚠️ Внимание: контекст диалога заполнен на {pct}% "
                    f"(~{total} токенов из {self.context_limit}). Рекомендуется начать новую сессию."
                )

            # Автосжатие (только без стратегии)
            if not self.context_strategy and self.compression_enabled:
                non_system = [m for m in self.conversation_history if m["role"] != "system"]
                if len(non_system) % self.compression_interval == 0:
                    comp_result = self.compress_history()
                    if comp_result:
                        assistant_message += (
                            f"\n\n📦 История сжата: {comp_result['tokens_before']} → {comp_result['tokens_after']} "
                            f"токенов (экономия {comp_result['compression_rate']}%)"
                        )

            # Обновление фактов после ответа (sticky_facts)
            if self.context_strategy == "sticky_facts":
                self._extract_facts(user_input, assistant_message)

            # Обрезка истории (sliding_window)
            if self.context_strategy == "sliding_window":
                self._apply_sliding_window()

            return assistant_message
        else:
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            return f"❌ Ошибка: {response.get('error', 'Неизвестная ошибка')}\n{response.get('details', '')}"

    # ─────────────── Управление историей ──────────────────────────

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

        # Сброс стратегий
        self.sticky_facts = {}
        self.branches = {}
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None
        if self.context_strategy:
            self._save_strategy_state()

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

    # ─────────────── Сжатие истории (компрессия) ──────────────────

    def _save_compression_mode(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET compression_enabled = ? WHERE id = ?",
                (int(self.compression_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["compression_enabled"] = self.compression_enabled

    def enable_compression(self):
        self.compression_enabled = True
        self._save_compression_mode()
        print("✅ Сжатие истории включено")

    def disable_compression(self):
        self.compression_enabled = False
        self._save_compression_mode()
        print("✅ Сжатие истории выключено")

    def toggle_compression(self):
        if self.compression_enabled:
            self.disable_compression()
        else:
            self.enable_compression()

    def compress_now(self) -> Optional[dict]:
        messages = [m for m in self.conversation_history if m["role"] != "system"]
        if len(messages) < self.compression_interval:
            print(f"ℹ️ В истории {len(messages)} сообщений, нужно минимум {self.compression_interval} для сжатия.")
            return None
        return self.compress_history()

    def get_raw_messages(self) -> list:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend([m for m in self.conversation_history if m["role"] != "system"])
        return result

    def get_compressed_messages(self) -> list:
        result = []

        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})

        for item in self.compression_history:
            if item["type"] == "summary":
                result.append({"role": "system", "content": f"[АРХИВ: {item['content']}]"})

        result.extend([m for m in self.conversation_history if m["role"] != "system"])

        return result

    def _count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def compress_history(self) -> Optional[dict]:
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

    def _load_compressed_summaries(self) -> list:
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO compressed_summaries (session_id, content, source_count, tokens_before, tokens_after) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.current_session["id"], content, source_count, tokens_before, tokens_after)
            )
            conn.commit()

    def _clear_compressed_summaries(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM compressed_summaries WHERE session_id = ?",
                (self.current_session["id"],)
            )
            conn.commit()

    # ─────────────── Статистика ───────────────────────────────────

    def get_stats(self) -> str:
        msg_count = len([m for m in self.conversation_history if m["role"] != "system"])

        comp_status = "вкл" if self.compression_enabled else "выкл"
        strat_name = self.context_strategy if self.context_strategy else "выкл"
        lines = [
            "📊 Статистика агента:",
            f"  • Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})",
            f"  • Стратегия контекста: {strat_name}",
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

        if self.context_strategy == "sliding_window":
            lines.extend([
                "",
                "  🪟 Sliding Window: активен",
                f"     Окно: последние 5 сообщений",
            ])

        if self.context_strategy == "sticky_facts":
            lines.extend([
                "",
                f"  📌 Sticky Facts: {len(self.sticky_facts)} фактов",
            ])
            for k, v in self.sticky_facts.items():
                lines.append(f"     {k}: {v[:80]}...")

        if self.context_strategy == "branching":
            cur = self.branches.get(self.current_branch_id, {})
            lines.extend([
                "",
                f"  🌿 Ветвление: {len(self.branches)} веток",
                f"     Текущая ветка: '{cur.get('name', '?')}' (ID: {self.current_branch_id})",
                f"     Токены ветки: prompt {cur.get('prompt_tokens', 0)} + completion {cur.get('completion_tokens', 0)} = {cur.get('total_tokens', 0)}",
            ])
            for bid, branch in self.branches.items():
                marker = " 👈" if bid == self.current_branch_id else ""
                lines.append(
                    f"       ID {bid}: '{branch['name']}' — "
                    f"msgs {len([m for m in branch['messages'] if m['role'] != 'system'])}, "
                    f"tokens {branch['total_tokens']}{marker}"
                )

        return "\n".join(lines)
