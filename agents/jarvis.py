import os
import json
import urllib.request
import urllib.error
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv

from agents.state_machine import PipelineAgent
from agents.mcp_manager import McpServerManager
from agents.jarvis_memory import TaskContext, Profile
from agents.jarvis_session import SessionMixin
from agents.jarvis_context import ContextStrategyMixin
from agents.jarvis_compression import CompressionMixin
from agents.jarvis_commands import CommandMixin

load_dotenv()

_AGENTS_DIR = Path(__file__).parent.resolve()
_DEFAULT_DB_PATH = str(_AGENTS_DIR / "memory" / "jarvis_history.db")


class JarvisAgent(SessionMixin, ContextStrategyMixin, CompressionMixin, CommandMixin):
    """
    Агент для взаимодействия с LLM через API с сохранением контекста в SQLite.

    Поддерживает:
    - Несколько изолированных сессий диалога
    - Переключение между сессиями
    - Автоматическое восстановление истории при перезапуске
    - Стратегии управления контекстом:
      \u2022 sliding_window — только последние 5 сообщений
      \u2022 sticky_facts — ключевые факты + последние 5 сообщений
      \u2022 branching — ветки диалога от checkpoint
    - Трёхуровневую модель памяти:
      \u2022 Short-term — текущий диалог (сессия)
      \u2022 Working — TaskContext (данные текущей задачи)
      \u2022 Long-term — Profile (профиль, предпочтения, знания)
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

        self._invariants_dir = _AGENTS_DIR / "memory" / "invariants"
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

        # Стратегии управления контекстом
        saved_strategy = self.current_session.get("context_strategy")
        self.context_strategy = saved_strategy if saved_strategy else None
        saved_facts = self.current_session.get("sticky_facts")
        self.sticky_facts = json.loads(saved_facts) if saved_facts and saved_facts != "{}" else {}
        self.branches = {}
        self.current_branch_id = 0
        self._next_branch_id = 1
        self.checkpoint_index = None

        self.conversation_history = self._load_messages()

        if self.context_strategy == "branching" and not self.branches:
            self._init_branches()

        # Трёхуровневая модель памяти
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

        # Инварианты
        self._invariants = []
        self._validator = None
        self.invariants_enabled = bool(self.current_session.get("invariants_enabled", True))
        self._load_invariants()

        # State Machine
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

        # MCP
        self.mcp_manager = McpServerManager()
        self.mcp_enabled = bool(self.current_session.get("mcp_enabled", False))
        self.mcp_max_iterations = 10

        # RAG
        self.rag_enabled = bool(self.current_session.get("rag_enabled", False))

        self.total_tokens_used = 0
        self.total_requests = 0
        self.last_usage = None

        self.compression_interval = 5
        self.compression_history = self._load_compressed_summaries()
        self.session_prompt_tokens = self.current_session.get("prompt_tokens", 0)
        self.session_completion_tokens = self.current_session.get("completion_tokens", 0)
        self.session_total_tokens = self.current_session.get("total_tokens", 0)

    # ─────────────── API ──────────────────────────────────────────

    def _build_messages(self, user_input: str) -> list:
        self.conversation_history.append({"role": "user", "content": user_input})
        return self.conversation_history

    def _call_api(self, messages: list, tools: Optional[list] = None) -> dict:
        """Прямой вызов Cloud.ru FM /chat/completions."""
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages
        }
        if tools:
            payload["tools"] = tools

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

    # ── Основной метод ────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """Основной метод агента: принимает запрос и возвращает ответ."""
        if not user_input or not user_input.strip():
            return "Пожалуйста, введите ваш запрос."

        if user_input.startswith("/"):
            resp = self._handle_command(user_input)
            if resp:
                self.conversation_history.append({"role": "command", "content": resp})
                self._save_message("command", resp)
            return resp

        self.conversation_history.append({"role": "user", "content": user_input})
        self._save_message("user", user_input)

        # State Machine routing
        if self.pipeline is not None:
            result = self.pipeline.chat(user_input)
            if isinstance(result, list):
                combined = ""
                for state, stage_response in result:
                    msg = f"[{state.value}]\n{stage_response}"
                    self.conversation_history.append({"role": "assistant", "content": msg})
                    self._save_message("assistant", msg)
                    combined = (combined + "\n\n---\n\n" + msg) if combined else msg
                self._save_sm_state()
                return combined
            else:
                self.conversation_history.append({"role": "command", "content": result})
                self._save_message("command", result)
                return result

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

        # RAG: поиск релевантных чанков и инжекция в контекст
        insert_idx = 1
        if self.rag_enabled:
            try:
                from ragger.search import search as rag_search
                rag_chunks = rag_search(user_input, top_k=5, strategy="structural")
                if rag_chunks:
                    rag_lines = [
                        "Используй следующие документы из базы знаний для ответа "
                        "на вопрос пользователя:"
                    ]
                    for i, r in enumerate(rag_chunks, 1):
                        rag_lines.append(
                            f"[{i}] Источник: {r['source']} / {r.get('section', '')}\n"
                            f"    {r['text']}"
                        )
                    messages.insert(
                        insert_idx,
                        {"role": "system", "content": "\n\n---\n\n".join(rag_lines)}
                    )
                    insert_idx += 1
            except Exception as e:
                print(f"[JARVIS][RAG] Error: {e}")

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
            messages.insert(insert_idx, {"role": "system", "content": memory_text})
            self._save_memory_state()

        # MCP: build tools list
        mcp_tools = []
        if self.mcp_enabled and self.mcp_manager.has_active_tools():
            mcp_tools = self.mcp_manager.get_openai_tools()

        tool_trace_lines = []
        usage_pt = 0
        usage_ct = 0
        usage_tt = 0

        response = self._call_api(messages, tools=mcp_tools or None)

        if response["success"] and mcp_tools:
            iterations = 0
            while response.get("tool_calls") and iterations < self.mcp_max_iterations:
                iterations += 1
                tcalls = response["tool_calls"]
                assistant_with_calls = {
                    "role": "assistant",
                    "content": response.get("content") or "",
                    "tool_calls": tcalls,
                }
                messages.append(assistant_with_calls)
                for call in tcalls:
                    call_id = call.get("id", "")
                    fn = call.get("function", {})
                    tname = fn.get("name", "")
                    targs = fn.get("arguments", "{}")
                    print(f"[JARVIS][MCP] tool call: {tname}({str(targs)[:200]})")
                    tresult = self.mcp_manager.execute_tool(tname, targs)
                    print(f"[JARVIS][MCP] result: {tresult[:200]}")
                    tool_trace_lines.append(f"\U0001f527 {tname}: {tresult[:300]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tresult,
                    })
                u = response.get("usage", {}) or {}
                usage_pt += u.get("prompt_tokens", 0)
                usage_ct += u.get("completion_tokens", 0)
                usage_tt += u.get("total_tokens", 0)
                response = self._call_api(messages, tools=mcp_tools)
                if not response["success"]:
                    break

        if response["success"]:
            assistant_message = response["content"] or ""

            if not assistant_message and response.get("tool_calls"):
                assistant_message = (
                    f"\u26a0\ufe0f Превышен лимит итераций tool-calling ({self.mcp_max_iterations}). "
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
                            "content": f"\u26a0\ufe0f Нарушение инварианта: {violation}\nИсправь ответ."
                        })
                        retry_response = self._call_api(retry_messages)
                        if retry_response["success"]:
                            assistant_message = retry_response["content"]
                        else:
                            break
                else:
                    assistant_message += (
                        f"\n\n\u26a0\ufe0f Инвариант нарушен. Не удалось исправить: {violation}"
                    )

            self.conversation_history.append({"role": "assistant", "content": assistant_message})
            self._save_message("assistant", assistant_message)

            if tool_trace_lines:
                trace_text = "\U0001f9f0 MCP-инструменты использованы:\n" + "\n".join(tool_trace_lines)
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
                    f"\n\n\u26a0\ufe0f Внимание: контекст диалога заполнен на {pct}% "
                    f"(~{total} токенов из {self.context_limit}). Рекомендуется начать новую сессию."
                )

            # Автосжатие (только без стратегии)
            if not self.context_strategy and self.compression_enabled:
                non_system = [m for m in self.conversation_history if m["role"] != "system"]
                if len(non_system) % self.compression_interval == 0:
                    comp_result = self.compress_history()
                    if comp_result:
                        assistant_message += (
                            f"\n\n\U0001f4e6 История сжата: {comp_result['tokens_before']} \u2192 {comp_result['tokens_after']} "
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

    # ─────────────── Статистика ───────────────────────────────────

    def get_stats(self) -> str:
        msg_count = len([m for m in self.conversation_history if m["role"] != "system"])

        comp_status = "вкл" if self.compression_enabled else "выкл"
        strat_name = self.context_strategy if self.context_strategy else "выкл"
        lines = [
            "📊 Статистика агента:",
            f"  \u2022 Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})",
            f"  \u2022 Стратегия контекста: {strat_name}",
            f"  \u2022 Режим сжатия: {comp_status}",
            f"  \u2022 Всего запросов: {self.total_requests}",
            f"  \u2022 Всего токенов (глобально): {self.total_tokens_used}",
            f"  \u2022 Сообщений в текущей сессии: {msg_count}",
            f"  \u2022 Модель: {self.model}",
            "",
            "  📈 Токены текущей сессии:",
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
                "     Окно: последние 5 сообщений",
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
