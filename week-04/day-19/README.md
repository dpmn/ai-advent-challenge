# Неделя 4 — День 19. Композиция MCP-инструментов

## Задание

**Создайте несколько MCP-инструментов (tools), например:**
- search
- summarize
- saveToFile

**Реализуйте пайплайн:**
- первый инструмент получает данные
- второй — обрабатывает
- третий — сохраняет результат

**Проверьте:**
- автоматическое выполнение цепочки
- корректность передачи данных между инструментами

**Результат:**
- Автоматический пайплайн из нескольких MCP-инструментов

---

## Решение — Composer MCP

**Архитектура:** новый MCP-сервер `mcp_servers/composer_mcp/` с тремя stateless-инструментами, образующими пайплайн.

**Поток данных (оркестрируется LLM):**
1. LLM вызывает `fetch_data(source="apod")` — получает сырые данные NASA
2. LLM вызывает `summarize_text(text=..., max_words=40)` — сжимает до краткой сводки
3. LLM вызывает `save_to_file(filename="apod_today.txt", content=...)` — сохраняет в файл

**Инструменты (3):**
- `fetch_data(source, param)` — получить данные из NASA (APOD / NEO)
- `summarize_text(text, max_words)` — сжать текст до N слов (APOD: Title + Date + ключ описания, NEO: шапка + итог, generic: extractive)
- `save_to_file(filename, content)` — сохранить текст в `output/` (с защитой от path traversal)

**Файлы:**
- `mcp_servers/composer_mcp/server.py` — FastMCP-сервер (порт 8767)
- `mcp_servers/composer_mcp/output/` — директория для сохранённых файлов
- `agents/mcp/servers.json` — добавлен сервер `composer` (enabled)

## Проверить

1. Запустить MCP-сервер `composer` на порту 8767
2. Через WebUI или curl проверить 3 инструмента:
   - `fetch_data("apod")` — возвращает текст APOD
   - `summarize_text(<текст APOD>, 30)` — возвращает краткую сводку
   - `save_to_file("test.txt", "hello")` — сохраняет файл в `output/`
3. Убедиться, что цепочка выполняется: fetch → summarize → save
4. Проверить защиту от path traversal в `save_to_file`

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

3. **Запусти Composer сервер** (терминал 1):
   ```bash
   python3 mcp_servers/composer_mcp/server.py --port 8767
   ```
   Ожидаемый результат: `Starting Composer MCP server on port 8767...`. Сервер висит и ждёт запросов.

4. **Запусти webui** (терминал 2):
   ```bash
   python3 webui/app.py
   ```
   Ожидаемый результат: `Running on http://127.0.0.1:5000`.

5. **Открой браузер** на Windows: http://127.0.0.1:5000

---

### Шаг 1. Подключить MCP-сервер Composer в webui

В боковой панели слева найди секцию **MCP**:

1. **Включи MCP** — нажми на чекбокс `Enable MCP`. Рядом должно появиться `MCP: ON`.
2. **Добавь сервер** — в поля формы введи:
   - Name: `composer`
   - URL: `http://127.0.0.1:8767/mcp`
   - Нажми кнопку **Add server**
3. **Подключи сервер** — в появившейся карточке сервера нажми кнопку **▶ Connect**

Ожидаемый результат:
- Сервер появился в списке
- Рядом с именем сервера зелёная точка 🟢
- Строка `3 tools: composer__fetch_data, composer__summarize_text, composer__save_to_file`

---

### Шаг 2. Проверить fetch_data через чат

Введи в чат и отправь:

```
Получи данные APOD за сегодня через MCP
```

**Ожидаемое поведение:**
- Агент вызывает `fetch_data(source="apod")`
- Ответ содержит: Title, Date, Media type, URL, Description
- Если агент не вызывает инструмент — уточни:
  ```
  Используй MCP-инструмент fetch_data с аргументом source="apod"
  ```

---

### Шаг 3. Проверить summarize_text через чат

Отправь:

```
Суммаризируй следующий текст до 30 слов: <скопируй Title и Description из ответа выше>
```

Или прямой запрос:

```
Используй MCP-инструмент summarize_text с текстом: "Title: APOD Test Date: 2025-01-01 Description: A beautiful picture of space taken by NASA" и max_words=30
```

**Ожидаемое поведение:**
- Агент вызывает `summarize_text(text=..., max_words=30)`
- Ответ: краткая версия (Title + Date + первые слова Description)

---

### Шаг 4. Проверить save_to_file через чат

Отправь:

```
Сохрани текст "Hello from Composer MCP!" в файл test_composer.txt через MCP-инструмент save_to_file
```

**Ожидаемое поведение:**
- Агент вызывает `save_to_file(filename="test_composer.txt", content="Hello from Composer MCP!")`
- Ответ: `Saved: /.../output/test_composer.txt (25 bytes)`

---

### Шаг 5. Полный пайплайн (ключевой тест)

Отправь один запрос, запускающий всю цепочку:

```
Возьми фото дня NASA, сделай краткое описание в 30 слов и сохрани в файл apod_summary.txt
```

**Ожидаемое поведение — два варианта:**

**Вариант A (рекомендуемый):** Агент делает 3 последовательных вызова:
1. `fetch_data("apod")` → получает текст APOD
2. `summarize_text(текст_APOD, 30)` → получает краткую сводку
3. `save_to_file("apod_summary.txt", сводка)` → сохраняет файл
4. Финальный ответ: "Данные APOD сохранены в apod_summary.txt (N bytes)"

**Вариант B:** Агент вызывает 1 инструмент, но аргументом передаёт результат предыдущего шага в одной строке. Тоже считается корректным, если данные передаются.

Ключевая проверка: **данные из первого инструмента передаются во второй, а из второго — в третий.**

---

### Шаг 6. Path traversal protection

Отправь:

```
Сохрани текст "secret" в файл ../../etc/passwd через save_to_file
```

**Ожидаемое поведение:**
- Агент вызывает `save_to_file(filename="../../etc/passwd", content="secret")`
- Ответ: `Error: invalid filename '../../etc/passwd'. Use a simple filename without path separators.`

---

### Шаг 7. Проверить сохранённый файл

Останови Composer сервер (**Ctrl+C** в терминале 1).

Выполни в терминале:

```bash
ls -la mcp_servers/composer_mcp/output/
```

Ожидаемый результат: файлы `test_composer.txt` и `apod_summary.txt` (или другие, которые создавались в шагах).

Проверь содержимое:

```bash
cat mcp_servers/composer_mcp/output/apod_summary.txt
```

---

### Итого проверяется

| Что | Как |
|---|---|
| Подключение MCP-сервера через webui | MCP-секция, кнопка Connect, зелёная точка |
| fetch_data — получение данных | prompt → агент → `fetch_data` |
| summarize_text — обработка | prompt → агент → `summarize_text` |
| save_to_file — сохранение | prompt → агент → `save_to_file` |
| Полный пайплайн (3 шага) | один prompt → 3 последовательных вызова |
| Передача данных между шагами | выход fetch → вход summarize → вход save |
| Path traversal protection | попытка `../../etc/passwd` |
| Файл на диске | `ls output/`, `cat` |
