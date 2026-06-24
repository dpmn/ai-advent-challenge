# Неделя 4 — День 17. Первый инструмент MCP

## Задание

Реализовать MCP-сервер вокруг NASA API (используя DEMO_KEY).
На данном этапе будем поднимать сервер локально (не зависимо от запуска агента), но с заделом на то, что будем
потом разворачивать его на VDS.

**Сделать:**
- регистрацию инструментов:
  - apod - Astronomy Picture of the Day — фото дня от NASA с описанием.
  - mars_photos - Фотографии с марсоходов NASA (Curiosity, Opportunity, Spirit).
  - neo_feed - Список астероидов, пролетающих близко к Земле, за период.
- описание входных параметров
- возврат результата
- конфиг для подключения Jarvis

**Проверить:**
- подключение mcp к Jarvis
- вызов mcp
- получение и использование результата

## Результат

### Созданные файлы

- `mcp_servers/nasa_mcp/server.py` — MCP-сервер на FastMCP (streamable-http, порт 8765)
- `agents/mcp/servers.json` — добавлен сервер `nasa-api` (disabled по умолчанию)

### Инструменты

| Инструмент | Описание | Параметры |
|---|---|---|
| `apod` | Astronomy Picture of the Day | `date_str` (опц., YYYY-MM-DD) |
| `mars_photos` | Фото марсоходов (через NASA Image Library) | `rover` (curiosity/opportunity/spirit), `query` (опц.), `page` |
| `neo_feed` | Астероиды вблизи Земли | `start_date`, `end_date` (опц., YYYY-MM-DD) |

### Примечания

- Старый NASA Mars Rover Photos API (herokuapp) больше недоступен (404).
- `mars_photos` переписан на NASA Image and Video Library API — возвращает реальные фото марсоходов из архива NASA.
- DEMO_KEY имеет лимит 30 запросов/час — при интенсивном тестировании может вернуть 429.

### Запуск

```bash
python mcp_servers/nasa_mcp/server.py --port 8765
```

### Подключение к Jarvis

```bash
/mcp connect nasa-api
/mcp tools
# далее обычный чат — Jarvis сам вызовет инструменты при необходимости
```

## Сценарий проверки

### Предусловия

- Убедись, что файл `.env` в корне проекта содержит `CLOUDRU_SECRET_KEY` (ключ Cloud.ru Foundation Models) и опционально `NASA_API_KEY` (если нет — будет использован `DEMO_KEY` с лимитом 30 запросов/час).
- Убедись, что порт 8765 свободен.
- Все команды сервера выполняются в **WSL (bash)**, если не указано иное.

### Шаг 1. Запуск NASA MCP-сервера

1. Открой терминал WSL и перейди в корень проекта:
   ```bash
   cd /mnt/c/Users/ardro/PycharmProjects/ai-advent-challenge
   ```
2. Запусти MCP-сервер NASA:
   ```bash
   python mcp_servers/nasa_mcp/server.py --port 8765
   ```
3. **Ожидаемый результат:** в консоли появится сообщение:
   ```
   Starting NASA MCP server on port 8765...
   ```
   Сервер висит, ожидает JSON-RPC запросов.

4. **Проверка (опционально, через второй терминал WSL):**
   ```bash
    curl -X POST http://127.0.0.1:8765/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
   ```
   Должен вернуть JSON с `"serverInfo":{"name":"nasa-api"}`.

### Шаг 2. Запуск WebUI (Flask)

1. Открой **новый** терминал WSL (сервер из шага 1 остаётся запущенным).
2. Запусти Flask-приложение:
   ```bash
   cd /mnt/c/Users/ardro/PycharmProjects/ai-advent-challenge
   python webui/app.py
   ```
3. **Ожидаемый результат:** Flask запущен на `http://127.0.0.1:5000`.

### Шаг 3. Подключение NASA MCP-сервера к Jarvis

1. Открой браузер и перейди на `http://127.0.0.1:5000`.
2. В поле ввода чата отправь команду:
   ```
   /mcp connect nasa-api
   ```
3. **Ожидаемый результат:** ответ:
   ```
   ✅ 'nasa-api' подключён. Инструментов: 3.
   ```
4. Проверь список инструментов:
   ```
   /mcp tools
   ```
5. **Ожидаемый результат:** ответ показывает три инструмента:
   ```
   🛠 Доступные инструменты (3):
     • nasa-api__apod: Get the Astronomy Picture of the Day from NASA...
     • nasa-api__mars_photos: Search photos from NASA Mars rovers...
     • nasa-api__neo_feed: Get the list of Near Earth Objects...
   ```
6. Проверь общий статус MCP:
   ```
   /mcp
   ```
   **Ожидаемый результат:** строка с зелёным кружком, `nasa-api`, connected, 3 tools.

### Шаг 4. Тест инструмента apod (Astronomy Picture of the Day)

1. Отправь в чат запрос:
   ```
   Покажи Astronomy Picture of the Day от NASA за 2023-12-25
   ```
   (Если хочешь проверить без даты — отправь `"Что сегодня показывает APOD?"`)
2. **Ожидаемый результат:** Jarvis вызывает инструмент `nasa-api__apod` с параметром `{"date_str": "2023-12-25"}` и возвращает ответ вида:
   ```
   Title: <название>
   Date: 2023-12-25
   Media type: image
   URL: https://apod.nasa.gov/apod/...
   Description: <описание>
   ```
   В истории чата появится command-сообщение `🧰 MCP-инструменты использованы: 🔧 nasa-api__apod: Title: ...`.

### Шаг 5. Тест инструмента mars_photos

1. Отправь в чат запрос:
   ```
   Найди фото с марсохода Curiosity, query: selfie
   ```
2. **Ожидаемый результат:** Jarvis вызывает `nasa-api__mars_photos` с параметрами `{"rover": "curiosity", "query": "selfie"}` и возвращает список фото (до 10):
   ```
   Rover: curiosity — 10 photo(s) found

   1. <название>
      Thumbnail: https://images-assets.nasa.gov/...
      Date: ...
      Description: ...
   ...
   ```
3. Дополнительно проверь другие варианты:
   - `"Покажи фото с марсохода Opportunity"` — должен вызвать `mars_photos` с `rover="opportunity"`.
   - `"Фото с Spirit, страница 2"` — должен вызвать с `rover="spirit", page=2`.

### Шаг 6. Тест инструмента neo_feed (астероиды)

1. Отправь в чат запрос:
   ```
   Какие астероиды пролетают рядом с Землёй с 2025-06-01 по 2025-06-07?
   ```
2. **Ожидаемый результат:** Jarvis вызывает `nasa-api__neo_feed` с параметрами `{"start_date": "2025-06-01", "end_date": "2025-06-07"}` и возвращает:
   ```
   NEO feed: 2025-06-01 → 2025-06-07

   --- 2025-06-01 (N object(s)) ---
     • <имя астероида> [safe|⚠️ HAZARDOUS]
       Diameter: ... | Velocity: ... km/h | Miss: ... km
   ...
   Total objects: N | Potentially hazardous: N
   ```
3. Дополнительно проверь вызов без дат:
   ```
   Какие астероиды летят к Земле?
   ```
   **Ожидаемый результат:** инструмент вызывается с параметрами по умолчанию (сегодня + 7 дней).

### Шаг 7. Проверка обработки ошибок

1. **Неверное имя марсохода**
   ```
   Фото с марсохода Венера
   ```
   **Ожидаемый результат:** Jarvis вызывает `mars_photos` с `rover="венера"`, инструмент возвращает `"Unknown rover 'венера'. Choose: curiosity, opportunity, spirit."`.

2. **Неверная дата (если в будущем)**
   ```
   Покажи APOD за 2099-01-01
   ```
   **Ожидаемый результат:** NASA API возвращает ошибку, инструмент возвращает сообщение об ошибке HTTP от NASA. Jarvis отображает текст ошибки.

### Шаг 8. Остановка и сброс

1. После проверки останови Flask (Ctrl+C в окне Flask).
2. Останови NASA MCP-сервер (Ctrl+C в окне сервера).
3. При желании отключи MCP-сервер из Jarvis (через уже остановленный WebUI необязательно):
   ```
   /mcp disconnect nasa-api
   ```

### Критерии успеха

- Все три инструмента зарегистрированы и отображаются в `/mcp tools`.
- Каждый инструмент возвращает осмысленный, читаемый ответ с реальными данными NASA.
- Ответ apod содержит `Title`, `URL` и `Description`.
- Ответ mars_photos содержит хотя бы одно фото с URL thumbnail.
- Ответ neo_feed содержит список астероидов с диаметром, скоростью и дистанцией.
- Инструменты корректно обрабатывают ошибочные входные данные (неизвестный rover, дата в будущем).
