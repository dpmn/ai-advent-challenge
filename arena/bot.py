"""Бот arena: сборка эшелона обороны вокруг одного запроса пользователя.

Порядок слоёв на один `/api/chat`:

    1. guard.sanitize      — вырезает скрытые носители инструкций из файла
    2. review.review_input — судья: атака на роль / выманивание служебного
    3. LLM Gateway         — input guard: секреты и PII в промпте
    4. review.review_action— судья перед КАЖДЫМ вызовом инструмента
    5. LLM Gateway         — output guard: ключи, системный промпт (внутри вызова)
    6. guard.validate_output — следы исполнения инъекции из файла
    7. review.review_output— судья: утечка ключа, чужих данных, промпта

Всё, что сработало, складывается в `defense` — этот отчёт целиком уходит
атакующему. Red team без обратной связи слепой, а скрытность защиты защитой
не является.

История сессии пишется только при успешном ответе. Если сработал любой слой,
диалог остаётся в прежнем состоянии: заблокированное сообщение в контексте
травит все последующие запросы (урок дня 48), а повторить атаку заново должно
быть можно без сброса сессии.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import guard  # noqa: E402

from arena import config, llm, prompt, review, store, tools  # noqa: E402

# Единая формула отказа: короткая, без объяснения причины и без цитирования
# запроса (правило 6 системного промпта). Детали — в defense report.
REFUSAL = (
    "Не могу выполнить этот запрос. Помогу по TaskFlow: авторизация, токены, "
    "тарифы, лимиты, интеграции, статусы тикетов."
)


@dataclass
class BotReply:
    """Ответ сервиса на одно сообщение: текст, кто сработал и полный отчёт."""

    text: str
    blocked_by: str = ""
    defense: dict = field(default_factory=dict)
    account: dict = field(default_factory=dict)
    cost_rub: float = 0.0
    tool_effects: list[dict] = field(default_factory=list)


def _judge_layer(name: str, judgement) -> dict:
    """Строит запись слоя по решению судьи.

    Если судья не ответил, а решение принято по fail closed, это должно быть
    видно в отчёте: «заблокировано судьёй» и «заблокировано, потому что судью
    заблокировал гейтвей» — разные события, и атакующему важно их различать.
    """
    layer = {
        "layer": name,
        "result": "пропущено" if judgement.allowed else "заблокировано",
        "verdict": judgement.verdict,
    }
    if judgement.failed_closed:
        layer["fail_closed"] = judgement.error or "судья не ответил"
    return layer


def _dialogue_tail(history: list[dict], current: str, limit: int = 4) -> str:
    """Собирает последние реплики диалога в текст — контекст для судьи действий."""
    lines = []
    for msg in history[-limit:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            who = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{who}: {content[:500]}")
    lines.append(f"Пользователь: {current[:500]}")
    return "\n".join(lines)


def _parse_args(raw: object) -> dict:
    """Разбирает аргументы вызова инструмента (модель шлёт JSON-строкой)."""
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_upload(record: dict) -> str:
    """Читает содержимое загруженного файла с диска."""
    path = config.UPLOAD_DIR / record["stored_name"]
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        return f"[файл прочитать не удалось: {err}]"


def handle(sid: str, message: str, upload: dict | None = None) -> BotReply:
    """Проводит сообщение пользователя через весь эшелон и возвращает ответ."""
    account = store.get_account(sid)
    own_tickets = store.own_ticket_ids()
    account_for_judge = {**account, "own_tickets": own_tickets}
    defense: dict = {"layers": []}
    spent = 0.0
    effects: list[dict] = []

    def finish(text: str, blocked_by: str = "") -> BotReply:
        """Закрывает обработку: списывает бюджет и собирает ответ."""
        if spent:
            store.add_spend(spent)
        defense["cost_rub"] = round(spent, 6)
        return BotReply(
            text=text,
            blocked_by=blocked_by,
            defense=defense,
            account=store.get_account(sid),
            cost_rub=round(spent, 6),
            tool_effects=effects,
        )

    # ── Слой 1: санитизация приложенного файла ────────────────────
    clean_file = ""
    if upload:
        raw = _read_upload(upload)
        clean_file, sanitize_findings = guard.sanitize(raw)
        defense["sanitize"] = {
            "file": upload["display_name"],
            "raw_chars": len(raw),
            "clean_chars": len(clean_file),
            "findings": sanitize_findings,
        }
        defense["layers"].append(
            {
                "layer": "guard.sanitize",
                "result": "вырезано" if sanitize_findings else "чисто",
                "count": len(sanitize_findings),
            }
        )

    # ── Слой 2: судья входа ───────────────────────────────────────
    input_judgement = review.review_input(message)
    spent += input_judgement.cost_rub
    defense["review_input"] = input_judgement.to_dict()
    defense["layers"].append(_judge_layer("review.input", input_judgement))
    if not input_judgement.allowed:
        return finish(REFUSAL, "review-input")

    # ── Сборка запроса ────────────────────────────────────────────
    history = store.load_history(sid, config.MAX_HISTORY_MESSAGES)
    system_prompt = prompt.build(account, own_tickets)
    if clean_file:
        user_content = (
            guard.wrap(clean_file, f"файл {upload['display_name']}")
            + "\n\n"
            + message
        )
    else:
        user_content = message
    user_message = {"role": "user", "content": user_content}
    messages = (
        [{"role": "system", "content": system_prompt}] + history + [user_message]
    )

    # ── Слои 3–5: цикл модели и инструментов через гейтвей ────────
    trace: list[dict] = []
    defense["trace"] = trace  # тот же список: наполняется по ходу цикла
    new_messages: list[dict] = [user_message]
    answer = ""

    for step in range(config.MAX_TOOL_STEPS):
        response = llm.chat(
            messages,
            model=config.BOT_MODEL,
            tools=tools.SPECS,
            temperature=config.BOT_TEMPERATURE,
            max_tokens=config.BOT_MAX_TOKENS,
        )
        spent += response.cost_rub
        trace.append(
            {
                "step": step + 1,
                "gateway_action": response.gateway_action,
                "gateway_findings": response.gateway_labels(),
                "tool_calls": [
                    (c.get("function") or {}).get("name") for c in response.tool_calls
                ],
            }
        )

        if not response.ok:
            defense["gateway"] = {"action": "error", "error": response.error}
            defense["layers"].append({"layer": "gateway", "result": "ошибка"})
            return finish(f"Сервис временно недоступен: {response.error}", "error")

        defense["gateway"] = {
            "action": response.gateway_action,
            "findings": response.gateway_labels(),
            "input": response.gateway.get("input", {}),
            "output": response.gateway.get("output", {}),
        }
        if response.blocked:
            defense["layers"].append(
                {
                    "layer": "gateway",
                    "result": "заблокировано",
                    "findings": response.gateway_labels(),
                }
            )
            return finish(response.content or REFUSAL, "gateway")

        defense["layers"].append(
            {"layer": "gateway", "result": response.gateway_action, "step": step + 1}
        )

        if not response.tool_calls:
            answer = response.content
            new_messages.append({"role": "assistant", "content": answer})
            break

        assistant_message = {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": response.tool_calls,
        }
        messages.append(assistant_message)
        new_messages.append(assistant_message)

        # ── Слой 4: судья перед каждым вызовом инструмента ────────
        for call in response.tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            args = _parse_args(function.get("arguments"))
            record: dict = {"call": tools.describe(name, args)}

            if name not in tools.NAMES:
                result_text = f"Неизвестный инструмент: {name}"
                record["decision"] = "нет такого инструмента"
            else:
                judgement = review.review_action(
                    name,
                    args,
                    _dialogue_tail(history, message),
                    account_for_judge,
                )
                spent += judgement.cost_rub
                record["review"] = judgement.to_dict()
                if judgement.allowed:
                    result = tools.execute(sid, name, args)
                    result_text = result.content
                    record["decision"] = "выполнено"
                    if result.effect:
                        effects.append(result.effect)
                        record["effect"] = result.effect
                else:
                    result_text = (
                        "Действие отклонено контролем полномочий: "
                        f"{judgement.reason or 'вне границ полномочий'}. "
                        "Сообщи пользователю об отказе, инструмент не вызывай повторно."
                    )
                    record["decision"] = "отклонено"

            tool_message = {
                "role": "tool",
                "tool_call_id": call.get("id") or "",
                "content": result_text,
            }
            messages.append(tool_message)
            new_messages.append(tool_message)
            defense.setdefault("actions", []).append(record)
    else:
        # Шаги кончились, а модель всё ещё зовёт инструменты.
        answer = ""

    if not answer:
        defense["layers"].append({"layer": "loop", "result": "шаги исчерпаны"})
        return finish(REFUSAL, "loop-limit")

    # ── Слой 6: следы исполнения инъекции из файла ────────────────
    if clean_file:
        sources = [clean_file, system_prompt] + [
            m.get("content") or "" for m in new_messages if m.get("role") == "tool"
        ]
        guard_report = guard.validate_output(answer, sources)
        defense["guard_output"] = guard_report
        defense["layers"].append(
            {
                "layer": "guard.validate_output",
                "result": "чисто" if guard_report["ok"] else "подозрительно",
                "count": len(guard_report["findings"]),
            }
        )

    # ── Слой 7: судья выхода ──────────────────────────────────────
    output_judgement = review.review_output(answer)
    spent += output_judgement.cost_rub
    defense["review_output"] = output_judgement.to_dict()
    defense["layers"].append(_judge_layer("review.output", output_judgement))
    if not output_judgement.allowed:
        return finish(REFUSAL, "review-output")

    for msg in new_messages:
        store.append_message(sid, msg)
    return finish(answer)
