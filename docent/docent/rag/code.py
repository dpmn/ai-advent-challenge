"""Извлечение docstring-ов и сигнатур из Python-файлов для RAG-индекса.

Индексируем не весь код, а его «карту»: docstring модуля и для каждого
класса/функции — сигнатуру и docstring. RAG получает семантику кода
(что делает модуль/функция) без шума реализации и лишних токенов.
"""

from __future__ import annotations

import ast

from docent.rag.chunker import Chunk

# Узлы, которые считаем «объявлениями» с сигнатурой и docstring.
_DECLS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _signature(lines: list[str], node: ast.AST) -> str:
    """Восстанавливает строку сигнатуры class/def из исходных строк файла.

    Идём от строки объявления до двоеточия, закрывающего заголовок на нулевой
    глубине скобок (корректно для многострочных сигнатур). Декораторы не
    включаются — `lineno` указывает на строку `def`/`class`, а не на декоратор.
    """
    start = node.lineno - 1
    depth = 0
    collected: list[str] = []
    for line in lines[start:]:
        collected.append(line)
        for ch in line:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if depth <= 0 and line.rstrip().endswith(":"):
            break
    joined = " ".join(part.strip() for part in collected)
    return joined.rstrip().rstrip(":").strip()


def chunk_python(text: str, source: str) -> list[Chunk]:
    """Возвращает чанки для Python-файла: docstring модуля + сигнатуры/docstring.

    Синтаксически битые файлы пропускаются (возвращается пустой список).
    Heading чанка — квалифицированное имя объявления (`Класс.метод`).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    lines = text.splitlines()
    chunks: list[Chunk] = []

    module_doc = ast.get_docstring(tree)
    if module_doc:
        chunks.append(Chunk(source=source, heading="(module)", text=module_doc))

    def visit(node: ast.AST, prefix: str) -> None:
        for child in getattr(node, "body", []):
            if not isinstance(child, _DECLS):
                continue
            name = f"{prefix}{child.name}"
            signature = _signature(lines, child)
            doc = ast.get_docstring(child) or ""
            body = signature if not doc else f"{signature}\n{doc}"
            chunks.append(Chunk(source=source, heading=name, text=body))
            if isinstance(child, ast.ClassDef):
                visit(child, f"{name}.")

    visit(tree, "")
    return chunks
