# Неделя 4 — День 18. Планировщик и фоновые задачи

## Задание

Сделайте MCP-инструмент с отложенным или периодическим выполнением.

Пример:
👉 reminder
👉 периодический сбор данных
👉 регулярный summary

Инструмент должен:
👉 сохранять данные (JSON / SQLite)
👉 выполняться по расписанию
👉 возвращать агрегированный результат

Результат:
Агент, который работает 24/7 и периодически выдаёт сводку

---

## Решение — Space Monitor

**Архитектура:** новый MCP-сервер `mcp_servers/space_monitor_mcp/` с фоновым потоком-сборщиком данных NASA (APOD + NEO).

**Поток данных:**
1. LLM вызывает `monitor_start(interval_seconds, sources)` — запускает фоновый поток
2. Фоновый поток каждые `interval` секунд делает запрос к NASA API с циклическим смещением дат:
   - APOD: `today - (counter % 30)` дней — обходит 30 последних дней
   - NEO: 7-дневное окно, сдвинутое на `(counter % 30)` дней назад
3. Результат сохраняется в SQLite (`data/monitor.db`)
4. LLM вызывает `monitor_summary()` — получает агрегированную сводку
5. LLM вызывает `monitor_stop()` — останавливает сборщик

**Инструменты (4):**
- `monitor_start` — запустить сборщик (interval, sources)
- `monitor_stop` — остановить сборщик
- `monitor_status` — статус + статистика БД
- `monitor_summary` — агрегированная сводка

**Файлы:**
- `mcp_servers/space_monitor_mcp/server.py` — FastMCP-сервер (порт 8766)
- `mcp_servers/space_monitor_mcp/collector.py` — фоновый поток + SQLite + NASA API
- `agents/mcp/servers.json` — добавлен сервер `space-monitor` (disabled)
- `.gitignore` — добавлен `*.db`

**Зависимости:** только `mcp[fastmcp]`, `python-dotenv` (уже есть в проекте).

---

## Проверить

1. Запустить MCP-сервер `space-monitor` на порту 8766
2. Через WebUI или curl проверить 4 инструмента: monitor_start, monitor_status, monitor_summary, monitor_stop
3. Убедиться, что фоновый сбор данных работает: через 2-3 тика появляются новые записи в SQLite
4. Проверить агрегацию: monitor_summary группирует по источникам, показывает количество и последние записи
5. Остановить сборщик, проверить, что статус изменился на "no active monitor"

---

## Сценарий проверки (через webui)

### Подготовка

1. Открой терминал **WSL** (bash). Перейди в корень проекта:
   ```bash
   cd /mnt/c/Users/ardro/PycharmProjects/ai-advent-challenge
   ```

2. Убедись, что в `.env` есть ключ NASA API:
   ```bash
   grep NASA_API_KEY .env
   ```
   Если нет — скопируй `DEMO_KEY` или рабочий ключ.

3. **Очисти БД** (чтобы начать с чистого листа):
   ```bash
   rm -f mcp_servers/space_monitor_mcp/data/monitor.db
   ```

4. **Запусти Space Monitor сервер** (терминал 1):
   ```bash
   python3 mcp_servers/space_monitor_mcp/server.py --port 8766
   ```
   Ожидаемый результат: `Starting Space Monitor MCP server on port 8766...`. Сервер висит и ждёт запросов.

5. **Запусти webui** (терминал 2):
   ```bash
   python3 webui/app.py
   ```
   Ожидаемый результат: `Running on http://127.0.0.1:5000`.

6. **Открой браузер** на Windows: http://127.0.0.1:5000

---

### Шаг 1. Подключить MCP-сервер Space Monitor в webui

В боковой панели слева найди секцию **MCP**:

1. **Включи MCP** — нажми на чекбокс `Enable MCP`. Рядом должно появиться `MCP: ON`.
2. **Добавь сервер** — в поля формы введи:
   - Name: `space-monitor`
   - URL: `http://127.0.0.1:8766/mcp`
   - Нажми кнопку **Add server**
3. **Подключи сервер** — в появившейся карточке сервера нажми кнопку **▶ Connect**

Ожидаемый результат:
- Сервер появился в списке
- Рядом с именем сервера зелёная точка 🟢
- Строка `4 tools: space-monitor__monitor_start, ...`

---

### Шаг 2. Запустить сборщик через чат

Введи в чат и отправь:

```
Запусти фоновый сбор данных NASA NEO с интервалом 10 секунд
```

**Ожидаемое поведение:**
- Агент вызывает MCP-инструмент `monitor_start(interval_seconds=10, sources=["neo"])`
- Ответ агента: `Monitor started: interval=10s, sources=[neo]`
- В истории чата появится command-блок с результатом вызова инструмента

Если агент вместо вызова инструмента отвечает текстом — уточни запрос:
```
Используй MCP-инструмент monitor_start с аргументами: interval_seconds=10, sources=["neo"]
```

---

### Шаг 3. Проверить статус после запуска

Отправь (сразу, без паузы):

```
Покажи статус монитора
```

**Ожидаемое поведение:**
- Агент вызывает `monitor_status()`
- В ответе: `Running: yes`, `Interval: 10s`, `Ticks completed: 0`, `DB records: 0`

---

### Шаг 4. Дождаться первого сбора

Подожди **15 секунд**. За это время фоновый поток выполнит первый тик (запрос к NASA NEO API).

---

### Шаг 5. Проверить статус после первого тика

Отправь ещё раз:

```
Покажи статус монитора
```

**Ожидаемое поведение:**
- `Ticks completed: 1`
- `Total collections: 1`
- `DB records: 1`
- `Last write: <ISO-время>` (текущее время UTC)

Если NASA API вернул ошибку — в статусе будет `Last error`, но это допустимо.

---

### Шаг 6. Получить сводку

Отправь:

```
Дай сводку собранных данных
```

**Ожидаемый ответ:**
```
Space Monitor Summary — 1 total records
  neo: 1 entries (<дата> .. <дата>)
    • NEO: <N> objects, <M> potentially hazardous
```

---

### Шаг 7. Дождаться ещё 2 тиков

Подожди **25 секунд** (накопится 2–3 тика).

---

### Шаг 8. Проверить накопление

Отправь:

```
Покажи статус и сводку
```

**Ожидаемое поведение:**
Агент сделает 2 вызова (`monitor_status` + `monitor_summary`) — либо последовательно, либо выбери один запрос для простоты. Лучше отправить два сообщения по очереди.

**В статусе:**
- `Ticks completed: 3` (или больше)
- `DB records: 3`

**В сводке:**
```
Space Monitor Summary — 3 total records
  neo: 3 entries (<дата1> .. <дата3>)
    • NEO: <N1> objects, <M1> potentially hazardous
    • NEO: <N2> objects, <M2> potentially hazardous
    • NEO: <N3> objects, <M3> potentially hazardous
```

Ключевая проверка: числа объектов различаются (циклические даты дают разные выборки).

---

### Шаг 9. Остановить сборщик

Отправь:

```
Останови монитор
```

**Ожидаемое поведение:**
- Агент вызывает `monitor_stop()`
- Ответ: `Monitor stopped. Total collected: 3 entries.`

Проверь статус снова:

```
Покажи статус монитора
```

- `Running: no`

---

### Шаг 10. Повторный запуск и защита от двойного запуска

Отправь:

```
Запусти монитор снова с интервалом 10 секунд (только NEO)
```

**Ожидаемое поведение:**
- Агент вызывает `monitor_start(10, ["neo"])`
- Ответ: `Monitor started: interval=10s, sources=[neo]`

Теперь отправь повторно (без остановки):

```
Запусти монитор
```

**Ожидаемое поведение:**
- Агент вызывает `monitor_start`
- Ответ: `Monitor is already running. Call stop() first.` — защита от повторного запуска.

---

### Шаг 11. Валидация невалидного source

Останови монитор сначала:

```
Останови монитор
```

Затем отправь:

```
Запусти монитор с источниками apod и mars
```

**Ожидаемое поведение:**
- Агент вызывает `monitor_start` с `sources=["apod", "mars"]`
- Ответ: `Invalid source 'mars'. Valid sources: apod, neo`

---

### Шаг 12. Проверка данных в SQLite

Останови оба сервера (**Ctrl+C** в терминалах 1 и 2).

Затем выполни прямой SQL-запрос:

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('mcp_servers/space_monitor_mcp/data/monitor.db')
rows = conn.execute('SELECT id, ts, source, summary FROM collections ORDER BY ts').fetchall()
for r in rows:
    print(r)
conn.close()
"
```

Ожидаемый результат:
- 3+ строки (NEO данные)
- `ts` — возрастающие временные метки ISO 8601
- `summary` — строки вида `NEO: <N> objects, <M> potentially hazardous`

Проверь также, что поле `data` содержит полный JSON-ответ NASA:

```bash
python3 -c "
import sqlite3, json
conn = sqlite3.connect('mcp_servers/space_monitor_mcp/data/monitor.db')
row = conn.execute('SELECT data FROM collections WHERE source=\"neo\" LIMIT 1').fetchone()
d = json.loads(row[0])
print('near_earth_objects keys:', list(d.get('near_earth_objects', {}).keys())[:3])
conn.close()
"
```

Должен увидеть даты в ключах `near_earth_objects`.

---

### Итого проверяется

| Что | Как |
|---|---|
| Подключение MCP-сервера через webui | MCP-секция, кнопка Connect, зелёная точка |
| Запуск сборщика через чат | prompt → агент → `monitor_start` |
| Статус работающего сборщика | prompt → агент → `monitor_status` |
| Агрегированная сводка | prompt → агент → `monitor_summary` |
| Фоновый сбор (несколько тиков) | пауза 40с между запросами |
| Остановка сборщика | prompt → агент → `monitor_stop` |
| Данные в SQLite | прямой SELECT |
| Защита от двойного запуска | повторный `monitor_start` без остановки |
| Валидация source | `sources: ["apod", "mars"]` → ошибка |
