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

- `webui/app.py` — Flask-сервер (~80 строк кода + ~230 строк импорта/настроек)
- `webui/templates/index.html` — SPA-шаблон (58 строк, статический)
- `webui/static/script.js` — фронтенд (vanilla JS, ~250 строк)
- `webui/static/style.css` — Darcula-тема (~250 строк)

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

DOMContentLoaded → loadSessions(), loadModels(), loadSettings()
```

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
1. Получить текст, очистить input, заблокировать кнопку ("···")
2. Оптимистично добавить user-бабл в DOM
3. POST /api/chat
4. На успех: renderMessages(data.messages) — всё заменяется (серверный источник истины)
5. Обновить settings и sessions
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
├── #sidebar (280px)
│   ├── .sidebar-header → h2 "Jarvis" + #new-session-btn
│   ├── #session-list → динамический список
│   ├── #sm-section (hidden) → h3 "State Machine" + #sm-status
│   └── .settings-section → h3 "Settings" + элементы
└── #chat-area
    ├── #messages-container → flex column, gap 12px, overflow-y scroll
    └── #input-area → textarea + send-btn
```

При добавлении нового блока в sidebar: проверь, нужно ли его прятать через `display:none` (как `#sm-section`).

## CSS Darcula-тема (`webui/static/style.css`)

### Цветовая палитра (все hex, без CSS-переменных)
| Назначение | Hex | Где используется |
|------------|-----|-----------------|
| Фон основной | `#2b2b2b` | body, input-area |
| Фон sidebar | `#3c3f41` | sidebar, assistant-bubble |
| Фон input/select | `#45494a` | form inputs, command-bubble |
| Текст основной | `#a9b7c6` | body, labels, message text |
| Текст muted | `#646464` | borders, scrollbar, delete-btn (idle) |
| Текст dim | `#888` | settings h3, system messages |
| Акцент синий | `#6897bb` | кнопки, активный session-item border, slider |
| Акцент синий hover | `#80b0d4` | hover кнопок |
| Акцент оранжевый | `#cc7832` | заголовок "Jarvis", delete-btn hover, SM badge |
| Фон user-бабла | `#214283` | `.message.user` |
| Border | `#646464` | sidebar border, settings border-top, input border |

### Сообщения
```css
.message { max-width: 75%; border-radius: 12px; white-space: pre-wrap }
.message.user       { align-self: flex-end; background: #214283; border-bottom-right-radius: 4px }
.message.assistant  { align-self: flex-start; background: #3c3f41; border-bottom-left-radius: 4px }
.message.system     { align-self: center; color: #888; font-style: italic; font-size: 12px; background: transparent }
.message.command    { align-self: flex-start; background: #45494a; font-family: monospace; color: #6897bb }
```

### Новый тип сообщения
Добавить CSS-класс:
```css
.message.newtype { align-self: flex-start; ... }
```
В JS: `div.classList.add("message", "newtype")` — класс `message` уже содержит общие стили.

### Session-item
- Активный: `border-left: 3px solid #6897bb`
- Hover: `background: #4e5254`
- Delete-btn: скрыт (`opacity: 0`), появляется на hover родителя

### SM badge
```css
background: #cc7832; color: #2b2b2b; font-size: 10px; font-weight: bold; padding: 2px 6px; border-radius: 8px;
```

### Скроллбар
```css
::-webkit-scrollbar { width: 8px; background: transparent }
::-webkit-scrollbar-thumb { background: #646464; border-radius: 4px }
::-webkit-scrollbar-thumb:hover { background: #888 }
```
