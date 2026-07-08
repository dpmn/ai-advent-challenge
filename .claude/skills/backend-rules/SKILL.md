---
name: backend-rules
description: |
  Архитектура agents/: jarvis.py (ядро) + 5 mixin-файлов
  (jarvis_memory, jarvis_session, jarvis_context, jarvis_compression,
  jarvis_commands), state_machine.py, invariants.py.
  Используй когда нужно понять структуру бэкенда, добавить
  новый метод, интеграцию или исправить баг в агенте
---

## Структура файлов

- `agents/jarvis.py` — ядро агента: `__init__`, `_build_messages`, `_call_api`, `chat`, `get_stats`. Поля RAG-конфигурации: `rag_top_k_before/after/threshold/mode/strict`. Атрибут `model_provider` ("cloud"/"local") — выставляется извне (webui) при смене модели. Хелпер `_rag_provider_kwargs()` возвращает `(kwargs, verify_model)` под провайдера: для local — индекс `ragger/data_local/`, эмбеддер Ollama (`nomic-embed-text`, prefix `search_query:`), rerank/verify через локальную модель; для cloud — дефолты (data/, text-embedding-3-small). `last_rag_debug: Optional[dict]` — отладка последнего RAG-прогона (provider, model, embed_model, timings, chunks, confidence), собирается в `chat()` и отдаётся webui. RAG-инжекция: при `rag_enabled=True` в `chat()` создаётся `RagPipeline` (search → filter → rerank → slice), затем `generate_answer()` из `ragger/answer.py` формирует структурированный `RagAnswer` с цитатами и источниками; rag_override используется только при непустом ответе; при `confidence=none` поведение зависит от `rag_strict`: strict on → ответ «Я не знаю» (контракт day-24, анти-галлюцинации), strict off (default) → fallback на основную LLM с историей и памятью. Флаг `task_memory_enabled` (default False) — рабочая память (TaskContext + авто-extraction) выключена по умолчанию; компрессия истории тоже выключена по умолчанию (`compression_enabled=False`)
- `agents/jarvis_memory.py` — Mixin: `TaskContext` (с методами `extract_and_update()`, `_detect_topic_change()`, `_trim_progress()`, `TASK_STATE_KEYS`), `Profile` (трёхуровневая память)
- `agents/jarvis_session.py` — Mixin: `SessionMixin` — SQLite `_init_db`, session CRUD, сообщения
- `agents/jarvis_context.py` — Mixin: `ContextStrategyMixin` — стратегии, branching, инварианты, memory state
- `agents/jarvis_compression.py` — Mixin: `CompressionMixin` — сжатие истории, get_raw/compressed_messages
- `agents/jarvis_commands.py` — Mixin: `CommandMixin` — `_handle_command` со всеми /командами
- `agents/state_machine.py` — FSM: `AgentState` (enum), `StageAgent`, `PipelineAgent`
- `agents/invariants.py` — система инвариантов: `Invariant` (ABC), `ForbiddenLibrariesInvariant`, `RequiredTechStackInvariant`, `AgentValidator`, `InvariantManager`
- `agents/mcp_manager.py` — MCP-клиент: `McpConnection`, `McpServerManager`. JSON-RPC 2.0 через `urllib`, handshake, tools/list, tools/call, пагинация, SSE-ответы
- `agents/mcp/__init__.py` — пакет для MCP-конфигов
- `agents/mcp/servers.json` — конфигурация MCP-серверов (name, url, transport, enabled)
- `mcp_servers/nasa_mcp/server.py` — MCP-сервер NASA API (FastMCP, streamable-http): 3 инструмента — apod, mars_photos, neo_feed
- `mcp_servers/space_monitor_mcp/server.py` — MCP-сервер Space Monitor (4 инструмента: monitor_start, monitor_stop, monitor_status, monitor_summary)
- `mcp_servers/space_monitor_mcp/collector.py` — BackgroundCollector — фоновый сбор NASA APOD/NEO в SQLite (threading, циклический обход дат)
- `mcp_servers/space_monitor_mcp/test_server.py` — интеграционный тест через JSON-RPC
- `mcp_servers/composer_mcp/server.py` — MCP-сервер Composer: композиция инструментов NASA (compose, apod_today, apod_range), использует NASA MCP как прокси
- `mcp_servers/composer_mcp/test_server.py` — интеграционный тест Composer через JSON-RPC
- `mcp_servers/ragger/server.py` — MCP-сервер Ragger: семантический поиск по проиндексированным документам проекта. Инструменты: `search_context` (поиск релевантных чанков), `list_sources` (список источников).
- `ragger/pipeline.py` — пайплайн индексации документов: chunking → эмбеддинги → FAISS. Сравнение fixed-size и structural стратегий. Флаг `--local`: эмбеддинги через Ollama (`nomic-embed-text`, 768-dim), индексы в `ragger/data_local/`; облачный индекс `ragger/data/` живёт параллельно
- `ragger/document_loader.py` — загрузка .md/.py из проекта
- `ragger/chunking.py` — две стратегии чанкинга (fixed-size 1000 tok / structural по заголовкам)
- `ragger/embedder.py` — `/v1/embeddings`: Cloud.ru (text-embedding-3-small) или Ollama; параметры `base_url` и `prefix` (task-префиксы nomic-embed-text: `search_document:` при индексации, `search_query:` при поиске); один httpx-клиент на все батчи
- `ragger/indexer.py` — FAISS IndexFlatIP + metadata.json
- `ragger/search.py` — семантический поиск: запрос → эмбеддинг → FAISS → топ-k чанков. `search()` и `RagPipeline` принимают `data_dir` + конфиг эмбеддера (`embed_base_url`, `embed_model`, `embed_prefix`, `embed_api_key`); кеш индексов по ключу `(data_dir, strategy)`; тайминги этапов (embed/faiss/rerank) в `_last_timings`. Класс `RagPipeline`: пайплайн search → threshold filter → LLM rerank → slice. Методы `run()` и `compare_modes()` (A/B-тест 3 режимов)
- `ragger/answer.py` — генерация структурированного ответа RAG: `RagAnswer` dataclass (answer, sources, confidence), `generate_answer()` — pre-verification (`_verify_relevance()`) через дешёвую LLM находит прямые ответы среди чанков, затем форматирует чанки с doc-ID разметкой, вызывает LLM на JSON-ответ (вопрос пользователя подставляется в промпт генерации — фикс day-28), робастный парсинг (4 попытки), fallback при пустых чанках, нерелевантности или ошибке LLM (confidence="none")
- `ragger/compare.py` — сравнение стратегий чанкинга (таблица)
- `ragger/reranker.py` — функции фильтрации и реранкинга: `threshold_filter()` (отсев по similarity score) и `llm_rerank()` (батч-реранкинг через LLM с JSON-массивом оценок, `timeout=120` у urlopen)
- `agents/memory/jarvis_history.db` — SQLite с 5 таблицами (sessions, messages, compressed_summaries, branches, stage_messages)
- `agents/memory/profiles/` — Markdown-файлы профилей
- `agents/memory/invariants/` — Markdown-файлы инвариантов

## Схема БД (`agents/memory/jarvis_history.db`)

SQLite, 5 таблиц. Все `session_id` с `ON DELETE CASCADE`. Создаются в `_init_db()`.

### sessions
| Поле | Тип | Назначение |
|------|-----|------------|
| id | INTEGER PK | Уникальный ID |
| name | TEXT | Имя сессии |
| prompt_tokens / completion_tokens / total_tokens | INTEGER | Счётчики токенов |
| compression_enabled | INTEGER 0/1 | Флаг сжатия (default 0 — выключено) |
| context_strategy | TEXT NULL | `sliding_window`, `sticky_facts`, `branching` |
| sticky_facts | TEXT JSON | Факты для sticky_facts |
| task_context | TEXT JSON | Рабочая память (TaskContext) |
| profile_name | TEXT | Активный профиль |
| sm_enabled / sm_validation_enabled | INTEGER 0/1 | Флаги SM |
| sm_current_state | TEXT | Текущий этап SM |
| sm_artifacts / sm_stage_configs | TEXT JSON | Артефакты и конфиги SM |
| invariants_enabled | INTEGER 0/1 | Флаг инвариантов |
| invariants_config | TEXT JSON | `{"enabled_ids": [...]}` |
| mcp_enabled | INTEGER 0/1 | Флаг MCP включён/выключен |
| mcp_config | TEXT JSON | Конфигурация MCP-серверов |
| rag_enabled | INTEGER 0/1 | Флаг RAG-режима: при включении перед каждым запросом LLM инжектятся релевантные чанки из FAISS |
| rag_top_k_before | INTEGER | Количество чанков до фильтрации (default 15) |
| rag_top_k_after | INTEGER | Количество чанков после фильтрации (default 8) |
| rag_threshold | REAL | Порог similarity score для threshold-фильтрации (default 0.2) |
| rag_mode | TEXT | Режим: `threshold`, `rerank`, `hybrid` (default `threshold`) |
| rag_strict | INTEGER 0/1 | Строгий режим RAG: при confidence=none — «Я не знаю» вместо fallback на основную LLM (default 0) |

### messages
`session_id → sessions.id`, `role` (user/assistant/system/command), `content`, `timestamp`

### compressed_summaries
`session_id → sessions.id`, `content` (текст саммари), `source_count`, `tokens_before`, `tokens_after`

### branches
`session_id → sessions.id`, `name`, `parent_branch_id`, `checkpoint_message_index`

### stage_messages
`session_id → sessions.id`, `stage` (PLANNING/EXECUTION/VALIDATION/DONE), `role`, `content`

### Миграции
Новые колонки — `ALTER TABLE ADD COLUMN` в `try/except sqlite3.OperationalError`.
Полная схема: `docs/database-schema.md`.

## JarvisAgent — архитектура

### Трёхуровневая память
1. **Short-term**: `self.conversation_history` — список `{role, content}`, таблица `messages`
2. **Working**: `self.task_context` (класс `TaskContext`) — key-value хранилище, JSON в колонке `sessions.task_context`
3. **Long-term**: `self.profile` (класс `Profile`) — Markdown в `profiles/<name>.md`

Все три уровня инжектятся в system prompt перед каждым API-вызовом.

### RAG-режим
- Флаг `rag_enabled` хранится в сессии (колонка `sessions.rag_enabled`)
- Параметры RAG: `rag_top_k_before` (до фильтрации, default 15), `rag_top_k_after` (после, default 8), `rag_threshold` (порог, default 0.2), `rag_mode` (режим, default `threshold`), `rag_strict` (default False, персистится в `sessions.rag_strict`)
- Команды: `/rag [on|off|config|compare]`, `/rag` (статус)
- Провайдер: `_rag_provider_kwargs()` выбирает под `model_provider` индекс (data/ или data_local/), эмбеддер (text-embedding-3-small или nomic-embed-text через Ollama) и rerank/verify-модели; при "local" весь путь идёт через Ollama без облачных вызовов
- В `chat()` при `rag_enabled=True`:
    1. Создаётся `RagPipeline(api_key, top_k_before, top_k_after, threshold, mode, **provider_kwargs)`
    2. Выполняется `pipeline.run(user_input)` — search → filter → rerank → slice
    3. `_verify_relevance()` (pre-verification) через дешёвую LLM проверяет, есть ли прямые ответы среди чанков; если нет — confidence="none"
    4. `generate_answer()` из `ragger/answer.py` вызывает LLM с doc-размеченными чанками, возвращает `RagAnswer` (answer, sources, confidence, quotes)
    5. При `confidence != "none"` и непустом ответе — форматируется rag_override (ответ + источники + цитаты) и используется как финальный (без повторного вызова main LLM)
    6. При `confidence="none"`: если `rag_strict=True` — финальный ответ «Я не знаю» (анти-галлюцинации); иначе rag_override = None, выполнение падает на основную LLM с историей диалога и памятью (чинит мета-вопросы, OOD-запросы, общие знания)
    7. Собирается `last_rag_debug` (provider, model, embed_model, timings embed/rerank/generate, chunks, confidence) — webui показывает его серой техстрокой под ответом
- RAG и MCP независимы и могут работать одновременно
- Три режима:
  - `threshold` — FAISS search → отсев по similarity score (`threshold_filter`)
  - `rerank` — FAISS search → LLM-реранкинг всех чанков (`llm_rerank`)
  - `hybrid` — FAISS search → threshold → LLM-реранкинг оставшихся

### Создание новой сессии
```python
agent.create_session(name="optional")  # возвращает dict сессии
agent.switch_session(session_id)       # переключает, восстанавливает всё состояние
agent.delete_session(session_id)
```

### Добавление нового метода в JarvisAgent
1. Если метод связан с БД/сессией/сообщениями — добавь в `jarvis_session.py` (mixin).
2. Если метод связан со стратегией контекста, ветвлением или инвариантами — добавь в `jarvis_context.py`.
3. Если метод связан со сжатием истории — добавь в `jarvis_compression.py`.
4. Если нужна новая команда — добавь ветку `elif` в `_handle_command()` в `jarvis_commands.py`.
5. Если нужна новая колонка в БД — добавь `ALTER TABLE ADD COLUMN` в `_init_db()` (в try/except).
6. Если новый параметр настройки — добавь чтение/запись в `/api/settings` в `webui/app.py`.
7. Если новый режим влияет на `chat()` (например RAG) — модифицируй `chat()` в `jarvis.py`. Флаг режима храни в сессии (БД `_save_*_state` + чтение в `_load_session`).

**MRO:** `JarvisAgent(SessionMixin, ContextStrategyMixin, CompressionMixin, CommandMixin)`. Все cross-mixin вызовы работают через `self.*`.

### API-вызов
```python
response = agent._call_api(messages)
# response = {"success": bool, "content": str, "usage": dict, "finish_reason": str}
# или {"success": False, "error": str, "details": str}
```
Прямой HTTP POST на Cloud.ru FM API через `urllib.request`. Не через OpenAI SDK.

### Стратегии контекста (взаимоисключающие с компрессией)
- `None` (default) — если компрессия вкл, то `[ARCHIVE]` + последние сообщения
- `sliding_window` — только последние 5 сообщений, старые удаляются из БД
- `sticky_facts` — LLM извлекает факты в JSON, инжектятся в system prompt + последние 5 сообщений
- `branching` — чекпоинты и параллельные ветки

Устанавливается `set_strategy(type, api_key, base_url)`.

### Компрессия
- По умолчанию **выключена** (day-28): новая сессия стартует без компрессии, включение — `/compression on`
- Каждые 5 сообщений — LLM создаёт саммари
- Хранится в таблице `compressed_summaries`
- В `chat()` обновляется после ответа

## MCP Integration (`mcp_manager.py`)

### McpServerManager
- Создаётся в `JarvisAgent.__init__()` как `self.mcp_manager`
- Загружает серверы из `agents/mcp/servers.json`
- Управляет коллекцией `McpConnection` (подключение/отключение)
- Конвертирует инструменты в OpenAI tool-calling формат (`convert_to_openai_tools()`)
- Состояние MCP (`mcp_enabled`) привязано к сессии, хранится в колонке `sessions.mcp_enabled`

### McpConnection
- Синхронный JSON-RPC 2.0 клиент через `urllib.request`
- Handshake: `initialize` → `notifications/initialized` → `tools/list`
- Поддерживает: `tools/list` (с пагинацией), `tools/call`
- SSE-ответы: `_parse_sse_response()` для серверов, возвращающих SSE

### Tool-calling в `chat()`
1. Если `mcp_enabled=True` и есть активные инструменты — `_call_api()` получает параметр `tools`
2. API Cloud.ru FM поддерживает tool calling (только на Qwen-Coder-Next / MiniMax-M2.5, НЕ на Qwen3-30B-A3B)
3. Ответ с `tool_calls` → извлекается `arguments`, вызывается `tools/call` через `McpConnection`
4. Результат вызова инструмента сохраняется как `command`-сообщение с префиксом `🔧`
5. Повторный вызов API с результатами инструмента для получения финального ответа

### Команда `/rag` (в `_handle_command()`)
- `/rag` — статус (вкл/выкл, параметры)
- `/rag on` — включить RAG: при каждом запросе подгружаются релевантные чанки из FAISS
- `/rag off` — выключить RAG: модель отвечает из своего общего знания
- `/rag config <key> <val>` — настройка параметров: `threshold` (0.0–1.0), `top_k_before`, `top_k_after`, `mode` (threshold/rerank/hybrid), `strict` (on/off — «Я не знаю» вместо fallback при confidence=none)
- `/rag compare <query>` — A/B-тест 3 режимов: прогоняет запрос через threshold/rerank/hybrid (с провайдер-конфигом агента) и показывает сравнительную статистику

### Команда `/task` (в `_handle_command()`)
- `/task` — статус рабочей памяти (вкл/выкл) и содержимое TaskContext
- `/task on` / `/task off` — включить/выключить рабочую память (default off; при выключенной нет ни extraction-вызова LLM, ни инжекции блока в system prompt)
- `/task clear` — очистить рабочую память
- `/task <key> <value>` — задать значение вручную

### Команды `/mcp` (в `_handle_command()`)
- `/mcp` — статус (вкл/выкл, список серверов, инструменты)
- `/mcp on` — включить MCP, пытается подключить все enabled серверы из `servers.json`
- `/mcp off` — выключить MCP, отключает все серверы через `disconnect_all()`
- `/mcp connect <name>` — подключить сервер
- `/mcp disconnect <name>` — отключить сервер
- `/mcp add <name> <url> [transport]` — добавить сервер
- `/mcp remove <name>` — удалить сервер
- `/mcp tools` — список инструментов всех подключённых серверов

### Миграция БД
```python
# в _init_db():
("mcp_enabled", "INTEGER DEFAULT 0"),
("mcp_config", "TEXT DEFAULT '{}'"),
# ALTER TABLE ADD COLUMN в try/except OperationalError
```

## State Machine (`state_machine.py`)

### Этапы (AgentState)
`PLANNING → EXECUTION → VALIDATION → DONE` (+ циклы VALIDATION→EXECUTION, DONE→PLANNING)

### Ключевые правила
- `PipelineAgent` создаётся в `JarvisAgent.__init__()`, если `sm_enabled=True`
- Если `pipeline` не None, `chat()` маршрутизирует туда
- `StageAgent` для каждого этапа хранит изолированную историю сообщений
- Артефакты этапов инжектятся в system prompt соседних этапов
- Режимы:
  - Auto-progression: после ответа текущего этапа сам проходит остальные (generic prompt `[auto] Continue...`)
  - Manual (default, `validation_enabled=True`): ждёт `/step` от пользователя
- Переходы: `self.pipeline.transition_to(AgentState.EXECUTION)` — проверяет `ALLOWED_TRANSITIONS`

### Добавление нового этапа SM
1. Добавить значение в `AgentState` (enum)
2. Добавить правила в `ALLOWED_TRANSITIONS`
3. Добавить system prompt в `STAGE_SYSTEM_PROMPTS`
4. Добавить модель по умолчанию в `STAGE_DEFAULT_MODELS`
5. Создать `StageAgent` в `PipelineAgent.__init__()`
6. Добавить сохранение/загрузку состояния

## Инварианты (`invariants.py`)

### Добавление нового инварианта
1. Создать класс-наследник `Invariant` (ABC):
   - `name: str`
   - `check(text) -> bool` — True если нарушений нет
   - `get_error_message() -> str`
   - `get_prompt_block() -> str` — что инжектить в system prompt
2. Добавить парсинг в `InvariantManager._load()` и `save()`

### Валидация в chat()
1. Prompt-block инжектится в system prompt перед вызовом
2. После ответа LLM — `_validator.validate(response)`
3. При нарушении — до 2 ретраев с error message в качестве system prompt
4. Если все ретраи не помогли — warning в конце ответа

## Конвенции кода (всегда соблюдать)

- Классы: PascalCase (`JarvisAgent`, `PipelineAgent`, `TaskContext`, `ForbiddenLibrariesInvariant`)
- Методы: snake_case, публичные без подчёркивания, приватные с `_`
- Docstring: у всех публичных классов/методов. Пример:
  ```python
  def chat(self, user_input: str) -> str:
      """Принимает запрос пользователя, возвращает ответ агента."""
  ```
- Ошибки API: всегда возвращать dict с `"success"` флагом, не кидать исключения
- Миграции БД: `ALTER TABLE ADD COLUMN` в `try/except sqlite3.OperationalError`
- Логирование: `print()` с префиксами, не `logging`
- Пути: вычислять через `Path(__file__).parent.resolve()` относительно `agents/`
- Конфиг API: `CLOUDRU_SECRET_KEY` из `.env`, base_url `https://foundation-models.api.cloud.ru/v1`
- Модели: Qwen/Qwen3-30B-A3B (базовая), Qwen/Qwen3-Coder-Next (средняя), MiniMaxAI/MiniMax-M2.5 (тяжёлая)
