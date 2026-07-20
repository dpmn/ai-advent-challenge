---
name: fridge-chef-context
description: >
  Карта целевого репо fridge-chef (Go): стек, layout, пакеты, команды запуска/проверки,
  собственный харнес. Грузи в начале advanced-дня, чтобы не перечитывать репо.
---

## Что это

- Пет-проект: генератор рецептов по продуктам из холодильника. RAG по корпусу русскоязычных рецептов.
- Формат — пошаговый **wizard** (продукты → варианты → рецепт), не чат: каждый LLM-вызов самодостаточен, истории нет.
- Репо: `github.com/dpmn/fridge-chef`, рабочая ветка `mvp`. Локально: `/home/development/personal/fridge-chef`.
- Прод: `https://fridge-chef.ru` (MVP, день 35). С дня 36 — основной проект advanced-блока.

## Стек

- **Go** — рантайм-сервис (API, поиск, LLM). stdlib-first, роутинг `net/http` (method-pattern, Go 1.22+), без фреймворков.
- **Python** — только офлайн-ingestion корпуса. В рантайме Python нет.
- LLM — OpenAI-совместимый провайдер через env (Cloud.ru / DeepSeek-V4-Flash по `.env.example`).

## Layout и пакеты

```text
cmd/fridge-chef/   # main: сборка зависимостей + запуск, логики нет
internal/
  api/       # HTTP-слой: маршруты, decode/writeJSON/writeError, хендлеры
  config/    # конфиг из env (FRIDGE_ADDR, CORPUS_PATH, LLM_BASE_URL/API_KEY/MODEL)
  corpus/    # in-memory корпус (recipes.jsonl+manifest+canon), ErrNotFound, Canon
  match/     # подбор рецептов-кандидатов по продуктам
  generate/  # сборка рецепта: Strict/Combine/Creative, интерфейс Chatter
  llm/       # OpenAI-совместимый клиент chat-модели (context+timeout+retry)
ingestion/   # Python-пайплайн подготовки корпуса (не Go)
corpus/vNNN/ # версии корпуса (JSONL), gitignored
web/         # вшитый в бинарник wizard-UI
docs/        # architecture.md (видение), roadmap.md (фичи по одной)
```

## Данные и инварианты

- Данные — сердце: корпус версионируется, активная версия иммутабельна, переключение/откат атомарны.
- При низкой уверенности retrieval — честное «не нашёл», не галлюцинации.

## Запуск и проверка

- `go run ./cmd/fridge-chef` → `curl http://localhost:8080/healthz` (нужен `.env` с `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`).
- Тесты `go test ./...`; линт `go vet ./...` + `gofmt -l .` (пусто). Эндпоинты — в `docs/architecture.md`.

## Собственный харнес репо (в его `.claude/`)

- skill `go-style` — конвенции Go (примеры, антипаттерны, шаблон файла). **Конвенции — там, здесь не дублируй.**
- субагенты `go-architect` (дизайн перед фичей), `go-reviewer` (ревью после реализации).
- Работаем по одной фиче из `docs/roadmap.md`; фазовые ворота — в его `CLAUDE.md`.

## Глубокие вопросы по коду

Не читай весь репо в свой контекст — спрашивай субагент `fridge-chef-explorer` точечным вопросом.
