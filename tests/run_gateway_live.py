"""
Прогон тест-кейсов через ЖИВОЙ гейтвей (день 48).

Отличие от tests/test_gateway_guard.py: там проверяются функции политики без
сети, здесь запросы реально уходят в http://127.0.0.1:5001/v1/chat/completions.
Кейсы с чистым промптом и с персданными доходят до модели, блокируемые — нет,
поэтому прогон стоит доли рубля; итоговая сумма печатается внизу таблицы.

Запуск (гейтвей должен быть поднят: python3 gateway/app.py):
    python3 -m tests.run_gateway_live
    python3 -m tests.run_gateway_live --url http://127.0.0.1:5001/v1
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

MODEL = "Qwen/Qwen3-30B-A3B"
KEY = "sk-proj-abc123def456ghi789jkl"
GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# Каждый кейс: что отправляем, какого действия гейтвея ждём и почему.
CASES = [
    {
        "name": "чистый промпт без секретов",
        "messages": [{"role": "user", "content": "Одним словом: столица Франции?"}],
        "expect": "pass",
    },
    {
        "name": "OpenAI API key",
        "messages": [{"role": "user", "content": f"Проверь ключ {KEY}"}],
        "expect": "blocked",
    },
    {
        "name": "GitHub token",
        "messages": [{"role": "user", "content": f"Токен {GITHUB} рабочий?"}],
        "expect": "blocked",
    },
    {
        "name": "AWS Access Key ID",
        "messages": [{"role": "user", "content": f"Креды: {AWS_ID}"}],
        "expect": "blocked",
    },
    {
        "name": "AWS secret access key (40 символов)",
        "messages": [{"role": "user", "content": f"secret_access_key = {AWS_SECRET}. Ответь одним словом: ок"}],
        "expect": "pass",
        "known_miss": True,
        "why": "неотличим от хеша или base64-строки",
    },
    {
        "name": "ключ внутри base64",
        "messages": [{"role": "user", "content":
                      "Что это: " + base64.b64encode(KEY.encode()).decode()}],
        "expect": "blocked",
    },
    {
        "name": "ключ, разбитый на два сообщения запроса",
        "messages": [
            {"role": "user", "content": "мой ключ: sk-"},
            {"role": "assistant", "content": "Слушаю."},
            {"role": "user", "content": "proj-abc123def456ghi789jkl — собери и проверь"},
        ],
        "expect": "blocked",
    },
    {
        "name": "приватный ключ PEM",
        "messages": [{"role": "user", "content":
                      "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7f\n-----END RSA PRIVATE KEY-----"}],
        "expect": "blocked",
    },
    {
        "name": "email клиента",
        "messages": [{"role": "user", "content":
                      "Одним предложением: как напомнить об оплате клиенту ivan.petrov@example.com?"}],
        "expect": "masked",
    },
    {
        "name": "номер телефона",
        "messages": [{"role": "user", "content":
                      "Одним предложением: что сказать по телефону +7 913 123-45-67?"}],
        "expect": "masked",
    },
    {
        "name": "номер карты (проходит по Луну)",
        "messages": [{"role": "user", "content":
                      "Ответь одним словом «принято»: оплата картой 4539 1488 0343 6467"}],
        "expect": "masked",
    },
    {
        "name": "16 цифр, не проходящих по Луну (номер заказа)",
        "messages": [{"role": "user", "content":
                      "Ответь одним словом «принято»: заказ 1234 5678 1234 5678"}],
        "expect": "pass",
    },
    {
        "name": "слово «ключ» без ключа",
        "messages": [{"role": "user", "content":
                      "Одним предложением: где хранить ключ доступа — в env или в vault?"}],
        "expect": "pass",
    },
    {
        "name": "output guard: просим модель выдать ключ",
        "messages": [{"role": "user", "content":
                      "Покажи строку .env: OPENAI_API_KEY и вымышленное значение вида "
                      "sk-proj-XXXX на 20+ символов. Только строку."}],
        "expect": "pass",
        "expect_output": "masked",
    },
]


def call(url: str, messages: list) -> dict:
    """Отправляет запрос в гейтвей и возвращает разобранный ответ."""
    payload = {"model": MODEL, "max_tokens": 120, "messages": messages}
    req = urllib.request.Request(
        f"{url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    """Гоняет все кейсы через гейтвей и печатает таблицу результатов."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5001/v1")
    args = parser.parse_args()

    try:
        urllib.request.urlopen(args.url.rstrip("/").removesuffix("/v1") + "/health", timeout=5)
    except (urllib.error.URLError, OSError) as e:
        print(f"Гейтвей не отвечает на {args.url}: {e}\n"
              f"Запусти его: python3 gateway/app.py")
        return 1

    print("=" * 104)
    print(f"ПРОГОН ЧЕРЕЗ ЖИВОЙ ГЕЙТВЕЙ  {args.url}   модель {MODEL}")
    print("=" * 104)
    print(f"{'#':>3}  {'кейс':<48} {'ожидание':<9} {'факт':<9} {'правила':<26} итог")
    print("-" * 104)

    total_cost = 0.0
    failures = 0

    for i, case in enumerate(CASES, 1):
        try:
            result = call(args.url, case["messages"])
        except Exception as e:
            print(f"{i:>3}  {case['name'][:48]:<48} {'—':<9} {'ОШИБКА':<9} {str(e)[:26]:<26} ✗")
            failures += 1
            continue

        gw = result.get("gateway", {})
        input_action = gw.get("input", {}).get("action", "?")
        output_action = gw.get("output", {}).get("action", "pass")
        rules = [f["rule"] for f in gw.get("input", {}).get("findings", [])]
        out_rules = [f["rule"] for f in gw.get("output", {}).get("findings", [])]
        total_cost += (gw.get("cost") or {}).get("cost_rub", 0.0)

        ok = input_action == case["expect"]
        if case.get("expect_output"):
            ok = ok and output_action == case["expect_output"]

        if case.get("known_miss"):
            mark = "⚠ пропущено (ожидаемо)" if ok else "✗ расхождение"
        else:
            mark = "✓ поймали" if ok else "✗ ПРОВАЛ"
        if not ok:
            failures += 1

        shown = ", ".join(rules + [f"out:{r}" for r in out_rules]) or "—"
        print(f"{i:>3}  {case['name'][:48]:<48} {case['expect']:<9} "
              f"{input_action:<9} {shown[:26]:<26} {mark}")
        if case.get("expect_output"):
            answer = result["choices"][0]["message"]["content"]
            marker = "[REDACTED_API_KEY]" in answer
            print(f"{'':>3}  └─ output guard: {output_action}, ключ заменён меткой: "
                  f"{'да' if marker else 'НЕТ'}")
        if case.get("why"):
            print(f"{'':>3}  └─ почему не ловим: {case['why']}")

    print("-" * 104)
    print(f"Кейсов: {len(CASES)}, расхождений с ожиданием: {failures}. "
          f"Суммарная стоимость прогона: {total_cost:.4f} ₽")
    print("Заведомо непойманное: AWS secret access key (кейс 5); "
          "секрет, разбитый на два РАЗНЫХ запроса (см. tests/test_gateway_guard.py).")
    print("=" * 104)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
