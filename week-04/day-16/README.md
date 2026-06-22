# Неделя 4 — День 16. Подключение MCP

## Задание

Установи MCP SDK, напиши MCP-сервер (`agents/mcp_server.py`) на FastMCP с 2-3 инструментами (echo, время, калькулятор).

**Интеграция в агента:**
- Создай `agents/mcp_manager.py` — класс `McpManager`, который:
  - запускает MCP-сервер как подпроцесс при `/mcp connect`
  - держит фоновый `threading.Thread` с `asyncio` event loop (MCP SDK async)
  - предоставляет синхронные методы `list_tools()` и `call_tool()`
  - на `disconnect()` завершает процесс и останавливает event loop
  - через `atexit.register()` чистит процесс при аварийном выходе
- Добавь в JarvisAgent команды `/mcp connect/list/call/status`
- Инжекть описание инструментов в system prompt при подключении (только в не-SM-режиме)
- Добавь колонку `mcp_enabled` в БД (через `ALTER TABLE ADD COLUMN`)

**Интеграция в WebUI:**
- Добавь секцию MCP в sidebar: кнопка Connect/Disconnect, статус, список инструментов
- Добавь эндпоинты: `/api/mcp/status /connect /disconnect /call`

**Проверь:**
- `/mcp connect` → `/mcp list` — инструменты отображаются
- WebUI: подключение работает, список инструментов виден
- LLM в не-SM-режиме получает описания инструментов в system prompt
- При `Ctrl+C` Flask — процесс MCP не остаётся висеть

**Результат:**
- MCP-сервер + McpManager-клиент в JarvisAgent
- Панель управления MCP в WebUI
