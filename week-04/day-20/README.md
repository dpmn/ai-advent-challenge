# Неделя 4 — День 20. Orchestration MCP

## Задание

Зарегистрируйте несколько MCP-серверов.

**Сделайте так, чтобы:**
- агент выбирал нужный инструмент
- корректно маршрутизировал запросы
- выполнял длинный флоу взаимодействия

**Проверьте:**
- сценарий, в котором используются инструменты с разных серверов
- корректность выбора и порядка вызовов

**Результат:**
- Длинный флоу взаимодействия с несколькими MCP-серверами и инструментами

## Реализация

### Созданные файлы

- `mcp_servers/analyzer_mcp/server.py` — новый MCP-сервер (FastMCP, порт 8768) с инструментами:
  - `extract_keywords(text, max_keywords)` — извлечение топ-N ключевых слов (с фильтрацией стоп-слов EN/RU)
  - `generate_report(title, content)` — генерация markdown-отчёта с метаданными
- `agents/mcp/servers.json` — добавлен сервер `analyzer` (enabled: true)
- `week-04/day-20/test_orchestration.py` — оркестрационный тест

### Тестирование

**Part A — прямая цепочка (McpServerManager):** 5 инструментов с 2 серверов последовательно:
1. `composer__fetch_data("apod")` — NASA APOD
2. `composer__summarize_text(text, 50)` — сжатие
3. `analyzer__extract_keywords(text, 5)` — ключевые слова
4. `analyzer__generate_report("Space Report")` — отчёт
5. `composer__save_to_file("space_report.md")` — сохранение

**Part B — LLM-оркестрация (JarvisAgent):** модель Qwen3-Coder-Next самостоятельно выбрала и вызвала все 5 инструментов в правильном порядке через tool-calling loop. Проверка выполнена по истории вызовов в `conversation_history`.

**Результат:** обе части пройдены ✅

## Сценарий проверки

### Подготовка

- Убедись, что `.env` содержит `CLOUDRU_SECRET_KEY`.
- Открой **три терминала**:
  - Терминал A: `python3 mcp_servers/composer_mcp/server.py --port 8767`
  - Терминал B: `python3 mcp_servers/analyzer_mcp/server.py --port 8768`
  - Терминал C: `python3 webui/app.py`
- Открой браузер на `http://127.0.0.1:5000`

### Проверка через WebUI

1. Включи MCP (кнопка включения или команда `/mcp on`)
2. Отправь агенту в чат запрос:

   ```
   Получи данные NASA APOD, сделай краткое содержание, выдели ключевые слова, сформируй красивый markdown-отчёт с заголовком "Space Report" и сохрани его в файл space_report.md. 
   ```
   ```
   Если APOD недоступен — используй fallback-текст: "A stunning view of the Milky Way galaxy arching over a mountain range."
   ```

3. **Ожидаемый результат в чате:**
   - Агент последовательно вызывает все 5 инструментов с двух разных серверов
   - Финальный ответ: summary APOD, список ключевых слов, подтверждение сохранения файла
   - Файл `mcp_servers/composer_mcp/output/space_report.md` создан, содержит markdown-отчёт
