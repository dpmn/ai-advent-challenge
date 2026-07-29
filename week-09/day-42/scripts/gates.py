#!/usr/bin/env python3
"""Четыре механизма оценки уверенности и сам каскад принятия решения.

Ступени выстроены по возрастанию цены, а не по «интересности»:

1. **Constraint** — программная проверка формата, словарей и логических
   инвариантов. Ноль обращений к модели, микросекунды. Жёсткое вето.
2. **Scoring** — уверенность из logprobs того же самого вызова: общая по
   ответу и отдельно по каждому из девяти полей. Тоже ноль доп. вызовов.
3. **Redundancy** — несколько сэмплов на один вход, голосование **по полям**.
   Дорого: каждый сэмпл — полноценная генерация.
4. **Self-check** — модель смотрит на свой ответ и спорные поля и ищет ошибку.

Формат ответа модели ступени не меняют: промпт остаётся тем же, что зашит
в обучающие примеры дня 41, иначе baseline станет несопоставим. Статус
`OK / UNSURE / FAIL` — выход каскада, а не поле в JSON модели.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field as dataclass_field

from common import schema

STATUS_OK = "OK"
STATUS_UNSURE = "UNSURE"
STATUS_FAIL = "FAIL"

_DECODER = json.JSONDecoder()


# --------------------------------------------------------------------------
# Ступень 1. Constraint
# --------------------------------------------------------------------------

@dataclass
class ConstraintResult:
    """Результат программной проверки ответа модели."""

    obj: dict | None
    clean_json: bool
    problems: list[str]

    @property
    def passed(self) -> bool:
        """Ответ разобран и не нарушает ни одного инварианта."""
        return self.obj is not None and not self.problems


def constraint_check(name: str, raw: str) -> ConstraintResult:
    """Проверяет сырой ответ модели: разбор JSON, схема, словари, инварианты.

    `check_invariants` дня 41 уже покрывает состав ключей, закрытые словари,
    форму `volume`, логику `pack_count` и главное — что `brand`/`line`/`shade`
    действительно встречаются во входной строке. Отдельная ценность в том,
    что всё это стоит ноль обращений к модели.
    """
    obj, clean = schema.parse_model_json(raw or "")
    if obj is None:
        return ConstraintResult(None, False, ["ответ не разобран как JSON"])
    if not isinstance(obj, dict):
        return ConstraintResult(None, clean, [f"ответ не объект: {type(obj).__name__}"])
    return ConstraintResult(obj, clean, schema.check_invariants(name, obj))


# --------------------------------------------------------------------------
# Ступень 2. Scoring по logprobs
# --------------------------------------------------------------------------

def json_span(raw: str) -> tuple[int, int] | None:
    """Границы JSON-объекта внутри сырого ответа.

    Базовая модель обрамляет ответ рассуждениями, и усреднять уверенность по
    всему тексту бессмысленно: болтовня разбавляет оценку тех токенов, от
    которых зависит результат. Считаем только по объекту.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    return start, end + 1


def field_spans(raw: str) -> dict[str, tuple[int, int]]:
    """Границы значения каждого поля внутри сырого ответа.

    Нужны, чтобы посчитать уверенность отдельно по полю: средняя по ответу
    прячет ровно тот случай, ради которого всё затевается — восемь полей
    уверенных и одно угаданное.
    """
    span = json_span(raw)
    if span is None:
        return {}
    start, end = span
    body = raw[start:end]

    spans: dict[str, tuple[int, int]] = {}
    for name in schema.FIELD_ORDER:
        match = re.search(r'"%s"\s*:\s*' % re.escape(name), body)
        if match is None:
            continue
        try:
            _, value_end = _DECODER.raw_decode(body, match.end())
        except ValueError:
            continue
        spans[name] = (start + match.end(), start + value_end)
    return spans


def _tokens_in_span(tokens: list[dict], span: tuple[int, int]) -> list[dict]:
    """Токены, пересекающиеся с указанным диапазоном символов."""
    lo, hi = span
    return [t for t in tokens if t["end"] > lo and t["start"] < hi]


def _confidence(tokens: list[dict]) -> dict | None:
    """Сводит logprob-ы набора токенов к паре чисел.

    `mean` — среднегеометрическая вероятность токена, устойчивая оценка
    «в целом уверен». `min` — вероятность самого сомнительного токена: одна
    угаданная цифра в объёме портит ответ целиком, и среднее её не покажет.
    """
    if not tokens:
        return None
    logprobs = [t["logprob"] for t in tokens]
    return {
        "mean": round(math.exp(sum(logprobs) / len(logprobs)), 4),
        "min": round(math.exp(min(logprobs)), 4),
        "tokens": len(logprobs),
    }


def score_confidence(raw: str, tokens: list[dict] | None) -> dict:
    """Считает уверенность по ответу целиком и по каждому полю.

    Возвращает `{"answer": {...}|None, "fields": {поле: {...}}}`. Если
    logprob-ов нет (провайдер их не отдал), возвращает пустую оценку —
    каскад в этом случае трактует уверенность как неизвестную и не
    пропускает ответ мимо избыточности.
    """
    if not tokens:
        return {"answer": None, "fields": {}}

    span = json_span(raw)
    answer_tokens = _tokens_in_span(tokens, span) if span else tokens
    fields = {}
    for name, field_span in field_spans(raw).items():
        conf = _confidence(_tokens_in_span(tokens, field_span))
        if conf is not None:
            fields[name] = conf
    return {"answer": _confidence(answer_tokens), "fields": fields}


def weakest_field(confidence: dict) -> tuple[str | None, float | None]:
    """Поле с наименьшей минимальной вероятностью токена и её значение."""
    fields = confidence.get("fields") or {}
    if not fields:
        return None, None
    name = min(fields, key=lambda f: fields[f]["min"])
    return name, fields[name]["min"]


# --------------------------------------------------------------------------
# Ступень 3. Redundancy
# --------------------------------------------------------------------------

def _canonical(value) -> str:
    """Сравнимое представление значения поля (порядок ключей и списков не важен)."""
    if isinstance(value, list):
        return json.dumps(sorted(json.dumps(v, ensure_ascii=False, sort_keys=True) for v in value),
                          ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass
class VoteResult:
    """Итог голосования нескольких сэмплов по одному входу."""

    merged: dict
    agreement: dict[str, float]
    disputed: list[str] = dataclass_field(default_factory=list)
    voters: int = 0
    changed: list[str] = dataclass_field(default_factory=list)


def vote(primary: dict, samples: list[dict | None]) -> VoteResult:
    """Голосует по каждому полю отдельно и собирает согласованный объект.

    Голосование по целому JSON почти всегда даёт «все три разные» и не несёт
    информации. По полям видно то, что нужно: `category` совпала три раза из
    трёх, а `volume` разошёлся — значит сомнителен именно объём.
    """
    voters = [obj for obj in [primary, *samples] if isinstance(obj, dict)]
    merged: dict = {}
    agreement: dict[str, float] = {}
    disputed: list[str] = []
    changed: list[str] = []

    for name in schema.FIELD_ORDER:
        present = [obj for obj in voters if name in obj]
        if not present:
            merged[name] = primary.get(name)
            agreement[name] = 0.0
            disputed.append(name)
            continue

        counts = Counter(_canonical(obj[name]) for obj in present)
        winner, hits = counts.most_common(1)[0]
        merged[name] = next(obj[name] for obj in present if _canonical(obj[name]) == winner)
        agreement[name] = round(hits / len(voters), 3)
        if agreement[name] < 1.0:
            disputed.append(name)
        if name in primary and _canonical(primary[name]) != winner:
            changed.append(name)

    return VoteResult(merged, agreement, disputed, len(voters), changed)


# --------------------------------------------------------------------------
# Ступень 4. Self-check
# --------------------------------------------------------------------------

SELFCHECK_SYSTEM = (
    "Ты проверяешь чужую разметку товара. На вход даётся название карточки, "
    "предложенный JSON и список полей, в которых есть сомнения.\n"
    "Твоя задача — не переписать разметку заново, а найти ошибку.\n\n"
    "Ответь ровно одним JSON-объектом:\n"
    '{"verdict":"confirm"|"reject","bad_fields":[...],"reason":"кратко"}\n\n'
    'verdict="confirm" — разметка верна, менять нечего.\n'
    'verdict="reject" — хотя бы одно поле заполнено неверно; перечисли такие поля '
    "в bad_fields.\n"
    "Поле неверно, если значение противоречит названию, выдумано (его нет в названии) "
    "или единица измерения переведена неправильно.\n"
    "Никакого текста вне JSON."
)


def build_selfcheck_prompt(name: str, obj: dict, disputed: list[str]) -> str:
    """Собирает user-часть запроса самопроверки."""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    lines = [f"Название: {name}", f"Разметка: {payload}"]
    if disputed:
        lines.append("Сомнительные поля: " + ", ".join(disputed))
    return "\n".join(lines)


def parse_selfcheck(raw: str) -> dict:
    """Разбирает ответ самопроверки.

    Нераспарсенный или невнятный вердикт трактуется как `unknown`: молча
    засчитывать его за подтверждение — значит превращать ступень контроля
    в источник ложных пропусков.
    """
    obj, _ = schema.parse_model_json(raw or "")
    if not isinstance(obj, dict):
        return {"verdict": "unknown", "bad_fields": [], "reason": "ответ не разобран"}
    verdict = obj.get("verdict")
    if verdict not in ("confirm", "reject"):
        verdict = "unknown"
    bad = obj.get("bad_fields")
    return {
        "verdict": verdict,
        "bad_fields": [f for f in bad if isinstance(f, str)] if isinstance(bad, list) else [],
        "reason": str(obj.get("reason", ""))[:200],
    }


# --------------------------------------------------------------------------
# Каскад
# --------------------------------------------------------------------------

@dataclass
class Thresholds:
    """Пороги принятия. Калибруются по факту прогона, а не назначаются заранее."""

    # Основная ручка — уверенность самого слабого поля. Порог по ответу
    # целиком по умолчанию выключен: две ручки сразу невозможно честно
    # развернуть в одну кривую «accept-rate от порога».
    min_field_conf: float = 0.90
    min_answer_conf: float = 0.0
    min_agreement: float = 1.0


@dataclass
class Decision:
    """Итоговое решение по одному ответу и как оно получено."""

    status: str
    obj: dict | None
    stage: str
    reasons: list[str]
    extra_calls: int
    confidence: float | None = None
    disputed: list[str] = dataclass_field(default_factory=list)

    def as_dict(self) -> dict:
        """Плоское представление для JSONL."""
        return {
            "status": self.status,
            "obj": self.obj,
            "stage": self.stage,
            "reasons": self.reasons,
            "extra_calls": self.extra_calls,
            "confidence": self.confidence,
            "disputed": self.disputed,
        }


def decide(record: dict, thresholds: Thresholds, use_redundancy: bool = True,
           use_selfcheck: bool = True) -> Decision:
    """Прогоняет один ответ через каскад и возвращает решение.

    Работает офлайн, по уже снятым данным прогона: ступени 3 и 4 берут готовые
    сэмплы и готовую самопроверку из записи. Это позволяет пересобирать каскад
    с любым порогом без новой аренды GPU — цена ступеней при этом считается
    честно, по фактическим замерам latency каждого вызова.
    """
    name = record["name"]
    base = record.get("base") or {}
    raw = base.get("raw") or ""

    # --- ступень 1: constraint, ноль вызовов
    constraint = constraint_check(name, raw)
    if not constraint.passed:
        return Decision(STATUS_FAIL, constraint.obj, "constraint", constraint.problems, 0)

    obj = constraint.obj
    confidence = score_confidence(raw, base.get("tokens") or [])
    answer_conf = (confidence.get("answer") or {}).get("mean")
    field_name, field_conf = weakest_field(confidence)

    # --- ступень 2: scoring, ноль вызовов
    confident = (
        answer_conf is not None
        and answer_conf >= thresholds.min_answer_conf
        and field_conf is not None
        and field_conf >= thresholds.min_field_conf
    )
    if confident:
        return Decision(STATUS_OK, obj, "scoring", [], 0, answer_conf)

    if not use_redundancy:
        reason = ("logprob-ов нет" if answer_conf is None
                  else f"низкая уверенность: {field_name}={field_conf}")
        return Decision(STATUS_UNSURE, obj, "scoring", [reason], 0, answer_conf)

    # --- ступень 3: redundancy, +N вызовов
    samples = record.get("samples") or []
    if not samples:
        # Голосовать не с кем. Считать это единогласием — значит принять
        # сомнительный ответ на основании того, что проверка не проводилась.
        return Decision(STATUS_UNSURE, obj, "redundancy", ["сэмплы не сняты"], 0, answer_conf)
    sample_objs = [schema.parse_model_json(s.get("raw") or "")[0] for s in samples]
    result = vote(obj, sample_objs)
    extra = len(samples)

    merged_problems = schema.check_invariants(name, result.merged)
    if merged_problems:
        return Decision(STATUS_FAIL, result.merged, "redundancy",
                        ["большинство нарушает инварианты"] + merged_problems, extra, answer_conf,
                        result.disputed)

    weak = [f for f in schema.FIELD_ORDER if result.agreement.get(f, 0.0) < thresholds.min_agreement]
    if not weak:
        return Decision(STATUS_OK, result.merged, "redundancy",
                        [f"сэмплы единогласны ({result.voters})"], extra, answer_conf)

    if not use_selfcheck:
        return Decision(STATUS_UNSURE, result.merged, "redundancy",
                        ["разошлись поля: " + ", ".join(weak)], extra, answer_conf, weak)

    # --- ступень 4: self-check, +1 вызов
    check = record.get("selfcheck") or {}
    verdict = parse_selfcheck(check.get("raw") or "") if check else {"verdict": "unknown",
                                                                    "bad_fields": [],
                                                                    "reason": "самопроверка не снята"}
    extra += 1 if check else 0
    reasons = ["разошлись поля: " + ", ".join(weak),
               f"самопроверка: {verdict['verdict']}" + (f" ({verdict['reason']})" if verdict["reason"] else "")]

    if verdict["verdict"] == "reject":
        return Decision(STATUS_FAIL, result.merged, "selfcheck", reasons, extra, answer_conf, weak)
    return Decision(STATUS_UNSURE, result.merged, "selfcheck", reasons, extra, answer_conf, weak)
