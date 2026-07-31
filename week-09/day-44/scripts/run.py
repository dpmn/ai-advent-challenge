#!/usr/bin/env python3
"""Прогон одной руки эксперимента по всем 100 примерам eval дня 41.

Руки (все на одной выборке, одним скорером, с одним эталоном):

    mono        вариант A — монолит, один запрос, промпт дня 41              1 вызов
    multi       вариант B — полная декомпозиция, один словарь на вызов       7 вызовов
    multi-lite  вариант B ужатый — нормализация + классификация + извлечение 3 вызова
    mono-x4     контроль — монолит четырьмя сэмплами и majority по полям     4 вызова

`mono-x4` нужен, чтобы отделить эффект декомпозиции от эффекта «просто больше
вызовов». Без него прирост многоэтапной руки нельзя приписать именно этапам —
день 42 уже показал, чем кончается вера в цифру без контроля.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
import time

import common
import stages
from common import RESULTS_DIR, SMALL_MODEL, Usage, load_eval, load_train, write_jsonl
from llm import OllamaClient

ARMS = ("mono", "multi", "multi-lite", "mono-x4")
SAMPLES = 4
SAMPLE_TEMPERATURE = 0.7


def majority(objs: list[dict | None]) -> dict:
    """Собирает объект из самых частых значений каждого поля.

    Значения сравниваются по сериализованному виду: `volume` — словарь,
    `purpose` — список, и оба не хешируются. Ничьи разрешаются в пользу
    первого сэмпла — он снят с той же температурой, что и остальные,
    и выделять его нечем.
    """
    result: dict = {}
    for field in common.FIELD_ORDER:
        counter: Counter = Counter()
        first_seen: dict[str, object] = {}
        for obj in objs:
            if not obj:
                continue
            value = obj.get(field)
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            counter[key] += 1
            first_seen.setdefault(key, value)
        result[field] = first_seen[counter.most_common(1)[0][0]] if counter else None
    return result


def run_arm(arm: str, client: OllamaClient, records: list[dict], models: dict,
            verbose: bool) -> list[dict]:
    """Гоняет одну руку по выборке и возвращает записи прогона."""
    out: list[dict] = []
    for index, record in enumerate(records, 1):
        usage = Usage()
        started = time.time()

        if arm == "mono":
            result = stages.run_mono(client, record["name"], models, usage)
        elif arm == "multi":
            result = stages.run_multi(client, record["name"], models, usage)
        elif arm == "multi-lite":
            result = stages.run_multi_lite(client, record["name"], models, usage)
        else:
            samples = [stages.run_mono(client, record["name"], models, usage,
                                       temperature=SAMPLE_TEMPERATURE, seed=seed)
                       for seed in range(SAMPLES)]
            result = {
                "obj": majority([s["obj"] for s in samples]),
                "calls": [call for s in samples for call in s["calls"]],
                "format_violations": [f for s in samples for f in s["format_violations"]],
            }

        out.append({
            "id": record["id"],
            "name": record["name"],
            "level": record["level"],
            "hard_flags": record["hard_flags"],
            "expected": record["expected"],
            "obj": result["obj"],
            "marks": result.get("marks"),
            "context": result.get("context"),
            "calls": result["calls"],
            "format_violations": result["format_violations"],
            "usage": usage.as_dict(),
            "wall_s": round(time.time() - started, 3),
        })

        if verbose:
            correct = sum(1 for f in common.FIELD_ORDER
                          if common.score.fields_equal(f, record["expected"].get(f),
                                                       (result["obj"] or {}).get(f)))
            print(f"  [{index:>3}/{len(records)}] {record['id']}  "
                  f"{correct}/9  {usage.calls} выз.  {usage.seconds:.1f} с  "
                  f"{record['name'][:48]}", flush=True)
    return out


def main() -> None:
    """CLI: гоняет выбранные руки и складывает результаты в results/."""
    parser = argparse.ArgumentParser(description="Прогон рук эксперимента дня 44")
    parser.add_argument("--arm", action="append", choices=[*ARMS, "all"],
                        help="какую руку гонять (можно несколько раз); по умолчанию all")
    parser.add_argument("--limit", type=int, help="взять только N примеров")
    parser.add_argument("--offset", type=int, default=0,
                        help="пропустить первые N примеров: контроль промптов на train, "
                             "которых отладка не видела")
    parser.add_argument("--split", choices=("eval", "train"), default="eval",
                        help="на чём гонять; промпты этапов отлаживаются на train, "
                             "чтобы не подгонять день под метрику, которой он меряется")
    parser.add_argument("--model", default=SMALL_MODEL, help="модель для всех этапов")
    parser.add_argument("--model-classify", help="отдельная модель на этап классификации")
    parser.add_argument("--model-extract", help="отдельная модель на этап извлечения")
    parser.add_argument("--suffix", default="", help="суффикс к именам файлов результатов")
    parser.add_argument("--quiet", action="store_true", help="без построчного вывода")
    args = parser.parse_args()

    arms = ARMS if not args.arm or "all" in args.arm else tuple(dict.fromkeys(args.arm))
    models = {
        "mono": args.model,
        "normalize": args.model,
        "classify": args.model_classify or args.model,
        "extract": args.model_extract or args.model,
    }

    client = OllamaClient()
    try:
        quantization = client.health(sorted(set(models.values())))
    except Exception as error:  # сеть или отсутствующая модель — дальше идти незачем
        print(f"Ollama недоступна: {error}", file=sys.stderr)
        raise SystemExit(1)

    records = load_eval() if args.split == "eval" else load_train()
    records = records[args.offset:]
    if args.limit:
        records = records[:args.limit]
    print(f"Выборка: {args.split}, {len(records)} примеров · модели: "
          + ", ".join(f"{name} ({quant})" for name, quant in quantization.items()))

    for arm in arms:
        print(f"\n=== рука {arm} ===", flush=True)
        started = time.time()
        results = run_arm(arm, client, records, models, verbose=not args.quiet)
        elapsed = time.time() - started

        name = f"{arm}{args.suffix}"
        write_jsonl(RESULTS_DIR / f"{name}.jsonl", results)
        meta = {
            "arm": arm,
            "split": args.split,
            "models": models,
            "quantization": quantization,
            "thinking": False,
            "temperature": SAMPLE_TEMPERATURE if arm == "mono-x4" else 0.0,
            "items": len(results),
            "calls": sum(r["usage"]["calls"] for r in results),
            "prompt_tokens": sum(r["usage"]["prompt_tokens"] for r in results),
            "gen_tokens": sum(r["usage"]["gen_tokens"] for r in results),
            "model_seconds": round(sum(r["usage"]["seconds"] for r in results), 2),
            "wall_seconds": round(elapsed, 2),
        }
        (RESULTS_DIR / f"{name}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{arm}: {meta['calls']} вызовов · {meta['gen_tokens']} ген-токенов · "
              f"{meta['wall_seconds']} с → results/{name}.jsonl")


if __name__ == "__main__":
    main()
