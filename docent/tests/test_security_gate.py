"""Юнит-тесты security-ворот: парсинг вердикта, fail closed, логика гейта (без сети).

Запуск: `python3 docent/tests/test_security_gate.py`. Выход 0 — все прошли, 1 — падение.
LLM и git замоканы: тесты проверяют решения ворот, а не качество находок модели.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from docent import agent, llm, security
from docent.agent import AgentResult
from docent.config import Config
from docent.security import SecurityFinding, Verdict

_DIFF = "--- a/app.py\n+++ b/app.py\n@@\n+TOKEN = 'x'\n"


def _response(content: str, finish_reason: str = "stop", gateway: dict | None = None) -> dict:
    """Собирает ответ провайдера в формате OpenAI для мока chat_full()."""
    body = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": finish_reason}
        ]
    }
    if gateway is not None:
        body["gateway"] = gateway
    return body


@contextmanager
def _patched(**attrs: object):
    """Временно подменяет атрибуты модулей: {"модуль.имя": значение}."""
    modules = {"agent": agent, "llm": llm, "security": security}
    saved: list[tuple[object, str, object]] = []
    for dotted, value in attrs.items():
        mod_name, attr = dotted.split(".", 1)
        module = modules[mod_name]
        saved.append((module, attr, getattr(module, attr)))
        setattr(module, attr, value)
    try:
        yield
    finally:
        for module, attr, old in saved:
            setattr(module, attr, old)


def _scan(content: str, **kwargs: object) -> Verdict:
    """Гоняет security.scan() с замоканным ответом LLM."""
    with _patched(**{"llm.chat_full": lambda *a, **k: _response(content, **kwargs)}):
        return security.scan(_DIFF, Config())


# ── Парсинг вердикта ─────────────────────────────────────────────


def test_clean_diff_gives_clean() -> None:
    """Пустой список находок — уровень CLEAN, ворота не блокируют."""
    verdict = _scan('{"findings": []}')
    assert verdict.level == "CLEAN", verdict
    assert not verdict.blocking
    assert not verdict.error


def test_max_severity_wins() -> None:
    """Уровень вердикта — максимум по находкам, а не первая или последняя."""
    raw = json.dumps({"findings": [
        {"severity": "LOW", "issue": "a"},
        {"severity": "CRITICAL", "issue": "b"},
        {"severity": "MEDIUM", "issue": "c"},
    ]})
    verdict = _scan(raw)
    assert verdict.level == "CRITICAL", verdict.level
    assert len(verdict.findings) == 3


def test_unknown_severity_raised_to_high() -> None:
    """Уровень не по формату поднимаем до HIGH, а не пропускаем молча."""
    verdict = _scan('{"findings": [{"severity": "спорно", "issue": "a"}]}')
    assert verdict.level == "HIGH", verdict.level
    assert verdict.blocking


def test_finding_without_issue_dropped() -> None:
    """Запись без текста проблемы не находка — отбрасываем."""
    raw = json.dumps({"findings": [{"severity": "HIGH"}, {"severity": "LOW", "issue": "a"}]})
    verdict = _scan(raw)
    assert len(verdict.findings) == 1, verdict.findings
    assert verdict.level == "LOW"


def test_medium_and_low_do_not_block() -> None:
    """MEDIUM и LOW проходят: коммит с предупреждением, а не доработка."""
    assert not _scan('{"findings": [{"severity": "MEDIUM", "issue": "a"}]}').blocking
    assert not _scan('{"findings": [{"severity": "LOW", "issue": "a"}]}').blocking


# ── Fail closed ──────────────────────────────────────────────────


def test_unparseable_answer_is_critical() -> None:
    """Ответ без JSON — CRITICAL: ворота закрываются, а не открываются."""
    verdict = _scan("не смог, извини")
    assert verdict.level == "CRITICAL", verdict
    assert "не распарсился" in verdict.error


def test_llm_error_is_critical() -> None:
    """Упавший вызов скана — тоже CRITICAL, а не «проверка пропущена»."""
    def boom(*a: object, **k: object) -> dict:
        raise llm.LLMError("timeout")

    with _patched(**{"llm.chat_full": boom}):
        verdict = security.scan(_DIFF, Config())
    assert verdict.level == "CRITICAL"
    assert "timeout" in verdict.error


def test_empty_diff_is_clean() -> None:
    """Пустой diff сканировать нечего — CLEAN без вызова LLM."""
    def boom(*a: object, **k: object) -> dict:
        raise AssertionError("LLM не должен вызываться на пустом diff")

    with _patched(**{"llm.chat_full": boom}):
        assert security.scan("   \n", Config()).level == "CLEAN"


# ── Блокировка гейтвеем ──────────────────────────────────────────


def test_gateway_block_by_finish_reason() -> None:
    """finish_reason=gateway_blocked — CRITICAL с метками правил прокси."""
    gateway = {"action": "block", "input": {"findings": [{"label": "OpenAI API key"}]}}
    verdict = _scan("...", finish_reason="gateway_blocked", gateway=gateway)
    assert verdict.level == "CRITICAL"
    assert verdict.gateway_blocked
    assert verdict.gateway_findings == ["OpenAI API key"]


def test_gateway_block_by_marker_text() -> None:
    """Блок распознаётся и по маркеру в тексте, без служебных полей."""
    verdict = _scan(security.GATEWAY_BLOCK_MARKER + "\n  • ключ")
    assert verdict.gateway_blocked and verdict.level == "CRITICAL"


def test_gateway_block_feedback_mentions_secret() -> None:
    """Фидбэк по блоку гейтвея объясняет модели, что убрать секрет из кода."""
    gateway = {"action": "block", "input": {"findings": [{"label": "Cloud.ru key"}]}}
    text = security.feedback(_scan("...", finish_reason="gateway_blocked", gateway=gateway))
    assert "Cloud.ru key" in text and "os.getenv" in text


# ── Фидбэк ───────────────────────────────────────────────────────


def test_feedback_has_location_and_only_blocking() -> None:
    """В фидбэк идут только HIGH/CRITICAL, с файлом, строкой и способом починки."""
    verdict = Verdict(level="CRITICAL", findings=[
        SecurityFinding("CRITICAL", "токен в коде", "читай из env", "app.py", 42),
        SecurityFinding("LOW", "нет комментария", "", "app.py", 7),
    ])
    text = security.feedback(verdict)
    assert "app.py:42" in text
    assert "токен в коде" in text
    assert "читай из env" in text
    assert "нет комментария" not in text


def test_summary_counts_by_severity() -> None:
    """Сводка перечисляет число находок по уровням."""
    verdict = Verdict(level="HIGH", findings=[
        SecurityFinding("HIGH", "a"), SecurityFinding("HIGH", "b"),
        SecurityFinding("LOW", "c"),
    ])
    text = security.summary(verdict)
    assert text.startswith("HIGH") and "HIGH: 2" in text and "LOW: 1" in text


# ── Логика ворот в цикле ─────────────────────────────────────────


def _gate(
    verdicts: list[Verdict],
    diff: str = _DIFF,
    git_ok: bool = True,
    answer: str = "сделал",
    stashed: list[list[str]] | None = None,
) -> AgentResult:
    """Гоняет agent._run() с замоканными генерацией, git и сканом.

    verdicts — вердикты по раундам; последний повторяется, если раундов больше.
    answer — финальный текст генерации (маркер блока гейтвея ловится по нему).
    stashed — если передан список, в него пишутся пути каждого вызова stash.
    """
    calls = {"n": 0}

    async def fake_generate(*a: object, **k: object) -> AgentResult:
        return AgentResult(text=answer)

    def fake_stash(_root: Path, paths: list[str], _reason: str) -> tuple[bool, str]:
        if stashed is not None:
            stashed.append(list(paths))
        return True, ""

    def fake_scan(_diff: str, _config: Config) -> Verdict:
        index = min(calls["n"], len(verdicts) - 1)
        calls["n"] += 1
        return verdicts[index]

    class FakeManager:
        """Заглушка MCP-менеджера: серверы в тестах не поднимаем."""

        async def __aenter__(self) -> "FakeManager":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def tool_specs(self) -> list[dict]:
            return []

        def tool_names(self) -> list[str]:
            return []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with _patched(**{
            "agent.McpManager": FakeManager,
            "agent._generate": fake_generate,
            "agent._staged_diff": lambda _root, _paths: (git_ok, diff),
            "agent._dirty_outside": lambda _root, _paths: [],
            "agent._stash_rejected": fake_stash,
            "agent._commit": lambda *a: (True, "abc1234"),
            "security.scan": fake_scan,
        }):
            return asyncio.run(agent._run(root, Config(), "цель", commit=True))


def test_gate_commits_clean_code() -> None:
    """Чистый скан — коммит с первого захода."""
    result = _gate([Verdict(level="CLEAN")])
    assert result.committed and result.commit_sha == "abc1234"
    assert result.rounds == 1


def test_gate_commits_medium_with_warning() -> None:
    """MEDIUM не блокирует: коммит есть, вердикт сохранён в результате."""
    result = _gate([Verdict(level="MEDIUM", findings=[SecurityFinding("MEDIUM", "a")])])
    assert result.committed
    assert result.verdict is not None and result.verdict.level == "MEDIUM"


def test_gate_retries_then_commits() -> None:
    """HIGH возвращает на доработку; чистый второй заход — коммит."""
    result = _gate([Verdict(level="HIGH", findings=[SecurityFinding("HIGH", "a")]),
                    Verdict(level="CLEAN")])
    assert result.committed
    assert result.rounds == 2, result.rounds


def test_gate_gives_up_after_max_rounds() -> None:
    """Три подряд CRITICAL — коммита нет, причина в тексте результата."""
    result = _gate([Verdict(level="CRITICAL", findings=[SecurityFinding("CRITICAL", "a")])])
    assert not result.committed
    assert result.rounds == agent._MAX_FIX_ROUNDS + 1
    assert "заходы на исправление исчерпаны" in result.text


def test_gate_stops_when_generation_blocked_by_gateway() -> None:
    """Блок генерации гейтвеем прекращает прогон сразу, без холостых заходов.

    Регрессия боевого прогона: секрет попал в контекст агента, гейтвей блокировал
    и генерацию, и скан — цикл трижды впустую сходил по кругу.
    """
    result = _gate(
        [Verdict(level="CRITICAL", gateway_blocked=True, gateway_findings=["GitHub token"])],
        answer=security.GATEWAY_BLOCK_MARKER + "\n  • GitHub token",
    )
    assert not result.committed
    assert result.rounds == 1, result.rounds
    assert "заблокировал и генерацию" in result.text


def test_rejected_changes_are_stashed() -> None:
    """Отвергнутое воротами убирается из рабочего дерева, чтобы не заразить следующую задачу."""
    stashed: list[list[str]] = []
    _gate([Verdict(level="CRITICAL", findings=[SecurityFinding("CRITICAL", "a")])],
          stashed=stashed)
    assert len(stashed) == 1, stashed  # ровно один stash — на исчерпании заходов


def test_commit_path_does_not_stash() -> None:
    """Успешный прогон ничего не прячет в stash."""
    stashed: list[list[str]] = []
    _gate([Verdict(level="CLEAN")], stashed=stashed)
    assert stashed == []


def test_gate_skips_when_no_changes() -> None:
    """Изменений нет — ни скана, ни коммита."""
    result = _gate([Verdict(level="CLEAN")], diff="")
    assert not result.committed and result.verdict is None


def test_gate_stops_when_git_fails() -> None:
    """git недоступен — ворота не коммитят и сообщают причину."""
    result = _gate([Verdict(level="CLEAN")], git_ok=False)
    assert not result.committed
    assert "git недоступен" in result.text


def _captured_log(verdicts: list[Verdict], **kwargs: object) -> list[dict]:
    """Гоняет ворота и возвращает записи, ушедшие в jsonl-лог."""
    written: list[dict] = []
    with _patched(**{"security.log_round": lambda root, entry: (
        written.append(entry), Path(root) / security.LOG_FILE)[1]
    }):
        _gate(verdicts, **kwargs)
    return written


def test_gate_writes_jsonl_log() -> None:
    """Каждый раунд пишется строкой в .docent/security-loop.jsonl."""
    written = _captured_log([Verdict(level="CLEAN")])
    assert len(written) == 2, written  # раунд скана + запись о коммите
    assert written[0]["outcome"] == "scan" and written[0]["round"] == 1
    assert written[0]["verdict"]["level"] == "CLEAN"
    assert written[1]["outcome"] == "committed" and written[1]["commit"] == "abc1234"


def test_gate_logs_when_nothing_changed() -> None:
    """«Агент ничего не изменил» тоже попадает в лог, а не теряется молча."""
    written = _captured_log([Verdict(level="CLEAN")], diff="")
    assert [e["outcome"] for e in written] == ["no_changes"], written


def test_gate_logs_git_error() -> None:
    """Ошибка git фиксируется в логе с исходом git_error."""
    written = _captured_log([Verdict(level="CLEAN")], git_ok=False)
    assert [e["outcome"] for e in written] == ["git_error"], written


def test_gate_logs_give_up() -> None:
    """Исчерпанные заходы фиксируются отдельной записью gave_up."""
    written = _captured_log([Verdict(level="CRITICAL", findings=[
        SecurityFinding("CRITICAL", "a")
    ])])
    assert [e["outcome"] for e in written] == ["scan", "scan", "scan", "gave_up"], written
    assert written[-1]["level"] == "CRITICAL"


def test_log_round_appends_jsonl() -> None:
    """log_round создаёт .docent/ и дописывает строки, не затирая прошлые."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        security.log_round(root, {"round": 1})
        path = security.log_round(root, {"round": 2})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["round"] == 2
    assert "ts" in json.loads(lines[0])


# ── Область видимости ворот (настоящий git) ──────────────────────


@contextmanager
def _repo():
    """Поднимает временный git-репозиторий с одним коммитом."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agent._git(root, "init", "-q")
        agent._git(root, "config", "user.email", "t@example.com")
        agent._git(root, "config", "user.name", "T")
        (root / "README.md").write_text("start\n", encoding="utf-8")
        agent._git(root, "add", "-A")
        agent._git(root, "commit", "-qm", "initial")
        yield root


def test_scan_ignores_files_outside_this_run() -> None:
    """Чужой файл в рабочем дереве не попадает в diff ворот.

    Регрессия боевого прогона: задача, провалившая ворота, оставляла файл
    незакоммиченным, и следующая задача сканировала и коммитила его вместе
    со своим — вердикт получался не про неё.
    """
    with _repo() as root:
        (root / "leftover.py").write_text("TOKEN = 'от прошлой задачи'\n", encoding="utf-8")
        (root / "mine.py").write_text("x = 1\n", encoding="utf-8")
        ok, diff = agent._staged_diff(root, ["mine.py"])
    assert ok
    assert "mine.py" in diff
    assert "leftover.py" not in diff


def test_docent_dir_never_staged() -> None:
    """Служебный `.docent/` не попадает ни в diff, ни в индекс."""
    with _repo() as root:
        (root / ".docent").mkdir()
        (root / ".docent" / "security-loop.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "mine.py").write_text("x = 1\n", encoding="utf-8")
        ok, diff = agent._staged_diff(root, ["mine.py", ".docent/security-loop.jsonl"])
        staged = agent._git(root, "diff", "--cached", "--name-only")[1]
    assert ok and ".docent" not in diff and ".docent" not in staged


def test_commit_leaves_foreign_changes_alone() -> None:
    """Коммит берёт только файлы прогона; чужие остаются в рабочем дереве."""
    with _repo() as root:
        (root / "leftover.py").write_text("TOKEN = 'от прошлой задачи'\n", encoding="utf-8")
        (root / "mine.py").write_text("x = 1\n", encoding="utf-8")
        agent._staged_diff(root, ["mine.py"])
        ok, sha = agent._commit(root, "цель", Verdict(level="CLEAN"), ["mine.py"])
        committed = agent._git(root, "show", "--name-only", "--format=", "HEAD")[1]
        status = agent._git(root, "status", "--porcelain")[1]
    assert ok and sha
    assert committed.strip() == "mine.py", committed
    assert "leftover.py" in status


def test_dirty_outside_lists_foreign_changes() -> None:
    """_dirty_outside показывает, что осталось вне прогона (для лога и stderr)."""
    with _repo() as root:
        (root / "leftover.py").write_text("y = 2\n", encoding="utf-8")
        (root / "mine.py").write_text("x = 1\n", encoding="utf-8")
        outside = agent._dirty_outside(root, ["mine.py"])
    assert outside == ["leftover.py"], outside


def test_stash_removes_rejected_file_from_tree() -> None:
    """`_stash_rejected` убирает файл из дерева и оставляет его восстановимым."""
    with _repo() as root:
        (root / "bad.py").write_text("TOKEN = 'ghp_секрет'\n", encoding="utf-8")
        (root / "other.py").write_text("keep\n", encoding="utf-8")
        agent._staged_diff(root, ["bad.py"])
        ok, _ = agent._stash_rejected(root, ["bad.py"], "gave_up")
        exists = (root / "bad.py").exists()
        other = (root / "other.py").exists()
        stashes = agent._git(root, "stash", "list")[1]
    assert ok and not exists, "отвергнутый файл должен уйти из рабочего дерева"
    assert other, "чужой файл трогать нельзя"
    assert "gave_up" in stashes, stashes


def test_no_written_paths_means_no_diff() -> None:
    """Агент ничего не записал — ворота не индексируют вообще ничего."""
    with _repo() as root:
        (root / "leftover.py").write_text("y = 2\n", encoding="utf-8")
        ok, diff = agent._staged_diff(root, [])
        status = agent._git(root, "status", "--porcelain")[1]
    assert ok and diff == ""
    assert status.startswith("??"), status  # файл так и остался неотслеживаемым


def main() -> int:
    """Прогоняет все тесты, печатает итог, возвращает код выхода."""
    tests = [obj for name, obj in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
        except Exception as err:  # noqa: BLE001 — тест-раннер намеренно ловит всё
            failed += 1
            print(f"  ❌ {test.__name__}: {err}")
    print(f"\n{len(tests) - failed}/{len(tests)} прошло")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
