"""Разбор прогона дня 49: что поймали ворота, что гейтвей, что прошло мимо обоих.

Читает лог ворот песочницы (`.docent/security-loop.jsonl`), её git-историю и
логи LLM Gateway (`/stats`, `/audit`), после чего печатает сводку в том виде,
в котором её просит задание.

Запуск: `python3 week-10/day-49/summary.py [песочница] [адрес гейтвея]`
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Простые маркеры небезопасного кода для финальной проверки закоммиченного.
# Это грубый grep, а не анализатор: он нужен, чтобы увидеть, что осталось в
# коде после обоих защитных слоёв, и не претендует на полноту.
_RESIDUAL = [
    (re.compile(r"http://(?!127\.0\.0\.1|localhost)"), "HTTP вместо HTTPS"),
    (re.compile(r"verify\s*=\s*False"), "отключена проверка сертификата"),
    (re.compile(r"shell\s*=\s*True"), "subprocess с shell=True"),
    (re.compile(r"\b(eval|exec)\s*\("), "eval/exec"),
    (re.compile(r"pickle\.loads?\s*\("), "pickle"),
    (re.compile(r"yaml\.load\s*\((?!.*SafeLoader)"), "yaml.load без SafeLoader"),
    (re.compile(r"(?i)\b(token|secret|password|api_?key)\s*=\s*[\"'][^\"']{8,}[\"']"),
     "похоже на секрет в коде"),
    (re.compile(r"(?i)(print|log(ger|ging)?\.\w+)\s*\([^)]*request\.(data|json|headers|get_data)"),
     "тело или заголовки запроса в логе"),
    # Отдельно от предыдущего правила: заголовки часто сперва кладут в
    # переменную (`headers = dict(request.headers)`), и совпадения «в одной
    # строке с логом» уже не будет. Полный набор заголовков несёт Authorization
    # и Cookie, поэтому отмечаем любое обращение к нему.
    (re.compile(r"dict\(\s*request\.headers\s*\)|request\.headers\b"),
     "полный набор заголовков (несёт Authorization, Cookie)"),
    (re.compile(r"requests\.(get|post|put)\((?![^)]*timeout)"), "сетевой вызов без таймаута"),
]

_RULE = "─" * 72


def _head(title: str) -> None:
    """Печатает заголовок раздела сводки."""
    print(f"\n{_RULE}\n{title}\n{_RULE}")


def _load_rounds(sandbox: Path) -> list[dict]:
    """Читает jsonl-лог ворот песочницы (пустой список, если лога нет)."""
    path = sandbox / ".docent" / "security-loop.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _fetch(url: str) -> dict | None:
    """Забирает JSON по URL; None, если гейтвей не отвечает."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _git(sandbox: Path, *args: str) -> str:
    """Запускает git в песочнице и возвращает вывод (пустая строка при ошибке)."""
    proc = subprocess.run(
        ["git", "-C", str(sandbox), *args], capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _by_goal(rounds: list[dict]) -> dict[str, list[dict]]:
    """Группирует записи лога по задаче, сохраняя порядок появления."""
    grouped: dict[str, list[dict]] = {}
    for entry in rounds:
        grouped.setdefault(entry.get("goal", "?"), []).append(entry)
    return grouped


def _short(text: str, limit: int = 60) -> str:
    """Укорачивает строку до limit символов с многоточием."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def print_tasks(rounds: list[dict]) -> None:
    """Печатает ход ворот по каждой задаче: заходы, вердикты, исход."""
    _head("1. ХОД ВОРОТ ПО ЗАДАЧАМ")
    if not rounds:
        print("  Лог ворот пуст — ни одна задача не дошла до скана.")
        return
    for number, (goal, entries) in enumerate(_by_goal(rounds).items(), start=1):
        print(f"\nЗадача {number}: {_short(goal, 68)}")
        for entry in entries:
            outcome = entry.get("outcome") or ("scan" if "verdict" in entry else "committed")
            round_no = entry.get("round", "?")
            if outcome == "scan":
                verdict = entry.get("verdict", {})
                mark = "БЛОК ГЕЙТВЕЯ" if verdict.get("gateway_blocked") else verdict.get("level", "?")
                scope = ", ".join(entry.get("touched") or entry.get("files") or []) or "-"
                print(f"  заход {round_no}: скан {scope} → {mark}")
                if entry.get("outside"):
                    print(f"            вне прогона (не сканируем): {', '.join(entry['outside'])}")
                for finding in verdict.get("findings", []):
                    where = finding.get("file") or "-"
                    if finding.get("line"):
                        where += f":{finding['line']}"
                    print(f"            [{finding['severity']}] {where} — {finding['issue']}")
                if verdict.get("error"):
                    print(f"            причина: {verdict['error']}")
            elif outcome == "committed":
                print(f"  заход {round_no}: ✅ КОММИТ {entry.get('commit')} (уровень {entry.get('level')})")
            elif outcome == "gave_up":
                print(f"  заход {round_no}: ⛔ заходы исчерпаны на {entry.get('level')} — коммита нет")
                if entry.get("stashed"):
                    print("            отвергнутое убрано в git stash — следующим задачам не мешает")
            elif outcome == "gateway_blocked_generation":
                rules = ", ".join(entry.get("gateway_findings") or []) or "?"
                print(f"  заход {round_no}: ⛔ гейтвей заблокировал и генерацию ({rules}) — прогон остановлен")
                if entry.get("stashed"):
                    print("            отвергнутое убрано в git stash — следующим задачам не мешает")
            elif outcome == "no_changes":
                print(f"  заход {round_no}: ℹ️  агент не изменил ни одного файла — скана не было")
            elif outcome == "git_error":
                print(f"  заход {round_no}: ⛔ ошибка git — {_short(entry.get('detail', ''), 80)}")
            else:
                print(f"  заход {round_no}: {outcome}")


def print_security_step(rounds: list[dict]) -> None:
    """Печатает всё, что поймал security step (второй вызов LLM)."""
    _head("2. ЧТО ПОЙМАЛ SECURITY STEP")
    found = False
    for entry in rounds:
        verdict = entry.get("verdict") or {}
        for finding in verdict.get("findings", []):
            found = True
            where = finding.get("file") or "-"
            if finding.get("line"):
                where += f":{finding['line']}"
            print(f"  [{finding['severity']}] {where}")
            print(f"      {finding['issue']}")
            if finding.get("fix"):
                print(f"      починка: {finding['fix']}")
    if not found:
        print("  Находок нет.")


def print_gateway(rounds: list[dict], gateway: str) -> None:
    """Печатает, что поймал LLM Gateway: со стороны ворот и из своих логов."""
    _head("3. ЧТО ПОЙМАЛ LLM GATEWAY")
    blocked_scans = [e for e in rounds if (e.get("verdict") or {}).get("gateway_blocked")]
    blocked_gen = [e for e in rounds if e.get("generation_gateway_blocked")]
    print(f"  Заблокированных сканов: {len(blocked_scans)}")
    for entry in blocked_scans:
        rules = ", ".join((entry.get("verdict") or {}).get("gateway_findings") or []) or "?"
        print(f"      заход {entry.get('round')}: правила — {rules}")
    print(f"  Заблокированных генераций: {len(blocked_gen)}")

    stats = _fetch(f"{gateway}/stats")
    if stats is None:
        print(f"  Гейтвей на {gateway} не отвечает — счётчики недоступны.")
        return
    print(
        "  Счётчики прокси: "
        f"запросов {stats.get('requests')}, "
        f"заблокировано {stats.get('blocked')}, "
        f"замаскировано {stats.get('masked')}, "
        f"пропущено чистыми {stats.get('passed')}, "
        f"ошибок {stats.get('errors')}"
    )
    print(
        f"  Токены: {stats.get('prompt_tokens')} на вход, "
        f"{stats.get('completion_tokens')} на выход | "
        f"стоимость: {stats.get('cost_rub')} ₽"
    )
    findings = stats.get("findings") or {}
    if findings:
        print("  Сработавшие правила прокси: " + ", ".join(f"{k} × {v}" for k, v in findings.items()))
    else:
        print("  Правила прокси ни разу не сработали.")

    audit = _fetch(f"{gateway}/audit?limit=50")
    records = (audit or {}).get("records") or []
    if records:
        actions: dict[str, int] = {}
        for record in records:
            actions[record.get("action", "?")] = actions.get(record.get("action", "?"), 0) + 1
        # Счётчики /stats живут в памяти процесса гейтвея, а аудит-файл — на
        # диске за весь день: числа сходиться не обязаны, если прокси
        # перезапускали или прогонов было несколько.
        print("  Аудит за сегодня (весь файл, не только этот прогон): "
              + ", ".join(f"{k} × {v}" for k, v in actions.items()))
        leaked = [r for r in records if "sk-" in (r.get("prompt_preview") or "")]
        print(f"  Записей аудита с сырым ключом в превью: {len(leaked)} (должно быть 0)")


def print_residual(sandbox: Path, rounds: list[dict]) -> None:
    """Печатает, что осталось в закоммиченном коде — прошло мимо обоих слоёв."""
    _head("4. ЧТО ПРОШЛО МИМО ОБОИХ (grep по закоммиченному коду)")
    files = [f for f in _git(sandbox, "ls-files").splitlines() if f.endswith(".py")]
    if not files:
        print("  Закоммиченного кода нет.")
        return
    reported = {
        (f.get("file") or "") for e in rounds for f in (e.get("verdict") or {}).get("findings", [])
    }
    hits = 0
    for name in files:
        text = (sandbox / name).read_text(encoding="utf-8", errors="replace")
        for pattern, label in _RESIDUAL:
            for match in pattern.finditer(text):
                hits += 1
                line_no = text[: match.start()].count("\n") + 1
                seen = " (по этому файлу находка была)" if name in reported else ""
                print(f"  {name}:{line_no} — {label}{seen}")
                print(f"      {_short(match.group(0), 80)}")
    if not hits:
        print("  Простые маркеры небезопасного кода не найдены.")
    print("\n  Это грубый grep, а не анализатор: он показывает пищу для разбора,")
    print("  а не окончательный вердикт по качеству кода.")


def print_commits(sandbox: Path) -> None:
    """Печатает историю песочницы и то, что осталось незакоммиченным."""
    _head("5. ИТОГ ПО КОММИТАМ")
    log = _git(sandbox, "log", "--oneline")
    print(log or "  Коммитов нет.")
    status = _git(sandbox, "status", "--short")
    print("\nНезакоммичено (ворота не пропустили или файл вне индекса):")
    print(status or "  чисто")


def main() -> int:
    """Печатает полную сводку прогона. Возвращает код выхода."""
    sandbox = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/day49-sandbox")
    gateway = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5001").rstrip("/")
    if not sandbox.exists():
        print(f"[error] песочница не найдена: {sandbox}", file=sys.stderr)
        return 1
    rounds = _load_rounds(sandbox)
    print(f"\nСВОДКА ПРОГОНА ДНЯ 49 — песочница {sandbox}")
    print_tasks(rounds)
    print_security_step(rounds)
    print_gateway(rounds, gateway)
    print_residual(sandbox, rounds)
    print_commits(sandbox)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
