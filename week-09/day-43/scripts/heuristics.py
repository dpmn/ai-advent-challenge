#!/usr/bin/env python3
"""Три эвристики, решающие, какие поля отправить на сильную модель.

Все три **бесплатны**: работают по единственному ответу слабой модели и его
logprob-ам, ноль дополнительных вызовов. Это прямое следствие дня 42, где
всё, что стоило вызовов (сэмплы, самопроверка), себя не оправдало: за те же
вызовы дешевле сразу спросить сильную модель.

| # | эвристика | что ловит | гранулярность |
|---|---|---|---|
| 1 | constraint  | битый JSON, чужое значение в закрытом словаре, инварианты дня 41 | поле, а при неразобранном JSON — все девять |
| 2 | confidence  | уверенность поля из logprob-ов ниже порога | поле |
| 3 | length      | утечка размышлений или обрыв генерации | все девять |

Порядок применения — по убыванию жёсткости: 1, 3, 2. Поле может быть
помечено несколькими эвристиками; в лог идут все причины, а в статистике
день считает вклад каждой отдельно.

Гранулярность здесь главное. День 42 закончился выводом, что решение
«принять/отклонить **ответ целиком**» вырождается: при пополевой точности 85%
и девяти полях почти каждый ответ содержит хотя бы одну ошибку. Поэтому
эскалируется поле, а не ответ.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from common import FIELD_ORDER, schema  # первым: он подкладывает дни 41 и 42 в sys.path

import gates  # noqa: E402  (модуль дня 42, импорт возможен только после common)

H_CONSTRAINT = "#1 constraint"
H_CONFIDENCE = "#2 confidence"
H_LENGTH = "#3 length"

# Порог откалиброван сухим прогоном (`route.py --no-escalate`), а не назначен
# заранее. На 22 товарах он разворачивается так: при 0,75 эскалируется 38%
# полей и **все 22 товара** — то есть сильную модель зовут для каждого, и
# графа «осталось на слабой» пустеет. При 0,5 эскалируется 19% полей, трое
# товаров проходят целиком локально, а точность выбора (доля действительно
# ошибочных среди эскалированных полей) держится 94,7% против базовых 44,4%.
DEFAULT_THRESHOLD = 0.5
# `min` — вероятность самого сомнительного токена поля. Среднее прячет ровно
# тот случай, ради которого всё затевается: значение верное на вид, но одна
# цифра в объёме угадана.
DEFAULT_METRIC = "min"
# Норма ответа — 162 символа (медиана дня 42). Всё, что длиннее вчетверо,
# это либо утечка размышлений, либо модель пересказывает промпт.
DEFAULT_MAX_CHARS = 650


@dataclass
class Suspicion:
    """Что эвристики думают об одном ответе слабой модели."""

    obj: dict | None
    clean_json: bool
    confidence: dict
    fields: dict[str, list[tuple[str, str]]] = dataclass_field(default_factory=dict)
    triggered: list[tuple[str, str]] = dataclass_field(default_factory=list)

    @property
    def suspect_fields(self) -> list[str]:
        """Поля к эскалации, в порядке схемы."""
        return [f for f in FIELD_ORDER if f in self.fields]

    @property
    def escalate(self) -> bool:
        """Нужен ли вообще вызов сильной модели."""
        return bool(self.fields)

    def by_heuristic(self) -> dict[str, list[str]]:
        """Раскладка «эвристика → поля», для статистики и лога."""
        out: dict[str, list[str]] = {}
        for name in self.suspect_fields:
            for heuristic, _ in self.fields[name]:
                out.setdefault(heuristic, [])
                if name not in out[heuristic]:
                    out[heuristic].append(name)
        return out

    def mark(self, fields: list[str], heuristic: str, reason: str) -> None:
        """Помечает поля как подозрительные с указанием причины."""
        for name in fields:
            self.fields.setdefault(name, []).append((heuristic, reason))
        self.triggered.append((heuristic, reason))


def problem_fields(problem: str) -> list[str]:
    """Сопоставляет нарушение инварианта дня 41 с полями, которых оно касается.

    Формулировки `check_invariants` начинаются с имени поля («form вне
    словаря», «volume.unit вне словаря»), кроме двух структурных случаев.
    Неузнанное нарушение трактуется как порча всего объекта: молча
    проигнорировать его — значит оставить поле слабой модели именно там,
    где программная проверка уже сказала «здесь брак».
    """
    if problem.startswith("нет ключей:"):
        listed = [f.strip() for f in problem.split(":", 1)[1].split(",")]
        return [f for f in listed if f in FIELD_ORDER] or list(FIELD_ORDER)
    if problem.startswith("лишние ключи:"):
        return list(FIELD_ORDER)
    head = problem.split()[0].split(".")[0].strip(":")
    return [head] if head in FIELD_ORDER else list(FIELD_ORDER)


def field_confidence(confidence: dict, name: str, metric: str) -> float | None:
    """Уверенность одного поля по выбранной метрике (`min` или `mean`)."""
    entry = (confidence.get("fields") or {}).get(name)
    return None if entry is None else entry.get(metric)


def analyze(name: str, raw: str, tokens: list[dict] | None,
            threshold: float = DEFAULT_THRESHOLD, metric: str = DEFAULT_METRIC,
            max_chars: int = DEFAULT_MAX_CHARS, done_reason: str | None = None) -> Suspicion:
    """Прогоняет ответ слабой модели через три эвристики.

    Работает и на живом прогоне, и офлайн по записанным данным — поэтому
    порог можно пересобрать в отчёте, не трогая ни одну модель.
    """
    constraint = gates.constraint_check(name, raw or "")
    _, clean = schema.parse_model_json(raw or "")
    confidence = gates.score_confidence(raw or "", tokens or [])
    suspicion = Suspicion(obj=constraint.obj, clean_json=clean, confidence=confidence)

    # --- #1 constraint: жёсткие нарушения, ноль вызовов
    if constraint.obj is None:
        suspicion.mark(list(FIELD_ORDER), H_CONSTRAINT, "ответ не разобран как JSON")
    else:
        for problem in constraint.problems:
            suspicion.mark(problem_fields(problem), H_CONSTRAINT, problem)

    # --- #3 length: утечка размышлений или обрыв
    if done_reason == "length":
        suspicion.mark(list(FIELD_ORDER), H_LENGTH, "генерация обрезана по лимиту токенов")
    elif len(raw or "") > max_chars:
        suspicion.mark(list(FIELD_ORDER), H_LENGTH,
                       f"ответ {len(raw or '')} символов (норма ~162)")

    # --- #2 confidence: пополевая уверенность из logprob-ов
    if constraint.obj is not None:
        for field_name in FIELD_ORDER:
            if field_name not in constraint.obj:
                continue
            value = field_confidence(confidence, field_name, metric)
            if value is None:
                suspicion.mark([field_name], H_CONFIDENCE, "уверенность не посчитана")
            elif value < threshold:
                suspicion.mark([field_name], H_CONFIDENCE,
                               f"{field_name} {value:.2f} < {threshold:g}")

    return suspicion


def merge(small_obj: dict | None, big_obj: dict | None, escalated: list[str]) -> dict | None:
    """Собирает гибридный ответ: эскалированные поля от сильной модели, остальные от слабой.

    Если слабая модель не выдала объект вовсе, гибрид — это целиком ответ
    сильной. Если сильная не ответила или не дала поле, остаётся значение
    слабой: подставлять `null` значило бы приписать routing-у порчу данных,
    которой не было.
    """
    if not isinstance(small_obj, dict):
        return big_obj if isinstance(big_obj, dict) else None
    merged = dict(small_obj)
    if isinstance(big_obj, dict):
        for name in escalated:
            if name in big_obj:
                merged[name] = big_obj[name]
    return merged
