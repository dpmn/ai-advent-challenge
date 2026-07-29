#!/usr/bin/env python3
"""Запасной способ снять baseline — прямо на арендованной машине, без сервера.

Основной путь (`baseline.py` через vLLM по OpenAI-совместимому API) требует,
чтобы vLLM подружился с 4-битным квантованием. Если он закапризничает, а
машина тарифицируется поминутно, разбираться некогда: этот скрипт грузит
модель через transformers + bitsandbytes и делает то же самое локально.

Запускать НА ВМ. Формат выхода совпадает с `baseline.py`, так что `score.py`
считает метрики одинаково для обоих путей.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import schema


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL-файл."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    """Точка входа: грузит модель в 4 битах и прогоняет отобранные примеры."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen3-14B")
    parser.add_argument("--picks", type=Path, required=True,
                        help="JSONL с отобранной десяткой (id/name/expected/hard_flags)")
    parser.add_argument("--out", type=Path, default=Path("responses.jsonl"))
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    # То же 4-битное NF4, в котором пойдёт QLoRA-тюн: baseline и «после»
    # должны меряться в одном квантовании, иначе прирост не отделить от
    # разницы точности.
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    print(f"гружу {args.model} в 4-bit NF4...")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=quant_config, device_map="auto",
    )
    model.eval()

    system_prompt = schema.build_system_prompt()
    picks = read_jsonl(args.picks)
    results = []

    for record in picks:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": record["name"]},
        ]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        started = time.time()
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        raw = tokenizer.decode(
            generated[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True,
        )

        results.append({
            "id": record["id"],
            "name": record["name"],
            "url": record.get("url", ""),
            "picked_for": record.get("picked_for", "random"),
            "hard_flags": record.get("hard_flags", []),
            "expected": record["expected"],
            "raw": raw,
            "error": None,
            "latency_s": round(time.time() - started, 2),
        })
        print(f"  [{record.get('picked_for', '?'):12s}] {len(raw):4d} симв  {record['name'][:52]}")

    with args.out.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"\nготово: {len(results)} ответов -> {args.out}")


if __name__ == "__main__":
    main()
