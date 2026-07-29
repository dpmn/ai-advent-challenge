#!/usr/bin/env python3
"""Считает метрики контроля качества по данным прогона и сравнивает три режима.

Режимы гоняются по одним и тем же ответам модели, снятым один раз:

  **A — no gate**       принимаем всё, что выдала модель (как день 41)
  **B — constraints**   только программные проверки, ноль доп. вызовов
  **C — full cascade**  constraints → scoring → redundancy → self-check

Главная метрика тут не «сколько отклонили», а **точность принятых против
точности всех**. Гейт, который режет 40% ответов, не улучшая качество
оставшихся, — просто потеря данных.

Порог уверенности не задаётся заранее: отчёт разворачивает его в кривую
accept-rate и точности, а выбранное значение отмечается отдельно.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import gates
import score as day41_score
from common import GPU_RUB_PER_HOUR, RESULTS_DIR, difficulty_level, read_jsonl, schema

SWEEP = [0.0, 0.50, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99, 0.995, 1.01]


def is_correct(expected: dict, obj: dict | None) -> bool:
    """Совпадают ли все девять полей с эталоном.

    Строгое сравнение по всему объекту, а не по полям: задача — решить,
    можно ли отдать ответ дальше без человека, а частично верная разметка
    для этого решения бесполезна.
    """
    if not isinstance(obj, dict):
        return False
    return all(day41_score.fields_equal(f, expected.get(f), obj.get(f)) for f in schema.FIELD_ORDER)


def base_latency(record: dict) -> float:
    """Время первого (обязательного для всех режимов) вызова."""
    return float((record.get("base") or {}).get("latency_s") or 0.0)


def extra_latency(record: dict, stage: str) -> float:
    """Время дополнительных вызовов, фактически понадобившихся на этой ступени."""
    spent = 0.0
    if stage in ("redundancy", "selfcheck"):
        spent += sum(float(s.get("latency_s") or 0.0) for s in record.get("samples") or [])
    if stage == "selfcheck" and record.get("selfcheck"):
        spent += float(record["selfcheck"].get("latency_s") or 0.0)
    return spent


def evaluate(records: list[dict], mode: str, threshold: float | None = None) -> dict:
    """Прогоняет выборку в одном из трёх режимов и собирает статистику."""
    stats = {
        "mode": mode,
        "total": len(records),
        "accepted": 0, "unsure": 0, "rejected": 0,
        "accepted_correct": 0, "false_accept": 0, "false_reject": 0,
        "overall_correct": 0,
        "extra_calls": 0,
        "latency_s": 0.0,
        "by_stage": Counter(),
        "by_level": defaultdict(lambda: {"n": 0, "accepted": 0, "accepted_correct": 0,
                                         "rejected": 0, "unsure": 0}),
        "rejected_reasons": Counter(),
        "fixed_by_vote": 0,
    }

    for record in records:
        expected = record["expected"]
        level = record.get("level") or difficulty_level(record.get("hard_flags"))
        raw = (record.get("base") or {}).get("raw") or ""
        base_obj, _ = schema.parse_model_json(raw)
        base_ok = is_correct(expected, base_obj)
        stats["overall_correct"] += int(base_ok)
        stats["latency_s"] += base_latency(record)

        if mode == "A":
            decision = gates.Decision(gates.STATUS_OK, base_obj, "none", [], 0)
        elif mode == "B":
            constraint = gates.constraint_check(record["name"], raw)
            decision = gates.Decision(
                gates.STATUS_OK if constraint.passed else gates.STATUS_FAIL,
                constraint.obj, "constraint", constraint.problems, 0,
            )
        else:
            decision = gates.decide(record, gates.Thresholds(min_field_conf=threshold))
            stats["latency_s"] += extra_latency(record, decision.stage)

        stats["extra_calls"] += decision.extra_calls
        stats["by_stage"][decision.stage] += 1
        bucket = stats["by_level"][level]
        bucket["n"] += 1

        decided_ok = is_correct(expected, decision.obj)
        if decision.status == gates.STATUS_OK:
            stats["accepted"] += 1
            bucket["accepted"] += 1
            stats["accepted_correct"] += int(decided_ok)
            bucket["accepted_correct"] += int(decided_ok)
            stats["false_accept"] += int(not decided_ok)
            if decided_ok and not base_ok:
                stats["fixed_by_vote"] += 1
        elif decision.status == gates.STATUS_UNSURE:
            stats["unsure"] += 1
            bucket["unsure"] += 1
        else:
            stats["rejected"] += 1
            bucket["rejected"] += 1
            stats["false_reject"] += int(base_ok)
            for reason in decision.reasons[:1]:
                stats["rejected_reasons"][reason.split(":")[0][:60]] += 1

    total = stats["total"] or 1
    accepted = stats["accepted"] or 1
    stats["accept_rate"] = stats["accepted"] / total
    stats["unsure_rate"] = stats["unsure"] / total
    stats["reject_rate"] = stats["rejected"] / total
    stats["accuracy_overall"] = stats["overall_correct"] / total
    stats["accuracy_accepted"] = stats["accepted_correct"] / accepted if stats["accepted"] else None
    stats["false_accept_rate"] = stats["false_accept"] / total
    stats["false_reject_rate"] = stats["false_reject"] / total
    stats["rub"] = stats["latency_s"] / 3600 * GPU_RUB_PER_HOUR
    stats["rub_per_accepted"] = stats["rub"] / accepted if stats["accepted"] else None
    stats["latency_per_item"] = stats["latency_s"] / total
    stats["by_stage"] = dict(stats["by_stage"])
    stats["by_level"] = {k: dict(v) for k, v in stats["by_level"].items()}
    stats["rejected_reasons"] = dict(stats["rejected_reasons"])
    return stats


def sweep(records: list[dict]) -> list[dict]:
    """Разворачивает порог уверенности в кривую: accept-rate и точность принятых."""
    return [evaluate(records, "C", t) | {"threshold": t} for t in SWEEP]


def pick_threshold(curve: list[dict]) -> dict:
    """Выбирает порог по чистому выходу: верно принятые минус ложно принятые.

    Наивное «минимум ложных пропусков» вырождается: гейт, который не
    принимает ничего, не пропускает и брака. Поэтому каждая принятая ошибка
    засчитывается со знаком минус против каждого принятого верного ответа —
    это и есть постановка «ошибка недопустима, но пустой выход бесполезен».
    При равенстве побеждает вариант с меньшим числом доп. вызовов.
    """
    return max(curve, key=lambda s: (s["accepted_correct"] - s["false_accept"], -s["extra_calls"]))


def pct(value: float | None) -> str:
    """Проценты для таблиц, с прочерком вместо пустоты."""
    return "—" if value is None else f"{value * 100:.1f}%"


def num(value: float | None, digits: int = 3) -> str:
    """Число для таблиц: прочерк, если считать было не из чего (ноль принятых)."""
    return "—" if value is None else f"{value:.{digits}f}"


def render(modes: dict[str, dict], curve: list[dict], chosen: dict, meta: dict) -> str:
    """Собирает markdown-отчёт."""
    a, b, c = modes["A"], modes["B"], modes["C"]
    lines = [
        "# День 42 — контроль качества инференса",
        "",
        f"Модель: `{meta.get('model', '?')}`, 4-bit NF4. "
        f"Примеров: **{a['total']}**. Порог уверенности: **{chosen['threshold']}**.",
        "",
        "## Три режима на одних и тех же ответах",
        "",
        "| | A — без гейта | B — только constraints | C — полный каскад |",
        "|---|---|---|---|",
        f"| принято | {pct(a['accept_rate'])} | {pct(b['accept_rate'])} | {pct(c['accept_rate'])} |",
        f"| на проверку человеку | {pct(a['unsure_rate'])} | {pct(b['unsure_rate'])} | {pct(c['unsure_rate'])} |",
        f"| отклонено | {pct(a['reject_rate'])} | {pct(b['reject_rate'])} | {pct(c['reject_rate'])} |",
        f"| **точность принятых** | **{pct(a['accuracy_accepted'])}** | "
        f"**{pct(b['accuracy_accepted'])}** | **{pct(c['accuracy_accepted'])}** |",
        f"| ложных пропусков (принят брак) | {a['false_accept']} | {b['false_accept']} | {c['false_accept']} |",
        f"| ложных отказов (отклонён верный) | {a['false_reject']} | {b['false_reject']} | {c['false_reject']} |",
        f"| доп. вызовов модели | {a['extra_calls']} | {b['extra_calls']} | {c['extra_calls']} |",
        f"| GPU-время, с | {a['latency_s']:.0f} | {b['latency_s']:.0f} | {c['latency_s']:.0f} |",
        f"| стоимость, ₽ | {a['rub']:.2f} | {b['rub']:.2f} | {c['rub']:.2f} |",
        f"| ₽ за принятый ответ | {num(a['rub_per_accepted'])} | "
        f"{num(b['rub_per_accepted'])} | {num(c['rub_per_accepted'])} |",
        "",
        f"Точность модели как есть (без всякого гейта): **{pct(a['accuracy_overall'])}**.",
        "",
        "## Где каскад останавливается (режим C)",
        "",
        "| ступень | решений |",
        "|---|---|",
    ]
    stage_titles = {"constraint": "1 — constraint (0 вызовов)",
                    "scoring": "2 — scoring (0 вызовов)",
                    "redundancy": "3 — redundancy (+сэмплы)",
                    "selfcheck": "4 — self-check (+1 вызов)"}
    for stage, title in stage_titles.items():
        if stage in c["by_stage"]:
            lines.append(f"| {title} | {c['by_stage'][stage]} |")

    if c["fixed_by_vote"]:
        lines += ["", f"Голосование по полям **исправило {c['fixed_by_vote']} ответов**: "
                      "жадный ответ был неверен, большинство сэмплов — верно."]

    lines += ["", "## Порог уверенности", "",
              "| порог | принято | точность принятых | ложных пропусков | доп. вызовов | ₽ |",
              "|---|---|---|---|---|---|"]
    for row in curve:
        mark = " ←" if row["threshold"] == chosen["threshold"] else ""
        lines.append(
            f"| {row['threshold']}{mark} | {pct(row['accept_rate'])} | "
            f"{pct(row['accuracy_accepted'])} | {row['false_accept']} | "
            f"{row['extra_calls']} | {row['rub']:.2f} |"
        )

    lines += ["", "## По уровням входов (режим C)", "",
              "| уровень | всего | принято | точность принятых | отклонено | человеку |",
              "|---|---|---|---|---|---|"]
    for level in ("корректные", "пограничные", "шумные"):
        bucket = c["by_level"].get(level)
        if not bucket:
            continue
        acc = bucket["accepted_correct"] / bucket["accepted"] if bucket["accepted"] else None
        lines.append(f"| {level} | {bucket['n']} | {bucket['accepted']} | {pct(acc)} | "
                     f"{bucket['rejected']} | {bucket['unsure']} |")

    if c["rejected_reasons"]:
        lines += ["", "## Причины отклонения (режим C)", "", "| причина | случаев |", "|---|---|"]
        for reason, count in sorted(c["rejected_reasons"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {reason} | {count} |")

    if meta.get("gpu_seconds"):
        lines += ["", "## Фактическая стоимость прогона", "",
                  f"Снято за один заход: {meta['calls']['total']} вызовов "
                  f"({meta['calls']['base']} base, {meta['calls']['samples']} сэмплов, "
                  f"{meta['calls']['selfcheck']} самопроверок), "
                  f"{meta['gpu_seconds']['total']:.0f} с GPU ≈ {meta.get('rub_total', 0):.2f} ₽. "
                  "Каскад пересобирается из этих данных офлайн с любым порогом."]

    return "\n".join(lines) + "\n"


def main() -> None:
    """Точка входа: считает три режима, кривую порога и пишет отчёт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=RESULTS_DIR / "runs.jsonl")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "report.md")
    parser.add_argument("--threshold", type=float,
                        help="зафиксировать порог вместо выбора по кривой")
    args = parser.parse_args()

    records = read_jsonl(args.runs)
    meta_path = args.runs.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    curve = sweep(records)
    chosen = (next((row for row in curve if row["threshold"] == args.threshold), None)
              if args.threshold is not None else None) or pick_threshold(curve)

    modes = {
        "A": evaluate(records, "A"),
        "B": evaluate(records, "B"),
        "C": chosen,
    }
    report = render(modes, curve, chosen, meta)
    print(report)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    args.out.with_suffix(".json").write_text(
        json.dumps({"modes": modes, "curve": curve, "meta": meta}, ensure_ascii=False, indent=2,
                   default=str),
        encoding="utf-8",
    )
    print(f"отчёт: {args.out}")


if __name__ == "__main__":
    main()
