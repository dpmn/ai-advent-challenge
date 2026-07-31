#!/usr/bin/env python3
"""Этапы декомпозиции инференса и их сборка в три конвейера.

Задача та же, что в днях 41–43: девять атрибутов из названия карточки
маркетплейса. Монолит просит у модели один JSON с девятью разнотипными
ключами при системном промпте на три словаря сразу — на `qwen3:0.6b` это
даёт 61,1% пополевой точности и 0% полностью верных ответов.

Декомпозиция разбирает тот же запрос на короткие этапы, в каждом из которых
модель видит **один** словарь и отвечает **одним** значением, а не структурой:

    этап 1  нормализация  — разметить вход: чистое название, числа, кавычки, скобки
    этап 2  классификация — category / form / purpose / is_set по закрытым словарям
    этап 3  извлечение    — brand / line / shade / volume / pack_count из входа

В полном варианте на каждое поле приходится отдельный вызов: десять коротких
запросов вместо одного длинного.

Сборка девяти полей в JSON детерминированная: модель не собирает структуру,
она только отвечает на вопросы. Всё, что этапы не смогли разобрать, остаётся
`None` и честно считается ошибкой — подставлять умолчания значило бы мерить
качество кода, а не качество инференса.

**Оригинал названия передаётся на каждый этап.** Разметка первого этапа
идёт рядом с ним, а не вместо: ошибка нормализации иначе отравляла бы все
девять полей без шанса на исправление, и день мерил бы вред каскада.
"""

from __future__ import annotations

import re

from common import FIELD_ORDER, schema

# Пустое значение в ответах этапов. Одинаковое во всех промптах: модели проще
# запомнить один знак, чем «нет» / «null» / «-» вперемешку.
EMPTY = "-"

_EMPTY_WORDS = {EMPTY, "", "нет", "none", "null", "не указано", "отсутствует", "—", "–"}

_UNIT_ALIASES = {
    "ml": "ml", "мл": "ml", "ml.": "ml",
    "g": "g", "г": "g", "гр": "g", "g.": "g",
    "pcs": "pcs", "шт": "pcs", "штук": "pcs",
}

_VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([A-Za-zА-Яа-я.]+)")
_INT_RE = re.compile(r"\d+")


# --------------------------------------------------------------------------
# Разбор строчных ответов
# --------------------------------------------------------------------------

def clean_value(text: str) -> str | None:
    """Чистит одно значение из ответа этапа: маркер списка, кавычки, точка, пустышки.

    Маркер снимается только если за ним идёт текст: одиночный `-` — это и есть
    условленное пустое значение, и съедать его нельзя.
    """
    value = (text or "").strip().strip("`").strip()
    value = re.sub(r"^[-*•—–]\s+", "", value).strip()
    value = re.sub(r"^[«\"'`]+|[»\"'`]+$", "", value).strip()
    value = value.rstrip(".").strip()
    if value.lower() in _EMPTY_WORDS:
        return None
    return value or None


def parse_lines(raw: str, keys: dict[str, str]) -> dict[str, str | None]:
    """Разбирает ответ вида `КЛЮЧ: значение` по строкам.

    `keys` — соответствие метки в ответе модели имени поля. Метка ищется без
    учёта регистра и в любом порядке строк: 0.6B иногда переставляет строки
    местами, и требовать точный порядок значило бы штрафовать за мелочь.
    """
    found: dict[str, str | None] = {name: None for name in keys.values()}
    for line in (raw or "").splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip().strip("*-•").strip().upper()
        for marker, name in keys.items():
            if label == marker:
                found[name] = clean_value(value)
    return found


def match_vocab(answer: str | None, vocab_name: str) -> tuple[str | None, bool]:
    """Приводит ответ этапа к значению закрытого словаря.

    Возвращает `(значение, формат_соблюдён)`. Сначала точное совпадение, потом
    поиск ровно одного словарного значения внутри ответа — 0.6B изредка пишет
    «Категория: шампунь» вместо голого значения. Если словарных значений
    в ответе несколько или ни одного, это нарушение формата, и поле остаётся
    пустым: домысливать за модель нельзя, иначе этап нельзя будет оценить.
    """
    if answer is None:
        return None, True
    vocab = schema.vocab(vocab_name)
    normalized = answer.strip().lower().replace("ё", "е")
    for value in vocab:
        if normalized == value.lower():
            return value, True
    hits = [v for v in vocab if re.search(rf"(?<![\w_]){re.escape(v.lower())}(?![\w_])", normalized)]
    if len(hits) == 1:
        return hits[0], False
    return None, False


def parse_bool(answer: str | None) -> tuple[bool | None, bool]:
    """Разбирает ответ да/нет этапа `is_set`."""
    if answer is None:
        return False, True
    normalized = answer.strip().lower()
    if normalized.startswith(("да", "yes", "true")):
        return True, True
    if normalized.startswith(("нет", "no", "false")):
        return False, True
    return None, False


def parse_volume(answer: str | None) -> tuple[dict | None, bool]:
    """Разбирает ответ этапа об объёме в `{"value": число, "unit": ...}`."""
    if answer is None:
        return None, True
    match = _VOLUME_RE.search(answer)
    if not match:
        return None, False
    unit = _UNIT_ALIASES.get(match.group(2).strip(".").lower())
    if unit is None:
        return None, False
    value = float(match.group(1).replace(",", "."))
    return {"value": int(value) if value.is_integer() else value, "unit": unit}, True


def parse_pack(answer: str | None) -> tuple[int | None, bool]:
    """Разбирает ответ этапа о количестве единиц в упаковке."""
    if answer is None:
        return None, True
    match = _INT_RE.search(answer)
    if not match:
        return None, False
    number = int(match.group())
    return (number, True) if number >= 1 else (None, False)


def parse_purpose(answer: str | None) -> tuple[list[str], bool]:
    """Разбирает мультиметку назначений: значения словаря через запятую."""
    if answer is None:
        return [], True
    vocab = schema.vocab("purpose")
    chosen: list[str] = []
    strict = True
    for part in re.split(r"[,;]", answer):
        value = clean_value(part)
        if value is None:
            continue
        matched, exact = match_vocab(value, "purpose")
        strict = strict and exact
        if matched and matched not in chosen:
            chosen.append(matched)
    if not chosen and vocab and answer.strip().lower() not in _EMPTY_WORDS:
        strict = False
    return chosen, strict


# --------------------------------------------------------------------------
# Промпты этапов
# --------------------------------------------------------------------------

def _category_lines() -> str:
    """Список категорий с подсказками — тот же текст, что в монолитном промпте."""
    hints = schema.load_schema()["category_hints"]
    lines = []
    for value in schema.vocab("category"):
        hint = hints.get(value)
        lines.append(f"- {value}" + (f" — {hint}" if hint else ""))
    return "\n".join(lines)


NORMALIZE_PROMPT = f"""Ты размечаешь название товара с витрины маркетплейса перед разбором.

Верни РОВНО четыре строки, без пояснений до и после:
ЧИСТО: <название без витринного мусора>
ЧИСЛА: <фрагменты с числами, через ;>
КАВЫЧКИ: <содержимое кавычек, через ;>
СКОБКИ: <содержимое скобок, через ;>

ПРАВИЛА
1. Ничего не переводить, не дописывать и не менять местами. Только копировать из входа.
2. Витринный мусор — восклицательные знаки, «!ЛИКВИДАЦИЯ!», «ХИТ», «АКЦИЯ», артикулы вида #317, одиночные скобки по краям.
3. В ЧИСЛА выписывай фрагмент целиком вместе с единицей: «400мл», «2 шт», «26 стирок», «SPF 30».
4. Если чего-то нет — поставь {EMPTY}.
5. Название бывает обрезано на середине слова — оставляй как есть, не дописывай.

ПРИМЕР
Вход: !$! Крем-спрей Сиф чистящий 500 мл, набор: 5 штук #317
ЧИСТО: Крем-спрей Сиф чистящий 500 мл, набор: 5 штук
ЧИСЛА: 500 мл; 5 штук
КАВЫЧКИ: {EMPTY}
СКОБКИ: {EMPTY}"""


CATEGORY_PROMPT = f"""Ты определяешь категорию товара по названию карточки маркетплейса.

СЛОВАРЬ КАТЕГОРИЙ
{_category_lines()}

ПРАВИЛА
1. Ответь РОВНО одним значением из словаря, скопированным дословно.
   Без пояснений, без кавычек, без markdown, без своих слов.
2. «прочее» — крайний случай. Сначала проверь все категории выше: товар с витрины
   почти всегда попадает в одну из них.
3. Категорию задаёт назначение товара, а не форма. Первое слово названия
   («Гель», «Крем», «Порошок», «Таблетки») — это форма, а не категория.

ПРИМЕРЫ
Вход: Крем-спрей Сиф чистящий 500 мл, набор: 5 штук
Ответ: чистящее_средство
Вход: "BIELITA COLOR" тон №05.2 Баклажан (с витаминами) (Белита-М)
Ответ: краска_для_волос
Вход: Жидкость для мытья посуды Fairy Лимон 450 мл
Ответ: средство_для_посуды
Вход: Типсы для наращивания ногтей прозрачные 100 шт
Ответ: расходники_бьюти"""


FORM_PROMPT = f"""Ты определяешь физическую форму выпуска товара по названию карточки маркетплейса.

СЛОВАРЬ ФОРМ: {", ".join(schema.vocab("form"))}

Ответь РОВНО одним значением из словаря или {EMPTY}. Без пояснений, без markdown.

ПРАВИЛА
1. Форма берётся только из самого названия товара.
2. Не угадывай форму по категории: «шампунь», «краска», «маска-патчи» форму не задают.
3. Формы во входе нет — ответь {EMPTY}. Пустой ответ лучше выдуманного.

ПРИМЕРЫ
Вход: Порошок для стирки белья с кислородом
Ответ: порошок
Вход: Маска-патчи тканевые
Ответ: {EMPTY}"""


PURPOSE_PROMPT = f"""Ты определяешь назначение товара по названию карточки маркетплейса.

СЛОВАРЬ НАЗНАЧЕНИЙ: {", ".join(schema.vocab("purpose"))}

Ответь значениями из словаря через запятую либо {EMPTY}. Без пояснений, без markdown.

ПРАВИЛА
1. Бери только то, что прямо написано во входе. Ничего не додумывать по смыслу товара.
2. Больше двух значений почти никогда не бывает. Весь словарь перечислять нельзя.
3. Во входе назначения нет — ответь {EMPTY}. Это самый частый правильный ответ.

ПРИМЕРЫ
Вход: Шампунь против перхоти и зуда с туей 400мл
Ответ: против_перхоти
Вход: Порошок для стирки белья с кислородом
Ответ: {EMPTY}
Вход: Крем для лица увлажняющий питательный
Ответ: увлажнение, питание"""


IS_SET_PROMPT = """Ты определяешь, является ли товар набором из РАЗНЫХ продуктов.

Ответь одним словом: да или нет. Без пояснений.

ПРАВИЛА
1. да — только если во входе перечислены РАЗНЫЕ продукты: «шампунь + бальзам».
2. Несколько одинаковых единиц — это не набор: «2 шт», «набор: 5 штук» -> нет.
3. Слово «набор» само по себе ничего не значит, смотри на состав.
4. Правильный ответ почти всегда — нет.

ПРИМЕРЫ
Вход: Порошок для стирки белья с кислородом
Ответ: нет
Вход: Крем-спрей чистящий 500 мл, набор: 5 штук
Ответ: нет
Вход: Набор Tomy Cherry шампунь 250 мл и гель д/душа 250 мл
Ответ: да"""


BRAND_PROMPT = f"""Ты находишь марку производителя в названии карточки товара маркетплейса.

Ответь одной строкой: марка дословно из входа либо {EMPTY}. Без пояснений, без markdown.

ПРАВИЛА
1. Марка — имя фирмы или бренда, а не описание товара и не его тип.
2. Значение обязано дословно встречаться во входе. Ничего не переводить и не додумывать.
3. Марки во входе нет — ответь {EMPTY}. Это частый правильный ответ.
4. Марка часто стоит в скобках в конце: «(Белита-М)» -> Белита-М.
5. Кавычки в значение не включай.
6. Числа с единицами («450 мл», «3000 г», «15 мм») маркой не бывают.
7. Никогда не бери ответ из примеров ниже: они не про твой вход.

ПРИМЕРЫ
Вход: "BIELITA COLOR" тон №05.2 Баклажан (с витаминами) (Белита-М)
Ответ: Белита-М
Вход: Шампунь для сухих волос 400 мл
Ответ: {EMPTY}"""


LINE_PROMPT = f"""Ты находишь линейку (серию) товара в названии карточки маркетплейса.

Ответь одной строкой: имя линейки дословно из входа либо {EMPTY}. Без пояснений.

ПРАВИЛА
1. Линейка — собственное имя серии внутри бренда, обычно рядом с маркой или в кавычках.
2. Линейка — это НЕ марка, НЕ тип товара и НЕ его описание.
3. Название товара целиком в ответ не копировать.
4. Линейки во входе нет — ответь {EMPTY}. Это самый частый правильный ответ.
5. Числа с единицами («450 мл», «0.07», «15 мм») линейкой не бывают.
6. Никогда не бери ответ из примеров ниже: они не про твой вход.

ПРИМЕРЫ
Вход: "BIELITA COLOR" тон №05.2 Баклажан (с витаминами) (Белита-М)
Ответ: BIELITA COLOR
Вход: Стиральный порошок для белых тканей 3 кг
Ответ: {EMPTY}"""


SHADE_PROMPT = f"""Ты находишь оттенок или цвет товара в названии карточки маркетплейса.

Ответь одной строкой: оттенок дословно из входа либо {EMPTY}. Без пояснений.

ПРАВИЛА
1. Оттенок — это код тона («05.2 Баклажан», «9/81», «10.012») или цвет («синяя», «бежевый»).
2. Значение обязано дословно встречаться во входе.
3. Оттенка во входе нет — ответь {EMPTY}. Это самый частый правильный ответ.
4. Объём, вес, количество и проценты («450 мл», «3000 г», «100 стирок», «15 мм», «5%»)
   оттенком НЕ являются. Название товара целиком — тоже не оттенок.
5. Кавычки в значение не включай.
6. Никогда не бери ответ из примеров ниже: они не про твой вход.

ПРИМЕРЫ
Вход: "BIELITA COLOR" тон №05.2 Баклажан (Белита-М)
Ответ: 05.2 Баклажан
Вход: Мочалка для тела синяя
Ответ: синяя
Вход: Порошок для стирки белья с кислородом 3000 г
Ответ: {EMPTY}"""


VOLUME_PROMPT = f"""Ты находишь объём или вес ОДНОЙ единицы товара в названии карточки маркетплейса.

Ответь одной строкой в формате «<число> <ml|g|pcs>» либо {EMPTY}. Без пояснений.

ПРАВИЛА
1. Приводи к ml или g: 1,3 л -> 1300 ml; 0.1 кг -> 100 g; 100 гр -> 100 g.
2. «2 шт по 300 мл» -> 300 ml. Не 600: объём одной единицы, а не всей упаковки.
3. Единицы измерения во входе нет («75,0») -> {EMPTY}.
4. Число не про объём («26 стирок», «15 лезвий», «72%», «SPF 30», «20 VOL», код оттенка) -> {EMPTY}.
5. Цифр во входе нет -> {EMPTY}.
6. Никогда не бери число из примеров ниже: они не про твой вход.

ПРИМЕРЫ
Вход: Шампунь для волос 2 шт по 300 мл
Ответ: 300 ml
Вход: Порошок для стирки белья с кислородом
Ответ: {EMPTY}"""


PACK_PROMPT = """Ты определяешь, сколько одинаковых единиц товара продаётся в предложении.

Ответь одним целым числом. Без пояснений.

ПРАВИЛА
1. Число больше 1 только при явном указании количества: «2 шт», «набор: 5 штук», «3 пачки».
2. Количество не указано -> 1. Это самый частый правильный ответ.
3. «400 мл», «3000 г», «26 стирок», «SPF 30» — это не количество единиц.
4. Никогда не бери число из примеров ниже: они не про твой вход.

ПРИМЕРЫ
Вход: Крем-спрей чистящий 500 мл, набор: 5 штук
Ответ: 5
Вход: Порошок для стирки белья с кислородом 3000 г
Ответ: 1"""


CLASSIFY_ALL_PROMPT = f"""Ты классифицируешь товар по названию карточки маркетплейса.

Верни РОВНО четыре строки, без пояснений до и после:
КАТЕГОРИЯ: <одно значение из словаря категорий>
ФОРМА: <одно значение из словаря форм или {EMPTY}>
НАЗНАЧЕНИЕ: <значения из словаря назначений через запятую или {EMPTY}>
НАБОР: <да, если это набор из РАЗНЫХ продуктов, иначе нет>

СЛОВАРЬ КАТЕГОРИЙ
{_category_lines()}

СЛОВАРЬ ФОРМ: {", ".join(schema.vocab("form"))}
СЛОВАРЬ НАЗНАЧЕНИЙ: {", ".join(schema.vocab("purpose"))}

ПРАВИЛА
0. Категорию задаёт назначение товара, а не форма: первое слово названия
   («Гель», «Крем», «Порошок») — это ФОРМА. «прочее» — крайний случай,
   товар с витрины почти всегда попадает в одну из категорий выше.
1. Только значения из словарей, скопированные дословно. Ничего своего.
2. Форму не угадывать по категории: «шампунь», «маска-патчи» форму не задают.
3. Назначение брать только прямо указанное во входе; больше двух значений почти не бывает,
   весь словарь перечислять нельзя. Чаще всего верный ответ — {EMPTY}.
4. Несколько одинаковых единиц («2 шт», «набор: 5 штук») — это не набор, НАБОР: нет.
   Правильный ответ почти всегда — нет.

ПРИМЕР
Вход: Порошок для стирки белья с кислородом
КАТЕГОРИЯ: средство_для_стирки
ФОРМА: порошок
НАЗНАЧЕНИЕ: {EMPTY}
НАБОР: нет"""


EXTRACT_ALL_PROMPT = f"""Ты выписываешь из карточки товара маркетплейса названия и числа.

Верни РОВНО пять строк, без пояснений до и после:
БРЕНД: <марка производителя>
ЛИНЕЙКА: <серия внутри бренда>
ОТТЕНОК: <оттенок, код оттенка или цвет>
ОБЪЁМ: <число> <ml|g|pcs>
КОЛИЧЕСТВО: <целое число>

ПРАВИЛА
1. Все значения берутся только из ВХОДА. Никогда не бери их из примеров ниже.
2. БРЕНД, ЛИНЕЙКА, ОТТЕНОК обязаны дословно встречаться во входе. Чего нет — {EMPTY},
   и это частый правильный ответ.
3. Кавычки в значение не включай: «Французский лимон» -> Французский лимон.
4. Бренд бывает в скобках в конце: «(Белита-М)» -> БРЕНД: Белита-М.
5. ОБЪЁМ — объём ОДНОЙ единицы, приведённый к ml или g: 1,3 л -> 1300 ml; 0.1 кг -> 100 g.
6. «2 шт по 300 мл» -> ОБЪЁМ: 300 ml, КОЛИЧЕСТВО: 2. Не 600.
7. Единицы нет («75,0») или число не про объём («26 стирок», «SPF 30», «20 VOL») -> ОБЪЁМ: {EMPTY}.
8. КОЛИЧЕСТВО больше 1 только при явном указании. Не указано -> 1.
9. Числа с единицами в БРЕНД, ЛИНЕЙКУ и ОТТЕНОК не тащить: для них есть ОБЪЁМ и КОЛИЧЕСТВО.
   ЛИНЕЙКА — короткое имя серии, а не всё название товара.

ПРИМЕР
Вход: Порошок для стирки белья с кислородом
БРЕНД: {EMPTY}
ЛИНЕЙКА: {EMPTY}
ОТТЕНОК: {EMPTY}
ОБЪЁМ: {EMPTY}
КОЛИЧЕСТВО: 1"""


# --------------------------------------------------------------------------
# Этапы
# --------------------------------------------------------------------------

NORMALIZE_KEYS = {"ЧИСТО": "clean", "ЧИСЛА": "numbers", "КАВЫЧКИ": "quoted", "СКОБКИ": "paren"}
CLASSIFY_KEYS = {"КАТЕГОРИЯ": "category", "ФОРМА": "form",
                 "НАЗНАЧЕНИЕ": "purpose", "НАБОР": "is_set"}
STRINGS_KEYS = {"БРЕНД": "brand", "ЛИНЕЙКА": "line", "ОТТЕНОК": "shade"}
NUMBERS_KEYS = {"ОБЪЁМ": "volume", "ОБЪЕМ": "volume", "КОЛИЧЕСТВО": "pack_count"}
EXTRACT_ALL_KEYS = {**STRINGS_KEYS, **NUMBERS_KEYS}


def normalize_stage(client, name: str, model: str, usage) -> tuple[dict, dict]:
    """Этап 1: размечает вход. Возвращает разметку и запись о вызове."""
    response = client.generate(NORMALIZE_PROMPT, name, model=model, num_predict=128)
    usage.add("normalize", response)
    parsed = parse_lines(response["raw"], NORMALIZE_KEYS)
    return parsed, {"stage": "normalize", "model": model, "raw": response["raw"],
                    "parsed": parsed, "latency_s": response["latency_s"]}


def build_context(name: str, marks: dict | None) -> str:
    """Собирает вход для последующих этапов: оригинал плюс разметка этапа 1.

    Оригинал идёт первым и всегда — этапы 2 и 3 не должны зависеть от того,
    насколько удачно сработала нормализация. Разметка добавляет подсказку,
    но не отбирает информацию.
    """
    if not marks:
        return name
    extras = []
    for label, key in (("числа", "numbers"), ("кавычки", "quoted"), ("скобки", "paren")):
        value = marks.get(key)
        if value:
            extras.append(f"{label}: {value}")
    clean = marks.get("clean")
    if clean and clean != name:
        extras.insert(0, f"без мусора: {clean}")
    if not extras:
        return name
    return f"{name}\n\nРазметка: " + "; ".join(extras)


def _record(stage: str, model: str, response: dict, parsed) -> dict:
    """Одна запись о вызове этапа для дампа прогона."""
    return {"stage": stage, "model": model, "raw": response["raw"], "parsed": parsed,
            "latency_s": response["latency_s"]}


def classify_stages(client, context: str, model: str, usage) -> tuple[dict, list[dict], list[str]]:
    """Этап 2 в полном виде: четыре микровызова, в каждом один словарь."""
    fields: dict = {}
    calls: list[dict] = []
    violations: list[str] = []

    response = client.generate(CATEGORY_PROMPT, context, model=model, num_predict=32)
    usage.add("category", response)
    value, strict = match_vocab(single_answer(response["raw"]), "category")
    fields["category"] = value
    if not strict:
        violations.append("category")
    calls.append(_record("category", model, response, value))

    response = client.generate(FORM_PROMPT, context, model=model, num_predict=32)
    usage.add("form", response)
    value, strict = match_vocab(single_answer(response["raw"]), "form")
    fields["form"] = value
    if not strict:
        violations.append("form")
    calls.append(_record("form", model, response, value))

    response = client.generate(PURPOSE_PROMPT, context, model=model, num_predict=64)
    usage.add("purpose", response)
    purposes, strict = parse_purpose(single_answer(response["raw"]))
    fields["purpose"] = purposes
    if not strict:
        violations.append("purpose")
    calls.append(_record("purpose", model, response, purposes))

    response = client.generate(IS_SET_PROMPT, context, model=model, num_predict=16)
    usage.add("is_set", response)
    flag, strict = parse_bool(single_answer(response["raw"]))
    fields["is_set"] = flag
    if not strict:
        violations.append("is_set")
    calls.append(_record("is_set", model, response, flag))

    return fields, calls, violations


def single_answer(raw: str) -> str | None:
    """Достаёт значение из ответа-однострочника.

    Модель иногда подписывает ответ («Ответ: Белита-М», «БРЕНД: -») и иногда
    добавляет вторую строку. Берётся первая непустая строка, метка перед
    двоеточием отбрасывается.
    """
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        head, sep, tail = line.partition(":")
        if sep and len(head.strip()) <= 20 and " " not in head.strip():
            line = tail
        return clean_value(line)
    return None


def extract_stages(client, context: str, model: str, usage) -> tuple[dict, list[dict], list[str]]:
    """Этап 3 в полном виде: по одному вызову на каждое из пяти полей.

    Один вопрос — один вызов. Промежуточный вариант, где три строковых поля
    просились одним ответом, 0.6B заполняла одним и тем же куском входа
    (бренд = линейка = оттенок), а в оттенок стабильно попадал объём. Разделение
    вызовов — и есть проверяемая гипотеза дня, а не способ обойти модель.

    Числа тоже врозь: `pack_count` — самая слабая графа 0.6B (45,5% в дне 43
    против 95,5% у 14B), и в монолите она тонет среди восьми других ключей.
    """
    fields: dict = {}
    calls: list[dict] = []
    violations: list[str] = []

    for field, prompt, budget in (("brand", BRAND_PROMPT, 32),
                                  ("line", LINE_PROMPT, 32),
                                  ("shade", SHADE_PROMPT, 32)):
        response = client.generate(prompt, context, model=model, num_predict=budget)
        usage.add(field, response)
        value = single_answer(response["raw"])
        fields[field] = value
        calls.append(_record(field, model, response, value))

    response = client.generate(VOLUME_PROMPT, context, model=model, num_predict=24)
    usage.add("volume", response)
    volume, strict_volume = parse_volume(single_answer(response["raw"]))
    fields["volume"] = volume
    if not strict_volume:
        violations.append("volume")
    calls.append(_record("volume", model, response, volume))

    response = client.generate(PACK_PROMPT, context, model=model, num_predict=16)
    usage.add("pack_count", response)
    pack, strict_pack = parse_pack(single_answer(response["raw"]))
    fields["pack_count"] = pack
    if not strict_pack:
        violations.append("pack_count")
    calls.append(_record("pack_count", model, response, pack))

    return fields, calls, violations


def assemble(fields: dict) -> dict:
    """Складывает девять полей в объект в порядке схемы.

    Никаких умолчаний: чего этапы не дали, остаётся `null` и считается
    ошибкой при оценке. Подставлять сюда, например, `pack_count = 1`
    значило бы чинить инференс кодом и мерить не то.
    """
    return {field: fields.get(field) for field in FIELD_ORDER}


# --------------------------------------------------------------------------
# Конвейеры
# --------------------------------------------------------------------------

def run_multi(client, name: str, models: dict, usage) -> dict:
    """Вариант B: полная декомпозиция, десять вызовов на товар — один вопрос на вызов."""
    marks, normalize_call = normalize_stage(client, name, models["normalize"], usage)
    context = build_context(name, marks)
    classified, classify_calls, classify_bad = classify_stages(
        client, context, models["classify"], usage)
    extracted, extract_calls, extract_bad = extract_stages(
        client, context, models["extract"], usage)

    fields = {**classified, **extracted}
    return {
        "obj": assemble(fields),
        "marks": marks,
        "context": context,
        "calls": [normalize_call, *classify_calls, *extract_calls],
        "format_violations": classify_bad + extract_bad,
    }


def run_multi_lite(client, name: str, models: dict, usage) -> dict:
    """Вариант B ужатый: нормализация, одна классификация, одно извлечение.

    Отвечает на вопрос, который иначе остался бы открытым: сколько дробления
    окупается. Полная декомпозиция платит за семь системных промптов вместо
    одного, и если три вызова дают то же качество — платить незачем.
    """
    marks, normalize_call = normalize_stage(client, name, models["normalize"], usage)
    context = build_context(name, marks)
    violations: list[str] = []

    response = client.generate(CLASSIFY_ALL_PROMPT, context, model=models["classify"],
                               num_predict=96)
    usage.add("classify_all", response)
    parsed = parse_lines(response["raw"], CLASSIFY_KEYS)
    category, strict_category = match_vocab(parsed.get("category"), "category")
    form, strict_form = match_vocab(parsed.get("form"), "form")
    purposes, strict_purpose = parse_purpose(parsed.get("purpose"))
    is_set, strict_set = parse_bool(parsed.get("is_set"))
    for ok, field in ((strict_category, "category"), (strict_form, "form"),
                      (strict_purpose, "purpose"), (strict_set, "is_set")):
        if not ok:
            violations.append(field)
    classified = {"category": category, "form": form, "purpose": purposes, "is_set": is_set}
    classify_call = _record("classify_all", models["classify"], response, classified)

    response = client.generate(EXTRACT_ALL_PROMPT, context, model=models["extract"],
                               num_predict=128)
    usage.add("extract_all", response)
    parsed = parse_lines(response["raw"], EXTRACT_ALL_KEYS)
    volume, strict_volume = parse_volume(parsed.get("volume"))
    pack, strict_pack = parse_pack(parsed.get("pack_count"))
    if not strict_volume:
        violations.append("volume")
    if not strict_pack:
        violations.append("pack_count")
    extracted = {
        "brand": parsed.get("brand"), "line": parsed.get("line"), "shade": parsed.get("shade"),
        "volume": volume, "pack_count": pack,
    }
    extract_call = _record("extract_all", models["extract"], response, extracted)

    return {
        "obj": assemble({**classified, **extracted}),
        "marks": marks,
        "context": context,
        "calls": [normalize_call, classify_call, extract_call],
        "format_violations": violations,
    }


def run_mono(client, name: str, models: dict, usage, temperature: float = 0.0,
             seed: int | None = None) -> dict:
    """Вариант A: монолит. Промпт дня 41 без единой правки.

    Правка промпта сделала бы сравнение бессмысленным: день мерил бы разницу
    промптов, а не разницу между одним запросом и тремя этапами.
    """
    response = client.generate(schema.build_system_prompt(), name, model=models["mono"],
                               num_predict=256, temperature=temperature, seed=seed)
    usage.add("mono", response)
    obj, clean = schema.parse_model_json(response["raw"])
    return {
        "obj": obj,
        "raw": response["raw"],
        "clean_json": clean,
        "calls": [_record("mono", models["mono"], response, obj)],
        "format_violations": [] if obj else FIELD_ORDER[:],
    }
