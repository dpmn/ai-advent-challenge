import sqlite3
import json
from typing import Optional
from datetime import datetime
from pathlib import Path

from agents.state_machine import PipelineAgent
from agents.jarvis_memory import TaskContext, Profile


class SessionMixin:
    """Mixin-класс для управления сессиями диалога и БД."""

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
            for rag_col in [
                ("rag_enabled", "INTEGER DEFAULT 0"),
                ("rag_top_k_before", "INTEGER DEFAULT 10"),
                ("rag_top_k_after", "INTEGER DEFAULT 5"),
                ("rag_threshold", "REAL DEFAULT 0.2"),
                ("rag_mode", "TEXT DEFAULT 'hybrid'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {rag_col[0]} {rag_col[1]}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def _get_last_session(self) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts, task_context, profile_name, "
                "sm_enabled, sm_validation_enabled, sm_current_state, sm_artifacts, sm_stage_configs, "
                "invariants_enabled, invariants_config, mcp_enabled, mcp_config, rag_enabled, "
                "rag_top_k_before, rag_top_k_after, rag_threshold, rag_mode "
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
                    "mcp_enabled": row[18], "mcp_config": row[19], "rag_enabled": row[20],
                    "rag_top_k_before": row[21], "rag_top_k_after": row[22],
                    "rag_threshold": row[23], "rag_mode": row[24],
                }
            return None

    def _load_session(self, session_id: int) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, created_at, prompt_tokens, completion_tokens, total_tokens, "
                "compression_enabled, context_strategy, sticky_facts, task_context, profile_name, "
                "sm_enabled, sm_validation_enabled, sm_current_state, sm_artifacts, sm_stage_configs, "
                "invariants_enabled, invariants_config, mcp_enabled, mcp_config, rag_enabled, "
                "rag_top_k_before, rag_top_k_after, rag_threshold, rag_mode "
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
                    "mcp_enabled": row[18], "mcp_config": row[19], "rag_enabled": row[20],
                    "rag_top_k_before": row[21], "rag_top_k_after": row[22],
                    "rag_threshold": row[23], "rag_mode": row[24],
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

    # ── Session CRUD ──────────────────────────────────────────

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
            "rag_enabled": 0,
            "rag_top_k_before": 10,
            "rag_top_k_after": 5,
            "rag_threshold": 0.2,
            "rag_mode": "hybrid",
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
        self.mcp_enabled = False
        self.rag_enabled = False
        self.rag_top_k_before = 10
        self.rag_top_k_after = 5
        self.rag_threshold = 0.2
        self.rag_mode = "hybrid"
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
        self.mcp_enabled = bool(session.get("mcp_enabled", False))
        self.rag_enabled = bool(session.get("rag_enabled", False))
        self.rag_top_k_before = session.get("rag_top_k_before", 10) or 10
        self.rag_top_k_after = session.get("rag_top_k_after", 5) or 5
        self.rag_threshold = float(session.get("rag_threshold", 0.2) or 0.2)
        self.rag_mode = session.get("rag_mode", "hybrid") or "hybrid"
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

    # ── Управление историей ──────────────────────────────────

    def reset_conversation(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (self.current_session["id"],))
            conn.commit()
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})
        self.compression_history = []
        self._clear_compressed_summaries()
        self.sticky_facts = {}
        self.branches = {}
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None
        if self.context_strategy:
            self._save_strategy_state()
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

    # ── Сохранение MCP-состояния ─────────────────────────────

    def _save_mcp_state(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET mcp_enabled = ? WHERE id = ?",
                (int(self.mcp_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["mcp_enabled"] = int(self.mcp_enabled)

    def _save_rag_state(self):
        """Сохраняет флаг RAG-режима в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET rag_enabled = ? WHERE id = ?",
                (int(self.rag_enabled), self.current_session["id"])
            )
            conn.commit()
        self.current_session["rag_enabled"] = int(self.rag_enabled)

    def _save_rag_config(self):
        """Сохраняет параметры RAG-пайплайна в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET rag_top_k_before = ?, rag_top_k_after = ?, "
                "rag_threshold = ?, rag_mode = ? WHERE id = ?",
                (self.rag_top_k_before, self.rag_top_k_after,
                 self.rag_threshold, self.rag_mode,
                 self.current_session["id"])
            )
            conn.commit()
        self.current_session["rag_top_k_before"] = self.rag_top_k_before
        self.current_session["rag_top_k_after"] = self.rag_top_k_after
        self.current_session["rag_threshold"] = self.rag_threshold
        self.current_session["rag_mode"] = self.rag_mode
