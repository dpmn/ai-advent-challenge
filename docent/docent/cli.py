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
from docent.render import render_markdown
from docent.spinner import Spinner

_COMMANDS_HELP = """🎓 docent — ассистент разработчика по репозиторию.

Команды:
  📚 init            построить индекс по документации и коду текущего репозитория
  💬 ask "вопрос"    ответить на вопрос о проекте (RAG + git-контекст)
  🔍 review          AI-ревью diff: баги, архитектура, рекомендации (diff из --diff/stdin)
  ❓ help            показать этот список команд
  🔑 auth            установить ключ API (задел, пока не реализовано)

🔐 Ключ API читается из переменной окружения {env}.
""".format(env=API_KEY_ENV)


def _repo_root() -> Path:
    """Возвращает корень репозитория для работы (текущий каталог)."""
    return Path.cwd()


def _print_bullets(header: str, items: list[str]) -> None:
    """Печатает заголовок и элементы маркированным списком (если непусто)."""
    if not items:
        return
    print(f"\n{header}:")
    for item in items:
        print(f"  • {item}")


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
    with Spinner("Индексирую документацию"):
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
    with Spinner("Доцент думает"):
        answer = ask(root, config, args.question)
    render_markdown(answer.text)
    _print_bullets("📄 Источники", answer.sources)
    _print_bullets("🔧 MCP-инструменты", answer.mcp_tools)
    return 0


def _read_diff(args: argparse.Namespace) -> str:
    """Читает diff: из файла `--diff` или из stdin (если это не терминал)."""
    if args.diff:
        return Path(args.diff).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _cmd_review(args: argparse.Namespace) -> int:
    """Делает AI-ревью diff: RAG-контекст проекта + анализ изменений.

    Текст ревью печатается в stdout (пригоден для `gh pr comment --body-file`),
    диагностика (контекст, модель) — в stderr, чтобы не пачкать комментарий.
    """
    from docent.reviewer import review

    root = _repo_root()
    if not is_initialized(root):
        print("[error] сначала выполните `docent init`.", file=sys.stderr)
        return 1
    if get_api_key() is None:
        print(f"[error] не задан {API_KEY_ENV} в окружении.", file=sys.stderr)
        return 1

    diff = _read_diff(args)
    if not diff.strip():
        print(
            "[error] пустой diff. Передайте `--diff <файл>` или подайте на stdin.\n"
            "  Пример: git diff HEAD~1 | docent review",
            file=sys.stderr,
        )
        return 1

    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else None
    config = load_config(root)
    if args.no_verify:
        config.review_verify = False
    with Spinner("Ревьюю изменения"):
        result = review(root, config, diff, files)
    render_markdown(result.text)
    if result.sources:
        print("\n📄 Контекст:", file=sys.stderr)
        for src in result.sources:
            print(f"  • {src}", file=sys.stderr)
    print(f"🤖 Модель: {result.model}", file=sys.stderr)
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

    p_review = sub.add_parser("review", help="AI-ревью diff (баги, архитектура, рекомендации)")
    p_review.add_argument("--diff", help="путь к файлу с diff (иначе читается stdin)")
    p_review.add_argument("--files", help="изменённые файлы через запятую (иначе — из diff)")
    p_review.add_argument(
        "--no-verify", action="store_true",
        help="пропустить пасс верификации находок (быстрее и дешевле)",
    )
    p_review.set_defaults(func=_cmd_review)

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
