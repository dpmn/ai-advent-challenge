#!/usr/bin/env python3
"""HTTP-сервер сильной модели. Запускается НА арендованной ВМ immers.cloud.

`Qwen3-14B` в том же 4-bit NF4, что baseline дня 41 и прогон дня 42, — иначе
эскалацию будет не с чем сравнивать.

Почему сервер, а не батчевый скрипт как в дне 42: routing живой. Слабая модель
работает на ноутбуке, и заранее неизвестно, какие товары и какие поля уйдут
наверх — это решают эвристики по ходу. Значит сильная модель должна отвечать
по запросу, а не отрабатывать заранее известный список.

Почему `transformers`, а не vLLM: сборка vLLM с Triton сожгла полтора часа
в дне 41 при цене 86,74 ₽/ч, а здесь нужен один запрос за раз — выигрыш
vLLM в пропускной способности не отбивает даже минуты установки.

Эндпоинты:
  GET  /health              — модель загружена, накопленные GPU-секунды
  POST /generate            — `{"names": [...]}` → ответы модели

Никакой авторизации: сервер слушает loopback ВМ, наружу ходит только через
ssh-туннель. Открывать его в интернет нельзя.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent.parent / "day-41" / "scripts"):
    if (_candidate / "schema.py").exists():
        sys.path.insert(0, str(_candidate))
        break

import schema  # noqa: E402  (импорт возможен только после правки sys.path)

DEFAULT_MODEL = "Qwen/Qwen3-14B"
DEFAULT_PORT = 8000
DEFAULT_MAX_NEW_TOKENS = 256


class BigModel:
    """Сильная модель: жадная генерация с замером GPU-времени.

    Доступ сериализуется мьютексом: карта одна, и параллельные запросы дали бы
    не ускорение, а OOM с потерей уже оплаченного времени (грабля дня 42).
    """

    def __init__(self, model_path: str, max_new_tokens: int) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch = torch
        self.max_new_tokens = max_new_tokens
        self.system_prompt = schema.build_system_prompt()
        self.gpu_seconds = 0.0
        self.calls = 0
        self.lock = threading.Lock()

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        print(f"гружу {model_path} в 4-bit NF4...", flush=True)
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
        self.model_path = model_path
        print("модель готова", flush=True)

    def render(self, user_content: str) -> str:
        """Разворачивает пару сообщений в текст через chat-шаблон модели.

        `enable_thinking=False` — не настройка вкуса. С шаблоном по умолчанию
        14B в дне 41 выдала 0% чистого JSON и 2214 символов вместо 162.
        """
        return self.tokenizer.apply_chat_template(
            [{"role": "system", "content": self.system_prompt},
             {"role": "user", "content": user_content}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )

    def generate(self, names: list[str]) -> tuple[list[dict], float]:
        """Генерирует ответы на список названий товаров одним батчем."""
        prompts = [self.render(name) for name in names]
        with self.lock:
            inputs = self.tokenizer(prompts, return_tensors="pt", padding=True).to(self.model.device)
            prompt_len = inputs["input_ids"].shape[1]
            started = time.time()
            with self.torch.no_grad():
                output = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    pad_token_id=self.tokenizer.pad_token_id,
                    do_sample=False,
                )
            elapsed = time.time() - started
            self.gpu_seconds += elapsed
            self.calls += len(names)

        generated = output[:, prompt_len:]
        eos_id, pad_id = self.tokenizer.eos_token_id, self.tokenizer.pad_token_id
        items = []
        for row in range(generated.shape[0]):
            ids = generated[row].tolist()
            length = len(ids)
            while length > 0 and ids[length - 1] in (eos_id, pad_id):
                length -= 1
            ids = ids[:length]
            items.append({
                "raw": self.tokenizer.decode(ids, skip_special_tokens=True),
                "gen_tokens": length,
                # GPU-время батча делится на его последовательности: платим за
                # время карты, а не за число запросов.
                "latency_s": round(elapsed / len(names), 3),
            })
        return items, elapsed


def make_handler(model: BigModel):
    """Создаёт обработчик запросов, замкнутый на загруженную модель."""

    class Handler(BaseHTTPRequestHandler):
        """Минимальный JSON-API поверх одной модели."""

        protocol_version = "HTTP/1.1"

        def _reply(self, code: int, payload: dict) -> None:
            """Отправляет JSON-ответ."""
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802  (имя задано базовым классом)
            """Отдаёт состояние сервера и накопленное GPU-время."""
            if self.path.rstrip("/") != "/health":
                self._reply(404, {"error": "нет такого эндпоинта"})
                return
            self._reply(200, {
                "ok": True,
                "model": model.model_path,
                "quantization": "nf4",
                "thinking": False,
                "gpu_seconds": round(model.gpu_seconds, 3),
                "calls": model.calls,
            })

        def do_POST(self) -> None:  # noqa: N802  (имя задано базовым классом)
            """Генерирует ответы на переданные названия товаров."""
            if self.path.rstrip("/") != "/generate":
                self._reply(404, {"error": "нет такого эндпоинта"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                names = payload.get("names") or []
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                    raise ValueError("names должен быть списком строк")
                items, elapsed = model.generate(names)
            except Exception as error:  # сервер не должен падать из-за одного запроса
                self._reply(400, {"error": f"{type(error).__name__}: {error}"})
                return
            self._reply(200, {
                "items": items,
                "gpu_seconds": round(elapsed, 3),
                "gpu_seconds_total": round(model.gpu_seconds, 3),
            })

        def log_message(self, fmt: str, *args) -> None:
            """Короткий лог в stdout вместо стандартного формата."""
            print(f"[serve_big] {fmt % args}", flush=True)

    return Handler


def main() -> None:
    """Точка входа: грузит модель и слушает порт."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--max-new-tokens", type=int, default=DEFAULT_MAX_NEW_TOKENS)
    args = parser.parse_args()

    model = BigModel(args.model, args.max_new_tokens)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(model))
    print(f"слушаю http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
