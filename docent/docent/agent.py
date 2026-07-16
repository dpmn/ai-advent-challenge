"""Агентный цикл docent: цель → LLM с MCP-инструментами → операции с файлами.

В отличие от assistant.ask() (one-shot: контекст собирает код), здесь решения
принимает модель: она сама выбирает, какие файлы искать, читать и писать,
через MCP-инструменты (git + files). Цикл крутится, пока модель запрашивает
инструменты, с жёстким лимитом шагов от зацикливания.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from docent import llm
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
    """Результат работы агента: финальный текст и лог вызванных инструментов."""

    text: str
    tool_calls: list[str] = field(default_factory=list)


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


async def _run(root: Path, config: Config, goal: str) -> AgentResult:
    """Крутит агентный цикл до финального ответа модели или лимита шагов."""
    used: list[str] = []
    executed: set[str] = set()  # Сигнатуры уже исполненных вызовов (имя+аргументы).
    futile = 0  # Подряд идущие «пустые» вызовы: неизвестный инструмент или повтор.
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
        for _ in range(_MAX_STEPS):
            message = llm.chat_tools(messages, tools, config)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
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


def run(root: Path, config: Config, goal: str) -> AgentResult:
    """Выполняет задачу-цель над файлами репозитория, возвращает AgentResult."""
    return asyncio.run(_run(root, config, goal))
