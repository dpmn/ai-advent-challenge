import os
import json
import urllib.request
import urllib.error
import sqlite3
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agents.state_machine import PipelineAgent
from agents.invariants import InvariantManager, AgentValidator, ForbiddenLibrariesInvariant, RequiredTechStackInvariant
from agents.mcp_manager import McpServerManager

load_dotenv()

_AGENTS_DIR = Path(__file__).parent.resolve()
_DEFAULT_DB_PATH = str(_AGENTS_DIR / "memory" / "jarvis_history.db")
_PROFILES_DIR = _AGENTS_DIR / "memory" / "profiles"
_INVARIANTS_DIR = _AGENTS_DIR / "memory" / "invariants"


class TaskContext:
    """
    Рабочая память (Working Memory) — данные текущей задачи.
    Хранится в оперативной памяти, может быть сериализована/загружена.
    """

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def clear(self):
        self._data.clear()

    def to_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["📋 Текущая задача (TaskContext):"]
        for k, v in self._data.items():
            lines.append(f"  • {k}: {v}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return dict(self._data)

    def load_dict(self, data: dict):
        self._data = dict(data)

    def remove(self, key: str):
        self._data.pop(key, None)

    def keys(self):
        return self._data.keys()


class Profile:
    """
    Долговременная память (Long-term Memory) — профиль пользователя,
    решения, предпочтения, знания. Хранится в виде markdown файлов
    в agents/memory/profiles/.
    """

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self._data: Dict[str, str] = {}
        self._profiles_dir = _PROFILES_DIR
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _file_path(self) -> Path:
        return self._profiles_dir / f"{self.profile_name}.md"

    def _load(self):
        path = self._file_path()
        if path.exists():
            content = path.read_text(encoding="utf-8")
            current_key = None
            current_value = []
            for line in content.split("\n"):
                if line.startswith("## "):
                    if current_key:
                        self._data[current_key] = "\n".join(current_value).strip()
                    current_key = line[3:].strip()
                    current_value = []
                elif current_key:
                    current_value.append(line)
            if current_key:
                self._data[current_key] = "\n".join(current_value).strip()

    def save(self):
        path = self._file_path()
        lines = [f"# Profile: {self.profile_name}"]
        for k, v in self._data.items():
            lines.append(f"\n## {k}")
            lines.append(v)
        path.write_text("\n".join(lines), encoding="utf-8")

    def set(self, key: str, value: str):
        self._data[key] = value
        self.save()

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def to_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["👤 Профиль пользователя (Profile):"]
        for k, v in self._data.items():
            first_line = v.split("\n")[0] if v else ""
            lines.append(f"  • {k}: {first_line[:120]}")
        return "\n".join(lines)

    def to_full_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["👤 Профиль пользователя:"]
        for k, v in self._data.items():
            lines.append(f"\n[{k}]\n{v}")
        return "\n".join(lines)

    def list_profiles(self) -> list:
        return sorted(
            p.stem for p in self._profiles_dir.glob("*.md")
        ) or ["default"]


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
    - Трёхуровневую модель памяти:
      • Short-term — текущий диалог (сессия)
      • Working — TaskContext (данные текущей задачи)
      • Long-term — Profile (профиль, предпочтения, знания)
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

        self._invariants_dir = _INVARIANTS_DIR
        self._invariants_dir.mkdir(parents=True, exist_ok=True)

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

        # ─── Трёхуровневая модель памяти ──────────────────────
        self.task_context = TaskContext()
        saved_task = self.current_session.get("task_context")
        if saved_task:
            try:
                self.task_context.load_dict(json.loads(saved_task))
            except (json.JSONDecodeError, TypeError):
                pass

        profile_name = self.current_session.get("profile_name", "default")
        if not profile_name:
            profile_name = "default"
        self.profile = Profile(profile_name)

        # ─── Инварианты ───────────────────────────────────────
        self._invariants: list = []
        self._validator: Optional[AgentValidator] = None
        self.invariants_enabled = bool(self.current_session.get("invariants_enabled", True))
        self._load_invariants()

        # ─── State Machine ────────────────────────────────────
        self.pipeline = None
        sm_enabled = self.current_session.get("sm_enabled", False)
        if sm_enabled:
            sm_validation = self.current_session.get("sm_validation_enabled", True)
            sm_current_state = self.current_session.get("sm_current_state", "PLANNING")
            sm_artifacts_raw = self.current_session.get("sm_artifacts", "{}")
            sm_stage_configs_raw = self.current_session.get("sm_stage_configs", "{}")
            try:
                sm_artifacts = json.loads(sm_artifacts_raw) if sm_artifacts_raw else {}
            except (json.JSONDecodeError, TypeError):
                sm_artifacts = {}
            try:
                sm_stage_configs = json.loads(sm_stage_configs_raw) if sm_stage_configs_raw else {}
            except (json.JSONDecodeError, TypeError):
                sm_stage_configs = {}
            self.pipeline = PipelineAgent(
                session_id=self.current_session["id"],
                api_key=self.api_key,
                base_url=self.base_url,
                db_path=self.db_path,
                current_state=sm_current_state,
                artifacts=sm_artifacts,
                validation_enabled=sm_validation,
                stage_configs=sm_stage_configs,
            )

        # ─── MCP ──────────────────────────────────────────────
        # Менеджер MCP-серверов общий на весь агент; флаг mcp_enabled
        # хранится в сессии и определяет, передавать ли tools в API.
        self.mcp_manager = McpServerManager()
        self.mcp_enabled = bool(self.current_session.get("mcp_enabled", False))
        self.mcp_max_iterations = 10

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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stage_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                ("task_context", "TEXT DEFAULT '{}'"),
                ("profile_name", "TEXT DEFAULT 'default'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_migration[0]} {col_migration[1]}")
                except sqlite3.OperationalError:
                    pass
            for sm_col in [
                ("sm_enabled", "INTEGER DEFAULT 0"),
                ("sm_validation_enabled", "INTEGER DEFAULT 1"),
                ("sm_current_state", "TEXT DEFAULT 'PLANNING'"),
                ("sm_artifacts", "TEXT DEFAULT '{}'"),
                ("sm_stage_configs", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {sm_col[0]} {sm_col[1]}")
                except sqlite3.OperationalError:
                    pass
            for inv_col in [
                ("invariants_enabled", "INTEGER DEFAULT 1"),
                ("invariants_config", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {inv_col[0]} {inv_col[1]}")
                except sqlite3.OperationalError:
                    pass
            for mcp_col in [
                ("mcp_enabled", "INTEGER DEFAULT 0"),
                ("mcp_config", "TEXT DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {mcp_col[0]} {mcp_col[1]}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    # ─────────────── Сессии ───────────────────────────────────────

    def _get_last_session(self) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts, task_context, profile_name, "
                "sm_enabled, sm_validation_enabled, sm_current_state, sm_artifacts, sm_stage_configs, "
                "invariants_enabled, invariants_config, mcp_enabled, mcp_config "
                "FROM sessions ORDER BY last_active_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6], "context_strategy": row[7], "sticky_facts": row[8],
                    "task_context": row[9], "profile_name": row[10],
                    "sm_enabled": row[11], "sm_validation_enabled": row[12],
                    "sm_current_state": row[13], "sm_artifacts": row[14], "sm_stage_configs": row[15],
                    "invariants_enabled": row[16], "invariants_config": row[17],
                    "mcp_enabled": row[18], "mcp_config": row[19],
                }
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts, task_context, profile_name, "
                "sm_enabled, sm_validation_enabled, sm_current_state, sm_artifacts, sm_stage_configs, "
                "invariants_enabled, invariants_config, mcp_enabled, mcp_config "
                "FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "created_at": row[2],
                    "prompt_tokens": row[3], "completion_tokens": row[4], "total_tokens": row[5],
                    "compression_enabled": row[6], "context_strategy": row[7], "sticky_facts": row[8],
                    "task_context": row[9], "profile_name": row[10],
                    "sm_enabled": row[11], "sm_validation_enabled": row[12],
                    "sm_current_state": row[13], "sm_artifacts": row[14], "sm_stage_configs": row[15],
                    "invariants_enabled": row[16], "invariants_config": row[17],
                    "mcp_enabled": row[18], "mcp_config": row[19],
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

    def create_session(self, name: Optional[str] = None, sm_enabled: bool = False) -> dict:
        if not name:
            name = f"Сессия от {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (name, compression_enabled, sm_enabled) VALUES (?, ?, ?)",
                (name, int(self.compression_enabled), int(sm_enabled))
            )
            session_id = cursor.lastrowid
            conn.commit()

        session = {
            "id": session_id, "name": name, "created_at": datetime.now().isoformat(),
            "compression_enabled": self.compression_enabled,
            "context_strategy": None, "sticky_facts": "{}",
            "task_context": "{}", "profile_name": "default",
            "sm_enabled": int(sm_enabled), "sm_validation_enabled": 1,
            "sm_current_state": "PLANNING", "sm_artifacts": "{}", "sm_stage_configs": "{}",
            "invariants_enabled": 1, "invariants_config": "{}",
            "mcp_enabled": 0, "mcp_config": "{}",
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

        self.task_context = TaskContext()
        self.profile = Profile("default")

        self.invariants_enabled = bool(self.current_session.get("invariants_enabled", True))
        self._load_invariants()

        # MCP: новая сессия по умолчанию выключена
        self.mcp_enabled = False

        self.pipeline = None
        if sm_enabled:
            sm_validation = True
            self.pipeline = PipelineAgent(
                session_id=session_id,
                api_key=self.api_key,
                base_url=self.base_url,
                db_path=self.db_path,
                current_state="PLANNING",
                validation_enabled=sm_validation,
            )

        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0

        print(f"✨ Создана новая сессия: {name} (ID: {session_id})"
              f"{' [SM]' if sm_enabled else ''}")
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

        # Восстанавливаем трёхуровневую память
        saved_task = session.get("task_context")
        self.task_context = TaskContext()
        if saved_task:
            try:
                self.task_context.load_dict(json.loads(saved_task))
            except (json.JSONDecodeError, TypeError):
                pass
        profile_name = session.get("profile_name", "default") or "default"
        self.profile = Profile(profile_name)

        self.invariants_enabled = bool(session.get("invariants_enabled", True))
        self._load_invariants()

        # MCP
        self.mcp_enabled = bool(session.get("mcp_enabled", False))

        # ─── State Machine ────────────────────────────────
        sm_enabled = session.get("sm_enabled", False)
        if sm_enabled:
            sm_validation = session.get("sm_validation_enabled", True)
            sm_current_state = session.get("sm_current_state", "PLANNING")
            sm_artifacts_raw = session.get("sm_artifacts", "{}")
            sm_stage_configs_raw = session.get("sm_stage_configs", "{}")
            try:
                sm_artifacts = json.loads(sm_artifacts_raw) if sm_artifacts_raw else {}
            except (json.JSONDecodeError, TypeError):
                sm_artifacts = {}
            try:
                sm_stage_configs = json.loads(sm_stage_configs_raw) if sm_stage_configs_raw else {}
            except (json.JSONDecodeError, TypeError):
                sm_stage_configs = {}
            self.pipeline = PipelineAgent(
                session_id=session_id,
                api_key=self.api_key,
                base_url=self.base_url,
                db_path=self.db_path,
                current_state=sm_current_state,
                artifacts=sm_artifacts,
                validation_enabled=sm_validation,
                stage_configs=sm_stage_configs,
            )
        else:
            self.pipeline = None

        status = "вкл" if self.compression_enabled else "выкл"
        strat = f" | Стратегия: {self.context_strategy}" if self.context_strategy else ""
        sm_tag = " | SM" if self.pipeline else ""
        print(f"🔄 Переключено на сессию: {session['name']} (ID: {session_id}) | Сжатие: {status}{strat}{sm_tag}")
        return True

    def list_sessions(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, last_active_at, prompt_tokens, completion_tokens, "
                "total_tokens, compression_enabled, context_strategy, sm_enabled "
                "FROM sessions ORDER BY last_active_at DESC"
            )
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "last_active": row[3],
                 "prompt_tokens": row[4], "completion_tokens": row[5], "total_tokens": row[6],
                 "compression_enabled": row[7], "context_strategy": row[8], "sm_enabled": row[9]}
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

    def _call_api(self, messages: list, tools: Optional[list] = None) -> dict:
        """Прямой вызов Cloud.ru FM /chat/completions.

        Если задан `tools` (OpenAI tool-calling формат), они передаются модели
        и в ответе может появиться поле `tool_calls`.
        """
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages
        }
        if tools:
            payload["tools"] = tools
            # tool_choice не задаём: некоторые модели Cloud.ru не поддерживают
            # "auto" и возвращают 400. Поведение по умолчанию — модель сама решает.

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
                content = message.get("content", "") or ""
                tool_calls = message.get("tool_calls") or []
                usage = result.get("usage", {})

                self.total_requests += 1
                self.total_tokens_used += usage.get("total_tokens", 0)

                return {
                    "success": True,
                    "content": content,
                    "tool_calls": tool_calls,
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

    def _save_memory_state(self):
        """Сохраняет task_context и profile_name в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET task_context = ?, profile_name = ? WHERE id = ?",
                (json.dumps(self.task_context.to_dict(), ensure_ascii=False),
                 self.profile.profile_name,
                 self.current_session["id"])
            )
            conn.commit()
        self.current_session["task_context"] = json.dumps(self.task_context.to_dict(), ensure_ascii=False)
        self.current_session["profile_name"] = self.profile.profile_name

    def _save_sm_state(self):
        """Сохраняет состояние PipelineAgent в БД сессии."""
        if not self.pipeline:
            return
        state = self.pipeline.to_dict()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET sm_current_state = ?, sm_artifacts = ?, "
                "sm_validation_enabled = ? WHERE id = ?",
                (
                    state["current_state"],
                    json.dumps(state["artifacts"], ensure_ascii=False),
                    int(state["validation_enabled"]),
                    self.current_session["id"],
                )
            )
            conn.commit()
        self.current_session["sm_current_state"] = state["current_state"]
        self.current_session["sm_artifacts"] = json.dumps(state["artifacts"], ensure_ascii=False)
        self.current_session["sm_validation_enabled"] = int(state["validation_enabled"])

    def _load_invariants(self):
        all_invariants = InvariantManager.load_all(self._invariants_dir)
        config_raw = self.current_session.get("invariants_config", "{}")
        try:
            config = json.loads(config_raw) if config_raw else {}
        except (json.JSONDecodeError, TypeError):
            config = {}
        enabled_ids = config.get("enabled_ids", [])
        if enabled_ids:
            for inv in all_invariants:
                inv.enabled = inv.name in enabled_ids
        self._invariants = all_invariants
        self._validator = AgentValidator(self._invariants) if self.invariants_enabled else None

    def _save_invariants_state(self):
        enabled_ids = [inv.name for inv in self._invariants if inv.enabled]
        config = json.dumps({"enabled_ids": enabled_ids}, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET invariants_enabled = ?, invariants_config = ? WHERE id = ?",
                (int(self.invariants_enabled), config, self.current_session["id"])
            )
            conn.commit()
        self.current_session["invariants_enabled"] = int(self.invariants_enabled)
        self.current_session["invariants_config"] = config

    def _save_mcp_state(self):
        """Сохраняет mcp_enabled в БД для текущей сессии."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET mcp_enabled = ? WHERE id = ?",
                (int(self.mcp_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["mcp_enabled"] = int(self.mcp_enabled)

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
        non_system = [m for m in self.conversation_history if m["role"] not in ("system", "command")]
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
        non_system = [m for m in self.conversation_history if m["role"] not in ("system", "command")]
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
        """Возвращает сообщения текущей ветки (без command-сообщений)."""
        result = []
        for m in self.conversation_history:
            if m["role"] == "command":
                continue
            result.append(m)
        return result

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
                "  /new [name]   — создать новую сессию (/new sm — с SM)\n"
                "  /clear        — очистить историю\n"
                "  /model [name] — показать/сменить модель\n"
                "  /temp [value] — показать/сменить температуру\n"
                "  /strategy [type] — показать/сменить стратегию\n"
                "  /compression [on|off|toggle] — управление сжатием\n"
                "  /context      — информация о контексте\n"
                "  /checkpoint   — сохранить checkpoint (branching)\n"
                "  /branch <name> — создать ветку\n"
                "  /branches     — список веток\n"
                "  /switch <id>  — переключить ветку\n"
                "  /sm           — статус State Machine\n"
                "  /sm validate [on|off] — вкл/выкл валидацию\n"
                "  /step [STAGE] — показать/сменить этап SM\n"
                "  /artifact     — артефакты этапов SM\n"
                "  /task [key value] — показать/задать рабочую память\n"
                "  /task clear   — очистить рабочую память\n"
                "  /profile [name] — показать/сменить профиль\n"
                "  /profile set <k> <v> — задать поле профиля\n"
                "  /profile new <name> — создать новый профиль\n"
                "  /profiles     — список доступных профилей\n"
                "  /invariant [on|off] — вкл/выкл проверку инвариантов\n"
                "  /invariant toggle <name> — вкл/выкл конкретный инвариант\n"
                "  /invariant show <name> — показать инвариант\n"
                "  /invariants — список инвариантов\n"
                "  /mcp          — статус MCP-серверов\n"
                "  /mcp on|off   — вкл/выкл tool-calling через MCP\n"
                "  /mcp connect <name> — подключить сервер\n"
                "  /mcp disconnect <name> — отключить сервер\n"
                "  /mcp add <name> <url> [transport] — добавить сервер\n"
                "  /mcp remove <name> — удалить сервер\n"
                "  /mcp tools    — список инструментов"
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
            sm_enabled = False
            if arg and arg.strip().lower().startswith("sm "):
                sm_enabled = True
                sm_name = arg.strip()[3:].strip()
                self.create_session(sm_name if sm_name else None, sm_enabled=True)
            elif arg and arg.strip().lower() == "sm":
                self.create_session(None, sm_enabled=True)
            else:
                name = arg.strip() if arg else None
                self.create_session(name)
            tag = " [SM]" if sm_enabled else ""
            return f"✅ Создана новая сессия: {self.current_session['name']}{tag}"

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
            # SM info
            if self.pipeline:
                lines.append("")
                lines.append("🤖 State Machine:")
                lines.append(f"  Этап: {self.pipeline.current_state.value}")
                lines.append(f"  Валидация: {'вкл' if self.pipeline.validation_enabled else 'выкл'}")
                lines.append(f"  Артефактов: {len(self.pipeline.artifacts)}")
            # Memory model info
            lines.append("")
            lines.append("🧠 Модель памяти:")
            tc = self.task_context.to_dict()
            lines.append(f"  Working (TaskContext): {len(tc)} полей")
            lines.append(f"  Long-term (Profile): '{self.profile.profile_name}' — {len(self.profile._data)} полей")
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

        # ── State Machine ────────────────────────────────────

        if cmd == "/sm":
            if not self.pipeline:
                return (
                    "State Machine не активен.\n"
                    "Создайте SM-сессию: /new sm [name]"
                )
            parts = cmd_input.strip().split(maxsplit=2)
            if len(parts) == 1:
                val_status = "вкл" if self.pipeline.validation_enabled else "выкл"
                return (
                    f"🤖 State Machine:\n"
                    f"  Этап: {self.pipeline.current_state.value}\n"
                    f"  Валидация: {val_status}\n"
                    f"  Артефактов: {len(self.pipeline.artifacts)}\n"
                    f"Команды: /sm validate [on|off], /step, /artifact"
                )
            if parts[1] == "validate" and len(parts) == 3:
                resp = self.pipeline._handle_sm_validate_command(cmd_input)
                self._save_sm_state()
                return resp
            return "Используйте: /sm, /sm validate [on|off]"

        if cmd == "/step":
            if not self.pipeline:
                return "State Machine не активен. Создайте SM-сессию: /new sm [name]"
            resp = self.pipeline._handle_step_command(cmd_input)
            self._save_sm_state()
            self._save_message("command", resp)
            return resp

        if cmd == "/artifact":
            if not self.pipeline:
                return "State Machine не активен."
            return self.pipeline._handle_artifact_command()

        # ── Рабочая память (TaskContext) ─────────────────────

        if cmd == "/task":
            if not arg:
                data = self.task_context.to_dict()
                if not data:
                    return "📋 Рабочая память пуста."
                lines = ["📋 Рабочая память (TaskContext):"]
                for k, v in data.items():
                    lines.append(f"  • {k}: {v}")
                return "\n".join(lines)
            parts = arg.strip().split(maxsplit=1)
            sub = parts[0].lower()
            if sub == "clear":
                self.task_context.clear()
                self._save_memory_state()
                return "✅ Рабочая память очищена."
            if len(parts) < 2:
                return "❌ Используйте: /task <key> <value> или /task clear"
            self.task_context.set(parts[0], parts[1])
            self._save_memory_state()
            return f"✅ Задано: {parts[0]} = {parts[1]}"

        # ── Долговременная память (Profile) ──────────────────

        if cmd == "/profile":
            if not arg:
                data = self.profile._data
                if not data:
                    return "👤 Профиль пуст. Используйте /profile set <key> <value>"
                lines = [f"👤 Профиль: {self.profile.profile_name}"]
                for k, v in data.items():
                    first = v.split("\n")[0][:100] if v else ""
                    lines.append(f"  • {k}: {first}")
                return "\n".join(lines)
            parts = arg.strip().split(maxsplit=2)
            sub = parts[0].lower()
            if sub == "set" and len(parts) >= 3:
                self.profile.set(parts[1], parts[2])
                self._save_memory_state()
                return f"✅ Профиль обновлён: {parts[1]} = {parts[2]}"
            elif sub == "set" and len(parts) < 3:
                return "❌ Используйте: /profile set <key> <value>"
            elif sub == "new" and len(parts) >= 2:
                name = parts[1]
                profiles = self.profile.list_profiles()
                if name in profiles:
                    return f"❌ Профиль '{name}' уже существует."
                self.profile = Profile(name)
                self._save_memory_state()
                return f"✅ Создан и активирован профиль: {name}"
            else:
                # Переключение профиля
                name = parts[0]
                profiles = self.profile.list_profiles()
                if name not in profiles:
                    return f"❌ Профиль '{name}' не найден. Доступны: {', '.join(profiles)}"
                self.profile = Profile(name)
                self._save_memory_state()
                return f"✅ Переключено на профиль: {name}"

        if cmd == "/profiles":
            profiles = self.profile.list_profiles()
            current = self.profile.profile_name
            lines = ["📂 Доступные профили:"]
            for p in profiles:
                marker = " 👈" if p == current else ""
                lines.append(f"  • {p}{marker}")
            return "\n".join(lines)

        if cmd == "/invariant":
            if not arg:
                if not self._invariants:
                    return "Инварианты не загружены."
                lines = ["⚠️ Инварианты:"]
                for inv in self._invariants:
                    status = "✅" if inv.enabled else "❌"
                    lines.append(f"  {status} {inv.name}")
                return "\n".join(lines)

            parts = arg.strip().split(maxsplit=2)
            sub = parts[0].lower()

            if sub == "toggle" and len(parts) >= 2:
                name = parts[1]
                for inv in self._invariants:
                    if inv.name == name:
                        inv.enabled = not inv.enabled
                        self._validator = AgentValidator(self._invariants) if self.invariants_enabled else None
                        self._save_invariants_state()
                        status = "включён" if inv.enabled else "выключен"
                        return f"✅ Инвариант '{name}' {status}."
                return f"❌ Инвариант '{name}' не найден."

            elif sub == "show" and len(parts) >= 2:
                name = parts[1]
                for inv in self._invariants:
                    if inv.name == name:
                        return inv.get_prompt_block()
                return f"❌ Инвариант '{name}' не найден."

            elif sub == "on":
                self.invariants_enabled = True
                self._validator = AgentValidator(self._invariants)
                self._save_invariants_state()
                return "✅ Проверка инвариантов включена."

            elif sub == "off":
                self.invariants_enabled = False
                self._validator = None
                self._save_invariants_state()
                return "✅ Проверка инвариантов выключена."

            return "❌ Используйте: /invariant, /invariant toggle <name>, /invariant show <name>, /invariant on, /invariant off"

        if cmd == "/invariants":
            return self._handle_command("/invariant")

        # ── MCP ───────────────────────────────────────────────
        if cmd == "/mcp":
            if not arg:
                # Краткий статус
                status = "вкл" if self.mcp_enabled else "выкл"
                servers = self.mcp_manager.list_servers()
                lines = [f"🔌 MCP: {status}"]
                if not servers:
                    lines.append("  Серверы не настроены. Используйте /mcp add <name> <url>")
                else:
                    for s in servers:
                        mark = "🟢" if s["connected"] else "⚪"
                        tools = f" ({s['tools_count']} tools)" if s["connected"] else ""
                        lines.append(f"  {mark} {s['name']} [{s['transport']}] {s['url']}{tools}")
                lines.append("")
                lines.append("Команды: /mcp connect <name>, /mcp disconnect <name>,")
                lines.append("         /mcp add <name> <url> [http], /mcp remove <name>, /mcp tools,")
                lines.append("         /mcp on|off (явное управление)")
                return "\n".join(lines)

            parts = arg.strip().split()
            sub = parts[0].lower()

            if sub == "on":
                self.mcp_enabled = True
                self._save_mcp_state()
                # Авто-подключение всех enabled серверов
                results = self.mcp_manager.connect_all_enabled()
                lines = ["✅ MCP включён."]
                for name, status in results.items():
                    lines.append(f"  • {name}: {status}")
                return "\n".join(lines) if results else "✅ MCP включён. Нет серверов для подключения."

            if sub == "off":
                self.mcp_enabled = False
                self._save_mcp_state()
                return "✅ MCP выключен. Инструменты не передаются модели."

            if sub == "connect" and len(parts) >= 2:
                name = parts[1]
                try:
                    info = self.mcp_manager.connect_server(name)
                    self.mcp_enabled = True
                    self._save_mcp_state()
                    return f"✅ '{name}' подключён. Инструментов: {info['tools_count']}."
                except Exception as e:
                    return f"❌ Не удалось подключить '{name}': {e}"

            if sub == "disconnect" and len(parts) >= 2:
                name = parts[1]
                if self.mcp_manager.disconnect_server(name):
                    if not self.mcp_manager.has_active_tools():
                        self.mcp_enabled = False
                        self._save_mcp_state()
                        return f"✅ '{name}' отключён. MCP автоматически выключен — нет активных серверов."
                    return f"✅ '{name}' отключён."
                return f"❌ '{name}' не был подключён."

            if sub == "add" and len(parts) >= 3:
                name = parts[1]
                url = parts[2]
                transport = parts[3] if len(parts) >= 4 else "http"
                entry = self.mcp_manager.add_server(name, transport, url)
                return f"✅ Сервер '{entry['name']}' добавлен ({entry['transport']} → {entry['url']})."

            if sub == "remove" and len(parts) >= 2:
                name = parts[1]
                if self.mcp_manager.remove_server(name):
                    return f"✅ Сервер '{name}' удалён."
                return f"❌ Сервер '{name}' не найден."

            if sub == "tools":
                tools = self.mcp_manager.get_openai_tools()
                if not tools:
                    return "Нет активных инструментов. Подключите сервер через /mcp connect <name>."
                lines = [f"🛠 Доступные инструменты ({len(tools)}):"]
                for t in tools:
                    fn = t["function"]
                    desc = (fn.get("description") or "").split("\n")[0][:80]
                    lines.append(f"  • {fn['name']}: {desc}")
                return "\n".join(lines)

            return ("❌ Используйте: /mcp, /mcp on|off, /mcp connect <name>, "
                    "/mcp disconnect <name>, /mcp add <name> <url> [transport], "
                    "/mcp remove <name>, /mcp tools")

        return f"❌ Неизвестная команда: {cmd}. Используйте /help для списка команд."

    # ── Основной метод ────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """Основной метод агента: принимает запрос и возвращает ответ."""
        if not user_input or not user_input.strip():
            return "Пожалуйста, введите ваш запрос."

        # ── Команды ──────────────────────────────────────────
        # Команды не попадают в историю как user-сообщения,
        # чтобы не засорять контекст служебным мусором.
        if user_input.startswith("/"):
            resp = self._handle_command(user_input)
            if resp:
                self.conversation_history.append({"role": "command", "content": resp})
                self._save_message("command", resp)
            return resp

        self.conversation_history.append({"role": "user", "content": user_input})
        self._save_message("user", user_input)

        # ── State Machine routing ──────────────────────────────
        if self.pipeline is not None:
            result = self.pipeline.chat(user_input)
            if isinstance(result, list):
                # Несколько этапов (авто-прогрессия) — каждый как отдельное сообщение
                combined = ""
                for state, stage_response in result:
                    msg = f"[{state.value}]\n{stage_response}"
                    self.conversation_history.append({"role": "assistant", "content": msg})
                    self._save_message("assistant", msg)
                    combined = (combined + "\n\n---\n\n" + msg) if combined else msg
                self._save_sm_state()
                return combined
            else:
                # Команда — сохраняем как command
                self.conversation_history.append({"role": "command", "content": result})
                self._save_message("command", result)
                return result

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

        # Инжектируем трёхуровневую модель памяти
        memory_blocks = []
        profile_block = self.profile.to_full_prompt_block()
        if profile_block:
            memory_blocks.append(profile_block)
        task_block = self.task_context.to_prompt_block()
        if task_block:
            memory_blocks.append(task_block)
        if self.invariants_enabled and self._validator:
            inv_block = self._validator.get_prompt_blocks()
            if inv_block:
                memory_blocks.append(inv_block)
        if memory_blocks:
            memory_text = "\n\n".join(memory_blocks)
            messages.insert(1, {"role": "system", "content": memory_text})
            self._save_memory_state()

        # ── MCP: build tools list ─────────────────────────
        mcp_tools: list = []
        if self.mcp_enabled and self.mcp_manager.has_active_tools():
            mcp_tools = self.mcp_manager.get_openai_tools()

        tool_trace_lines: list[str] = []
        usage_pt = 0
        usage_ct = 0
        usage_tt = 0

        response = self._call_api(messages, tools=mcp_tools or None)

        if response["success"] and mcp_tools:
            # Loop: пока модель просит вызвать инструменты — выполняем и шлём дальше.
            iterations = 0
            while response.get("tool_calls") and iterations < self.mcp_max_iterations:
                iterations += 1
                tcalls = response["tool_calls"]
                # 1. Добавляем сообщение ассистента с tool_calls в messages для следующего вызова.
                assistant_with_calls = {
                    "role": "assistant",
                    "content": response.get("content") or "",
                    "tool_calls": tcalls,
                }
                messages.append(assistant_with_calls)
                # 2. Исполняем каждый tool_call и добавляем результаты.
                for call in tcalls:
                    call_id = call.get("id", "")
                    fn = call.get("function", {})
                    tname = fn.get("name", "")
                    targs = fn.get("arguments", "{}")
                    print(f"[JARVIS][MCP] tool call: {tname}({str(targs)[:200]})")
                    tresult = self.mcp_manager.execute_tool(tname, targs)
                    print(f"[JARVIS][MCP] result: {tresult[:200]}")
                    tool_trace_lines.append(f"🔧 {tname}: {tresult[:300]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tresult,
                    })
                # Аккумулируем токены промежуточных вызовов
                u = response.get("usage", {}) or {}
                usage_pt += u.get("prompt_tokens", 0)
                usage_ct += u.get("completion_tokens", 0)
                usage_tt += u.get("total_tokens", 0)
                # 3. Следующий вызов
                response = self._call_api(messages, tools=mcp_tools)
                if not response["success"]:
                    break

        if response["success"]:
            assistant_message = response["content"] or ""

            # Если loop завершился без текста (например, всё ещё tool_calls после лимита),
            # подставим сообщение о превышении лимита.
            if not assistant_message and response.get("tool_calls"):
                assistant_message = (
                    f"⚠️ Превышен лимит итераций tool-calling ({self.mcp_max_iterations}). "
                    "Модель продолжает запрашивать инструменты."
                )

            # Валидация инвариантов (с retry)
            if self.invariants_enabled and self._validator:
                max_retries = 2
                for attempt in range(max_retries + 1):
                    violation = self._validator.validate(assistant_message)
                    if not violation:
                        break
                    if attempt < max_retries:
                        retry_messages = messages.copy()
                        retry_messages.append({"role": "user", "content": assistant_message})
                        retry_messages.append({
                            "role": "system",
                            "content": f"⚠️ Нарушение инварианта: {violation}\nИсправь ответ."
                        })
                        retry_response = self._call_api(retry_messages)
                        if retry_response["success"]:
                            assistant_message = retry_response["content"]
                        else:
                            break
                else:
                    assistant_message += (
                        f"\n\n⚠️ Инвариант нарушен. Не удалось исправить: {violation}"
                    )

            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            self._save_message("assistant", assistant_message)

            # Сохраняем след вызовов MCP-инструментов как command-сообщение,
            # чтобы он был виден в UI и истории.
            if tool_trace_lines:
                trace_text = "🧰 MCP-инструменты использованы:\n" + "\n".join(tool_trace_lines)
                self.conversation_history.append({"role": "command", "content": trace_text})
                self._save_message("command", trace_text)

            usage = response.get("usage", {})
            self.last_usage = usage
            pt = usage.get("prompt_tokens", 0) + usage_pt
            ct = usage.get("completion_tokens", 0) + usage_ct
            tt = usage.get("total_tokens", 0) + usage_tt

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

        # Сброс State Machine
        if self.pipeline:
            sm_enabled = self.current_session.get("sm_enabled", False)
            sm_validation = self.current_session.get("sm_validation_enabled", True)
            self.pipeline = PipelineAgent(
                session_id=self.current_session["id"],
                api_key=self.api_key,
                base_url=self.base_url,
                db_path=self.db_path,
                current_state="PLANNING",
                validation_enabled=sm_validation,
            )
            self._save_sm_state()

        self.task_context = TaskContext()
        self.profile = Profile(self.profile.profile_name)
        self._save_memory_state()

        self._load_invariants()

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
        result.extend([m for m in self.conversation_history if m["role"] not in ("system", "command")])
        return result

    def get_compressed_messages(self) -> list:
        result = []

        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})

        for item in self.compression_history:
            if item["type"] == "summary":
                result.append({"role": "system", "content": f"[АРХИВ: {item['content']}]"})

        result.extend([m for m in self.conversation_history if m["role"] not in ("system", "command")])

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
