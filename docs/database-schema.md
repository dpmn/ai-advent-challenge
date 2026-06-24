# Схема базы данных JarvisAgent

## Обзор

База данных SQLite хранится в файле `agents/memory/jarvis_history.db`.

Вся структура автоматически создаётся при первом запуске агента (метод `_init_db()`).

В проекте **нет индексов и миграций** — достаточно простого описания таблиц и связей.

---

## Таблицы

### sessions

Хранит информацию о сессиях диалога. Каждая сессия — независимый чат с отдельной историей.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID сессии |
| `name` | TEXT NOT NULL | Имя сессии (человекочитаемое) |
| `created_at` | TIMESTAMP | Дата создания (автоматически: CURRENT_TIMESTAMP) |
| `last_active_at` | TIMESTAMP | Последняя активность (обновляется при сохранении сообщений) |
| `prompt_tokens` | INTEGER DEFAULT 0 | Количество prompt tokens за сессию |
| `completion_tokens` | INTEGER DEFAULT 0 | Количество completion tokens за сессию |
| `total_tokens` | INTEGER DEFAULT 0 | Сумма всех токенов за сессию |
| `compression_enabled` | INTEGER DEFAULT 1 | Флаг включённого сжатия (0/1) |
| `context_strategy` | TEXT DEFAULT NULL | Стратегия управления контекстом: `sliding_window`, `sticky_facts`, `branching` или NULL |
| `sticky_facts` | TEXT DEFAULT '{}' | JSON-объект с ключевыми фактами (для стратегии sticky_facts) |
| `task_context` | TEXT DEFAULT '{}' | JSON-объект с рабочей памятью (TaskContext) — данные текущей задачи |
| `profile_name` | TEXT DEFAULT 'default' | Имя активного профиля (долговременная память) |
| `sm_enabled` | INTEGER DEFAULT 0 | Флаг State Machine (0/1). Устанавливается при создании сессии, не меняется |
| `sm_validation_enabled` | INTEGER DEFAULT 1 | Флаг валидации перед переходом между этапами SM (0/1) |
| `sm_current_state` | TEXT DEFAULT 'PLANNING' | Текущий этап SM: `PLANNING`, `EXECUTION`, `VALIDATION`, `DONE` |
| `sm_artifacts` | TEXT DEFAULT '{}' | JSON-объект с артефактами этапов (plan, execution, validation, done) |
| `sm_stage_configs` | TEXT DEFAULT '{}' | JSON-объект с per-stage настройками LLM (model, temperature, max_tokens) |
| `invariants_enabled` | INTEGER DEFAULT 1 | Глобальный флаг проверки инвариантов |
| `invariants_config` | TEXT DEFAULT '{}' | JSON с enabled_ids — какие инварианты активны в сессии |
| `mcp_enabled` | INTEGER DEFAULT 0 | Флаг включённого MCP (0/1) |
| `mcp_config` | TEXT DEFAULT '{}' | JSON-объект с конфигурацией MCP-серверов |

**Важные поля для архитектуры:**
- Основная связь: `sessions.id` → `messages.session_id`, `compressed_summaries.session_id`, `branches.session_id`, `stage_messages.session_id`
- `context_strategy` определяет логику хранения/обработки сообщений
- `sm_enabled` включает State Machine-маршрутизацию в `chat()`

---

### messages

Хранит все сообщения всех сессий. Каждая запись — одно сообщение (user/assistant/system).

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID сообщения |
| `session_id` | INTEGER NOT NULL | ID сессии (связь с `sessions.id`) |
| `role` | TEXT NOT NULL | Роль сообщения: `user`, `assistant`, `system`, `command` |
| `content` | TEXT NOT NULL | Текст сообщения |
| `timestamp` | TIMESTAMP | Время создания (автоматически: CURRENT_TIMESTAMP) |

**Связи:**
- Внешний ключ: `FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE`
- При удалении сессии **автоматически удаляются** все её сообщения

**Использование:**
- Сортировка по `timestamp ASC` для получения хронологической истории
- Фильтрация по `session_id` для изоляции чатов
- `role = "system"` — системный промпт, остальные — user/assistant/commands

---

### compressed_summaries

Хранит итоги сжатия истории (архивные сводки).

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID записи |
| `session_id` | INTEGER NOT NULL | ID сессии (связь с `sessions.id`) |
| `content` | TEXT NOT NULL | Текст суммаризации (сжатая форма истории) |
| `source_count` | INTEGER DEFAULT 0 | Количество сообщений, из которых собрана суммаризация |
| `tokens_before` | INTEGER DEFAULT 0 | Исходный объём токенов перед сжатием |
| `tokens_after` | INTEGER DEFAULT 0 | Токенов после сжатия |
| `created_at` | TIMESTAMP | Время создания суммаризации |

**Связи:**
- Внешний ключ: `FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE`
- При удалении сессии **автоматически удаляются** все суммаризации

**Использование:**
- При загрузке сессии суммаризации подключаются к началу истории как `system`-сообщения
- Позволяют восстановить "старую" историю без потерь при сжатии

---

### branches

Хранит информацию о ветках диалога (для стратегии `branching`).

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID ветки (0 = main) |
| `session_id` | INTEGER NOT NULL | ID сессии (связь с `sessions.id`) |
| `name` | TEXT NOT NULL | Имя ветки (например, "experiment-1", "fallback") |
| `parent_branch_id` | INTEGER DEFAULT 0 | ID родительской ветки (для отслеживания иерархии) |
| `checkpoint_message_index` | INTEGER DEFAULT 0 | Индекс сообщения, от которого создана ветка |
| `created_at` | TIMESTAMP | Время создания ветки |

**Связи:**
- Внешний ключ: `FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE`
- При удалении сессии **автоматически удаляются** все ветки

**Использование:**
- При инициализации `branching`-стратегии загружаются сохранённые ветки из БД
- На каждой ветке свой `conversation_history`, свои счётчики токенов
- Реальные сообщения хранятся в оперативной памяти (`self.branches[<>]["messages"]`), а не в этой таблице (таблица хранит метаданные о ветках)

---

### stage_messages

Хранит изолированную историю сообщений каждого этапа State Machine.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID записи |
| `session_id` | INTEGER NOT NULL | ID сессии (связь с `sessions.id`) |
| `stage` | TEXT NOT NULL | Этап SM: `PLANNING`, `EXECUTION`, `VALIDATION`, `DONE` |
| `role` | TEXT NOT NULL | Роль сообщения: `user`, `assistant` |
| `content` | TEXT NOT NULL | Текст сообщения |
| `timestamp` | TIMESTAMP | Время создания (автоматически: CURRENT_TIMESTAMP) |

**Связи:**
- Внешний ключ: `FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE`
- При удалении сессии **автоматически удаляются** все сообщения этапов

**Использование:**
- При инициализации `PipelineAgent` загружает историю каждого этапа отдельно
- Каждый `StageAgent` работает только со своими сообщениями (фильтр по `stage`)
- Позволяет ре-входить на этап без потери контекста

---

## Схема связей

```
sessions
    │
    ├── 1:N ──▶ messages
    │               └── (пример: сессия №5 имеет 127 сообщений)
    │
    ├── 1:N ──▶ compressed_summaries
    │               └── (пример: сессия №5 имеет 8 суммаризаций)
    │
    ├── 1:N ──▶ branches
    │               └── (пример: сессия №5 имеет 3 ветки: main, experiment, fallback)
    │
    └── 1:N ──▶ stage_messages
                    └── (пример: сессия №5 имеет 12 сообщений этапов SM)
```

**ON DELETE CASCADE**: удаление сессии → автоматическое удаление всех связанных данных.

---

## Логика работы с БД

### Создание сессии
1. Вставляется строка в `sessions`
2. Возвращается `id` новой сессии
3. Сообщения и суммаризации хранятся только в памяти (БД не трогаем)
4. При первом `user`-сообщении оно **сохраняется в `messages`**

### Сохранение сообщений
- Каждое `user` и `assistant` сообщение сохраняется в `messages` сразу после отправки в API
- Строка обновляется `last_active_at` в `sessions`

### Загрузка сессии
1. Берётся последняя активная сессия из `sessions`
2. Загружаются все её сообщения из `messages` (по `session_id`)
3. Загружаются суммаризации из `compressed_summaries`
4. Для `branching` загружаются метаданные из `branches`

### Очистка истории
- Команда `/clear` удаляет все `messages` для текущей сессии
- Суммаризации и метаданные стратегий сбрасываются в памяти (но остаются в БД до удаления сессии)

### Удаление сессии
- Удаляется строка из `sessions`
- Благодаря `ON DELETE CASCADE` автоматически удаляются все `messages`, `compressed_summaries`, `branches`

---

## Примеры запросов

**Последняя активная сессия:**
```sql
SELECT id, name, created_at, last_active_at, prompt_tokens, completion_tokens, total_tokens
FROM sessions
ORDER BY last_active_at DESC LIMIT 1;
```

**Все сообщения сессии с ID = 5:**
```sql
SELECT role, content, timestamp
FROM messages
WHERE session_id = 5
ORDER BY timestamp ASC;
```

**Все ветки сессии с ID = 5:**
```sql
SELECT id, name, parent_branch_id, checkpoint_message_index, created_at
FROM branches
WHERE session_id = 5;
```

**Суммаризации сессии с ID = 5:**
```sql
SELECT content, source_count, tokens_before, tokens_after
FROM compressed_summaries
WHERE session_id = 5
ORDER BY created_at ASC;
```

---

## Space Monitor Database (`mcp_servers/space_monitor_mcp/data/monitor.db`)

Вспомогательная SQLite БД, создаваемая `BackgroundCollector` из `mcp_servers/space_monitor_mcp/collector.py`.

### collections

Хранит собранные данные NASA (APOD, NEO).

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Уникальный ID записи |
| `ts` | TEXT NOT NULL | Время сбора (UTC ISO-8601) |
| `source` | TEXT NOT NULL | Источник: `apod` или `neo` |
| `data` | TEXT NOT NULL | Полный JSON-ответ от NASA API |
| `summary` | TEXT | Краткое текстовое описание (например, "APOD 2026-06-20: Title") |

Используется инструментами MCP-сервера:
- `monitor_status` — общее количество записей, время последней записи
- `monitor_summary` — группировка по `source`, количество, диапазон дат, последние 5 записей

---

## Практические рекомендации

**Для разработки/отладки:**
1. `sqlite3 agents/memory/jarvis_history.db` — открыть БД
2. `.tables` — таблицы
3. `SELECT * FROM sessions;` — проверить сессии
4. `SELECT COUNT(*) FROM messages;` — общее количество сообщений

**Важно:**
- Не редактировать БД вручную — есть риск нарушить целостность данных
- При удалении сессии — проверяйте каскадное удаление
- Для сброса всей истории — удалите файл БД (`rm agents/memory/jarvis_history.db`) и перезапустите агента
