"""Сборка отчёта атакующего из JSONL-лога прогона.

Читает лог `run-*.jsonl`, строит markdown: сводка по векторам, все подозрения
на утечку с выдержками, счётчики. Токен и адрес мишени в отчёт не тащим сверх
того, что уже публично.

Использование: python3 -m redteam.report logs/run-YYYYMMDD-HHMMSS.jsonl
"""

import json
import sys


def _load(path: str) -> list[dict]:
    """Читает JSONL-лог в список записей."""
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build(path: str) -> str:
    """Строит markdown-отчёт по логу прогона."""
    records = _load(path)
    attempts = [r for r in records if r.get("event") == "attempt"]
    by_vector: dict[str, list[dict]] = {}
    for a in attempts:
        by_vector.setdefault(a["vector"], []).append(a)

    lines = ["# Отчёт атакующего — прогон red team-харнеса", ""]
    lines.append(f"Лог: `{path}`  ")
    lines.append(f"Всего попыток: {len(attempts)}  ")
    suspects = [a for a in attempts if a.get("verdict", {}).get("suspect")]
    lines.append(f"Подозрений на утечку: {len(suspects)}")
    lines.append("")

    lines.append("## Сводка по векторам")
    lines.append("")
    lines.append("| Вектор | Попыток | Подозрений |")
    lines.append("|---|---|---|")
    for vector, items in by_vector.items():
        susp = sum(1 for a in items if a.get("verdict", {}).get("suspect"))
        lines.append(f"| {vector} | {len(items)} | {susp} |")
    lines.append("")

    if suspects:
        lines.append("## Подозрения на утечку (проверить вручную)")
        lines.append("")
        for a in suspects:
            last = a["steps"][-1]
            lines.append(f"### {a['vector']} — попытка {a['attempt']}")
            lines.append("")
            lines.append(f"**Payload:** `{last['payload'][:300]}`")
            lines.append("")
            lines.append("**Ответ мишени (выдержка):**")
            lines.append("")
            lines.append("```")
            lines.append(last["response"][:800])
            lines.append("```")
            lines.append("")
            lines.append("**Почему подозрение:** " + "; ".join(a["verdict"]["reasons"]))
            lines.append("")
    else:
        lines.append("## Подозрений на утечку нет")
        lines.append("")
        lines.append("Ни один вектор не дал признаков раскрытия секрета или промпта.")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Точка входа CLI."""
    if len(sys.argv) < 2:
        sys.exit("Укажи путь к логу: python3 -m redteam.report logs/run-*.jsonl")
    print(build(sys.argv[1]))


if __name__ == "__main__":
    main()
