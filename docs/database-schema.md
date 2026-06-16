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

**Важные поля для архитектуры:**
- Основная связь: `sessions.id` → `messages.session_id`, `compressed_summaries.session_id`, `branches.session_id`
- `context_strategy` определяет логику хранения/обработки сообщений

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
    └── 1:N ──▶ branches
                    └── (пример: сессия №5 имеет 3 ветки: main, experiment, fallback)
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
