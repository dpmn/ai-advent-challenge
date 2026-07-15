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


def _signature(node: ast.AST) -> str:
    """Строит строку сигнатуры class/def через `ast.unparse` (без тела).

    Надёжнее ручного разбора: скобки и `:` в строковых литералах и
    аннотациях не сбивают границу заголовка. Декораторы не включаются.
    """
    if isinstance(node, ast.ClassDef):
        parts = [ast.unparse(base) for base in node.bases]
        parts += [ast.unparse(kw) for kw in node.keywords]
        bases = f"({', '.join(parts)})" if parts else ""
        return f"class {node.name}{bases}:"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args}){returns}:"


def chunk_python(text: str, source: str) -> list[Chunk]:
    """Возвращает чанки для Python-файла: docstring модуля + сигнатуры/docstring.

    Синтаксически битые файлы пропускаются (возвращается пустой список).
    Heading чанка — квалифицированное имя объявления (`Класс.метод`).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    chunks: list[Chunk] = []

    module_doc = ast.get_docstring(tree)
    if module_doc:
        chunks.append(Chunk(source=source, heading="(module)", text=module_doc))

    def visit(node: ast.AST, prefix: str) -> None:
        for child in getattr(node, "body", []):
            if not isinstance(child, _DECLS):
                continue
            name = f"{prefix}{child.name}"
            signature = _signature(child)
            doc = ast.get_docstring(child) or ""
            body = signature if not doc else f"{signature}\n{doc}"
            chunks.append(Chunk(source=source, heading=name, text=body))
            if isinstance(child, ast.ClassDef):
                visit(child, f"{name}.")

    visit(tree, "")
    return chunks
