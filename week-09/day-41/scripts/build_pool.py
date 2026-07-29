#!/usr/bin/env python3
"""Собирает пул кандидатов для датасета из сырых выгрузок маркетплейсов.

Сливает файлы выгрузок, отбрасывает мусор, схлопывает near-дубли в группы
и печатает распределение по черновой таксономии — оно нужно, чтобы
финализировать список категорий перед разметкой.

Важно: исходное название (`name`) в пул попадает БЕЗ очистки. Мусорные
префиксы вроде "!$!" — часть обучающего сигнала, модель должна научиться
их игнорировать сама. Нормализация применяется только к ключу дедупликации.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DAY_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = DAY_DIR / "datasets" / "raw"
OUT_PATH = DAY_DIR / "datasets" / "pool.jsonl"

# Файл product_names_and_urls.json (200 записей) исключён намеренно:
# он целиком входит в product_names_and_urls_2000.json.
SOURCES = [
    ("file1", RAW_DIR / "product_names.json"),
    ("file2000", RAW_DIR / "product_names_and_urls_2000.json"),
]

MIN_NAME_LEN = 12
MAX_PER_GROUP = 2

# Мусор по краям строки: "!", "! !", "!$!", "(", ")", кавычки, слэши, "!0011 /".
_EDGE_JUNK = re.compile(r'^[\s!$()\[\]"\'«»\-–—/\\.,;:*#№]+|[\s!$()\[\]"\'«»\-–—/\\.,;:*#]+$')
_LEADING_CODE = re.compile(r"^\s*!?\d{3,}\s*/\s*")

# Хвосты про количество в упаковке — снимаются только в ключе дедупликации,
# чтобы "…набор: 5 штук" и "…набор: 7 штук" схлопнулись в одну группу.
_PACK_TAILS = [
    re.compile(r"[,\s]*набор:?\s*\d+\s*штук?и?", re.I),
    re.compile(r"[(\[]?\s*\d+\s*шт\.?[)\]]?", re.I),
    re.compile(r"[,\s-]*\d+\s*пачк\w*", re.I),
    re.compile(r"[,\s-]*\d+\s*упаковк\w*", re.I),
    re.compile(r"[,\s-]*\d+\s*штук\w*", re.I),
]

# Черновая таксономия для отчёта. Порядок значим: частные правила выше общих.
# Это НЕ финальный словарь категорий — он выбирается по факту распределения,
# а настоящую категорию проставит LLM-разметчик.
#
# Правило = (label, тип продукта, объект применения | None). Двухфакторность нужна,
# потому что между типом и объектом на витрине вставляют что угодно:
# "Бальзам ДЛЯ УВЛАЖНЕНИЯ И ПИТАНИЯ волос".
HAIR = r"волос|локон|прядь|кожи головы|перхот|блонд|шевелюр"
FACE = r"\bлиц|\bкож|глаз|веки|век\b|морщин|пор[ыа]\b|носогуб"
TAXONOMY_PROBE: list[tuple[str, str, str | None]] = [
    ("дезинсекция", r"от насеком|таракан|от крыс|мышин|отрав|приманк|ловушк|шашк|от клещ|от комар|от моли|репеллент|инсектицид|от муравь|от мошк|от блох|от грызун", None),
    ("расходники_бьюти", r"ресниц|типсы|гель-?лак|фрез|кисть для|кисти для|пинцет|ламинирован|наращиван|слайдер|для ногт|праймер|биотатуаж|пигмент для|аппарат для маникюр|дип пудра|дип систем|для маникюр|для педикюр|для бров", None),
    ("аксессуары", r"расч[её]ск|мочалк|губк|спонж|органайзер|лезви|станок|бигуди|шапочк|полотенц|щетк|пилк|\bбаф\b|зеркал[оа]\b|дозатор|пульверизатор|перчатк|салфетк|тряпк|швабр|веник|контейнер|футляр|косметичк", None),
    ("краска_для_волос", r"краск|красител|koleston|color touch|illumina|окислител|оксигент|оксид \d|порошок для осветл|обесцвеч|блондоран|осветлител|тон\s*№|хна\b", HAIR + r"|koleston|color touch|illumina|оксигент|блондоран"),
    ("оттеночное", r"оттеночн|тонирующ|против желтизн|нейтрализующ|тон[ие]рован|goodbye yellow", None),
    ("укладка_волос", r"для укладк|\bлак\b|мусс|воск|\bгель\b|стик|пудра для об[ъь]ем|термозащит|фиксац|\bспрей\b|паста|жидкост|крем-?спрей|филлер", HAIR),
    ("шампунь", r"шампун", None),
    ("бальзам_кондиционер", r"бальзам|кондиционер|ополаскиват", HAIR),
    ("маска_для_волос", r"\bмаск|ботокс|протеинирован|кератинов", HAIR),
    ("уход_для_волос", r"сыворотк|\bмасл|лосьон|концентрат|флюид|эликсир|ампул|актив|тоник|\bспрей|уход", HAIR + r"|от выпадени|роста волос"),
    ("депиляция", r"депиляц|шугаринг|удалени[ея] волос|воск для эпил|эпиляц|бритв|для бритья", None),
    ("средство_от_кожных_проблем", r"от прыщ|акне|витилиго|грибк|псориаз|\bмазь\b|пластырь от|от пигментац|отбеливающий крем|от папиллом|от бородав|от герпес|от демодекоз|от растяж|от целлюлит|от мозол|от потлив|от варикоз", None),
    ("очищение_лица", r"мицелляр|снятия макияжа|для умыван|ремувер|очищающ|гидрофильн|\bпенк|демакияж", None),
    ("маска_для_лица", r"\bмаск|патч", FACE + r"|тканев|альгинатн|глинян"),
    ("бальзам_для_губ", r"для губ|\bгуб\b", None),
    ("сыворотка_для_лица", r"сыворотк|серум|эссенц", None),
    ("тоник_лосьон", r"\bтоник|тонер|лосьон", FACE),
    ("скраб_пилинг", r"скраб|пилинг|эксфолиант|обертыван", None),
    ("крем_уходовый", r"\bкрем|гель-?крем|флюид|\bбальзам", FACE + r"|для рук|для тела|для ног|для ступн|увлажня|питатель"),
    ("масло_косметическое", r"\bмасл", None),
    ("мыло", r"\bмыло|мыльн", None),
    ("гель_для_душа", r"для душа|пена для ванн|бомбочк|соль для ванн|для тела|для ванн", None),
    ("дезодорант", r"дезодорант|антиперспирант", None),
    ("декоративная_косметика", r"\bтушь|помад|блеск|\bтени\b|тональн|консилер|румян|подводк|хайлайтер|карандаш для|база под макияж|фиксирующий спрей|для макияжа|бб-?крем|сс-?крем|\bглиттер|\bшиммер", None),
    ("парфюмерия", r"\bдухи\b|парфюм|туалетная вода|спрей-?мист|\bодеколон|\bаромат для", None),
    ("гигиена_полости_рта", r"зубн|ополаскиватель для рта|ирригатор|для полости рта", None),
    ("стирка", r"для стирки|порошок стиральн|стиральный порошок|капсул\w* для стирк|ловушка цвета|для белья", None),
    ("отбеливатель_пятновыводитель", r"отбеливател|пятновывод|от пятен", None),
    ("средство_для_посуды", r"для посуды|посудомоеч|для мытья посуд", None),
    ("сантехника", r"для унитаз|для ванной|от налета|от налёта|от ржавчин|блок для унитаз|для труб|канализац|засор|сантехник|от известков|от накип", None),
    ("средство_для_стекол", r"для стекол|для ст[её]кол|для зеркал", None),
    ("освежитель_аромат", r"освежител|ароматизатор|\bсаше\b|аромадиффуз|благовони|\bаромапалочк", None),
    ("чистящее_средство", r"чистящ|анти-?жир|жироудалит|для плиты|для кухни|универсальн|доместос|domestos|\bcif\b|\bсиф\b|санитарн|дезинфиц|для духовк|для микроволнов|для ковр|для мебели|очистител|для уборк|моющ|для пола|против бактер|для удален", None),
    ("набор_подарочный", r"подарочный набор|бьюти[\s-]*бокс|набор косметик|косметический набор|\bнабор\b", None),
]
_TAXONOMY_COMPILED = [
    (name, re.compile(type_pat, re.I), re.compile(obj_pat, re.I) if obj_pat else None)
    for name, type_pat, obj_pat in TAXONOMY_PROBE
]


def normalize_key(name: str) -> str:
    """Строит ключ дедупликации: снимает мусор по краям, хвосты про упаковку и регистр."""
    s = _LEADING_CODE.sub("", name)
    s = s.lower()
    for pat in _PACK_TAILS:
        s = pat.sub(" ", s)
    s = _EDGE_JUNK.sub("", s)
    s = re.sub(r"[\s]+", " ", s)
    return s.strip(" ,.-")


def probe_category(name: str) -> str:
    """Возвращает черновую категорию по ключевым словам (только для отчёта)."""
    for label, type_pat, obj_pat in _TAXONOMY_COMPILED:
        if not type_pat.search(name):
            continue
        if obj_pat is not None and not obj_pat.search(name):
            continue
        return label
    return "не_определено"


def marketplace(url: str) -> str:
    """Определяет площадку по url."""
    if "wildberries" in url:
        return "wb"
    if "ozon" in url:
        return "ozon"
    return "нет_url" if not url else "прочее"


def load_source(tag: str, path: Path) -> list[dict]:
    """Читает одну сырую выгрузку (в файлах есть BOM) и приводит к общему виду."""
    if not path.exists():
        sys.exit(f"нет файла: {path}")
    raw = json.load(path.open(encoding="utf-8-sig"))["product_names"]
    return [
        {"name": item["name"].strip(), "url": item.get("url", ""), "source": tag}
        for item in raw
    ]


def build_pool() -> tuple[list[dict], dict]:
    """Сливает источники, фильтрует и схлопывает near-дубли. Возвращает пул и статистику."""
    records: list[dict] = []
    for tag, path in SOURCES:
        records.extend(load_source(tag, path))

    stats = {"загружено": len(records)}

    seen_exact: set[str] = set()
    unique: list[dict] = []
    for rec in records:
        if rec["name"] in seen_exact:
            continue
        seen_exact.add(rec["name"])
        unique.append(rec)
    stats["после_точной_дедупликации"] = len(unique)

    kept: list[dict] = []
    dropped_short: list[str] = []
    for rec in unique:
        if len(rec["name"]) < MIN_NAME_LEN:
            dropped_short.append(rec["name"])
            continue
        kept.append(rec)
    stats["отброшено_коротких"] = len(dropped_short)
    stats["примеры_коротких"] = dropped_short[:10]

    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in kept:
        groups[normalize_key(rec["name"])].append(rec)
    stats["групп_near_дублей"] = len(groups)
    stats["групп_размером_больше_1"] = sum(1 for g in groups.values() if len(g) > 1)

    pool: list[dict] = []
    for gid, (key, members) in enumerate(sorted(groups.items())):
        # Записи с url ценнее: по ним возможна ручная сверка бренда на карточке.
        members.sort(key=lambda r: (not r["url"], r["name"]))
        for rec in members[:MAX_PER_GROUP]:
            pool.append(
                {
                    "id": f"p{len(pool):05d}",
                    "name": rec["name"],
                    "url": rec["url"],
                    "source": rec["source"],
                    "group_id": gid,
                    "norm": key,
                    "probe_category": probe_category(rec["name"]),
                }
            )
    stats["в_пуле"] = len(pool)
    return pool, stats


def main() -> None:
    """Точка входа: собирает пул, пишет JSONL и печатает отчёт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="куда писать пул")
    args = parser.parse_args()

    pool, stats = build_pool()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for rec in pool:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("=== ПУЛ ===")
    for key in ("загружено", "после_точной_дедупликации", "отброшено_коротких",
                "групп_near_дублей", "групп_размером_больше_1", "в_пуле"):
        print(f"  {key:32s} {stats[key]}")
    print(f"  примеры отброшенных коротких: {stats['примеры_коротких']}")

    print("\n=== ПЛОЩАДКИ ===")
    for mp, cnt in Counter(marketplace(r["url"]) for r in pool).most_common():
        print(f"  {mp:12s} {cnt}")

    print("\n=== ЧЕРНОВОЕ РАСПРЕДЕЛЕНИЕ ПО КАТЕГОРИЯМ ===")
    cats = Counter(r["probe_category"] for r in pool)
    for label, cnt in cats.most_common():
        mark = "  " if cnt >= 15 else " *"
        print(f"{mark}{label:32s} {cnt}")
    print(f"\n  узлов всего: {len(cats)}")
    print(f"  узлов с >=15 примерами: {sum(1 for c in cats.values() if c >= 15)}")
    print(f"  покрыто узлами с >=15: {sum(c for c in cats.values() if c >= 15)}")
    print("  (* — узел мельче 15 примеров, кандидат на слияние или в «прочее»)")

    undecided = [r["name"] for r in pool if r["probe_category"] == "не_определено"]
    print(f"\n=== НЕ ОПРЕДЕЛЕНО: {len(undecided)} ===")
    for name in undecided[:40]:
        print(f"  ? {name}")

    print(f"\nпул записан: {args.out}")


if __name__ == "__main__":
    main()
