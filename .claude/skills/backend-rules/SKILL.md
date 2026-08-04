---
name: backend-rules
description: |
  Архитектура agents/: jarvis.py (ядро) + 5 mixin-файлов
  (jarvis_memory, jarvis_session, jarvis_context, jarvis_compression,
  jarvis_commands), state_machine.py, invariants.py, ragger/.
  Используй когда нужно понять структуру бэкенда, добавить
  новый метод, интеграцию или исправить баг в агенте
---

## Карта файлов (детали — в docstring-ах самих файлов)

- `agents/jarvis.py` — ядро: `chat()`, `_call_api()`, `_build_messages()`, RAG-поля `rag_*`, `model_provider` ("cloud"/"local", ставит webui), `_rag_provider_kwargs()`, `_local_llm_profile()`, `last_rag_debug`, `self.persona` (имя активного системного промпта), `guard_enabled`/`last_guard_report` (защита от непрямой инъекции, day-47), `_log_exchange()` (пишет в JSONL через `JarvisLogger`)
- `agents/guard.py` — три слоя защиты от indirect prompt injection: `sanitize()` (вырезает HTML-комментарии/невидимый текст/zero-width из результатов инструментов), `wrap()` (оборачивает данные инструмента в `<untrusted_data>` с пометкой «не инструкции»), `validate_output()` (проверяет ответ модели на следы исполнения инъекции)
- `agents/jarvis_memory.py` — `TaskContext` (working memory), `Profile` (long-term, Markdown в `agents/memory/profiles/`)
- `agents/jarvis_session.py` — `SessionMixin`: SQLite `_init_db`, CRUD сессий и сообщений
- `agents/jarvis_context.py` — `ContextStrategyMixin`: стратегии контекста (sliding_window / sticky_facts / branching), инварианты, memory state
- `agents/jarvis_compression.py` — `CompressionMixin`: сжатие истории
- `agents/jarvis_commands.py` — `CommandMixin`: `_handle_command()` — все /команды (синтаксис и список смотри там), включая `/persona`, `/guard`
- `agents/personas.py` — системные промпты агента (`default`/`vuln`/`safe`/`summarizer`/`analyst`/`searcher`) + `resolve_persona_name()`; `vuln`/`safe` — day-46 (prompt injection в промпте), `summarizer`/`analyst`/`searcher` — day-47 (indirect injection, без защитных инструкций в промпте — вся защита в `guard.py`)
- `agents/jarvis_logger.py` — `JarvisLogger`: JSONL-лог обменов (`logs/jarvis-YYYY-MM-DD.jsonl`, вне `_init_db`/SQLite, каталог в `.gitignore`), поле `guard` (enabled/sanitized/output)
- `agents/state_machine.py` — FSM: `AgentState`, `StageAgent`, `PipelineAgent`
- `agents/invariants.py` — `Invariant` (ABC), `AgentValidator`, `InvariantManager`
- `agents/mcp_manager.py` — MCP-клиент: JSON-RPC 2.0 через urllib, SSE-ответы; конфиг серверов `agents/mcp/servers.json`
- `mcp_servers/*/server.py` — FastMCP-серверы (nasa, space_monitor, composer, ragger, support, sources), streamable-http
- `ragger/` — RAG-пайплайн: `pipeline.py` (индексация; `--local` → Ollama, индекс `data_local/`), `document_loader.py`, `chunking.py`, `embedder.py`, `indexer.py` (FAISS), `search.py` (`RagPipeline`: search → filter → rerank → slice), `reranker.py`, `answer.py` (`generate_answer` → `RagAnswer`), `ollama_client.py` (нативный `/api/chat`)
- `agents/memory/jarvis_history.db` — SQLite, 5 таблиц; схема: `docs/database-schema.md`

## Куда добавлять

1. БД/сессии/сообщения → `jarvis_session.py`; стратегии/ветвление/инварианты → `jarvis_context.py`; сжатие → `jarvis_compression.py`; новая /команда → `elif` в `_handle_command()`.
2. Новая колонка БД → `ALTER TABLE ADD COLUMN` в `_init_db()` в `try/except sqlite3.OperationalError`; синхронизируй `docs/database-schema.md`.
3. Новый параметр настройки → чтение/запись в `/api/settings` (`webui/app.py`).
4. Режим, влияющий на `chat()` (как RAG/MCP/SM) → правь `chat()` в `jarvis.py`; флаг режима храни в сессии (`_save_*_state` + чтение в `_load_session`). Исключение — флаги уровня процесса, а не сессии (`mcp_enabled`, `guard_enabled`): их нельзя сбрасывать при смене/создании сессии, иначе тумблер в панели врёт про реальное состояние (баг дня 46/фикс дня 47).

**MRO:** `JarvisAgent(SessionMixin, ContextStrategyMixin, CompressionMixin, CommandMixin)`; cross-mixin вызовы через `self.*`.

## Неочевидное (грабли)

- API-вызовы: прямой `urllib.request` POST, НЕ OpenAI SDK. Ответ `_call_api` — dict с флагом `"success"`; исключения не кидать.
- Tool calling у Cloud.ru работает только на Qwen3-Coder-Next и MiniMax-M2.5 (не на Qwen3-30B-A3B).
- RAG fallback: при `confidence=none` и `rag_strict=off` (default) запрос уходит в основную LLM с историей и памятью; `strict on` → «Я не знаю» (анти-галлюцинации, контракт day-24). rag_override используется только при непустом ответе.
- Локальный провайдер: весь RAG-путь через Ollama — индекс `data_local/`, эмбеддер `nomic-embed-text` с task-префиксами (`search_document:` при индексации, `search_query:` при поиске); эти префиксы обязаны совпадать с индексом.
- `num_ctx` — load-time параметр Ollama: разное значение в соседних запросах перегружает модель (десятки секунд), поэтому `_local_llm_profile()` держит его единым на всех этапах (rerank/verify/generate). Поле `llm_mode: baseline` активного профиля Jarvis отключает оптимизации (путь day-28 через /v1 без метрик).
- `docs/database-schema.md` грузится в RAG-индекс с сохранением заголовков (`keep_headings=True`) — без разреза по секциям эмбеддинг табличного дока размывается и не находится.
- Память: три уровня (history / TaskContext / Profile) инжектятся в system prompt перед каждым вызовом; TaskContext (`/task on`) и компрессия (`/compression on`) по умолчанию выключены.
- SM: если `agent.pipeline` не None, `chat()` маршрутизирует в него; у каждого этапа изолированная история, артефакты инжектятся соседним этапам; переходы — по `ALLOWED_TRANSITIONS`. Новый этап = enum + ALLOWED_TRANSITIONS + STAGE_SYSTEM_PROMPTS + STAGE_DEFAULT_MODELS + StageAgent + сохранение состояния.
- Инварианты: prompt-block в system prompt до вызова, `validate()` после; при нарушении до 2 ретраев, затем warning в конце ответа.
- Guard (day-47): слои 1 (`sanitize`) и 3 (`validate_output`) детерминированы (regex, без вызова модели), не зависят от модели/промпта. Слой 2 (`wrap`) — просьба к модели в тексте обёртки, не гарантия; не держит инъекции, лежащие в видимом тексте источника. Сравнение в слое 3 идёт с **очищенным** (после `sanitize`) текстом источника, не с сырым.

## Конвенции

- PascalCase классы, snake_case методы; docstring у всех публичных классов/методов/функций.
- Логи: `print()` с префиксами, не `logging`. Пути: `Path(__file__).parent.resolve()`.
- Конфиг: `CLOUDRU_SECRET_KEY` из `.env`, base_url `https://foundation-models.api.cloud.ru/v1`.
- Модели Cloud.ru: Qwen3-30B-A3B (дёшево) / Qwen3-Coder-Next (средне) / MiniMax-M2.5 (тяжело).
