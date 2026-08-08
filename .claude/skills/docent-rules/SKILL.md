---
name: docent-rules
description: |
  Архитектура docent/: портабельный CLI-ассистент (pip/uv-пакет) —
  cli.py (подкоманды), assistant.py (ask), agent.py (do, агентный цикл,
  --commit включает security-ворота из security.py), reviewer.py (review),
  rag/, mcp/ (stdio-серверы git и files).
  Используй когда нужно добавить CLI-команду, MCP-инструмент/сервер,
  поменять RAG-индексацию, агентный цикл или security-ворота docent
---

## Карта файлов (детали — в docstring-ах и docent/README.md)

- `docent/cli.py` — argparse-подкоманды: `_cmd_*()` + `_build_parser()` + `_COMMANDS_HELP`
- `docent/config.py` — `Config` (dataclass), реестр `MODELS`, `resolve_model()`, `.docent/config.json`
- `docent/llm.py` — сырой httpx-клиент: `chat()`, `chat_full()` (то же, но отдаёт всё тело ответа — `finish_reason` и служебное поле `gateway`), `chat_tools()` (function calling), `embed()`
- `docent/assistant.py` — `ask`: one-shot RAG + git-контекст (контекст собирает код, не модель)
- `docent/agent.py` — `do`: tool-calling цикл (модель сама выбирает инструменты), анти-цикл повторов, `_MAX_STEPS`, принудительный финал; `--commit` оборачивает цикл генерации (`_generate()`) внешним циклом ворот из `security.py` (до `_MAX_FIX_ROUNDS` доработок), коммит делает сам через `subprocess` по pathspec файлов прогона (`written`), не через MCP-инструмент
- `docent/security.py` — security-ворота: `scan()` (второй вызов LLM с отдельным промптом под стек проекта) → `Verdict` (уровень + находки + флаг блокировки гейтвеем), `feedback()` (текст возврата в генерацию), `summary()`, `log_round()` (jsonl в `.docent/security-loop.jsonl`); fail closed на любой нештатной ветке
- `docent/reviewer.py` — `review`: ревью diff — JSON-находки → фильтр самоопровержений → verify-вердикты → markdown; модель `review_model` (heavy), retry/fallback; `parse_json_block()` публичный, переиспользуется `security.py`
- `docent/rag/` — `index.py` (build/query, `_collect_files` + `_SKIP_DIRS`), `chunker.py` (markdown по заголовкам), `code.py` (python через ast), `store.py` (numpy cosine)
- `docent/mcp/` — `registry.py` (`SERVERS`), `manager.py` (stdio-сессии, `call()`, `tool_specs()`), `servers/git.py`, `servers/files.py`
- `tests/` — pytest

## Куда добавлять

1. Новый MCP-инструмент → функция с `@mcp.tool` в `servers/*.py`; регистрация автоматическая (менеджер собирает через `list_tools()`).
2. Новый MCP-сервер → модуль в `servers/` с `main()` (stdio) + `ServerSpec` в `registry.py`.
3. Новая CLI-команда → `_cmd_*()` + парсер в `_build_parser()` + строка в `_COMMANDS_HELP` + docstring шапки `cli.py`.
4. Новая модель → `config.MODELS`; в конфиге хранится id провайдера, `resolve_model()` понимает и алиас, и id.

## Неочевидное (грабли)

- MCP здесь — **stdio-подпроцессы** (`python -m …`), НЕ streamable-http как в `mcp_servers/` этого репо. Не путать транспорты.
- `.docent/config.json` **кэширует дефолты**: изменение дефолтов в `config.py` не подхватывается в уже инициализированных репо — правь config.json или пересоздавай (грабли day-34 с `index_globs`).
- Ключ — `DOCENT_API_KEY` (провайдер-нейтральный), не `CLOUDRU_SECRET_KEY`. В этом репо: `export DOCENT_API_KEY=$(grep '^CLOUDRU_SECRET_KEY=' .env | cut -d= -f2-)`.
- Сырой httpx, НЕ openai SDK — умышленно (минимум зависимостей), не заменять.
- `agent.py` принудительно подменяет `repo_path` корнем репо в каждом вызове — модель не может увести агента в другой каталог. Не убирать.
- Тексты `[error]` инструментов — интерфейс с моделью: формулировки запретов («политика безопасности, обойти нельзя») гасят попытки обхода; менять аккуратно.
- `servers/files.py`: запреты (`.env*`, `*.key`, `*.pem`, `.git/`, `.docent/`, служебные каталоги) действуют и на чтение, и на запись; `write_file` пишет сразу и возвращает unified diff.
- Установка editable (`uv pip install -e ./docent`): правки кода подхватываются без переустановки, новые entry-points — нет.
- Портабельность: в коде docent никаких привязок к этому репо (пути, имена) — он работает в любом репозитории.
- Гарантии качества ревью — структурные и программные (JSON-парсер, фильтр «бага нет», вердикты), НЕ промпт-запреты: модель их нарушает (урок PR #23). Не заменять код-фильтры на «запрещено писать X» в промпте.
- Сетевые ошибки httpx оборачиваются в `LLMError` в `llm._post()` — вызывающие ловят только `LLMError`. Heavy-модель на ревью думает дольше 120s — таймаут ревью отдельный (`_REVIEW_TIMEOUT`).
- Коммит в `agent.py` **не** MCP-инструмент, а прямой `subprocess` внутри `_run()`: если бы модель видела инструмент коммита, она могла бы вызвать его в обход security-ворот.
- Ворота смотрят только на файлы прогона (`written`, собирается по `write_file`), не на весь working tree — иначе чужие незакоммиченные изменения (например, отвергнутые прошлой задачей) попадают в чужой diff и чужой коммит. `.docent/` исключён из pathspec отдельно, иначе лог ворот попадает в проверяемый diff.
- Отклонённые ворота изменения уходят в `git stash`, а не остаются в working tree: иначе следующая задача читает файл с секретом, тащит его в свой контекст, и LLM Gateway блокирует уже её — одна проваленная задача глушит весь прогон.
- Если гейтвей блокирует не только скан, но и саму генерацию — секрет уже в истории сообщений агента, доработка через фидбэк не поможет (следующий вызов тоже будет заблокирован); `_run()` в этом случае выходит сразу, без холостых заходов.

## Конвенции

- Docstring у всех публичных функций/классов (по-русски); описания `@mcp.tool` — по-английски, с перечислением Args.
- Вывод: полезное — в stdout (рендер rich), диагностика/прогресс — в stderr.
- У каждого инструмента лимит вывода (`_MAX_OUTPUT_CHARS` = 40k) и защита входа (валидация ref-ов в git, `_safe_path` в files).
