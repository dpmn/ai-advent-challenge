#!/usr/bin/env python3
"""Якорь «всё на сильной»: прогоняет всю выборку через `Qwen3-14B`.

Снимается **за кадром**, до живого прогона routing-а, и нужен по двум причинам.

Во-первых, это верхняя граница качества и цены: без неё непонятно, много ли
routing недобрал по точности и много ли сэкономил по деньгам.

Во-вторых — и это важнее — здесь появляются ответы сильной модели **по всем
полям всех товаров**, включая те, которые routing оставил слабой. Только с
ними можно офлайн пересобрать любую политику маршрутизации: случайную
эскалацию той же доли, статичный раздел полей, другой порог уверенности.
Иначе за каждым сравнением пришлось бы снова арендовать GPU.

Стоит это ~30 секунд GPU на 22 товара.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from big import BigClient, BigModelUnavailable
from common import BIG_MODEL, BIG_URL, PICKS_PATH, RESULTS_DIR, read_jsonl, rub, schema, write_jsonl


def main() -> None:
    """Точка входа: полный проход сильной модели по выборке."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--picks", type=Path, default=PICKS_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "anchor_big.jsonl")
    parser.add_argument("--big-url", default=BIG_URL)
    parser.add_argument("--batch-size", type=int, default=8,
                        help="сколько товаров отдавать одним батчем")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    picks = read_jsonl(args.picks)
    if args.limit:
        picks = picks[: args.limit]

    client = BigClient(args.big_url)
    try:
        info = client.health()
    except BigModelUnavailable as error:
        raise SystemExit(f"сильная модель недоступна: {error}")
    print(f"сильная: {info['model']} ({info['quantization']}, immers), товаров: {len(picks)}")

    records: list[dict] = []
    for start in range(0, len(picks), args.batch_size):
        chunk = picks[start : start + args.batch_size]
        items = client.generate([p["name"] for p in chunk])
        for pick, item in zip(chunk, items):
            obj, clean = schema.parse_model_json(item["raw"])
            records.append({
                "id": pick["id"],
                "name": pick["name"],
                "level": pick["level"],
                "expected": pick["expected"],
                "big": {"model": BIG_MODEL, **item, "obj": obj, "clean_json": clean},
            })
        print(f"  {min(start + len(chunk), len(picks))}/{len(picks)}", flush=True)

    write_jsonl(args.out, records)
    parsed = sum(1 for r in records if isinstance(r["big"]["obj"], dict))
    print(f"\nразобрано JSON: {parsed}/{len(records)}")
    print(f"GPU: {client.gpu_seconds:.1f} c ≈ {rub(client.gpu_seconds):.2f} ₽")

    args.out.with_suffix(".meta.json").write_text(json.dumps({
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": info["model"],
        "quantization": info["quantization"],
        "items": len(records),
        "parsed": parsed,
        "gpu_seconds": round(client.gpu_seconds, 2),
        "rub": round(rub(client.gpu_seconds), 3),
        "batch_size": args.batch_size,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"якорь: {args.out}")


if __name__ == "__main__":
    main()
