# Неделя 5 — День 23. Реранкинг и фильтрация

## Задание

**Добавьте второй этап после поиска:**
- reranker или фильтр релевантности (порог similarity / отдельная модель / heuristic)

**Настройте:**
- порог отсечения нерелевантных результатов
- топ-K до и после фильтрации

**Сравните:**
- качество без фильтра/rewriting
- качество с фильтром

**Результат:**
- Улучшенный RAG: фильтрация/реранкинг + query rewrite + сравнение режимов

## Выполнено

### Новые файлы:
- `ragger/reranker.py` — функции `threshold_filter()` (отсечение по similarity score) и `llm_rerank()` (батч-реранкинг через LLM, один запрос на все чанки с JSON-массивом оценок)

### Изменённые файлы:
- `ragger/search.py` — рефакторинг: добавлен класс `RagPipeline` с методами `run()` (search → filter → rerank → slice) и `compare_modes()` (A/B-тест 3 режимов). Функция `search()` сохранена для обратной совместимости. `_load_index` → публичный алиас `load_index`
- `agents/jarvis_session.py` — миграция БД: новые колонки `rag_top_k_before`, `rag_top_k_after`, `rag_threshold`, `rag_mode`. Метод `_save_rag_config()`. Загрузка/сохранение полей во всех CRUD-методах
- `agents/jarvis.py` — новые поля `rag_top_k_before/after/threshold/mode` в `__init__`. В `chat()` — вызов `RagPipeline.run()` вместо inline-поиска. В `get_stats()` — блок конфигурации RAG
- `agents/jarvis_commands.py` — `/rag` расширена: `config <key> <val>`, `compare <query>`. Добавлены `_handle_rag_config()` и `_handle_rag_compare()`
- `mcp_servers/ragger/server.py` — `_load_index` → `load_index`

### Команды:
```
/rag                    — статус + параметры
/rag on|off             — вкл/выкл RAG
/rag config <key> <val> — threshold / top_k_before / top_k_after / mode
/rag compare <query>    — сравнение threshold / rerank / hybrid
```

### Режимы:
- `threshold` — FAISS search → отсев по similarity score
- `rerank` — FAISS search → LLM-реранкинг всех чанков
- `hybrid` — FAISS search → threshold → LLM-реранкинг оставшихся
