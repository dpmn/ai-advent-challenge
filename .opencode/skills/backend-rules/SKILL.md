---
name: backend-rules
description: |
  Архитектура agents/jarvis.py, state_machine.py, invariants.py.
  Используй когда нужно понять структуру бэкенда, добавить
  новый метод, интеграцию или исправить баг в агенте
license: MIT
compatibility: opencode
metadata:
  audience: developer
---

## Структура файлов

- `agents/jarvis.py` — основной агент (~1800 строк), один класс `JarvisAgent`
- `agents/state_machine.py` — FSM (~400 строк): `AgentState` (enum), `StageAgent`, `PipelineAgent`
- `agents/invariants.py` — система инвариантов (~300 строк): `Invariant` (ABC), `ForbiddenLibrariesInvariant`, `RequiredTechStackInvariant`, `AgentValidator`, `InvariantManager`
- `agents/mcp_manager.py` — MCP-клиент (~400 строк): `McpConnection`, `McpServerManager`. JSON-RPC 2.0 через `urllib`, handshake, tools/list, tools/call, пагинация, SSE-ответы
- `agents/mcp/__init__.py` — пакет для MCP-конфигов
- `agents/mcp/servers.json` — конфигурация MCP-серверов (name, url, transport, enabled)
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
| compression_enabled | INTEGER 0/1 | Флаг сжатия |
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

### Создание новой сессии
```python
agent.create_session(name="optional")  # возвращает dict сессии
agent.switch_session(session_id)       # переключает, восстанавливает всё состояние
agent.delete_session(session_id)
```

### Добавление нового метода в JarvisAgent
1. Если метод связан с сессией — добавь в `JarvisAgent`.
2. Если нужна новая команда — добавь ветку `elif` в `_handle_command()`.
3. Если нужна новая колонка в БД — добавь `ALTER TABLE ADD COLUMN` в `_init_db()` (в try/except).
4. Если новый параметр настройки — добавь чтение/запись в `/api/settings` в `webui/app.py`.

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

### Команды `/mcp` (в `_handle_command()`)
- `/mcp` — статус (вкл/выкл, список серверов, инструменты)
- `/mcp on` / `/mcp off` — вкл/выкл MCP
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
