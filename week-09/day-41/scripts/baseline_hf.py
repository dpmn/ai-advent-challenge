#!/usr/bin/env python3
"""Запасной способ снять baseline — прямо на арендованной машине, без сервера.

Основной путь (`baseline.py` через vLLM по OpenAI-совместимому API) требует,
чтобы vLLM подружился с 4-битным квантованием. Если он закапризничает, а
машина тарифицируется поминутно, разбираться некогда: этот скрипт грузит
модель через transformers + bitsandbytes и делает то же самое локально.

Запускать НА ВМ. Формат выхода совпадает с `baseline.py`, так что `score.py`
считает метрики одинаково для обоих путей.

Флаг `--enable-thinking` управляет режимом размышлений в chat-шаблоне. Он есть
не для удобства: первый baseline (28.07.2026) снимался через vLLM, где шаблон
применялся по умолчанию, то есть с размышлениями, и дал 0% чистого JSON при
средней длине ответа 1865 символов. Этот скрипт с самого начала звал шаблон
с `enable_thinking=False`, поэтому расхождение с днём 42 объяснялось путём
снятия, а не кодом. Флаг позволяет снять обе ветки на одной машине и одном
стеке и проверить это, а не предполагать.
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
    parser.add_argument("--model", default="Qwen/Qwen3-4B")
    parser.add_argument("--picks", type=Path, required=True,
                        help="JSONL с отобранной десяткой (id/name/expected/hard_flags)")
    parser.add_argument("--out", type=Path, default=Path("responses.jsonl"))
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--enable-thinking", action="store_true",
                        help="включить режим размышлений в chat-шаблоне "
                             "(так снимался baseline через vLLM 28.07.2026)")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="сколько промптов гнать одновременно")
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

    print(f"гружу {args.model} в 4-bit NF4 "
          f"(размышления: {'вкл' if args.enable_thinking else 'выкл'})...")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    # Левый паддинг обязателен для батчевой генерации у decoder-only:
    # при правом модель продолжает паддинг, а не текст.
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=quant_config, device_map="auto",
    )
    model.eval()

    system_prompt = schema.build_system_prompt()
    picks = read_jsonl(args.picks)
    results = []

    for start in range(0, len(picks), args.batch_size):
        chunk = picks[start : start + args.batch_size]
        texts = [
            tokenizer.apply_chat_template(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": record["name"]}],
                tokenize=False, add_generation_prompt=True,
                enable_thinking=args.enable_thinking,
            )
            for record in chunk
        ]
        inputs = tokenizer(texts, return_tensors="pt", padding=True).to(model.device)
        prompt_len = inputs["input_ids"].shape[1]

        started = time.time()
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        # Latency амортизируется по батчу: сравнивать её между ветками честно
        # только при одинаковом batch-size, что и делается.
        elapsed = (time.time() - started) / len(chunk)

        for record, sequence in zip(chunk, generated):
            raw = tokenizer.decode(sequence[prompt_len:], skip_special_tokens=True)
            results.append({
                "id": record["id"],
                "name": record["name"],
                "url": record.get("url", ""),
                "picked_for": record.get("picked_for", "random"),
                "hard_flags": record.get("hard_flags", []),
                "expected": record["expected"],
                "raw": raw,
                "error": None,
                "latency_s": round(elapsed, 2),
            })
            print(f"  [{record.get('picked_for', '?'):12s}] {len(raw):5d} симв  "
                  f"{record['name'][:52]}", flush=True)

    with args.out.open("w", encoding="utf-8") as fh:
        for result in results:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"\nготово: {len(results)} ответов -> {args.out}")


if __name__ == "__main__":
    main()
