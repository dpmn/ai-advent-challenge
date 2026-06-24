---
name: webui-rules
description: |
  Архитектура webui/. Flask-роуты, JS-фронтенд, Darcula-тема CSS.
  Используй когда нужно добавить новый эндпоинт, UI-компонент,
  изменить стиль или исправить баг в интерфейсе
license: MIT
compatibility: opencode
metadata:
  audience: developer
---

## Структура файлов

- `webui/app.py` — Flask-сервер
- `webui/templates/index.html` — SPA-шаблон (#chat-topbar, #sidebar-toggle, #theme-toggle, .inner wrappers)
- `webui/static/script.js` — фронтенд (vanilla JS, тема, MCP, сайдбар)
- `webui/static/style.css` — Claude-inspired theme (CSS custom properties, light/dark override, all components)

## Flask-сервер (`webui/app.py`)

### Инициализация
```python
agent = JarvisAgent(
    model=AVAILABLE_MODELS[0],
    temperature=0.3,
    system_prompt="Ты — полезный AI-ассистент по имени Jarvis...",
    context_limit=40000,
    max_tokens=5000,
)
```
Один глобальный инстанс на весь сервер. Сессии управляются внутри `JarvisAgent`.

### Все эндпоинты

| Метод | Путь | Что делает |
|-------|------|------------|
| GET | `/` | `render_template("index.html")` |
| GET | `/api/sessions` | Список сессий + `current_id` |
| POST | `/api/sessions` | Создать сессию. Body: `{name, sm_enabled}` |
| DELETE | `/api/sessions/<id>` | Удалить сессию |
| POST | `/api/sessions/<id>/switch` | Переключить на сессию |
| GET | `/api/sessions/<id>/messages` | Сообщения сессии (только если id = current) |
| POST | `/api/chat` | Отправить сообщение. Body: `{message}`. Возвращает `{response, messages}` |
| GET | `/api/models` | Список моделей + текущая |
| GET | `/api/settings` | Все настройки |
| POST | `/api/settings` | Обновить model, temperature, max_tokens, context_limit, invariants_enabled |
| GET | `/api/stats` | Статистика агента (текст) |
| GET | `/api/mcp` | MCP-статус: `{enabled, servers: [...], tools: [...]}` |
| POST | `/api/mcp/toggle` | Вкл/выкл MCP. Body: `{enabled: bool}` |
| POST | `/api/mcp/add` | Добавить сервер. Body: `{name, url, transport?}` |
| DELETE | `/api/mcp/<name>` | Удалить сервер |
| POST | `/api/mcp/<name>/connect` | Подключить сервер |
| POST | `/api/mcp/<name>/disconnect` | Отключить сервер |

### Добавление нового эндпоинта
1. Добавить метод/роут в `app.py`
2. Если данные нужны на фронтенде — вызвать в `script.js` через `fetch()`
3. Если нужно в settings — добавить чтение в `get_settings()` и запись в `update_settings()`
4. Если новая модель — добавить ID в `DEFAULT_MODELS`

### Типовой паттерн эндпоинта
```python
@app.route("/api/example")
def example():
    data = agent.some_method()
    return jsonify({"key": data})
```

## Фронтенд (`webui/static/script.js`)

### Стек
Vanilla JavaScript. Никаких фреймворков, сборщиков, зависимостей.
Все запросы — `async/await` + `fetch()`.

### Архитектура
```javascript
let currentSessionId = null;

DOMContentLoaded → loadSessions(), loadModels(), loadSettings(), loadMcp()
```

### Theme & Sidebar
- `toggleSidebar()` — toggles `.hidden` on `#sidebar`
- `setTheme(isLight)` — sets `body.light`, updates button icon (`☾`/`☀`), saves to `localStorage('jarvis-theme')`
- `toggleTheme()` — toggles `body.light`

Theme restored on load from `localStorage`.

### MCP-функции
- `loadMcp()` — GET /api/mcp, загружает MCP-статус
- `renderMcp(data)` — отрисовывает MCP-секцию: чекбокс, список серверов, tools-info
- `toggleMcp()` — POST /api/mcp/toggle, переключает вкл/выкл
- `addMcpServer()` — POST /api/mcp/add, берёт name/url из формы
- `mcpServerAction(name, action)` — connect/disconnect/delete сервера

Инициализация: `loadMcp()` вызывается в `DOMContentLoaded` и в `sendMessage()` (после ответа сервера).

### Функции для работы с сессиями
- `loadSessions()` — GET /api/sessions, обновляет список
- `renderSessions(sessions, currentId)` — отрисовывает `.session-item` в `#session-list`
- `switchSession(id)` — POST /api/sessions/{id}/switch
- `createSession()` — POST /api/sessions, спрашивает имя у prompt()
- `deleteSession(id)` — DELETE, с confirm()

### Функции сообщений
- `loadMessages()` — GET /api/sessions/{id}/messages
- `renderMessages(messages)` — рендер всех сообщений
- `sendMessage()` — POST /api/chat

### Паттерн sendMessage()
```
1. Получить текст, очистить input, сбросить высоту textarea, заблокировать кнопку ("···")
2. Оптимистично добавить user-бабл в DOM (удалить .empty-chat если есть)
3. POST /api/chat
4. На успех: renderMessages(data.messages) — всё заменяется (серверный источник истины)
5. Обновить settings, sessions и MCP (loadMcp)
6. Разблокировать кнопку
```

### Функции settings
- `loadModels()`, `loadSettings()` — загрузка
- `updateSettings()` — POST текущих значений

### Правила при добавлении UI-компонента
1. Если элемент статический — добавь в `index.html`
2. Если динамический — создавай через `document.createElement()` в `script.js`
3. Все рендер-функции должны очищать контейнер (`innerHTML = ""`) перед заполнением
4. Сообщения — через `textContent` (безопасно, HTML экранируется)
5. Список сессий — через `innerHTML` + `escHtml()` для имени сессии
6. Новый элемент в настройках: добавь в `loadSettings()` (чтение) и `updateSettings()` (запись)
7. Не используй debounce/throttle — WIP, но старайся избегать частых вызовов

### Функция escHtml
```javascript
function escHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}
```

### События
- `#send-btn.onclick` → `sendMessage()`
- `#message-input` `keydown` → Enter отправляет, Shift+Enter новая строка
- `#message-input` `input` → auto-resize height (max 200px)
- `#sidebar-toggle.onclick` → `toggleSidebar()`
- `#theme-toggle.onclick` → `toggleTheme()`
- `#temp-slider.oninput` → `updateSettings()` (на каждый slide)
- `#model-select.onchange` → `updateSettings()`
- `#max-tokens-input.onchange` → `updateSettings()`
- `#context-limit-input.onchange` → `updateSettings()`

### Добавление нового обработчика
```javascript
document.getElementById("element-id").addEventListener("change", async () => {
    await updateSettings();
});
```

## HTML-шаблон (`webui/templates/index.html`)

Структура (без Jinja2-логики, полностью статический):
```
#app (flex, 100vh)
├── #sidebar (280px, hides via .hidden → margin-left: -280px)
│   ├── .sidebar-header → h2 "Jarvis" + #new-session-btn
│   ├── #session-list → динамический список
│   ├── #sm-section (hidden by default) → h3 "State Machine" + #sm-status
│   ├── #mcp-section → h3 "MCP" + .mcp-toggle (checkbox + toggle-track)
│   │                       #mcp-servers-list + #mcp-tools-info
│   │                       .mcp-add-form (name + url + Add server button)
│   └── .settings-section → h3 "Settings" + элементы
└── #chat-area
    ├── #chat-topbar → #sidebar-toggle (☰), #chat-topbar-title ("Jarvis"), #theme-toggle (☾/☀)
    ├── #messages-container → .inner (max-width: 768px, центрирован)
    └── #input-area
        └── #input-inner (max-width: 768px, центрирован, border-radius: 16px)
            ├── #message-input (textarea, auto-resize)
            └── #send-btn ("Send →")
```

При добавлении нового блока в sidebar: проверь, нужно ли его прятать через `display:none` (как `#sm-section`).

## CSS — Claude-inspired theme (`webui/static/style.css`)

### CSS Custom Properties (`:root` + `body.light`)

Все цвета через CSS-переменные. Тёмная тема — `:root`, светлая — `body.light`.

Ключевые переменные:
| Переменная | Dark | Light | Назначение |
|-----------|------|-------|------------|
| `--bg-main` | `#212121` | `#f0f2f5` | Основной фон |
| `--bg-sidebar` | `#2a2a2a` | `#ffffff` | Фон sidebar |
| `--bg-user` | `#2b3f5c` | `#d3e3fd` | Фон user-бабла |
| `--bg-assistant` | `#2d2d2d` | `#ffffff` | Фон assistant-бабла |
| `--accent` | `#d4a574` | `#d4a574` | Акцент (кнопки, переключатели) |
| `--text-primary` | `#d1d5db` | `#333` | Основной текст |
| `--danger` | `#cc7832` | `#cc7832` | Опасные действия |

### Сообщения
```css
.message { max-width: 85%; border-radius: 14px; white-space: pre-wrap; line-height: 1.6 }
.message.user       { align-self: flex-end; background: var(--bg-user); border-bottom-right-radius: 4px }
.message.assistant  { align-self: flex-start; background: var(--bg-assistant); border-bottom-left-radius: 4px }
.message.system     { align-self: center; color: var(--text-muted); font-style: italic; font-size: 12px }
.message.command    { align-self: flex-start; font-family: monospace; color: var(--accent) }
```

### Chat layout
- `#messages-container` — flex, центрирует `.inner` (max-width: 768px)
- `#input-inner` — обёртка вокруг textarea+send, max-width: 768px, border-radius: 16px
- `#chat-topbar` — flex bar с sidebar-toggle (☰), title, theme-toggle (☾/☀)

### Theme toggle (`#theme-toggle`)
- `☾` — тёмная тема (значок по умолчанию)
- `☀` — светлая тема
- Переключение: `document.body.classList.toggle("light")`
- Сохранение: `localStorage.setItem("jarvis-theme", "light"|"dark")`

### Sidebar
- Ширина 280px, скрывается через `.hidden { margin-left: -280px }`
- Плавная анимация: `transition: margin-left 0.25s ease`

### Session-item
- Активный: `border-left: 3px solid var(--accent)`
- Hover: `background: var(--bg-hover)`
- Delete-btn: скрыт (`opacity: 0`), появляется на hover родителя

### SM badge
```css
background: var(--accent); color: var(--accent-text); font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 4px;
```

### MCP-стили
- `.mcp-toggle` — кастомный toggle switch (checkbox + `.toggle-track` с `::before`)
  - off: серый фон + кружок; on: `--accent` фон + кружок смещён `translateX(18px)`
- `.mcp-server-item` — строка сервера с именем, точкой статуса, кол-вом tools
- `.mcp-dot` — кружок статуса (серый), `.mcp-dot.connected` — зелёный `#6a8759`
- `.mcp-server-actions` — кнопки ▶ / ✕ / 🗑
- `.mcp-add-form` — форма добавления сервера (name, url, button)
- `.mcp-tools-info` — строка с активными инструментами
- `.mcp-empty` — заглушка "No servers configured"

### Input area
- `#input-inner` — обёртка с border-radius: 16px, padding, border
- `#input-inner:focus-within` — border-color: `var(--accent)`
- `#message-input` — textarea, transparent bg, no border/outline, max-height: 200px
- `#send-btn` — `var(--accent)` фон, `var(--accent-text)` текст, border-radius: 10px

### Скроллбар
```css
::-webkit-scrollbar { width: 6px; background: transparent }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 3px }
::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-hover) }
```
