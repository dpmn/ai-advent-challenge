import sqlite3
import json
from typing import Optional

from agents.invariants import InvariantManager, AgentValidator


class ContextStrategyMixin:
    """Mixin-класс для стратегий управления контекстом: sliding_window, sticky_facts, branching."""

    # ── Сохранение состояния в БД ────────────────────────────

    def _save_strategy_state(self):
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

    # ── Основная стратегия ───────────────────────────────────

    def set_strategy(self, strategy: Optional[str]) -> str:
        valid = {None, "sliding_window", "sticky_facts", "branching"}
        if strategy not in valid:
            return f"❌ Неизвестная стратегия. Допустимые: {[s for s in valid if s is not None]}"

        old_strategy = self.context_strategy
        self.context_strategy = strategy

        if strategy is not None and self.compression_enabled:
            self.disable_compression()

        if strategy == "sticky_facts" and old_strategy != "sticky_facts":
            if not self.sticky_facts:
                self._extract_facts_initial()

        if strategy == "branching" and old_strategy != "branching":
            self._init_branches()

        if strategy is None and old_strategy == "branching":
            self.branches = {}
            self.current_branch_id = 0
            self._next_branch_id = 1
            self.checkpoint_index = None

        self._save_strategy_state()

        strat_name = strategy if strategy else "выкл"
        print(f"🔀 Стратегия управления контекстом: {strat_name}")
        return f"✅ Стратегия установлена: {strat_name}"

    # ── Sliding Window ───────────────────────────────────────

    def _get_sliding_window_messages(self, window_size: int = 5) -> list:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        non_system = [m for m in self.conversation_history if m["role"] not in ("system", "command")]
        result.extend(non_system[-window_size:])
        return result

    def _apply_sliding_window(self, window_size: int = 5):
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        if len(non_system) <= window_size:
            return
        kept = non_system[-window_size:]
        self.conversation_history = []
        if self.system_prompt:
            self.conversation_history.append({"role": "system", "content": self.system_prompt})
        self.conversation_history.extend(kept)
        self._delete_old_messages(window_size)

    # ── Sticky Facts ─────────────────────────────────────────

    def _get_sticky_facts_messages(self, window_size: int = 5) -> list:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        if self.sticky_facts:
            facts_lines = []
            for k, v in self.sticky_facts.items():
                facts_lines.append(f"  \u2022 {k}: {v}")
            facts_str = "\U0001f4cc Ключевые факты диалога:\n" + "\n".join(facts_lines)
            result.append({"role": "system", "content": facts_str})
        non_system = [m for m in self.conversation_history if m["role"] not in ("system", "command")]
        result.extend(non_system[-window_size:])
        return result

    def _extract_facts_initial(self):
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
        try:
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

    # ── Branching ────────────────────────────────────────────

    def _init_branches(self):
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
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._update_session_tokens()
        if 0 in self.branches:
            self.conversation_history = self.branches[0]["messages"].copy()

    def _get_branching_messages(self) -> list:
        result = []
        for m in self.conversation_history:
            if m["role"] == "command":
                continue
            result.append(m)
        return result

    def save_checkpoint(self) -> str:
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен. Используйте: /strategy branching"
        non_system = [m for m in self.conversation_history if m["role"] != "system"]
        self.checkpoint_index = len(non_system)
        print(f"📍 Checkpoint сохранён на сообщении #{self.checkpoint_index} в ветке '{self.branches[self.current_branch_id]['name']}'")
        return f"✅ Checkpoint сохранён. Следующая ветка начнётся отсюда (сообщение #{self.checkpoint_index})."

    def create_branch(self, name: str) -> str:
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен."
        if self.checkpoint_index is None:
            return "❌ Сначала сохраните checkpoint: /checkpoint"
        if any(b["name"] == name for b in self.branches.values()):
            return f"❌ Ветка с именем '{name}' уже существует. Используйте другое имя."

        self.branches[self.current_branch_id]["messages"] = self.conversation_history.copy()

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

        self.current_branch_id = branch_id
        self.conversation_history = new_history.copy()

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
        if self.context_strategy != "branching":
            return "❌ Режим ветвления не активен."
        if branch_id not in self.branches:
            return f"❌ Ветка с ID {branch_id} не найдена. Используйте: /branches"

        cur = self.branches[self.current_branch_id]
        cur["messages"] = self.conversation_history.copy()
        cur["prompt_tokens"] = self.session_prompt_tokens
        cur["completion_tokens"] = self.session_completion_tokens
        cur["total_tokens"] = self.session_total_tokens

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
