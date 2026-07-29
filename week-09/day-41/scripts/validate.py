#!/usr/bin/env python3
"""Валидатор датасета.

Два режима.

`--invariants` — проверка черновой разметки против входных строк
(галлюцинации, словари, нормализация единиц). Отсеивает брак до сборки датасета.

Без флага — проверка готового train/eval в messages-формате, как требует задание:
каждая строка — валидный JSON, все три роли на месте, нет пустых content.
Дополнительно ловит то, что задание не требует, но что убьёт метрики:
утечку групп near-дублей между train и eval и битые эталонные ответы.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import schema

DAY_DIR = Path(__file__).resolve().parent.parent
DATASETS = DAY_DIR / "datasets"
REQUIRED_ROLES = ("system", "user", "assistant")


def read_jsonl_strict(path: Path) -> tuple[list[dict], list[str]]:
    """Читает JSONL, отдельно возвращая ошибки разбора построчно."""
    errors: list[str] = []
    records: list[dict] = []
    if not path.exists():
        return records, [f"{path.name}: файла нет"]
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                errors.append(f"{path.name}:{lineno}: пустая строка")
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{lineno}: не валидный JSON — {exc}")
    return records, errors


def validate_messages_file(path: Path) -> tuple[list[str], list[dict]]:
    """Проверяет файл датасета в messages-формате. Возвращает ошибки и записи."""
    records, errors = read_jsonl_strict(path)

    for idx, record in enumerate(records, 1):
        where = f"{path.name}:{idx}"

        messages = record.get("messages")
        if not isinstance(messages, list):
            errors.append(f"{where}: нет массива messages")
            continue

        roles = [m.get("role") for m in messages]
        for role in REQUIRED_ROLES:
            if role not in roles:
                errors.append(f"{where}: нет роли {role}")

        for message in messages:
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                errors.append(f"{where}: пустой content у роли {message.get('role')!r}")

        assistant = next((m["content"] for m in messages if m.get("role") == "assistant"), None)
        user = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if assistant is None or user is None:
            continue

        obj, clean = schema.parse_model_json(assistant)
        if obj is None:
            errors.append(f"{where}: эталонный ответ не разбирается в JSON")
            continue
        if not clean:
            errors.append(f"{where}: эталонный ответ не чистый JSON (обёртки или текст вокруг)")
        for problem in schema.check_invariants(user, obj):
            errors.append(f"{where}: эталон не проходит инвариант — {problem}")

    return errors, records


def check_leakage(train: list[dict], evalset: list[dict]) -> list[str]:
    """Ищет пересечение групп near-дублей между train и eval.

    Утечка здесь не ломает формат, но завышает метрики: модель увидит на eval
    почти те же строки, что заучила на train.
    """
    train_groups = {r.get("group_id") for r in train if r.get("group_id") is not None}
    eval_groups = {r.get("group_id") for r in evalset if r.get("group_id") is not None}
    shared = train_groups & eval_groups
    if shared:
        return [f"утечка: {len(shared)} групп near-дублей есть и в train, и в eval — {sorted(shared)[:10]}"]

    train_inputs = {r["messages"][1]["content"] for r in train if len(r.get("messages", [])) > 1}
    eval_inputs = {r["messages"][1]["content"] for r in evalset if len(r.get("messages", [])) > 1}
    overlap = train_inputs & eval_inputs
    if overlap:
        return [f"утечка: {len(overlap)} одинаковых входных строк в train и eval"]
    return []


def report_distribution(name: str, records: list[dict]) -> None:
    """Печатает распределение по категориям в файле датасета."""
    cats: Counter = Counter()
    for record in records:
        assistant = next((m["content"] for m in record.get("messages", []) if m.get("role") == "assistant"), None)
        if assistant:
            obj, _ = schema.parse_model_json(assistant)
            if obj:
                cats[obj.get("category")] += 1
    print(f"\n  распределение {name} ({sum(cats.values())}):")
    for cat, cnt in cats.most_common():
        print(f"    {cat:32s} {cnt}")


def run_invariants(path: Path) -> int:
    """Режим проверки черновой разметки. Возвращает код выхода."""
    records, errors = read_jsonl_strict(path)
    for error in errors:
        print(f"  {error}")

    unparsed = [r for r in records if r.get("obj") is None]
    dirty = [r for r in records if r.get("obj") is not None and not r.get("clean_json")]
    flagged = [r for r in records if r.get("problems")]

    print(f"=== РАЗМЕТКА: {len(records)} записей ===")
    print(f"  не разобрано в JSON:        {len(unparsed)}")
    print(f"  разобрано, но не чистый JSON: {len(dirty)}")
    print(f"  с нарушением инвариантов:   {len(flagged)} (включая неразобранные)")
    print(f"  чистых, готовых в датасет:  {len(records) - len(flagged)}")

    if flagged:
        kinds = Counter(p.split(":")[0].split(" (")[0] for r in flagged for p in r["problems"])
        print("\n  по типам нарушений:")
        for kind, cnt in kinds.most_common():
            print(f"    {cnt:4d}  {kind}")
        print("\n  примеры:")
        for record in flagged[:15]:
            print(f"    - {record['name'][:70]}")
            for problem in record["problems"]:
                print(f"        {problem}")

    return 0 if not unparsed and not errors else 1


def run_dataset_check(train_path: Path, eval_path: Path, verbose: bool) -> int:
    """Режим проверки готового датасета. Возвращает код выхода."""
    train_errors, train = validate_messages_file(train_path)
    eval_errors, evalset = validate_messages_file(eval_path)
    leak_errors = check_leakage(train, evalset)
    all_errors = train_errors + eval_errors + leak_errors

    print("=== ВАЛИДАЦИЯ ДАТАСЕТА ===")
    print(f"  train: {len(train)} примеров, ошибок {len(train_errors)}")
    print(f"  eval:  {len(evalset)} примеров, ошибок {len(eval_errors)}")
    print(f"  утечка между train и eval: {'НЕТ' if not leak_errors else 'ЕСТЬ'}")

    if all_errors:
        print(f"\n  всего ошибок: {len(all_errors)}")
        for error in all_errors[:40]:
            print(f"    {error}")
        if len(all_errors) > 40:
            print(f"    ... ещё {len(all_errors) - 40}")
    else:
        print("\n  ВСЁ ЧИСТО: каждая строка — валидный JSON, три роли на месте,")
        print("  пустых content нет, эталоны проходят инварианты, утечки нет.")

    if verbose:
        report_distribution("train", train)
        report_distribution("eval", evalset)

    return 1 if all_errors else 0


def main() -> None:
    """Точка входа: выбирает режим проверки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invariants", action="store_true",
                        help="проверять черновую разметку вместо готового датасета")
    parser.add_argument("--labeled", type=Path, default=DATASETS / "labeled.jsonl")
    parser.add_argument("--train", type=Path, default=DATASETS / "train.jsonl")
    parser.add_argument("--eval", type=Path, default=DATASETS / "eval.jsonl")
    parser.add_argument("-v", "--verbose", action="store_true", help="печатать распределение категорий")
    args = parser.parse_args()

    if args.invariants:
        sys.exit(run_invariants(args.labeled))
    sys.exit(run_dataset_check(args.train, args.eval, args.verbose))


if __name__ == "__main__":
    main()
