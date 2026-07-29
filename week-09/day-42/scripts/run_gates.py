#!/usr/bin/env python3
"""Снимает все данные для каскада за один прогон. Запускать НА арендованной ВМ.

Модель грузится в том же 4-bit NF4, что и baseline дня 41, — иначе цифры
контроля будет не с чем сравнивать.

Три прохода, каждый батчами:

1. **base** — жадная генерация (`temperature=0`), как в baseline. Дополнительно
   снимаются logprob-ы каждого токена и его границы в тексте: из них считается
   уверенность по ответу и по каждому полю.
2. **samples** — по два сэмпла (`temperature>0`) на каждый ответ, прошедший
   программные проверки. Нужны для голосования по полям.
3. **selfcheck** — самопроверка для тех, у кого сэмплы разошлись хотя бы в одном поле.

Проходы 2 и 3 снимаются **для всех подходящих примеров, а не только для тех,
кого отсеял порог уверенности**. Так каскад потом пересобирается офлайн с любым
порогом, без новой аренды GPU: цена каждой ступени известна из фактических
замеров времени по каждому вызову.

Результат — `results/runs.jsonl`. Метрики считает `report.py` уже локально.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import gates
from common import GPU_RUB_PER_HOUR, read_jsonl, schema, write_jsonl

DEFAULT_MODEL = "Qwen/Qwen3-14B"


def token_offsets(tokenizer, ids: list[int], logprobs: list[float]) -> tuple[list[dict], str]:
    """Считает границы каждого токена в декодированном тексте.

    Смещения нужны, чтобы привязать logprob к конкретному полю JSON: без них
    уверенность считается только по ответу целиком, а именно поэлементная
    оценка и отличает осмысленный scoring от одной общей цифры.

    Декодируем нарастающим префиксом: у BPE склейка токенов не совпадает с
    конкатенацией их текстов, и наивное сложение даёт съехавшие границы.
    """
    tokens: list[dict] = []
    previous = ""
    for index, logprob in enumerate(logprobs):
        current = tokenizer.decode(ids[: index + 1], skip_special_tokens=True)
        tokens.append({"start": len(previous), "end": len(current), "logprob": round(logprob, 5)})
        previous = current
    return tokens, previous


class Runner:
    """Обёртка над моделью: батчевая генерация с замером времени и токенов."""

    def __init__(self, model_path: str, max_new_tokens: int, batch_size: int) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch = torch
        self.max_new_tokens = max_new_tokens
        self.batch_size = batch_size

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        print(f"гружу {model_path} в 4-bit NF4...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        # Левый паддинг обязателен для батчевой генерации у decoder-only:
        # при правом модель продолжает паддинг, а не текст.
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path, quantization_config=quant_config, device_map="auto",
        )
        self.model.eval()
        self.gpu_seconds = 0.0

    def render(self, system_prompt: str, user_content: str) -> str:
        """Разворачивает пару сообщений в текст через chat-шаблон модели."""
        return self.tokenizer.apply_chat_template(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_content}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )

    def generate(self, prompts: list[str], temperature: float = 0.0,
                 samples: int = 1, with_scores: bool = False) -> list[list[dict]]:
        """Генерирует ответы на список промптов, возвращая по `samples` штук на каждый.

        Каждый ответ — словарь с сырым текстом, числом сгенерированных токенов,
        амортизированной latency и, если запрошено, токенами с logprob-ами.
        """
        # `num_return_sequences=samples` умножает число одновременных
        # последовательностей в батче. Без деления `--batch-size 8` означает
        # 8 последовательностей на базовом проходе и 16 на сэмплировании —
        # прогон 29.07.2026 упал на этом с OOM, уже сняв базовый проход.
        # Батч считаем в последовательностях, а не в промптах.
        step = max(1, self.batch_size // max(1, samples))
        results: list[list[dict]] = []
        for start in range(0, len(prompts), step):
            chunk = prompts[start : start + step]
            results.extend(self._generate_batch(chunk, temperature, samples, with_scores))
            print(f"  {min(start + len(chunk), len(prompts))}/{len(prompts)}", flush=True)
        return results

    def _generate_batch(self, prompts: list[str], temperature: float,
                        samples: int, with_scores: bool) -> list[list[dict]]:
        """Генерирует один батч и раскладывает результат по исходным промптам."""
        inputs = self.tokenizer(prompts, return_tensors="pt", padding=True).to(self.model.device)
        prompt_len = inputs["input_ids"].shape[1]

        kwargs = dict(
            max_new_tokens=self.max_new_tokens,
            pad_token_id=self.tokenizer.pad_token_id,
            num_return_sequences=samples,
            return_dict_in_generate=True,
            output_scores=with_scores,
        )
        if temperature > 0:
            kwargs.update(do_sample=True, temperature=temperature, top_p=0.95)
        else:
            kwargs.update(do_sample=False)

        started = time.time()
        with self.torch.no_grad():
            output = self.model.generate(**inputs, **kwargs)
        elapsed = time.time() - started
        self.gpu_seconds += elapsed

        generated = output.sequences[:, prompt_len:]
        transitions = None
        if with_scores:
            transitions = self.model.compute_transition_scores(
                output.sequences, output.scores, normalize_logits=True,
            )

        eos_id = self.tokenizer.eos_token_id
        pad_id = self.tokenizer.pad_token_id
        per_sequence: list[dict] = []
        for row in range(generated.shape[0]):
            ids = generated[row].tolist()
            # Обрезаем хвост из eos/pad: он не часть ответа, а его logprob-ы
            # разбавили бы оценку уверенности.
            length = len(ids)
            while length > 0 and ids[length - 1] in (eos_id, pad_id):
                length -= 1
            ids = ids[:length]

            if with_scores and transitions is not None:
                logprobs = [float(x) for x in transitions[row].tolist()[:length]]
                tokens, text = token_offsets(self.tokenizer, ids, logprobs)
            else:
                tokens = []
                text = self.tokenizer.decode(ids, skip_special_tokens=True)

            per_sequence.append({
                "raw": text,
                "gen_tokens": length,
                # Время батча делится на все выданные последовательности, а не
                # на промпты: иначе сумма по двум сэмплам одного примера
                # посчитает стоимость избыточности вдвое.
                "latency_s": round(elapsed / (len(prompts) * samples), 2),
                "batch_wall_s": round(elapsed, 2),
                "tokens": tokens,
            })

        return [per_sequence[i * samples : (i + 1) * samples] for i in range(len(prompts))]


def main() -> None:
    """Точка входа: три прохода по выборке, результат в runs.jsonl."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--picks", type=Path, default=Path("picks50.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("runs.jsonl"))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--samples", type=int, default=2,
                        help="сколько дополнительных сэмплов на ответ для голосования")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--limit", type=int, help="прогнать только первые N примеров (дымовой прогон)")
    args = parser.parse_args()

    picks = read_jsonl(args.picks)
    if args.limit:
        picks = picks[: args.limit]
    print(f"примеров: {len(picks)}")

    system_prompt = schema.build_system_prompt()
    runner = Runner(args.model, args.max_new_tokens, args.batch_size)
    passes: dict[str, float] = {}

    # --- проход 1: жадная генерация с logprob-ами
    print("\n[1/3] base (temperature=0, с logprob-ами)")
    mark = runner.gpu_seconds
    prompts = [runner.render(system_prompt, p["name"]) for p in picks]
    base = runner.generate(prompts, temperature=0.0, samples=1, with_scores=True)
    passes["base"] = round(runner.gpu_seconds - mark, 1)

    records = []
    for pick, outputs in zip(picks, base):
        records.append({**pick, "base": outputs[0], "samples": [], "selfcheck": None})

    # Чекпойнт после самого дорогого прохода. Падение на сэмплировании
    # (OOM 29.07.2026) иначе стирает уже оплаченное GPU-время, а базовый
    # проход — единственный, который нельзя пересобрать офлайн.
    checkpoint = args.out.with_suffix(".base.jsonl")
    write_jsonl(checkpoint, records)
    print(f"  чекпойнт базового прохода -> {checkpoint}")

    # --- проход 2: сэмплы для голосования
    votable = [r for r in records if gates.constraint_check(r["name"], r["base"]["raw"]).passed]
    print(f"\n[2/3] samples (temperature={args.temperature}, {args.samples} на пример) "
          f"для {len(votable)} прошедших проверки")
    mark = runner.gpu_seconds
    if votable and args.samples > 0:
        sample_prompts = [runner.render(system_prompt, r["name"]) for r in votable]
        sampled = runner.generate(sample_prompts, temperature=args.temperature,
                                  samples=args.samples, with_scores=False)
        for record, outputs in zip(votable, sampled):
            record["samples"] = outputs
    passes["samples"] = round(runner.gpu_seconds - mark, 1)

    # --- проход 3: самопроверка там, где сэмплы разошлись
    needs_check = []
    for record in votable:
        primary = gates.constraint_check(record["name"], record["base"]["raw"]).obj
        sample_objs = [schema.parse_model_json(s["raw"])[0] for s in record["samples"]]
        result = gates.vote(primary, sample_objs)
        record["vote_disputed"] = result.disputed
        if result.disputed:
            needs_check.append(record)

    print(f"\n[3/3] selfcheck для {len(needs_check)} примеров со спорными полями")
    mark = runner.gpu_seconds
    if needs_check:
        check_prompts = []
        for record in needs_check:
            primary = gates.constraint_check(record["name"], record["base"]["raw"]).obj
            check_prompts.append(runner.tokenizer.apply_chat_template(
                [{"role": "system", "content": gates.SELFCHECK_SYSTEM},
                 {"role": "user", "content": gates.build_selfcheck_prompt(
                     record["name"], primary, record["vote_disputed"])}],
                tokenize=False, add_generation_prompt=True, enable_thinking=False,
            ))
        checks = runner.generate(check_prompts, temperature=0.0, samples=1, with_scores=False)
        for record, outputs in zip(needs_check, checks):
            record["selfcheck"] = outputs[0]
    passes["selfcheck"] = round(runner.gpu_seconds - mark, 1)

    write_jsonl(args.out, records)

    sample_calls = sum(len(r["samples"]) for r in records)
    total_calls = len(records) + sample_calls + len(needs_check)
    meta = {
        "model": args.model,
        "picks": len(records),
        "batch_size": args.batch_size,
        "max_new_tokens": args.max_new_tokens,
        "temperature": args.temperature,
        "samples_per_item": args.samples,
        "calls": {"base": len(records), "samples": sample_calls, "selfcheck": len(needs_check),
                  "total": total_calls},
        "gpu_seconds": {**passes, "total": round(runner.gpu_seconds, 1)},
        "rub_per_hour": GPU_RUB_PER_HOUR,
        "rub_total": round(runner.gpu_seconds / 3600 * GPU_RUB_PER_HOUR, 2),
    }
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nготово: {len(records)} записей -> {args.out}")
    print(f"вызовов модели: {total_calls} (base {len(records)}, "
          f"samples {sample_calls}, selfcheck {len(needs_check)})")
    print(f"GPU-время: {runner.gpu_seconds:.0f} с ≈ {meta['rub_total']:.1f} ₽ "
          f"по {GPU_RUB_PER_HOUR} ₽/ч")
    print(f"мета: {meta_path}")


if __name__ == "__main__":
    main()
