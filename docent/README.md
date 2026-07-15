# docent

Портабельный CLI-ассистент разработчика. Индексирует документацию (README,
`docs/`) и код (docstring-и и сигнатуры) репозитория в лёгкий локальный RAG:
отвечает на вопросы о проекте (подмешивая git-контекст через MCP) и делает
AI-ревью diff. Ставится как pip/uv-пакет, работает в любом репозитории.

## Установка

```bash
uv pip install -e ./docent      # или: pip install -e ./docent
```

## Ключ API

Провайдер — любой OpenAI-совместимый API (по умолчанию Cloud.ru Foundation
Models, задаётся `base_url` в `.docent/config.json`). Ключ читается из
провайдер-нейтральной переменной окружения:

```bash
export DOCENT_API_KEY=<ваш ключ>
```

## Команды

```bash
docent init            # построить индекс .docent/ по документации и коду репо
docent ask "вопрос"    # ответить на вопрос о проекте (RAG + git-контекст)
docent review          # AI-ревью diff (баги/архитектура/рекомендации); из --diff или stdin
docent help            # список команд
docent auth            # задел под установку ключа через CLI (пока заглушка)
```

Пример ревью текущей ветки:

```bash
git diff main...HEAD | docent review
```

## Как устроено

- **RAG (`docent/rag/`)** — chunking по типу файла: markdown по заголовкам
  (`chunker.py`), Python — docstring-и и сигнатуры через `ast` (`code.py`).
  Эмбеддинги Cloud.ru (`openai/text-embedding-3-small`), brute-force косинус на
  numpy. Индекс в `.docent/` (`index.npy` + `chunks.json`), без FAISS/reranker.
  Паттерны в конфиге: `index_globs` (доки), `code_globs` (код).
- **Ревью (`docent/reviewer.py`)** — по diff собирает контекст (полные версии
  изменённых файлов + соседний RAG-контекст) и просит модель выдать ревью в трёх
  секциях. Production-ready: retry с backoff, fallback-модель из конфига
  (`fallback_model`), усечение больших diff, пропуск не-кодовых diff.
- **MCP (`docent/mcp/`)** — реестр серверов + менеджер, поднимающий каждый
  включённый сервер как stdio-подпроцесс. Сейчас включён сервер `git`
  (`git_current_branch`, `git_head`, `git_list_files`). Структура рассчитана на
  несколько MCP-серверов.
- **LLM (`docent/llm.py`)** — сырой httpx-клиент к Cloud.ru (chat + embeddings).
  Модель задаётся в конфиге; реестр моделей (`config.MODELS`) — задел под
  opencode-style выбор модели.
- **Вывод** — ответ рендерится в терминал как markdown через `rich`
  (`docent/render.py`): в интерактивном терминале с форматированием, при
  перенаправлении в файл/пайп — сырым текстом.
