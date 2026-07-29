#!/usr/bin/env python3
"""Отбирает из пула кандидатов на разметку.

Две задачи одновременно:
1. Стратификация по категориям с полом — чтобы мелкие узлы таксономии
   не получили по три примера и не остались невыученными.
2. Намеренный перекос в сторону тяжёлых случаев (грязные единицы, наборы,
   обрезанные названия, коды оттенков). Без него метрика «нормализация единиц»
   посчитается на горстке примеров и ничего не покажет.

Берём с запасом: часть кандидатов отвалится на автопроверке инвариантов.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import schema

DAY_DIR = Path(__file__).resolve().parent.parent
POOL_PATH = DAY_DIR / "datasets" / "pool.jsonl"
OUT_PATH = DAY_DIR / "datasets" / "candidates.jsonl"

# Черновая категория из build_pool.py -> узел финальной таксономии.
# Нужна только для стратификации: настоящую категорию проставит разметчик.
PROBE_TO_FINAL = {
    "шампунь": "шампунь",
    "бальзам_кондиционер": "уход_для_волос",
    "маска_для_волос": "уход_для_волос",
    "уход_для_волос": "уход_для_волос",
    "краска_для_волос": "краска_для_волос",
    "оттеночное": "краска_для_волос",
    "укладка_волос": "стайлинг",
    "крем_уходовый": "уход_за_лицом",
    "сыворотка_для_лица": "уход_за_лицом",
    "тоник_лосьон": "уход_за_лицом",
    "очищение_лица": "очищение_лица",
    "скраб_пилинг": "очищение_лица",
    "маска_для_лица": "маска_для_лица",
    "средство_от_кожных_проблем": "средство_от_кожных_проблем",
    "масло_косметическое": "уход_за_телом",
    "дезодорант": "уход_за_телом",
    "мыло": "мыло",
    "гель_для_душа": "гель_для_душа",
    "бальзам_для_губ": "средство_для_губ",
    "депиляция": "депиляция",
    "декоративная_косметика": "декоративная_косметика",
    "лак_для_ногтей": "декоративная_косметика",
    "парфюмерия": "парфюмерия",
    "стирка": "средство_для_стирки",
    "кондиционер_для_белья": "средство_для_стирки",
    "отбеливатель_пятновыводитель": "средство_для_стирки",
    "чистящее_средство": "чистящее_средство",
    "сантехника": "чистящее_средство",
    "средство_для_стекол": "чистящее_средство",
    "средство_для_посуды": "средство_для_посуды",
    "освежитель_аромат": "освежитель_аромат",
    "аксессуары": "аксессуары",
    "расходники_бьюти": "расходники_бьюти",
    "дезинсекция": "дезинсекция",
    "гигиена_полости_рта": "прочее",
    "набор_подарочный": "прочее",
    "не_определено": "прочее",
}

_DIRTY_UNIT_RE = re.compile(r"\d+\s*(л\b|кг|гр\b|г\b)|\d+,\d+\s*(мл|л|г|кг)", re.I)
_QUOTED_RE = re.compile(r"[\"«»']")
_JUNK_PREFIX_RE = re.compile(r'^\s*[!$()\[\]#&*]|^\s*\d{3,}\s*/')
_SHADE_CODE_RE = re.compile(r"\b\d{1,2}[./,]\d{1,3}\b|\bтон\s*№?\s*\d")
_SET_RE = re.compile(r"\bнабор\b|\+|\bи\s+гель\b|\bдуо\b|\b\dв\d\b", re.I)

# Каждый класс сложности должен набрать столько примеров, иначе метрика по нему пустая.
HARD_FLOOR = 45

# Витрина WB режет название на 60 символах: в пуле 183 записи ровно на 60 и 94 на 59,
# и почти все оборваны на середине слова. Это и есть надёжный признак обрезки —
# «кончается строчной буквой» ловил 85% пула и был бесполезен.
WB_TITLE_LIMIT = 60
TRUNCATION_WINDOW = 2

# Доля «прочего» в выборке. Без потолка пропорциональный добор сливает туда
# треть бюджета: в пуле это самый крупный черновой узел (не_определено + наборы).
MISC_SHARE = 0.12


def hard_flags(name: str) -> list[str]:
    """Помечает, к каким классам сложности относится название."""
    flags = []
    if _DIRTY_UNIT_RE.search(name):
        flags.append("dirty_unit")
    if schema.has_pack_marker(name):
        flags.append("pack")
    if _QUOTED_RE.search(name):
        flags.append("quoted")
    if _JUNK_PREFIX_RE.search(name):
        flags.append("junk_prefix")
    if _SHADE_CODE_RE.search(name):
        flags.append("shade_code")
    if _SET_RE.search(name):
        flags.append("set_like")
    if len(schema.find_volume_candidates(name)) > 1:
        flags.append("multi_volume")
    if WB_TITLE_LIMIT - TRUNCATION_WINDOW < len(name) <= WB_TITLE_LIMIT and name[-1].isalpha():
        flags.append("truncated")
    if not flags:
        flags.append("simple")
    return flags


def load_pool(path: Path) -> list[dict]:
    """Читает пул кандидатов."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def select(pool: list[dict], total: int, floor: int, seed: int) -> list[dict]:
    """Отбирает кандидатов: пол на категорию, затем добор по классам сложности."""
    rng = random.Random(seed)
    for rec in pool:
        rec["final_probe"] = PROBE_TO_FINAL.get(rec["probe_category"], "прочее")
        rec["hard_flags"] = hard_flags(rec["name"])

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for rec in pool:
        by_cat[rec["final_probe"]].append(rec)
    for bucket in by_cat.values():
        # Записи с url ценнее: по ним возможна сверка бренда на карточке товара.
        rng.shuffle(bucket)
        bucket.sort(key=lambda r: not r["url"])

    # Квоты: пол каждому узлу, остаток — пропорционально весу узла в пуле.
    # «Прочее» под потолком, иначе оно съедает треть выборки.
    weights = Counter(r["final_probe"] for r in pool)
    real_cats = [c for c in by_cat if c != "прочее"]
    misc_quota = min(int(total * MISC_SHARE), len(by_cat.get("прочее", [])))

    quota = {cat: min(floor, len(by_cat[cat])) for cat in by_cat}
    quota["прочее"] = misc_quota
    spare = total - sum(quota.values())
    if spare > 0 and real_cats:
        weight_sum = sum(weights[c] for c in real_cats)
        for cat in real_cats:
            extra = int(spare * weights[cat] / weight_sum)
            quota[cat] = min(quota[cat] + extra, len(by_cat[cat]))

    chosen: dict[str, dict] = {}
    for cat, bucket in by_cat.items():
        for rec in bucket[: quota[cat]]:
            chosen[rec["id"]] = rec

    # Добор редких классов сложности до порога — без него метрика по классу пустая.
    for flag in ("dirty_unit", "multi_volume", "shade_code", "pack", "truncated",
                 "junk_prefix", "quoted", "set_like"):
        have = sum(1 for r in chosen.values() if flag in r["hard_flags"])
        if have >= HARD_FLOOR:
            continue
        pool_for_flag = [r for r in pool
                         if flag in r["hard_flags"]
                         and r["id"] not in chosen
                         and r["final_probe"] != "прочее"]
        rng.shuffle(pool_for_flag)
        for rec in pool_for_flag[: HARD_FLOOR - have]:
            chosen[rec["id"]] = rec

    # Хвост добираем пропорционально, обходя «прочее».
    remaining = total - len(chosen)
    if remaining > 0:
        rest = [r for r in pool if r["id"] not in chosen and r["final_probe"] != "прочее"]
        rest.sort(key=lambda r: (-weights[r["final_probe"]], rng.random()))
        for rec in rest[:remaining]:
            chosen[rec["id"]] = rec

    out = sorted(chosen.values(), key=lambda r: r["id"])
    rng.shuffle(out)
    return out


def main() -> None:
    """Точка входа: отбирает кандидатов, пишет JSONL и печатает покрытие."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total", type=int, default=600, help="сколько кандидатов отобрать")
    parser.add_argument("--floor", type=int, default=15, help="минимум на категорию")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--pool", type=Path, default=POOL_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    pool = load_pool(args.pool)
    chosen = select(pool, args.total, args.floor, args.seed)

    with args.out.open("w", encoding="utf-8") as fh:
        for rec in chosen:
            fh.write(json.dumps(
                {k: rec[k] for k in ("id", "name", "url", "source", "group_id", "final_probe", "hard_flags")},
                ensure_ascii=False,
            ) + "\n")

    print(f"=== ОТОБРАНО: {len(chosen)} из {len(pool)} ===\n")

    print("по категориям (черновая оценка):")
    cats = Counter(r["final_probe"] for r in chosen)
    for cat in schema.vocab("category"):
        cnt = cats.get(cat, 0)
        mark = " " if cnt >= args.floor else "!"
        print(f" {mark}{cat:32s} {cnt}")

    print("\nпо классам сложности:")
    flags = Counter(f for r in chosen for f in r["hard_flags"])
    for flag, cnt in flags.most_common():
        mark = " " if cnt >= HARD_FLOOR or flag == "simple" else "!"
        print(f" {mark}{flag:16s} {cnt}")

    with_url = sum(1 for r in chosen if r["url"])
    print(f"\nсо ссылкой на карточку: {with_url} (пойдут в eval для ручной сверки)")
    print(f"кандидаты записаны: {args.out}")


if __name__ == "__main__":
    main()
