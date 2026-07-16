"""MCP-сервер git-контекста (stdio). Read-only инструменты над локальным репо.

Запуск как подпроцесс: `python -m docent.mcp.servers.git`. Инструменты
принимают repo_path (абсолютный путь к репозиторию), который передаёт клиент.

Логика git-обёртки перенесена из более раннего прототипа project_tools_mcp;
транспорт изменён на stdio ради self-contained CLI (ни портов, ни ручного
запуска сервера).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docent-git")

# Ограничение размера вывода одного инструмента (защита от гигантских листингов).
_MAX_OUTPUT_CHARS = 40_000

# Допустимые символы в git-ref (ветки, теги, хэши, диапазоны a..b / a...b).
_REF_RE = re.compile(r"^[A-Za-z0-9._/^~-]+(\.\.\.?[A-Za-z0-9._/^~-]+)?$")


def _valid_ref(ref: str) -> bool:
    """Проверяет, что ref безопасен: без флагов-опций и посторонних символов."""
    return bool(ref) and not ref.startswith("-") and bool(_REF_RE.match(ref))


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


@mcp.tool(
    description="Show recent commits of a local git repository, one per line "
    "('<hash> <date> <subject>'). Args: repo_path; ref_range — optional ref or "
    "range (e.g. 'main..day-34', default: current branch history); "
    "max_count — number of commits (default 20)."
)
def git_log(repo_path: str, ref_range: str = "", max_count: int = 20) -> str:
    """Возвращает последние коммиты: хэш, дата, заголовок (по одному на строку)."""
    args = ["log", f"-{max(1, min(max_count, 100))}", "--format=%h %ad %s", "--date=short"]
    if ref_range:
        if not _valid_ref(ref_range):
            return f"[error] Недопустимый ref: {ref_range}"
        args.append(ref_range)
    ok, out = _run_git(repo_path, *args)
    return out.strip() if ok else f"[error] {out}"


@mcp.tool(
    description="Show unified diff between two refs of a local git repository "
    "(base...target). Args: repo_path; base — base ref (e.g. 'main'); "
    "target — target ref (default 'HEAD'); stat_only — if true, show only "
    "per-file change statistics instead of the full diff."
)
def git_diff(repo_path: str, base: str, target: str = "HEAD", stat_only: bool = False) -> str:
    """Возвращает diff между base и target (unified или --stat)."""
    if not _valid_ref(base):
        return f"[error] Недопустимый ref: {base}"
    if not _valid_ref(target):
        return f"[error] Недопустимый ref: {target}"
    args = ["diff", f"{base}...{target}"]
    if stat_only:
        args.insert(1, "--stat")
    ok, out = _run_git(repo_path, *args)
    if not ok:
        return f"[error] {out}"
    return out.strip() or f"(изменений между {base} и {target} нет)"


def main() -> None:
    """Запускает git-сервер по stdio-транспорту."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
