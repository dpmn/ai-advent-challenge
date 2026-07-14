# docent

Портабельный CLI-ассистент разработчика. Индексирует документацию репозитория
(README, `docs/`) в лёгкий локальный RAG и отвечает на вопросы о проекте,
подмешивая git-контекст через MCP. Ставится как pip/uv-пакет, работает в любом
репозитории.

## Установка

```bash
uv pip install -e ./docent      # или: pip install -e ./docent
```

## Ключ API

Провайдер — Cloud.ru Foundation Models (OpenAI-совместимый API). Ключ читается
из переменной окружения:

```bash
export CLOUDRU_SECRET_KEY=<ваш ключ>
```

## Команды

```bash
docent init            # построить индекс .docent/ по документации текущего репо
docent ask "вопрос"    # ответить на вопрос о проекте (RAG + git-контекст)
docent help            # список команд
docent auth            # задел под установку ключа через CLI (пока заглушка)
```

## Как устроено

- **RAG (`docent/rag/`)** — chunking markdown по заголовкам, эмбеддинги Cloud.ru
  (`openai/text-embedding-3-small`), brute-force косинус на numpy. Индекс лежит
  в `.docent/` (`index.npy` + `chunks.json`), без FAISS/reranker.
- **MCP (`docent/mcp/`)** — реестр серверов + менеджер, поднимающий каждый
  включённый сервер как stdio-подпроцесс. Сейчас включён сервер `git`
  (`git_current_branch`, `git_head`, `git_list_files`). Структура рассчитана на
  несколько MCP-серверов.
- **LLM (`docent/llm.py`)** — сырой httpx-клиент к Cloud.ru (chat + embeddings).
  Модель задаётся в конфиге; реестр моделей (`config.MODELS`) — задел под
  opencode-style выбор модели.
