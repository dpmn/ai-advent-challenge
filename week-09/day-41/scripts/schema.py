#!/usr/bin/env python3
"""Единственный источник правды по схеме извлечения атрибутов.

Загружает `schema.json`, собирает из него system-промпт и предоставляет
программные инварианты для проверки разметки. Всё остальное — разметка,
baseline, сборка датасета, скоринг — импортирует отсюда, чтобы словари
и промпт не разъехались между этапами.

Запуск напрямую рендерит промпт в `prompts/system.txt` (снимок для истории).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DAY_DIR = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent

# На арендованную ВМ скрипты копируются плоско, в один каталог, поэтому
# schema.json ищется и рядом с модулем, а не только на уровень выше.
SCHEMA_PATH = next(
    (p for p in (DAY_DIR / "schema.json", _HERE / "schema.json") if p.exists()),
    DAY_DIR / "schema.json",
)
PROMPT_PATH = DAY_DIR / "prompts" / "system.txt"

FIELD_ORDER = [
    "category", "brand", "line", "volume",
    "pack_count", "form", "purpose", "shade", "is_set",
]

# Единицы во входной строке -> (целевая единица, множитель).
_UNIT_MAP = {
    "мл": ("ml", 1), "ml": ("ml", 1),
    "л": ("ml", 1000), "l": ("ml", 1000), "литр": ("ml", 1000),
    "г": ("g", 1), "гр": ("g", 1), "g": ("g", 1), "грамм": ("g", 1),
    "кг": ("g", 1000), "kg": ("g", 1000),
    "шт": ("pcs", 1), "штук": ("pcs", 1), "pcs": ("pcs", 1),
}
_VOLUME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(мл|ml|литр\w*|л|l|кг|kg|грамм\w*|гр|г|g|штук\w*|шт)\b\.?",
    re.I,
)
_PACK_RE = re.compile(
    r"(\d+\s*шт|набор:?\s*\d+|\d+\s*пачк|\d+\s*упаковк|\d+\s*штук"
    r"|\d+\s*лист|\d+\s*саше|\d+\s*пакетик|\d+\s*ампул|\d+\s*флакон|\d+\s*банк"
    r"|шт\s*[*х×]\s*\d+|\d+\s*[*х×]\s*\d+\s*(мл|г)|по\s*\d+\s*(мл|г))",
    re.I,
)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Читает schema.json (кешируется)."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def vocab(name: str) -> list[str]:
    """Возвращает закрытый словарь по имени."""
    return load_schema()["vocabularies"][name]


def build_system_prompt() -> str:
    """Собирает system-промпт из схемы.

    Один и тот же промпт идёт в разметку, в baseline и в обучающие примеры —
    иначе сравнение «до/после тюна» будет нечестным.
    """
    schema = load_schema()
    hints = schema["category_hints"]

    lines = [
        "Ты извлекаешь атрибуты товара из названия карточки маркетплейса "
        "(косметика, бытовая химия и смежные товары).",
        "",
        "Верни ровно один JSON-объект с девятью ключами в этом порядке:",
        "category, brand, line, volume, pack_count, form, purpose, shade, is_set",
        "",
        "ЗНАЧЕНИЯ ПОЛЕЙ",
        '- category: одно значение из словаря категорий.',
        '- brand: марка производителя, строка из входа, либо null.',
        '- line: линейка/серия, строка из входа, либо null.',
        '- volume: {"value": число, "unit": "ml"|"g"|"pcs"} либо null.',
        "- pack_count: целое >= 1 либо null.",
        "- form: одно значение из словаря форм либо null.",
        "- purpose: массив значений из словаря назначений, возможно пустой.",
        "- shade: оттенок или его код, строка из входа, либо null.",
        "- is_set: true или false.",
        "",
        "СЛОВАРЬ КАТЕГОРИЙ",
    ]
    for cat in vocab("category"):
        hint = hints.get(cat)
        lines.append(f"- {cat}" + (f" — {hint}" if hint else ""))

    lines += [
        "",
        "СЛОВАРЬ ФОРМ: " + ", ".join(vocab("form")),
        "СЛОВАРЬ НАЗНАЧЕНИЙ: " + ", ".join(vocab("purpose")),
        "",
        "ПРАВИЛА",
    ]
    lines += [f"{i}. {rule}" for i, rule in enumerate(schema["rules"], 1)]

    lines += ["", "ПРИМЕРЫ"]
    for ex in schema["examples"]:
        compact = json.dumps(ex["output"], ensure_ascii=False, separators=(",", ":"))
        lines.append(f"Вход: {ex['input']}")
        lines.append(f"Выход: {compact}")

    return "\n".join(lines)


def normalize_for_match(text: str) -> str:
    """Приводит строку к виду для проверки вхождения.

    Гасит регистр, ё/е, кавычки и пробелы вокруг дефисов: на витрине пишут
    "темно -бордовый", и приведение к "темно-бордовый" — осмысленная
    нормализация, а не галлюцинация. Новых слов такая чистка не создаёт,
    поэтому выдуманный бренд по-прежнему ловится.
    """
    cleaned = text.lower().replace("ё", "е")
    cleaned = re.sub(r"[«»\"'`]", "", cleaned)
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_model_json(text: str) -> tuple[dict | None, bool]:
    """Разбирает ответ модели.

    Возвращает (объект, был_ли_ответ_чистым_JSON). «Чистый» — значит без
    markdown-обёрток и текста вокруг; это отдельная метрика качества.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped), True
    except json.JSONDecodeError:
        pass

    candidate = _FENCE_RE.sub("", stripped).strip()
    if candidate != stripped:
        try:
            return json.loads(candidate), False
        except json.JSONDecodeError:
            pass

    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1]), False
        except json.JSONDecodeError:
            pass
    return None, False


def find_volume_candidates(name: str) -> set[tuple[float, str]]:
    """Находит во входной строке все нормализованные пары (значение, единица)."""
    found: set[tuple[float, str]] = set()
    for raw_value, raw_unit in _VOLUME_RE.findall(name):
        unit_key = raw_unit.lower().rstrip(".")
        target = _UNIT_MAP.get(unit_key)
        if target is None:
            for prefix, mapped in _UNIT_MAP.items():
                if unit_key.startswith(prefix):
                    target = mapped
                    break
        if target is None:
            continue
        unit, factor = target
        found.add((round(float(raw_value.replace(",", ".")) * factor, 3), unit))
    return found


def has_pack_marker(name: str) -> bool:
    """Есть ли во входной строке явное указание количества единиц."""
    return _PACK_RE.search(name) is not None


def check_invariants(name: str, obj: dict) -> list[str]:
    """Проверяет размеченный объект против входной строки.

    Возвращает список нарушений. Пустой список — разметка прошла автопроверку;
    это не гарантия правильности, но отсекает галлюцинации и грубые промахи.
    """
    problems: list[str] = []

    missing = [f for f in FIELD_ORDER if f not in obj]
    if missing:
        problems.append(f"нет ключей: {', '.join(missing)}")
    extra = [k for k in obj if k not in FIELD_ORDER]
    if extra:
        problems.append(f"лишние ключи: {', '.join(extra)}")

    category = obj.get("category")
    if category not in vocab("category"):
        problems.append(f"category вне словаря: {category!r}")

    form = obj.get("form")
    if form is not None and form not in vocab("form"):
        problems.append(f"form вне словаря: {form!r}")

    purpose = obj.get("purpose")
    if not isinstance(purpose, list):
        problems.append(f"purpose не массив: {purpose!r}")
    else:
        bad = [p for p in purpose if p not in vocab("purpose")]
        if bad:
            problems.append(f"purpose вне словаря: {bad}")

    if not isinstance(obj.get("is_set"), bool):
        problems.append(f"is_set не bool: {obj.get('is_set')!r}")

    haystack = normalize_for_match(name)
    for field in ("brand", "line", "shade"):
        value = obj.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field} не непустая строка: {value!r}")
        elif normalize_for_match(value) not in haystack:
            problems.append(f"{field} отсутствует во входе (галлюцинация): {value!r}")

    volume = obj.get("volume")
    if volume is not None:
        if not isinstance(volume, dict) or set(volume) != {"value", "unit"}:
            problems.append(f"volume неверной формы: {volume!r}")
        elif volume["unit"] not in vocab("unit"):
            problems.append(f"volume.unit вне словаря: {volume['unit']!r}")
        elif not isinstance(volume["value"], (int, float)):
            problems.append(f"volume.value не число: {volume['value']!r}")
        else:
            pair = (round(float(volume["value"]), 3), volume["unit"])
            candidates = find_volume_candidates(name)
            if candidates and pair not in candidates:
                problems.append(f"volume {pair} не сходится с найденным во входе {sorted(candidates)}")
            elif not candidates:
                problems.append(f"volume {pair} есть, но во входе единиц не найдено")

    pack = obj.get("pack_count")
    if pack is not None:
        if not isinstance(pack, int) or isinstance(pack, bool) or pack < 1:
            problems.append(f"pack_count не целое >= 1: {pack!r}")
        elif pack > 1 and not has_pack_marker(name):
            problems.append(f"pack_count={pack}, но во входе нет указания количества")

    return problems


def main() -> None:
    """Рендерит system-промпт в prompts/system.txt."""
    prompt = build_system_prompt()
    PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_PATH.write_text(prompt + "\n", encoding="utf-8")
    print(f"промпт записан: {PROMPT_PATH}")
    print(f"строк: {prompt.count(chr(10)) + 1}, символов: {len(prompt)}")
    print(f"категорий: {len(vocab('category'))}, форм: {len(vocab('form'))}, назначений: {len(vocab('purpose'))}")


if __name__ == "__main__":
    main()
