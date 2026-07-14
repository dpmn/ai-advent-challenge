"""MCP-сервер git-контекста (stdio). Read-only инструменты над локальным репо.

Запуск как подпроцесс: `python -m docent.mcp.servers.git`. Инструменты
принимают repo_path (абсолютный путь к репозиторию), который передаёт клиент.

Логика git-обёртки перенесена из более раннего прототипа project_tools_mcp;
транспорт изменён на stdio ради self-contained CLI (ни портов, ни ручного
запуска сервера).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docent-git")

# Ограничение размера вывода одного инструмента (защита от гигантских листингов).
_MAX_OUTPUT_CHARS = 40_000


def _run_git(repo_path: str, *args: str, timeout: int = 30) -> tuple[bool, str]:
    """Выполняет git-команду в repo_path. Возвращает (ok, вывод или ошибка)."""
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        return False, f"Каталог не найден: {repo}"
    if not (repo / ".git").exists():
        return False, f"Не git-репозиторий: {repo}"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)}: таймаут {timeout}s"
    except FileNotFoundError:
        return False, "git не установлен"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return False, f"git {' '.join(args)} failed: {err[:500]}"
    out = result.stdout
    if len(out) > _MAX_OUTPUT_CHARS:
        out = out[:_MAX_OUTPUT_CHARS] + f"\n… [обрезано, всего {len(out)} символов]"
    return True, out


@mcp.tool(
    description="Get the currently checked-out git branch of a local repository. "
    "Args: repo_path — absolute path to the repository."
)
def git_current_branch(repo_path: str) -> str:
    """Возвращает имя текущей ветки репозитория."""
    ok, out = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if ok else f"[error] {out}"


@mcp.tool(
    description="Get the short hash and subject of a commit ref (default HEAD). "
    "Args: repo_path, ref (e.g. 'HEAD', 'main')."
)
def git_head(repo_path: str, ref: str = "HEAD") -> str:
    """Возвращает короткий хэш и заголовок коммита ref ('<hash> <subject>')."""
    ok, out = _run_git(repo_path, "log", "-1", "--format=%h %s", ref)
    return out.strip() if ok else f"[error] {out}"


@mcp.tool(
    description="List files tracked in the working tree of a local git repository. "
    "Args: repo_path — absolute path to the repository."
)
def git_list_files(repo_path: str) -> str:
    """Возвращает список отслеживаемых файлов (по одному пути на строку)."""
    ok, out = _run_git(repo_path, "ls-files")
    return out.strip() if ok else f"[error] {out}"


def main() -> None:
    """Запускает git-сервер по stdio-транспорту."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
