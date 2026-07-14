"""CLI docent: команды init / ask / help / auth.

`docent init`  — построить индекс `.docent/` по документации текущего репо.
`docent ask "…"` — ответить на вопрос о проекте (RAG + git-контекст).
`docent help`  — показать список команд.
`docent auth`  — задел под установку ключа через CLI (пока заглушка).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docent import __version__
from docent.config import (
    API_KEY_ENV,
    Config,
    get_api_key,
    is_initialized,
    load_config,
    save_config,
)

_COMMANDS_HELP = """docent — ассистент разработчика по репозиторию.

Команды:
  docent init            построить индекс по документации текущего репозитория
  docent ask "вопрос"    ответить на вопрос о проекте (RAG + git-контекст)
  docent help            показать этот список команд
  docent auth            установить ключ API (задел, пока не реализовано)

Ключ API читается из переменной окружения {env}.
""".format(env=API_KEY_ENV)


def _repo_root() -> Path:
    """Возвращает корень репозитория для работы (текущий каталог)."""
    return Path.cwd()


def _cmd_init(args: argparse.Namespace) -> int:
    """Строит RAG-индекс по документации текущего репозитория."""
    # Импорт внутри команды: сеть/эмбеддинги нужны только здесь.
    from docent.rag import index

    root = _repo_root()
    if get_api_key() is None:
        print(f"[error] не задан {API_KEY_ENV} в окружении.", file=sys.stderr)
        return 1
    config = load_config(root) if is_initialized(root) else Config()
    save_config(root, config)
    print(f"Индексирую документацию в {root} …")
    stats = index.build(root, config)
    print(f"Готово: файлов {stats.files}, чанков {stats.chunks}. Индекс: .docent/")
    if stats.chunks == 0:
        print(
            "Предупреждение: под паттерны индексации ничего не попало "
            f"({', '.join(config.index_globs)}).",
            file=sys.stderr,
        )
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    """Отвечает на вопрос о проекте, используя индекс и git-контекст."""
    from docent.assistant import ask

    root = _repo_root()
    if not is_initialized(root):
        print("[error] сначала выполните `docent init`.", file=sys.stderr)
        return 1
    if get_api_key() is None:
        print(f"[error] не задан {API_KEY_ENV} в окружении.", file=sys.stderr)
        return 1
    config = load_config(root)
    answer = ask(root, config, args.question)
    print(answer.text)
    if answer.sources:
        print("\nИсточники: " + ", ".join(answer.sources))
    return 0


def _cmd_help(args: argparse.Namespace) -> int:
    """Печатает список команд docent."""
    print(_COMMANDS_HELP)
    return 0


def _cmd_auth(args: argparse.Namespace) -> int:
    """Заглушка: установка ключа через CLI (задел на будущее)."""
    print(
        "auth пока не реализован (задел). "
        f"Установите ключ через переменную окружения {API_KEY_ENV}."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Собирает argparse-парсер со всеми подкомандами."""
    parser = argparse.ArgumentParser(prog="docent", description="Ассистент разработчика по репозиторию.")
    parser.add_argument("--version", action="version", version=f"docent {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="построить индекс по документации репозитория").set_defaults(func=_cmd_init)

    p_ask = sub.add_parser("ask", help="ответить на вопрос о проекте")
    p_ask.add_argument("question", help="вопрос о проекте")
    p_ask.set_defaults(func=_cmd_ask)

    sub.add_parser("help", help="показать список команд").set_defaults(func=_cmd_help)
    sub.add_parser("auth", help="установить ключ API (задел)").set_defaults(func=_cmd_auth)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI: парсит аргументы и вызывает подкоманду."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        print(_COMMANDS_HELP)
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
