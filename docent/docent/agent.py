"""Агентный цикл docent: цель → LLM с MCP-инструментами → операции с файлами.

В отличие от assistant.ask() (one-shot: контекст собирает код), здесь решения
принимает модель: она сама выбирает, какие файлы искать, читать и писать,
через MCP-инструменты (git + files). Цикл крутится, пока модель запрашивает
инструменты, с жёстким лимитом шагов от зацикливания.

С флагом commit=True между генерацией и коммитом встают security-ворота
(`security.py`): сгенерированный diff уходит на отдельный скан, HIGH/CRITICAL
возвращают цикл на доработку с фидбэком, MEDIUM/LOW проходят с предупреждением,
чисто — коммитим. Коммит делает сам цикл через subprocess, а не MCP-инструмент:
инструмент был бы доступен модели, и она смогла бы закоммитить в обход ворот.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from docent import llm, security
from docent.config import Config
from docent.mcp.manager import McpManager

_SYSTEM_PROMPT = (
    "Ты — агент-ассистент разработчика, работающий с файлами конкретного "
    "репозитория через инструменты. Корень репозитория: {root}\n"
    "Сегодняшняя дата: {today}\n\n"
    "Тебе дают задачу на уровне цели. Сам решай, какие инструменты вызвать: "
    "ищи (search_files), смотри структуру (list_files), читай (read_file), "
    "создавай и изменяй файлы (write_file). Git-контекст — через git_* "
    "инструменты. Пути передавай относительно корня репозитория.\n\n"
    "Правила:\n"
    "- Используй ТОЛЬКО предоставленные инструменты. Шелла (run_shell_command, "
    "bash и т.п.) НЕ существует — не пытайся его вызывать.\n"
    "- Если инструмент вернул [error] о запрете доступа — это осознанная "
    "политика безопасности. НЕ пытайся обойти запрет другим путём: сообщи "
    "пользователю о запрете в финальном ответе и заверши работу.\n"
    "- Не повторяй вызов инструмента с теми же аргументами — результат уже "
    "есть в контексте.\n"
    "- Не выдумывай содержимое файлов — сначала прочитай.\n"
    "- Перед изменением файла обязательно прочитай его текущую версию и "
    "передавай в write_file ПОЛНОЕ новое содержимое.\n"
    "- В финальном ответе кратко перечисли, что сделал и какие файлы затронул; "
    "если менял файлы — упомяни суть изменений (diff уже показан).\n"
    "- Отвечай на русском, кратко и по делу."
)

# Максимум итераций цикла (LLM → инструменты) — защита от зацикливания.
_MAX_STEPS = 15
# Сколько раз ворота возвращают цикл на доработку, прежде чем сдаться.
# Итого до трёх генераций: первая плюс два захода с фидбэком.
_MAX_FIX_ROUNDS = 2
# Лимит длины результата инструмента, попадающего в контекст модели.
_MAX_TOOL_RESULT_CHARS = 20_000
# Сколько подряд «пустых» вызовов (неизвестный инструмент / повтор) терпим,
# прежде чем принудительно попросить модель дать финальный ответ.
_MAX_FUTILE_STREAK = 3

# Финальный запрос при зацикливании: просим ответ по уже собранным данным.
_WRAP_UP_PROMPT = (
    "Вызовы инструментов заблокированы (повторы или несуществующие "
    "инструменты). Сформулируй финальный ответ пользователю на основе уже "
    "собранной информации. Если задача невыполнима из-за запретов доступа — "
    "прямо скажи об этом и объясни причину."
)


@dataclass
class AgentResult:
    """Результат работы агента: финальный текст и лог вызванных инструментов.

    Поля ворот заполняются только при commit=True: вердикт последнего скана,
    факт коммита, число сделанных заходов и путь к jsonl-логу раундов.
    """

    text: str
    tool_calls: list[str] = field(default_factory=list)
    verdict: security.Verdict | None = None
    committed: bool = False
    commit_sha: str = ""
    rounds: int = 0
    log_path: str = ""


def _git(root: Path, *args: str, timeout: int = 30) -> tuple[bool, str]:
    """Запускает git в корне репозитория. Возвращает (успех, вывод)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return False, f"{type(err).__name__}: {err}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


def _pathspec(paths: list[str]) -> list[str]:
    """Строит pathspec из путей, тронутых прогоном, с исключением `.docent/`.

    Ворота смотрят ТОЛЬКО на файлы текущего прогона. Иначе чужие изменения в
    рабочем дереве (например, файл прошлой задачи, которую ворота не пропустили)
    попадают в проверяемый diff: вердикт получается не про эту задачу, фидбэк
    уводит агента чинить чужой код, а коммит заглатывает то, что свои же ворота
    отвергли.

    `.docent/` исключаем отдельно: там служебное состояние docent, включая лог
    самих ворот — иначе ворота ревьюят собственные записи.
    """
    return [*paths, ":(exclude).docent"]


def _staged_diff(root: Path, paths: list[str]) -> tuple[bool, str]:
    """Индексирует тронутые прогоном файлы и возвращает (успех, diff индекса).

    Индексируем перед diff, иначе новые файлы в него не попадут — а агент чаще
    создаёт файлы, чем правит. Коммит потом берёт ровно то, что проверили ворота.
    """
    if not paths:
        return True, ""
    spec = _pathspec(paths)
    ok, out = _git(root, "add", "-A", "--", *spec)
    if not ok:
        return False, out
    return _git(root, "diff", "--cached", "--", *spec)


def _commit(
    root: Path, goal: str, verdict: security.Verdict, paths: list[str]
) -> tuple[bool, str]:
    """Коммитит файлы прогона. Возвращает (успех, sha или текст ошибки).

    Коммитим по тому же pathspec, что сканировали: всё, что лежит в рабочем
    дереве помимо файлов прогона, коммита не касается.
    """
    subject = " ".join(goal.split())[:60]
    message = f"docent: {subject}\n\nSecurity-ворота: {security.summary(verdict)}"
    ok, out = _git(root, "commit", "-m", message, "--", *_pathspec(paths))
    if not ok:
        return False, out
    ok_sha, sha = _git(root, "rev-parse", "--short", "HEAD")
    return True, sha if ok_sha else ""


def _stash_rejected(root: Path, paths: list[str], reason: str) -> tuple[bool, str]:
    """Убирает отвергнутые воротами изменения в git stash. Возвращает (успех, вывод).

    Оставлять отвергнутый код в рабочем дереве нельзя: следующая задача его
    прочитает и утащит в свой контекст. Если там секрет, гейтвей заблокирует уже
    её генерацию — одна проваленная задача глушит все последующие (боевой
    прогон дня 49). Stash — не удаление: изменения возвращаются `git stash pop`.
    """
    if not paths:
        return True, ""
    return _git(root, "stash", "push", "-u", "-m", f"docent: {reason}", "--", *paths)


def _dirty_outside(root: Path, paths: list[str]) -> list[str]:
    """Возвращает изменённые файлы рабочего дерева вне файлов прогона."""
    ok, out = _git(root, "status", "--porcelain", "--", ".", ":(exclude).docent")
    if not ok:
        return []
    touched = set(paths)
    outside = []
    for line in out.splitlines():
        name = line[3:].strip().strip('"')
        if name and name not in touched:
            outside.append(name)
    return outside


def _to_openai_tools(specs: list[dict]) -> list[dict]:
    """Преобразует спецификации MCP-инструментов в OpenAI-формат tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        }
        for spec in specs
    ]


def _progress(name: str, args: dict) -> None:
    """Печатает в stderr строку прогресса о вызове инструмента."""
    shown = {k: v for k, v in args.items() if k != "repo_path"}
    brief = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in shown.items())
    print(f"  🔧 {name}({brief})", file=sys.stderr, flush=True)


def _show_diff(result: str) -> None:
    """Печатает в stderr diff изменений файла, чтобы правки были видны сразу."""
    indented = "\n".join(f"  {line}" for line in result.splitlines())
    print(indented, file=sys.stderr, flush=True)


def _wrap_up(messages: list[dict], config: Config, used: list[str]) -> AgentResult:
    """Принудительный финал: последний запрос без tools по собранным данным."""
    final = [m for m in messages] + [{"role": "user", "content": _WRAP_UP_PROMPT}]
    text = llm.chat(final, config)
    return AgentResult(text=text, tool_calls=used)


async def _generate(
    manager: McpManager,
    root: Path,
    config: Config,
    messages: list[dict],
    tools: list[dict],
    known: set[str],
    used: list[str],
    executed: set[str],
    written: set[str] | None = None,
) -> AgentResult:
    """Крутит один заход генерации до финального ответа модели или лимита шагов.

    Работает поверх общих `messages`, `used` и `executed`: при возврате из ворот
    на доработку заход продолжает ту же историю, а не начинает с нуля.
    `written` копит пути, записанные через write_file — по ним ворота потом
    ограничивают diff и коммит файлами именно этого прогона.
    """
    futile = 0  # Подряд идущие «пустые» вызовы: неизвестный инструмент или повтор.
    for _ in range(_MAX_STEPS):
        message = llm.chat_tools(messages, tools, config)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            messages.append(message)
            return AgentResult(text=message.get("content") or "", tool_calls=used)
        messages.append(message)
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # repo_path всегда наш корень: модель не может увести агента
            # в другой каталог.
            args["repo_path"] = str(root)
            _progress(name, args)
            signature = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if name not in known:
                result = (
                    f"[error] неизвестный инструмент: {name}. Других "
                    f"инструментов нет, доступны только: {', '.join(known)}."
                )
                futile += 1
            elif signature in executed:
                result = (
                    f"[error] повторный вызов {name} с теми же аргументами — "
                    "результат уже есть выше. Смени подход или дай "
                    "финальный ответ."
                )
                futile += 1
            else:
                result = await manager.call(name, args)
                executed.add(signature)
                used.append(name)
                futile = 0
                if name == "write_file":
                    _show_diff(result)
                    path = str(args.get("path") or "").strip()
                    if path and written is not None:
                        written.add(path)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "content": result[:_MAX_TOOL_RESULT_CHARS],
                }
            )
        if futile >= _MAX_FUTILE_STREAK:
            return _wrap_up(messages, config, used)
    return _wrap_up(messages, config, used)


def _note(text: str) -> None:
    """Печатает строку хода ворот в stderr (stdout занят финальным ответом)."""
    print(text, file=sys.stderr, flush=True)


async def _run(root: Path, config: Config, goal: str, commit: bool) -> AgentResult:
    """Крутит генерацию, а при commit=True — ещё и security-ворота перед коммитом."""
    used: list[str] = []
    executed: set[str] = set()  # Сигнатуры уже исполненных вызовов (имя+аргументы).
    written: set[str] = set()  # Пути, записанные через write_file в этом прогоне.
    async with McpManager() as manager:
        tools = _to_openai_tools(manager.tool_specs())
        known = set(manager.tool_names())
        messages: list[dict] = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT.format(root=root, today=date.today()),
            },
            {"role": "user", "content": goal},
        ]
        result = await _generate(
            manager, root, config, messages, tools, known, used, executed, written
        )
        if not commit:
            return result

        verdict: security.Verdict | None = None

        def log(outcome: str, **fields: object) -> None:
            """Пишет строку раунда в jsonl. Исход фиксируем на каждой ветке.

            Пустая запись — худший вид лога: по нему нельзя понять, задача
            провалилась или её просто не было. Поэтому логируются и «изменений
            нет», и ошибка git, а не только успешный скан.
            """
            result.log_path = str(
                security.log_round(
                    root,
                    {"round": result.rounds, "goal": goal, "outcome": outcome, **fields},
                )
            )

        def reject(paths: list[str], outcome: str, note: str, **fields: object) -> AgentResult:
            """Завершает прогон без коммита: сообщение, stash отвергнутого, лог.

            Отвергнутые изменения убираем из рабочего дерева, иначе следующая
            задача прочитает их и утащит в свой контекст.
            """
            _note(f"⛔ {note}")
            result.text += f"\n\n[ворота] {note} Лог: {result.log_path}"
            stashed, out = _stash_rejected(root, paths, outcome)
            if paths and stashed:
                _note(
                    f"🧹 Отвергнутые изменения ({', '.join(paths)}) убраны в git "
                    "stash, рабочее дерево чистое. Вернуть: git stash pop"
                )
            elif paths:
                _note(f"⚠️  Не удалось убрать изменения в stash: {out}")
            log(outcome, stashed=stashed, touched=paths, **fields)
            return result

        for round_no in range(1, _MAX_FIX_ROUNDS + 2):
            result.rounds = round_no
            # Гейтвей мог заблокировать саму генерацию — это тоже событие ворот.
            gen_blocked = security.GATEWAY_BLOCK_MARKER in (result.text or "")

            paths = sorted(written)
            ok, diff = _staged_diff(root, paths)
            if not ok:
                _note(f"⛔ Ворота: git недоступен — {diff}")
                result.text += f"\n\n[ворота] git недоступен: {diff}"
                log("git_error", detail=diff[:500])
                return result
            if not diff.strip():
                _note(
                    "ℹ️  Ворота: агент не изменил ни одного файла — "
                    "сканировать и коммитить нечего."
                )
                result.text += "\n\n[ворота] изменений нет: скан и коммит пропущены."
                log("no_changes", generation_gateway_blocked=gen_blocked)
                return result

            outside = _dirty_outside(root, paths)
            if outside:
                _note(
                    "ℹ️  Вне этого прогона изменены и не будут ни сканироваться, "
                    f"ни коммититься: {', '.join(outside)}"
                )
            _note(
                f"🛡  Security-скан (раунд {round_no}): {len(diff)} символов diff "
                f"по файлам прогона ({', '.join(paths)})…"
            )
            verdict = security.scan(diff, config)
            result.verdict = verdict
            log(
                "scan",
                files=_changed_files(diff),
                touched=paths,
                outside=outside,
                generation_gateway_blocked=gen_blocked,
                verdict=verdict.to_dict(),
            )
            _note(f"🛡  Вердикт: {security.summary(verdict)}")
            for finding in verdict.findings:
                where = finding.location() or "-"
                _note(f"     [{finding.severity}] {where} — {finding.issue}")

            # Гейтвей заблокировал не только скан, но и саму генерацию: значит
            # секрет попал в историю сообщений агента, и следующий вызов тоже
            # будет заблокирован. Фидбэк тут бесполезен — крутить заходы вхолостую
            # незачем, выходим сразу.
            if gen_blocked:
                return reject(
                    paths,
                    "gateway_blocked_generation",
                    "LLM Gateway заблокировал и генерацию: в контекст агента попал "
                    "секрет, дальнейшие вызовы тоже будут заблокированы. "
                    "Коммита нет.",
                    level=verdict.level,
                    gateway_findings=verdict.gateway_findings,
                )
            if not verdict.blocking:
                break
            if round_no > _MAX_FIX_ROUNDS:
                return reject(
                    paths,
                    "gave_up",
                    f"{verdict.level}: заходы на исправление исчерпаны, коммита нет.",
                    level=verdict.level,
                )
            _note("↩️  Возврат на доработку с фидбэком.")
            messages.append({"role": "user", "content": security.feedback(verdict)})
            previous_log = result.log_path
            result = await _generate(
                manager, root, config, messages, tools, known, used, executed, written
            )
            result.verdict = verdict
            result.log_path = previous_log

        assert verdict is not None
        if verdict.level != "CLEAN":
            _note(
                f"⚠️  {verdict.level} не блокирует — коммитим с предупреждением: "
                f"{security.summary(verdict)}"
            )
        ok, sha = _commit(root, goal, verdict, sorted(written))
        if not ok:
            result.text += f"\n\n[ворота] коммит не удался: {sha}"
            _note(f"⛔ Коммит не удался: {sha}")
            log("commit_failed", detail=sha[:500])
            return result
        result.committed = True
        result.commit_sha = sha
        log("committed", commit=sha, level=verdict.level, touched=sorted(written))
        _note(f"✅ Закоммичено: {sha}")
        return result


def _changed_files(diff: str) -> list[str]:
    """Достаёт имена файлов из заголовков «+++ b/…» unified diff."""
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
    return files


def run(root: Path, config: Config, goal: str, commit: bool = False) -> AgentResult:
    """Выполняет задачу-цель над файлами репозитория, возвращает AgentResult.

    commit=True включает security-ворота и коммит результата: изменения
    индексируются, diff уходит на скан, и коммит происходит только если ворота
    не вернули HIGH/CRITICAL.
    """
    return asyncio.run(_run(root, config, goal, commit))
