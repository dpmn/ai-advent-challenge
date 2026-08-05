import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv

from agents import guard
from agents.state_machine import PipelineAgent
from agents.mcp_manager import McpServerManager
from agents.jarvis_logger import JarvisLogger
from agents.personas import resolve_persona_name
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
            model: str = "Qwen/Qwen3-Coder-Next",
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
        # Провайдер активной модели: "cloud" (Cloud.ru) или "local" (Ollama).
        # Выставляется webui/app.py при смене модели; управляет выбором
        # RAG-индекса (data/ vs data_local/) и моделей rerank/verify.
        self.model_provider = "cloud"
        # Техническая информация о последнем RAG-запросе (тайминги этапов) для UI.
        self.last_rag_debug: Optional[dict] = None
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        # Имя активной персоны (день 46): нужно в логе, чтобы отличать прогоны
        # атак на промпт-жертву от прогонов на защищённом промпте.
        self.persona = resolve_persona_name(system_prompt)
        self.logger = JarvisLogger()
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.context_limit = context_limit

        if not self.api_key:
            raise ValueError("API ключ не найден. Установите переменную окружения CLOUDRU_SECRET_KEY")

        self._init_db()

        self._invariants_dir = _AGENTS_DIR / "memory" / "invariants"
        self._invariants_dir.mkdir(parents=True, exist_ok=True)

        self.compression_enabled = compression_enabled if compression_enabled is not None else False

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
        # Рабочая память по умолчанию выключена: авто-extraction — лишний
        # LLM-вызов на каждое сообщение (и 404 на локальной модели).
        # Включается командой /task on, флаг не персистится.
        self.task_memory_enabled = False
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
        # Флаг MCP — уровня процесса, а не сессии: подключения живут в
        # mcp_manager, общем на весь агент, и при переключении сессий не рвутся.
        # Здесь он только восстанавливается при старте из последней сессии.
        self.mcp_enabled = bool(self.current_session.get("mcp_enabled", False))
        self.mcp_max_iterations = 10

        # Защита от непрямой инъекции (день 47). Тоже уровня процесса: слои
        # применяются к результатам инструментов, а инструменты общие.
        # По умолчанию включена — небезопасное состояние должно требовать действия.
        self.guard_enabled = True
        self.last_guard_report: Optional[dict] = None

        # LLM Gateway (день 48): прокси с input/output guard, аудитом и учётом
        # стоимости. Флаг уровня процесса, как guard и mcp. По умолчанию выключен:
        # гейтвей — отдельный процесс (python3 gateway/app.py), и молча ломать
        # чат из-за того, что его не подняли, нельзя.
        self.gateway_url = os.getenv("GATEWAY_URL", "http://127.0.0.1:5001/v1")
        self.gateway_enabled = False
        self.last_gateway_report: Optional[dict] = None

        # RAG
        self.rag_enabled = bool(self.current_session.get("rag_enabled", False))
        self.rag_top_k_before = self.current_session.get("rag_top_k_before", 15) or 15
        self.rag_top_k_after = self.current_session.get("rag_top_k_after", 8) or 8
        self.rag_threshold = float(self.current_session.get("rag_threshold", 0.2) or 0.2)
        self.rag_mode = self.current_session.get("rag_mode", "threshold") or "threshold"
        self.rag_strict = bool(self.current_session.get("rag_strict", False))

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

    def _api_base(self) -> str:
        """Возвращает адрес, куда уходит запрос: гейтвей или провайдер напрямую."""
        return self.gateway_url if self.gateway_enabled else self.base_url

    def _call_api(self, messages: list, tools: Optional[list] = None) -> dict:
        """Прямой вызов /chat/completions — у провайдера или через LLM Gateway."""
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.gateway_enabled:
            # Гейтвей сам выбирает апстрим по этому заголовку: принимать от
            # клиента произвольный URL нельзя, иначе он станет открытым релеем.
            headers["X-Upstream"] = "local" if self.model_provider == "local" else "cloud"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._api_base()}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choice = result.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "") or ""
                tool_calls = message.get("tool_calls") or []
                usage = result.get("usage", {})

                # Отчёт гейтвея приходит отдельным полем; на прямом пути его нет.
                self.last_gateway_report = result.get("gateway")

                self.total_requests += 1
                self.total_tokens_used += usage.get("total_tokens", 0)

                return {
                    "success": True,
                    "content": content,
                    "tool_calls": tool_calls,
                    "usage": usage,
                    "gateway": result.get("gateway"),
                    "finish_reason": choice.get("finish_reason", "unknown")
                }

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else "No error body"
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}", "details": error_body}
        except urllib.error.URLError as e:
            if self.gateway_enabled:
                return {
                    "success": False,
                    "error": f"LLM Gateway недоступен ({self.gateway_url}): {e.reason}. "
                             f"Запусти его: python3 gateway/app.py — или выключи /gateway off",
                }
            return {"success": False, "error": f"URL Error: {e.reason}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    # ── Основной метод ────────────────────────────────────────────

    def _local_llm_profile(self) -> dict | None:
        """Профиль инференса Ollama для локального провайдера (day-29), None для cloud.

        Режим выбирается полем `llm_mode` активного профиля Jarvis
        (agents/memory/profiles/*.md): "baseline" — путь day-28 как есть
        (OpenAI-совместимый /v1, серверный num_ctx, полный промпт),
        иначе (в т.ч. default) — оптимизированный нативный путь.

        num_ctx одинаковый на всех этапах: это load-time параметр, разное
        значение в соседних запросах перегружает модель (десятки секунд).
        8192 подобран под 6 GiB VRAM: веса Q4_K_M (~4.4 GiB) + KV-cache
        (~450 MiB) влезают целиком на GPU — против 18/29 слоёв при
        серверном дефолте 32768.
        """
        if self.model_provider != "local":
            return None
        if self.profile.get("llm_mode", "").strip().lower() == "baseline":
            return None
        num_ctx = 8192
        return {
            "transport": "ollama",
            "keep_alive": "30m",
            "gen_options": {"num_ctx": num_ctx, "temperature": 0.0, "num_predict": 1024},
            "verify_options": {"num_ctx": num_ctx, "temperature": 0.0, "num_predict": 5},
            "rerank_options": {"num_ctx": num_ctx, "temperature": 0.0, "num_predict": 256},
            "chunk_char_limit": 1600,
            "prompt_style": "compact",
        }

    def _rag_provider_kwargs(self) -> tuple[dict, str]:
        """Возвращает (kwargs для RagPipeline, verify_model) под активного провайдера.

        Для "local" весь RAG-путь идёт через Ollama: эмбеддинг запроса
        (nomic-embed-text, индекс data_local/), реранк, верификация и
        генерация — активной локальной моделью, инференс — по профилю
        _local_llm_profile(). Для "cloud" — Cloud.ru и индекс data/
        (дефолты RagPipeline).
        """
        from ragger.search import DATA_DIR, DATA_DIR_LOCAL

        if self.model_provider == "local":
            return {
                "rerank_model": self.model,
                "base_url": self.base_url,
                "data_dir": DATA_DIR_LOCAL,
                "embed_api_key": self.api_key,
                "embed_model": "nomic-embed-text",
                "embed_base_url": self.base_url,
                "embed_prefix": "search_query: ",
                "llm_profile": self._local_llm_profile(),
            }, self.model
        return {"data_dir": DATA_DIR}, "Qwen/Qwen3-30B-A3B"

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

        _t0 = time.monotonic()
        tool_records = []

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

        # RAG: пайплайн поиска → генерация ответа с цитатами и источниками
        rag_override = None  # если установлен — используется как финальный ответ, API не вызывается
        rag_answer = None
        self.last_rag_debug = None
        if self.rag_enabled:
            try:
                import time as _time
                from ragger.search import RagPipeline

                provider_kwargs, verify_model = self._rag_provider_kwargs()

                pipeline = RagPipeline(
                    api_key=self.api_key,
                    top_k_before=self.rag_top_k_before,
                    top_k_after=self.rag_top_k_after,
                    threshold=self.rag_threshold,
                    mode=self.rag_mode,
                    **provider_kwargs,
                )
                rag_chunks = pipeline.run(user_input)

                from ragger.answer import generate_answer
                t_gen0 = _time.monotonic()
                rag_answer = generate_answer(
                    query=user_input,
                    chunks=rag_chunks,
                    api_key=self.api_key,
                    model=self.model,
                    base_url=self.base_url,
                    verify_model=verify_model,
                    llm_profile=provider_kwargs.get("llm_profile"),
                )
                t_gen = _time.monotonic() - t_gen0

                timings = dict(pipeline._last_timings)
                timings["generate_s"] = t_gen
                self.last_rag_debug = {
                    "provider": self.model_provider,
                    "model": self.model,
                    "embed_model": provider_kwargs.get("embed_model", "openai/text-embedding-3-small"),
                    "chunks": len(rag_chunks),
                    "confidence": rag_answer.confidence,
                    "timings": {k: round(v, 2) for k, v in timings.items()},
                }
                gen_metrics = (rag_answer.llm_metrics or {}).get("generate")
                if gen_metrics:
                    self.last_rag_debug["gen_tok_s"] = gen_metrics.get("tok_s")
                    self.last_rag_debug["gen_load_s"] = gen_metrics.get("load_s")
                    self.last_rag_debug["gen_eval_count"] = gen_metrics.get("eval_count")
                print(f"[JARVIS][RAG] provider={self.model_provider} "
                      f"chunks={len(rag_chunks)} confidence={rag_answer.confidence} "
                      f"timings={self.last_rag_debug['timings']}")

                if rag_answer.confidence != "none" and rag_answer.answer.strip():
                    sources_str = "\n".join(
                        f"  - {s['source']} [{s['chunk_id']}] (раздел: {s.get('section', '—')})"
                        for s in rag_answer.sources
                    )
                    citations_lines = []
                    for s in rag_answer.sources:
                        quote = s.get("quote", "").strip()
                        if quote:
                            citations_lines.append(
                                f"- \"{quote}\" — {s['source']} [{s['chunk_id']}]"
                            )
                    citations_str = "\n".join(citations_lines) if citations_lines else "—"
                    rag_override = (
                        f"{rag_answer.answer}\n\n"
                        f"📚 **Источники:**\n{sources_str}\n\n"
                        f"💬 **Цитаты:**\n{citations_str}"
                    )
                # confidence == "none": поведение зависит от rag_strict (день 24 недели 5).
                # strict on — честное "не знаю" без обращения к основной LLM (анти-галлюцинации).
                # strict off — rag_override = None, вопрос падает на основную LLM с историей
                # и памятью: чинит мета-вопросы ("О чём говорили?") и OOD-запросы из общего
                # знания (Гагарин), но модель может галлюцинировать.
                elif self.rag_strict:
                    rag_override = (
                        rag_answer.answer.strip()
                        or "Я не знаю ответа на этот вопрос. В базе знаний нет информации по данной теме."
                    )
            except Exception as e:
                print(f"[JARVIS][RAG] Error: {e}")
                import traceback
                traceback.print_exc()

        if rag_override:
            assistant_message = rag_override
            self.conversation_history.append({"role": "assistant", "content": assistant_message})
            self._save_message("assistant", assistant_message)
            self._update_task_state(user_input, assistant_message)
            self._log_exchange(user_input, assistant_message, [], _t0, extra={"path": "rag"})
            return assistant_message

        # Инжектируем трёхуровневую модель памяти
        memory_blocks = []
        profile_block = self.profile.to_full_prompt_block()
        if profile_block:
            memory_blocks.append(profile_block)
        task_block = self.task_context.to_prompt_block() if self.task_memory_enabled else ""
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

        # MCP: build tools list
        mcp_tools = []
        if self.mcp_enabled and self.mcp_manager.has_active_tools():
            mcp_tools = self.mcp_manager.get_openai_tools()

        tool_trace_lines = []
        # Слои защиты (день 47): что вырезано санитайзером и что агент имел право
        # использовать. guard_sources — тексты ПОСЛЕ санитизации: сравнивать ответ
        # с сырым текстом нельзя, иначе спрятанная инструкция считается легальной.
        guard_hits = []
        guard_sources = []
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
                    # Полный результат, без обрезки: по нему видно, что именно
                    # прочитал агент (например, инъекцию внутри тикета).
                    tool_records.append({"name": tname, "args": targs, "result": tresult})

                    tool_content = tresult
                    if self.guard_enabled:
                        cleaned, hits = guard.sanitize(tresult)
                        for h in hits:
                            print(f"[JARVIS][GUARD] вырезано ({tname}): "
                                  f"{h['rule']} ×{h['count']} {h['sample']}")
                            guard_hits.append({"tool": tname, **h})
                        guard_sources.append(cleaned)
                        tool_content = guard.wrap(cleaned, tname)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": tool_content,
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

            # Гейтвей заблокировал запрос — секрет остался в тексте пользователя.
            # Оставить его в истории значит отправлять его в модель при каждом
            # следующем сообщении сессии (и хранить в SQLite). Поэтому сообщение
            # выкидывается из контекста, а в чат уходит объяснение гейтвея.
            gateway_report = response.get("gateway") or {}
            if gateway_report.get("action") == "blocked":
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
                self._delete_last_message("user")
                # Предупреждение сохраняется как служебное сообщение: история в
                # UI перерисовывается из базы, и без этого ответ гейтвея пропал
                # бы с экрана сразу после отправки.
                self._save_message("command", assistant_message)
                # В лог агента текст запроса не пишется: в нём секрет. Разбор
                # инцидента идёт по аудиту гейтвея — там маски и отпечатки.
                self._log_exchange("(заблокировано гейтвеем, текст не сохранён)",
                                   assistant_message, [], _t0,
                                   extra={"gateway": gateway_report})
                return assistant_message

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

            # Слой 3: проверка ответа на следы исполненной инъекции.
            # Работает только когда агент реально читал внешние данные.
            self.last_guard_report = None
            if self.guard_enabled and guard_sources:
                report = guard.validate_output(assistant_message, guard_sources, self.persona)
                self.last_guard_report = report
                if not report["ok"]:
                    print(f"[JARVIS][GUARD] выход: {report['findings']}")
                    assistant_message = (
                        guard.format_warning(report) + "\n\n" + assistant_message
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

            # Автоматическое обновление task state (память задачи)
            if user_input and assistant_message and not user_input.startswith("/"):
                self._update_task_state(user_input, assistant_message)

            # В лог кладём и состояние защиты: без него по записи не отличить
            # «инъекция не сработала» от «слой её снял».
            log_extra = {}
            if tool_records:
                log_extra["guard"] = {
                    "enabled": self.guard_enabled,
                    "sanitized": guard_hits,
                    "output": self.last_guard_report,
                }
            # Вердикт гейтвея — в тот же лог: у гейтвея свой аудит, но по логу
            # агента должно быть видно, ушёл запрос наружу или его срезали.
            if self.gateway_enabled:
                log_extra["gateway"] = self.last_gateway_report or {"action": "нет отчёта"}
            self._log_exchange(user_input, assistant_message, tool_records, _t0,
                               extra=log_extra or None)
            return assistant_message
        else:
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            error_message = (
                f"❌ Ошибка: {response.get('error', 'Неизвестная ошибка')}\n{response.get('details', '')}"
            )
            self._log_exchange(
                user_input, "", tool_records, _t0, error=response.get("error", "unknown")
            )
            return error_message

    def _log_exchange(
            self,
            user_input: str,
            assistant_message: str,
            tool_records: list,
            started_at: float,
            error: Optional[str] = None,
            extra: Optional[dict] = None
    ) -> None:
        """Пишет обмен в JSONL-лог (logs/). Ошибки логирования чат не ломают."""
        self.logger.log_exchange(
            session_id=self.current_session["id"] if self.current_session else None,
            model=self.model,
            persona=self.persona,
            user_input=user_input,
            response=assistant_message,
            tool_calls=tool_records,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            error=error,
            extra=extra,
        )

    # ─────────────── Память задачи (авто-extraction) ─────────────

    def _update_task_state(self, user_input: str, assistant_message: str):
        """Извлекает и обновляет task state через дешёвую LLM после каждого ответа.

        Если тема сменилась относительно текущего goal — не вызывает extract_and_update,
        а только обновляет last_focus. goal и progress сохраняются.
        Не делает ничего, если рабочая память выключена (task_memory_enabled).
        """
        if not self.task_memory_enabled:
            return
        try:
            # Проверка смены темы: если тема не связана с goal — не трогаем goal/progress
            if self.task_context.get("goal"):
                if self.task_context.detect_topic_change(user_input, self.api_key):
                    print(f"[JARVIS] Topic changed — preserving goal, updating last_focus")
                    self.task_context.set("last_focus", user_input[:100])
                    self._save_memory_state()
                    return

            self.task_context.extract_and_update(
                user_input=user_input,
                assistant_response=assistant_message,
                api_key=self.api_key,
                model="Qwen/Qwen3-30B-A3B",
                base_url=self.base_url,
            )
            self._save_memory_state()
            print(f"[JARVIS] Task state updated: {self.task_context.to_dict()}")
        except Exception as e:
            print(f"[JARVIS] Task state extraction error: {e}")

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

        if self.rag_enabled:
            lines.extend([
                "",
                f"  📖 RAG: вкл",
                f"     Режим: {self.rag_mode}",
                f"     top_k_before: {self.rag_top_k_before}",
                f"     top_k_after: {self.rag_top_k_after}",
                f"     threshold: {self.rag_threshold}",
            ])

        return "\n".join(lines)
