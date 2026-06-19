# Неделя 3 — День 14. Инварианты и ограничения состояния

## Задание

Добавьте в ассистента инварианты, которые он не имеет права нарушать.

**Примеры инвариантов:**
- выбранная архитектура
- принятые технические решения
- ограничения по стеку
- бизнес-правила

**Сделайте так, чтобы:**
- инварианты хранились отдельно от диалога
- ассистент явно учитывал их в рассуждениях
- ассистент отказывался предлагать решения, которые их нарушают

**Проверьте:**
- что происходит при конфликте запроса и инварианта
- как ассистент объясняет отказ

## Реализация

### Новые файлы

- **`agents/invariants.py`** — модуль системы инвариантов:
  - `Invariant` (ABC) — базовый класс с методами `check()`, `get_error_message()`, `get_prompt_block()`
  - `ForbiddenLibrariesInvariant` — проверяет запрещённые библиотеки (регистронезависимый поиск подстроки)
  - `RequiredTechStackInvariant` — проверяет соблюдение требований к стеку (поиск фраз "pip install", "установите" и т.д.)
  - `AgentValidator` — прогоняет ответ через все enabled-инварианты, возвращает первую ошибку или None
  - `InvariantManager` — загружает/сохраняет инварианты в `.md` файлы (парсинг по `## section`)

- **`agents/memory/invariants/no-external-libs.md`** — инвариант (enabled): «используй только стандартную библиотеку Python, никаких сторонних библиотек»
- **`agents/memory/invariants/no-numpy-pandas.md`** — инвариант (disabled): «запрещены numpy, pandas»

### Изменённые файлы

- **`agents/jarvis.py`**:
  - Импорт `InvariantManager`, `AgentValidator`, `ForbiddenLibrariesInvariant`, `RequiredTechStackInvariant`
  - Константа `_INVARIANTS_DIR`
  - DB-миграция: колонки `invariants_enabled` (INTEGER DEFAULT 1) и `invariants_config` (TEXT DEFAULT '{}')
  - В `__init__()`: инициализация `_invariants_dir`, `_invariants`, `_validator`, `invariants_enabled`, вызов `_load_invariants()`
  - `_load_invariants()` — загружает инварианты из файлов, применяет per-session настройки (enabled_ids)
  - `_save_invariants_state()` — сохраняет состояние инвариантов в БД
  - В `chat()`: инжекция prompt-блоков инвариантов в system prompt + валидация ответа с retry (до 2 попыток)
  - Команды `/invariant` (on/off/toggle/show) и `/invariants`
  - Обновлён `/help`
  - `create_session()` — добавлены поля `invariants_enabled: 1`, `invariants_config: "{}"`
  - `switch_session()` — загружает инварианты после профиля
  - `_load_session()` и `_get_last_session()` — выборка полей `invariants_enabled`, `invariants_config`
  - `reset_conversation()` — перезагружает инварианты

- **`webui/app.py`**:
  - GET `/api/settings` — возвращает `invariants_enabled` и список `invariants`
  - POST `/api/settings` — обрабатывает `invariants_enabled` (on/off через `_handle_command`)

- **`docs/database-schema.md`** — добавлено описание колонок `invariants_enabled`, `invariants_config`

## Сценарий проверки

### Предусловие
Запущен WebUI: `python webui/app.py`, открыт http://127.0.0.1:5000

### Шаг 1. Проверить, что инварианты загружены
1. Отправить в чат: `/invariant`
2. Ожидается: список из двух инвариантов — `no-external-libs` (✅) и `no-numpy-pandas` (❌)

### Шаг 2. Проверить, что включённый инвариант влияет на ответ
1. Убедиться, что `no-external-libs` включён (✅).
2. Отправить: *«Напиши код, который парсит CSV-файл с помощью pandas»*
3. Ожидается: ассистент **не должен** предлагать `pandas`. Ответ должен использовать `csv` из стандартной библиотеки. Если ассистент всё же написал `pandas` — сработает retry (до 2 попыток), и в конце ответа появится `⚠️ Инвариант нарушен. Не удалось исправить: ...`

### Шаг 3. Проверить, что выключенный инвариант не влияет
1. Отправить: `/invariant toggle no-numpy-pandas`
2. Ожидается: `✅ Инвариант 'no-numpy-pandas' включён.`
3. Отправить: *«Напиши код, который считает среднее арифметическое с помощью numpy»*
4. Ожидается: ассистент **не должен** использовать `numpy` — сработает `ForbiddenLibrariesInvariant`, retry попытается исправить.

### Шаг 4. Проверить /invariant show
1. Отправить: `/invariant show no-external-libs`
2. Ожидается: отобразится полный текст инварианта с требованием

### Шаг 5. Проверить отключение проверки инвариантов
1. Отправить: `/invariant off`
2. Отправить: *«Напиши код, который парсит CSV с помощью pandas»*
3. Ожидается: ассистент может спокойно предложить `pandas` — инварианты не проверяются

### Шаг 6. Проверить включение обратно
1. Отправить: `/invariant on`
2. Отправить: `/invariant`
3. Ожидается: список инвариантов с их статусами (состояния toggle сохранились)

### Шаг 7. Проверить настройки в WebUI (опционально)
1. Открыть вкладку Settings (или GET `/api/settings`)
2. Проверить, что отображаются `invariants_enabled: true/false` и список инвариантов с `enabled` статусами

## Результаты проверки

*(будут заполнены после прогона сценария)*
