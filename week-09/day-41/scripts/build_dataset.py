#!/usr/bin/env python3
"""Собирает train/eval из проверенной разметки.

Ключевая деталь — сплит идёт по ГРУППАМ near-дублей, а не по строкам.
На витрине один товар лежит десятком карточек ("…набор: 5 штук",
"…набор: 7 штук"). Если такая группа разъедется между train и eval,
модель увидит на eval почти те же строки, что заучила, и метрики
окажутся завышенными.

В eval приоритетно уходят записи со ссылкой на карточку: только по ним
человек может разрешить спорный бренд, открыв товар.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import schema

DAY_DIR = Path(__file__).resolve().parent.parent
DATASETS = DAY_DIR / "datasets"

META_FIELDS = ("id", "name", "url", "source", "group_id", "hard_flags")


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def accepted(records: list[dict]) -> list[dict]:
    """Оставляет только разметку, прошедшую автопроверку инвариантов."""
    return [r for r in records
            if r.get("obj") is not None and not r.get("problems")]


def to_message_record(record: dict, system_prompt: str) -> dict:
    """Превращает размеченную запись в пример в messages-формате.

    Эталонный ответ сериализуется компактно и в фиксированном порядке ключей —
    иначе модель будет учиться ещё и случайному порядку полей.
    """
    ordered = {field: record["obj"].get(field) for field in schema.FIELD_ORDER}
    assistant = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    result = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": record["name"]},
            {"role": "assistant", "content": assistant},
        ]
    }
    result.update({field: record[field] for field in META_FIELDS if field in record})
    result["category"] = record["obj"].get("category")
    return result


def split_by_groups(records: list[dict], eval_size: int, seed: int) -> tuple[list[dict], list[dict]]:
    """Делит записи на train/eval по группам near-дублей со стратификацией."""
    rng = random.Random(seed)

    groups: dict[int, list[dict]] = defaultdict(list)
    for record in records:
        groups[record["group_id"]].append(record)

    # Категория группы — по первой записи; внутри группы товар один и тот же.
    group_items = []
    for gid, members in groups.items():
        has_url = any(m.get("url") for m in members)
        hard = len({f for m in members for f in m.get("hard_flags", []) if f != "simple"})
        group_items.append({
            "gid": gid,
            "members": members,
            "category": members[0]["category"],
            "has_url": has_url,
            "hard": hard,
        })

    by_category: dict[str, list[dict]] = defaultdict(list)
    for item in group_items:
        by_category[item["category"]].append(item)

    total = len(records)
    eval_records: list[dict] = []
    train_records: list[dict] = []

    for category, items in by_category.items():
        rng.shuffle(items)
        # Группы со ссылкой — вперёд: их можно вычитать вручную по карточке.
        # Дальше порядок случайный, чтобы eval повторял реальное распределение:
        # приоритет по «сложности» перекашивает выборку и выбивает простые
        # случаи, на которых проверяется отсутствие регресса.
        items.sort(key=lambda i: not i["has_url"])
        in_category = sum(len(i["members"]) for i in items)
        target = round(eval_size * in_category / total)

        taken = 0
        cutoff = 0
        for idx, item in enumerate(items):
            if taken >= target:
                cutoff = idx
                break
            eval_records.extend(item["members"])
            taken += len(item["members"])
            cutoff = idx + 1
        for item in items[cutoff:]:
            train_records.extend(item["members"])

    train_records, eval_records = top_up_unit_classes(train_records, eval_records, rng)
    rng.shuffle(eval_records)
    rng.shuffle(train_records)
    return train_records, eval_records


# Метрика «нормализация единиц» считается только на этих классах. При честном
# пропорциональном отборе их набирается 12-14 на сотню — шаг метрики 7 п.п.,
# читать тяжело. Добираем до порога обменом на простые группы: перекос
# ограничен двумя классами и не трогает остальную выборку.
UNIT_CLASSES = ("dirty_unit", "multi_volume")
UNIT_FLOOR = 20


def _has_unit_class(record: dict) -> bool:
    """Относится ли запись к классам, на которых меряется нормализация единиц."""
    return any(flag in record.get("hard_flags", []) for flag in UNIT_CLASSES)


def top_up_unit_classes(train: list[dict], evalset: list[dict], rng) -> tuple[list[dict], list[dict]]:
    """Доводит представленность единичных классов в eval до порога обменом групп."""
    have = sum(1 for r in evalset if _has_unit_class(r))
    if have >= UNIT_FLOOR:
        return train, evalset

    train_by_group: dict[int, list[dict]] = defaultdict(list)
    for record in train:
        train_by_group[record["group_id"]].append(record)
    eval_by_group: dict[int, list[dict]] = defaultdict(list)
    for record in evalset:
        eval_by_group[record["group_id"]].append(record)

    donors = [gid for gid, members in train_by_group.items() if any(_has_unit_class(m) for m in members)]
    plain = [gid for gid, members in eval_by_group.items() if not any(_has_unit_class(m) for m in members)]
    rng.shuffle(donors)
    rng.shuffle(plain)

    moved_in: set[int] = set()
    moved_out: set[int] = set()
    for donor in donors:
        if have >= UNIT_FLOOR or not plain:
            break
        # Меняем внутри одной категории, иначе поедет стратификация.
        category = train_by_group[donor][0]["category"]
        match = next((g for g in plain
                      if g not in moved_out and eval_by_group[g][0]["category"] == category), None)
        if match is None:
            continue
        moved_in.add(donor)
        moved_out.add(match)
        plain.remove(match)
        have += sum(1 for m in train_by_group[donor] if _has_unit_class(m))

    if not moved_in:
        return train, evalset

    new_train = [r for r in train if r["group_id"] not in moved_in]
    new_eval = [r for r in evalset if r["group_id"] not in moved_out]
    for gid in moved_in:
        new_eval.extend(train_by_group[gid])
    for gid in moved_out:
        new_train.extend(eval_by_group[gid])
    print(f"  обмен групп ради классов единиц: +{len(moved_in)} в eval, "
          f"итого записей с единичными ловушками {have}")
    return new_train, new_eval


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Пишет записи в JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize(name: str, records: list[dict]) -> None:
    """Печатает состав файла по категориям и классам сложности."""
    print(f"\n  {name}: {len(records)}")
    cats = Counter(r["category"] for r in records)
    print(f"    категорий: {len(cats)}, со ссылкой: {sum(1 for r in records if r.get('url'))}")
    thin = [c for c, n in cats.items() if n < 5]
    if thin:
        print(f"    тоньше 5 примеров: {', '.join(sorted(thin))}")
    flags = Counter(f for r in records for f in r.get("hard_flags", []))
    print("    классы сложности: " + ", ".join(f"{k}={v}" for k, v in flags.most_common()))


def main() -> None:
    """Точка входа: собирает train/eval из размеченного файла."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labeled", type=Path, default=DATASETS / "labeled.jsonl")
    parser.add_argument("--total", type=int, default=500)
    parser.add_argument("--eval-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=41)
    args = parser.parse_args()

    raw = read_jsonl(args.labeled)
    good = accepted(raw)
    print(f"размечено {len(raw)}, прошло инварианты {len(good)}, "
          f"отбраковано {len(raw) - len(good)}")

    system_prompt = schema.build_system_prompt()
    records = [to_message_record(r, system_prompt) for r in good]

    if len(records) > args.total:
        rng = random.Random(args.seed)
        # Режем по группам, чтобы не разорвать near-дубли лишним усечением.
        by_group: dict[int, list[dict]] = defaultdict(list)
        for record in records:
            by_group[record["group_id"]].append(record)
        group_ids = list(by_group)
        rng.shuffle(group_ids)
        trimmed: list[dict] = []
        for gid in group_ids:
            if len(trimmed) + len(by_group[gid]) > args.total:
                continue
            trimmed.extend(by_group[gid])
        records = trimmed
        print(f"усечено до {len(records)} (целились в {args.total})")

    train, evalset = split_by_groups(records, args.eval_size, args.seed)

    write_jsonl(DATASETS / "train.jsonl", train)
    write_jsonl(DATASETS / "eval.jsonl", evalset)

    print(f"\n=== ДАТАСЕТ: {len(train)} train / {len(evalset)} eval ===")
    summarize("train", train)
    summarize("eval", evalset)

    train_groups = {r["group_id"] for r in train}
    eval_groups = {r["group_id"] for r in evalset}
    print(f"\n  пересечение групп train/eval: {len(train_groups & eval_groups)} (должно быть 0)")
    print(f"\n  записано: {DATASETS / 'train.jsonl'}, {DATASETS / 'eval.jsonl'}")


if __name__ == "__main__":
    main()
