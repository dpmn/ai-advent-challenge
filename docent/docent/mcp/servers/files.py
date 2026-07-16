"""MCP-сервер файловых операций (stdio). Инструменты агента над файлами репо.

Запуск как подпроцесс: `python -m docent.mcp.servers.files`. Все инструменты
принимают repo_path (абсолютный путь к репозиторию) и работают только внутри
него: выход за корень, `.env*`, `*.key`, `.git/`, `.docent/` — запрещены.

Запись выполняется сразу (без подтверждения), но `write_file` возвращает
unified diff старого и нового содержимого — изменения всегда видны.
"""

from __future__ import annotations

import difflib
import fnmatch
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docent-files")

# Ограничение размера вывода одного инструмента (защита от гигантских файлов).
_MAX_OUTPUT_CHARS = 40_000
# Максимум совпадений в выводе search_files.
_MAX_MATCHES = 200
# Максимум путей в выводе list_files.
_MAX_LIST = 500

# Каталоги, в которые не заходим ни при чтении, ни при записи.
_DENY_DIRS = {".git", ".docent", "__pycache__", ".venv", "venv", "node_modules", ".egg-info"}
# Паттерны имён файлов, запрещённых и на чтение, и на запись (секреты).
_DENY_FILES = [".env", ".env.*", "*.key", "*.pem"]


def _deny_reason(rel: Path) -> str | None:
    """Возвращает причину запрета для относительного пути или None, если можно.

    Текст причины отдаётся модели: он объясняет, что запрет осознанный
    (политика безопасности), чтобы агент не пытался его обойти.
    """
    for part in rel.parts:
        if part in _DENY_DIRS or part.endswith(".egg-info"):
            return (
                f"'{part}' — служебный каталог, работа с ним запрещена "
                "политикой безопасности. Выбери путь вне него."
            )
    if any(fnmatch.fnmatch(rel.name, pat) for pat in _DENY_FILES):
        return (
            f"'{rel.name}' — файл с секретами, чтение и запись запрещены "
            "политикой безопасности. Запрет обойти нельзя."
        )
    return None


def _safe_path(repo_path: str, path: str) -> tuple[Path | None, str]:
    """Резолвит path внутри repo_path. Возвращает (путь, "") или (None, ошибка).

    Гарантирует, что итоговый путь лежит внутри репозитория и не задевает
    запрещённые каталоги и файлы-секреты.
    """
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        return None, f"Каталог не найден: {repo}"
    target = (repo / path).resolve()
    try:
        rel = target.relative_to(repo)
    except ValueError:
        return None, (
            f"Путь вне репозитория: {path}. Разрешены только пути внутри "
            f"{repo}, запрет обойти нельзя."
        )
    reason = _deny_reason(rel)
    if reason:
        return None, f"Доступ к '{rel}' запрещён: {reason}"
    return target, ""


def _truncate(text: str) -> str:
    """Обрезает текст до лимита вывода с пометкой об усечении."""
    if len(text) > _MAX_OUTPUT_CHARS:
        return text[:_MAX_OUTPUT_CHARS] + f"\n… [обрезано, всего {len(text)} символов]"
    return text


@mcp.tool(
    description="List files of a local repository as relative paths, one per line. "
    "Args: repo_path — absolute path to the repository; subdir — optional "
    "subdirectory to list (relative to repo root, default: whole repo)."
)
def list_files(repo_path: str, subdir: str = "") -> str:
    """Возвращает относительные пути файлов репозитория (без служебных каталогов)."""
    base, err = _safe_path(repo_path, subdir or ".")
    if base is None:
        return f"[error] {err}"
    if not base.is_dir():
        return f"[error] Не каталог: {subdir}"
    repo = Path(repo_path).expanduser().resolve()
    paths: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo)
        if _deny_reason(rel) is not None:
            continue
        paths.append(str(rel))
        if len(paths) >= _MAX_LIST:
            paths.append(f"… [обрезано на {_MAX_LIST} файлах]")
            break
    return _truncate("\n".join(paths)) if paths else "(файлов нет)"


@mcp.tool(
    description="Read a text file from a local repository. "
    "Args: repo_path — absolute path to the repository; path — file path "
    "relative to the repo root."
)
def read_file(repo_path: str, path: str) -> str:
    """Возвращает содержимое текстового файла (с лимитом размера)."""
    target, err = _safe_path(repo_path, path)
    if target is None:
        return f"[error] {err}"
    if not target.is_file():
        return f"[error] Файл не найден: {path}"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"[error] Бинарный файл, чтение не поддерживается: {path}"
    return _truncate(text)


@mcp.tool(
    description="Search text files of a local repository for a substring "
    "(case-insensitive). Returns 'path:line_number: line' matches. "
    "Args: repo_path — absolute path to the repository; query — substring to "
    "find; glob — optional filename pattern relative to repo root "
    "(default '**/*', e.g. '**/*.py')."
)
def search_files(repo_path: str, query: str, glob: str = "**/*") -> str:
    """Ищет подстроку по файлам репозитория, возвращает совпадения с номерами строк."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        return f"[error] Каталог не найден: {repo}"
    if not query:
        return "[error] Пустой запрос поиска"
    needle = query.lower()
    matches: list[str] = []
    for p in sorted(repo.glob(glob)):
        if not p.is_file():
            continue
        rel = p.relative_to(repo)
        if _deny_reason(rel) is not None:
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            if needle in line.lower():
                matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(matches) >= _MAX_MATCHES:
                    matches.append(f"… [обрезано на {_MAX_MATCHES} совпадениях]")
                    return _truncate("\n".join(matches))
    return _truncate("\n".join(matches)) if matches else "(совпадений нет)"


@mcp.tool(
    description="Create or overwrite a text file in a local repository and "
    "return a unified diff of the change. Parent directories are created "
    "automatically. Args: repo_path — absolute path to the repository; path — "
    "file path relative to the repo root; content — full new file content."
)
def write_file(repo_path: str, path: str, content: str) -> str:
    """Записывает файл (создание/перезапись) и возвращает unified diff изменений."""
    target, err = _safe_path(repo_path, path)
    if target is None:
        return f"[error] {err}"
    if target.is_dir():
        return f"[error] Это каталог, не файл: {path}"
    old = ""
    existed = target.is_file()
    if existed:
        try:
            old = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"[error] Бинарный файл, перезапись запрещена: {path}"
    if old == content:
        return f"Без изменений: {path} уже содержит этот текст."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            content.splitlines(),
            fromfile=f"a/{path}" if existed else "/dev/null",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    action = "перезаписан" if existed else "создан"
    return _truncate(f"Файл {action}: {path}\n{diff}")


def main() -> None:
    """Запускает files-сервер по stdio-транспорту."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
