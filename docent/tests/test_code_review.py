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
from docent.reviewer import _extract_sources, _load_review_notes, _truncate_at_line


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
