# Неделя 4 — День 16. Подключение MCP

## Задание

Доработать Jarvis, чтобы он мог подключаться с mcp-серверам (удалённым и локальным).
Сделаем подключение по аналогии с opencode - через конфиги сервера в /agents/mcp.
Для проверки можно использовать какой-нибудь публичный MCP-сервер без авторизации.

**Jarvis должен уметь:**
- устанавливать MCP-соединения (одно или несколько одновременно)
- получать от MCP список доступных инструментов
- управлять MCP серверами через webui

**Проверить:**
- соединение устанавливается
- список инструментов корректно возвращается
- 

## Реализация

### Структура файлов

```
agents/
├── mcp/
│   ├── __init__.py          # Пакет
│   └── servers.json         # Конфиг MCP-серверов (список)
├── mcp_manager.py           # McpServerManager — синхронный JSON-RPC 2.0 клиент
└── jarvis.py                # Модифицирован: _call_api, chat, _handle_command, БД
webui/
├── app.py                   # +MCP API эндпоинты
├── static/
│   ├── script.js            # +MCP-функции (load, render, toggle, add, connect)
│   └── style.css            # +MCP-стили (.mcp-section, .mcp-server, .mcp-toggle…)
└── templates/
    └── index.html           # +#mcp-section в sidebar
```

### 1. `agents/mcp/servers.json`

Конфигурация в формате JSON, поддерживает несколько серверов с разными транспортами:

```json
{
  "servers": [
    {
      "name": "grep-app",
      "transport": "http",
      "url": "https://mcp.grep.app/",
      "headers": {},
      "enabled": true
    }
  ]
}
```

### 2. `agents/mcp_manager.py` — `McpServerManager`

Классы:
- **`McpConnection`** — хранит состояние одного подключения: url, headers, connected flag, список tools
- **`McpServerManager`** — управляет всеми серверами

**Архитектура:**
- Синхронный JSON-RPC 2.0 клиент поверх `urllib` (mcp SDK не используется — SDK async, а Jarvis синхронный)
- Полный handshake: `initialize` → `notifications/initialized` → `tools/list`
- `tools/call` для вызова инструментов
- Обработка SSE-формата ответа (mcp.grep.app отвечает `event: message\ndata: {...}`)
- Имена инструментов префиксированы именем сервера (`grep-app__searchGitHub`) для избежания коллизий
- `has_active_tools()` — проверяет, есть ли хоть один подключённый сервер с инструментами
- `connect_all_enabled()` — подключает все серверы с `enabled: true`

**Методы:**
| Метод | Описание |
|-------|----------|
| `connect_server(name)` | Handshake + tools/list |
| `disconnect_server(name)` | Сброс connected флага |
| `load_servers()` / `save_servers()` | Чтение/запись servers.json |
| `list_servers()` | Статус всех серверов |
| `get_openai_tools()` | MCP tools → OpenAI function-calling format |
| `execute_tool(name, args)` | Вызов инструмента через tools/call |
| `add_server(name, transport, url)` | Добавление сервера в конфиг |
| `remove_server(name)` | Удаление из конфига |
| `has_active_tools()` | Есть ли подключённые серверы с инструментами |

### 3. `agents/jarvis.py` — модификации

**БД миграция (`_init_db`):**
```python
for mcp_col in [
    ("mcp_enabled", "INTEGER DEFAULT 0"),
    ("mcp_config", "TEXT DEFAULT '{}'"),
]:
    try:
        conn.execute(f"ALTER TABLE sessions ADD COLUMN {mcp_col[0]} {mcp_col[1]}")
    except sqlite3.OperationalError:
        pass
```

**Session load/save:** расширены 4 места (`_get_last_session`, `_load_session`, `create_session`, `switch_session`) для `mcp_enabled`, `mcp_config`.

**`__init__`:**
```python
self.mcp_manager = McpServerManager()
self.mcp_enabled = bool(self.current_session.get("mcp_enabled", False))
self.mcp_max_iterations = 10
```

**`_call_api(messages, tools=None)`:**
- Добавлен опциональный параметр `tools: list = None`
- Если передан — включается в payload
- Возвращает `tool_calls` из ответа:
```python
return {
    "success": True,
    "content": content,
    "tool_calls": message.get("tool_calls"),
    "usage": usage,
    "finish_reason": choice.get("finish_reason", "unknown")
}
```

**`chat()` — tool calling loop:**
```
1. Если mcp_enabled + has_active_tools() → get_openai_tools()
2. _call_api(messages, tools=mcp_tools)
3. Пока есть tool_calls и итераций < max (10):
   a. Добавить assistant message с tool_calls в messages (transient)
   b. Выполнить execute_tool для каждого вызова
   c. Добавить tool result в messages
   d. _call_api(messages, tools=mcp_tools)
4. Финальный assistant_message → в БД и conversation_history
5. Все промежуточные вызовы → только в memory, не в БД
6. Tool trace → command-сообщение в БД для видимости в UI
```

**Команды (`_handle_command`):**
| Команда | Действие |
|---------|----------|
| `/mcp` | Статус всех серверов |
| `/mcp connect <name>` | Подключить сервер (авто-включает MCP) |
| `/mcp disconnect <name>` | Отключить сервер (авто-выключает MCP, если последний) |
| `/mcp on` | Включить передачу tools модели |
| `/mcp off` | Выключить передачу tools |
| `/mcp add <name> <url> [http]` | Добавить сервер в конфиг |
| `/mcp remove <name>` | Удалить сервер из конфига |
| `/mcp tools` | Список всех инструментов |

**Редизайн UX (по результатам тестирования):**
- `/mcp connect <name>` теперь сам включает `mcp_enabled` (не требует `/mcp on`)
- `/mcp disconnect <name>` последнего сервера авто-выключает MCP
- Явный prompt-блок с описанием инструментов удалён — модель корректно использует tools через нативный `tools` параметр API

### 4. WebUI

**`app.py` — новые эндпоинты:**
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/mcp` | Статус MCP (серверы, состояние) |
| POST | `/api/mcp/toggle` | Вкл/выкл MCP |
| POST | `/api/mcp/add` | Добавить сервер |
| DELETE | `/api/mcp/remove` | Удалить сервер |
| POST | `/api/mcp/connect` | Подключить сервер |
| POST | `/api/mcp/disconnect` | Отключить сервер |

**`index.html` — `#mcp-section`:**
- Toggle switch (вкл/выкл MCP)
- Список серверов: имя, статус, URL, количество tools, кнопки ▶/✕
- Форма добавления нового сервера
- Подсказка о доступных инструментах

**`script.js` — функции:**
- `loadMcp()` — загрузка и рендер MCP-секции
- `renderMcp(data)` — рендер списка серверов
- `toggleMCP()` — вкл/выкл MCP
- `addMcpServer()` — добавление сервера
- `mcpServerAction(name, action)` — connect/disconnect

**`style.css` — новые классы:**
- `.mcp-section`, `.mcp-header`, `.mcp-toggle`, `.mcp-server`, `.mcp-server-info`, `.mcp-tools-info`, `.mcp-add-form`, `.mcp-btn`

### 5. Protocol & Edge Cases

- **Транспорт:** HTTP JSON-RPC 2.0. mcp.grep.app использует SSE-формат (`event: message\ndata: {...}`), обрабатывается парсером в McpConnection
- **Ограничение Cloud.ru FM:** `tool_choice="auto"` не поддерживается, поэтому параметр не передаётся. Модели Qwen3-Coder-Next и MiniMax-M2.5 корректно вызывают tools без явного tool_choice
- **Защита:** max_iterations=10 предотвращает бесконечный loop
- **Коллизии имён:** инструменты разных серверов могут пересекаться → префикс `{server_name}__{tool_name}`
- **Токены:** MCP-вызовы раздувают контекст, но финальный assistant + tool_trace сохраняются компактно

## Сценарий проверки

### Предварительные шаги
1. Запусти Flask: `python ./webui/app.py`
2. Открой браузер на `http://127.0.0.1:5000`
3. Убедись, что в левом сайдбаре есть секция "MCP"

### Проверка через чат

**1. `/mcp`**
- Должен показать статус "выкл" и список серверов с конфигурацией

**2. `/mcp connect grep-app`**
- Проверка что MCP включился
- Должен показать инструмент: `searchGitHub`

**3. Tool-calling**
- Отправь: `Найди реальные примеры использования useEffect в React. Используй searchGitHub.`
- Модель должна вызвать `searchGitHub`, вернуть настоящий код из репозиториев

**4. `/mcp`**
- Должен показать статус "вкл", сервер "grep-app" с зелёным кружком 🟢

**5. `/mcp disconnect grep-app`**
- Должен показать "MCP автоматически выключен — нет активных серверов"

### Проверка через WebUI

**6. Переключение MCP toggle**
- В sidebar секции MCP переключи toggle в положение ON
- Должны автоматически подключиться серверы

**7. Connect/Disconnect кнопки**
- Нажми ▶ (Connect) на сервере
- Статус должен смениться на 🟢 Connected (N tools)

**8. Добавление сервера**
- Введи имя "test", URL "https://example.com/mcp"
- Сервер должен появиться в списке
- Обнови страницу — сервер должен сохраниться

**9. `/mcp tools`**
- Должен показать searchGitHub с описанием и схемой

**10. `/help`**
- В справке должна быть команда /mcp

### Проверка изоляции

**11. Создай новую сессию через UI**
- MCP должен быть выключен
- `/mcp` покажет "выкл"

### Проверка curl

**12. API эндпоинты**
```bash
curl -s http://127.0.0.1:5000/api/mcp | python3 -m json.tool
# Должен вернуть статус MCP

curl -s -X POST -H "Content-Type: application/json" \
  -d '{"name":"grep-app"}' \
  http://127.0.0.1:5000/api/mcp/connect
# Должен подключить сервер
```

## Выводы

MCP интегрирован в JarvisAgent через синхронный JSON-RPC 2.0 клиент, без использования mcp SDK (который async и несовместим с синхронной архитектурой агента). Native function calling (OpenAI-формат) используется для передачи инструментов модели, без prompt-based подхода.

**Ключевые архитектурные решения:**
- Управление MCP через `servers.json` — добавление/удаление серверов без правки кода
- McpServerManager — общий на весь агент, не зависит от сессии (но флаг `mcp_enabled` сессионный)
- Tool calling loop с max_iterations=10, transient сообщения в памяти
- Команды + WebUI для управления — полный UX

**Багфиксы по результатам теста:**
1. `/mcp connect` не включал `mcp_enabled` — починено
2. Явный prompt с описанием инструментов не нужен — удалён, модель работает через tools параметр
3. `/mcp disconnect` последнего сервера авто-выключает MCP
4. `_invariants_dir` не был инициализирован до `create_session()` — починено
