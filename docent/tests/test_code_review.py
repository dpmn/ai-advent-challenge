"""Юнит-тесты чанкера кода и хелперов ревью (без сети).

Запуск: `python3 docent/tests/test_code_review.py`. Выход 0 — все прошли, 1 — есть падение.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from docent.config import Config
from docent.rag.chunker import Chunk
from docent.rag.code import _signature, chunk_python
from docent.rag.store import Hit
from docent.reviewer import (
    Finding,
    _drop_self_refuted,
    _extract_sources,
    _load_review_notes,
    _parse_findings,
    parse_json_block,
    _read_changed_files,
    _render_findings,
    _truncate_at_line,
)


def _headings(src: str) -> dict[str, str]:
    """Возвращает {heading: первая строка текста} для чанков Python-файла."""
    return {c.heading: c.text.splitlines()[0] for c in chunk_python(src, "t.py")}


def test_signature_edge_cases() -> None:
    """Сигнатуры с ловушками (скобки/`:` в литералах, *args, аннотации)."""
    src = (
        "def f(x: dict = {1: 2}, s: str = 'a):b', *args, **kw) -> 'List[int]':\n"
        "    '''док.'''\n"
        "    return x\n"
    )
    heads = _headings(src)
    sig = heads["f"]
    assert sig.startswith("def f(") and sig.endswith("-> 'List[int]':"), sig
    assert "*args" in sig and "**kw" in sig, sig
    # скобка/двоеточие внутри строкового литерала не оборвали сигнатуру
    assert "'a):b'" in sig, sig


def test_async_class_and_nested() -> None:
    """async-метод, класс с базой/metaclass и вложенный класс."""
    src = (
        "class Outer(Base, metaclass=Meta):\n"
        "    '''внешний.'''\n"
        "    async def method(self, n: int = 0) -> None:\n"
        "        '''метод.'''\n"
        "        ...\n"
        "    class Inner:\n"
        "        '''вложенный.'''\n"
    )
    heads = _headings(src)
    assert heads["Outer"] == "class Outer(Base, metaclass=Meta):", heads
    assert heads["Outer.method"].startswith("async def method("), heads
    assert "Outer.Inner" in heads, heads  # вложенный класс проиндексирован


def test_module_doc_and_no_docstring() -> None:
    """Docstring модуля индексируется; функция без docstring — тоже (сигнатура)."""
    src = "'''модульный док.'''\n\ndef bare():\n    return 1\n"
    chunks = {c.heading: c.text for c in chunk_python(src, "t.py")}
    assert chunks["(module)"] == "модульный док."
    assert chunks["bare"] == "def bare():"  # без docstring — одна сигнатура


def test_syntax_error_returns_empty() -> None:
    """Синтаксически битый файл не роняет чанкер, а даёт пустой список."""
    assert chunk_python("def broken(:\n    pass", "t.py") == []


def test_signature_direct_class_no_bases() -> None:
    """Класс без баз — без скобок."""
    import ast

    node = ast.parse("class A:\n    pass").body[0]
    assert _signature(node) == "class A:"


def _hits(*sources: str) -> list[Hit]:
    return [Hit(chunk=Chunk(source=s, heading="", text=""), score=1.0) for s in sources]


def test_sources_only_at_end() -> None:
    """Маркер SOURCES учитывается только на последней строке ответа."""
    hits = _hits("a.py", "b.py", "c.py")

    # Маркер в конце — парсится, вырезается.
    text, src = _extract_sources("ревью\n\nSOURCES: 1, 3", hits)
    assert src == ["a.py", "c.py"], src
    assert "SOURCES" not in text and text == "ревью", repr(text)

    # Упоминание SOURCES в теле — НЕ парсится и НЕ вырезается.
    body = "Совет: пиши SOURCES: 2 в конце.\n\n## Рекомендации\n- ок"
    text2, src2 = _extract_sources(body, hits)
    assert src2 == [], src2
    assert "SOURCES: 2" in text2, text2

    # none — пустой список.
    text3, src3 = _extract_sources("ревью\nSOURCES: none", hits)
    assert src3 == [] and text3 == "ревью", (src3, text3)


def test_truncate_at_line() -> None:
    """Усечение идёт по границе строки, с маркером и числом пропущенных строк."""
    text = "a\nbb\nccc\ndddd\n"
    out = _truncate_at_line(text, 5, "[{n}]")
    assert out == "a\nbb\n[3]", repr(out)
    # Короткий текст не трогается.
    assert _truncate_at_line("short", 100, "[{n}]") == "short"


def test_load_review_notes() -> None:
    """Notes читаются, если файл есть; иначе — пустая строка."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = Config()
        assert _load_review_notes(root, cfg) == ""  # файла нет
        (root / cfg.review_notes).write_text("решение X\n", encoding="utf-8")
        assert _load_review_notes(root, cfg) == "решение X"


def test_read_changed_files_skips_missing() -> None:
    """Отсутствующие файлы пропускаются, существующие читаются с заголовком."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.py").write_text("print('a')\n", encoding="utf-8")
        block = _read_changed_files(root, ["a.py", "missing.py"], 6000, 20)
        assert "--- a.py ---" in block and "print('a')" in block, block
        assert "missing.py" not in block, block


def test_parse_findings_schema_enforced() -> None:
    """Парсер выкидывает пункты без file/claim и баги без сценария провала."""
    raw = (
        'Вот ревью:\n```json\n{"findings": ['
        '{"file": "a.py", "line": 5, "section": "bugs", "claim": "IndexError", '
        '"evidence": "при пустом списке падает"},'
        '{"file": "", "section": "bugs", "claim": "без файла", "evidence": "x"},'
        '{"file": "b.py", "section": "bugs", "claim": "баг без сценария", "evidence": ""},'
        '{"file": "c.py", "section": "неизвестная", "claim": "совет", "evidence": "почему"}'
        '], "sources": [2, "мусор"]}\n```'
    )
    parsed = _parse_findings(raw)
    assert parsed is not None
    findings, sources = parsed
    assert len(findings) == 2, findings  # пункт без file и баг без evidence выкинуты
    assert findings[0].file == "a.py" and findings[0].line == 5
    assert findings[1].section == "recommendations"  # неизвестная секция → рекомендации
    assert sources == [2], sources
    # Совсем не JSON — None (сигнал отдать сырой текст).
    assert _parse_findings("просто текст без скобок") is None


def test_drop_self_refuted_pr23_regression() -> None:
    """Регрессия PR #23: самоопровергающиеся находки выкидываются кодом."""
    real = Finding(file="a.py", claim="выход за границы", evidence="при n=0 KeyError", section="bugs")
    pr23 = [
        Finding(
            file="docent/agent.py",
            claim="подмена repo_path в аргументах вызова инструмента",
            evidence="Однако в текущем коде root всегда берётся как _repo_root(). Значит, бага нет — поведение корректно.",
            section="bugs",
        ),
        Finding(
            file="docent/mcp/servers/files.py",
            claim="_MAX_LIST = 500 — лимит путей",
            evidence="Это не баг, а feature.",
            section="arch",
        ),
        Finding(
            file="docent/agent.py",
            claim="_MAX_FUTILE_STREAK = 3",
            evidence="Значит, поведение корректно — бага нет.",
            section="arch",
        ),
    ]
    kept = _drop_self_refuted([real, *pr23])
    assert kept == [real], [f.claim for f in kept]


def test_render_findings_sections() -> None:
    """Рендер: три секции, «Замечаний нет» для пустых, файл:строка и сценарий."""
    out = _render_findings(
        [Finding(file="a.py", line=7, claim="деление на ноль", evidence="при x=0", section="bugs")]
    )
    assert "## Потенциальные баги" in out and "**a.py:7**" in out, out
    assert "Сценарий: при x=0" in out, out
    assert out.count("Замечаний нет") == 2, out  # arch и recommendations пусты
    # Совсем без находок — трижды «Замечаний нет».
    assert _render_findings([]).count("Замечаний нет") == 3


def test_parse_json_block_tolerant() -> None:
    """JSON достаётся из ограждений/преамбул; мусор — None."""
    assert parse_json_block('```json\n{"verdicts": []}\n```') == {"verdicts": []}
    assert parse_json_block("Ответ: {\"a\": 1} готово") == {"a": 1}
    assert parse_json_block("нет здесь json") is None
    assert parse_json_block("[1, 2]") is None  # список, не объект


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
