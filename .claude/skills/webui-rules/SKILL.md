---
name: webui-rules
description: |
  Архитектура webui/: Flask-сервер (app.py), SPA на vanilla JS
  (script.js), Claude-inspired тема (style.css), шаблон index.html.
  Используй когда нужно добавить новый эндпоинт, UI-компонент,
  изменить стиль или исправить баг в интерфейсе
---

## Карта файлов (роуты и функции смотри в самих файлах — они короткие)

- `webui/app.py` — Flask-сервер, все роуты (`/api/sessions`, `/api/chat`, `/api/models`, `/api/settings`, `/api/stats`, `/api/mcp/*`); один глобальный `JarvisAgent`, сессии живут внутри агента
- `webui/static/script.js` — фронтенд: vanilla JS, `fetch()` + async/await, без фреймворков, сборщиков и зависимостей
- `webui/static/style.css` — тема через CSS custom properties: тёмная в `:root`, светлая — переопределения в `body.light`; все цвета только через переменные
- `webui/templates/index.html` — статический SPA-шаблон без Jinja-логики: `#sidebar` (сессии, MCP, settings) + `#chat-area` (topbar, messages, input)

## Модели и провайдеры

- `AVAILABLE_MODELS = CLOUD_MODELS (env AVAILABLE_MODELS) + LOCAL_MODELS (кванты Ollama)`
- `MODEL_PROVIDERS`: model_id → `{base_url, api_key}`. Новая модель с другим провайдером — регистрируй здесь
- Смена модели в `POST /api/settings` переключает `agent.base_url`, `agent.api_key` и `agent.model_provider` ("local"/"cloud")
- Ollama: env `OLLAMA_BASE_URL` (default `http://localhost:11434/v1`), api_key-заглушка `"ollama"`; `/api/models` отдаёт `local_models` — фронт помечает их суффиксом `(local)`

## Добавление эндпоинта / настройки

1. Роут в `app.py`: `return jsonify({...})`; данные брать из методов `agent.*`
2. Нужно на фронте — `fetch()` в `script.js`
3. Новая настройка — чтение в `get_settings()`, запись в `update_settings()` (app.py) + `loadSettings()`/`updateSettings()` (script.js)

## Правила фронтенда

- Сервер — источник истины: после `sendMessage()` история целиком заменяется `renderMessages(data.messages)`
- Рендер-функции очищают контейнер (`innerHTML = ""`) перед заполнением
- Пользовательский текст — только через `textContent` или `escHtml()` (защита от XSS)
- Динамические элементы — `document.createElement()`; статические — в `index.html`; новый блок в sidebar — реши, прятать ли по умолчанию (`display:none`, как `#sm-section`)
- Тема: `body.light` + `localStorage('jarvis-theme')`, переключатель ☾/☀ в topbar
- Техстрока RAG (`.rag-debug`): `buildRagDebugDiv()` строит её из `rag_debug` ответа `/api/chat` (тайминги, chunks, confidence; на локальном пути ещё tok/s и load). Хранится в `lastRagDebug`/`lastRagDebugSessionId` и дорисовывается в `renderMessages()` — так она переживает перерисовки; не персистится (сбрасывается перезагрузкой и следующим сообщением)
