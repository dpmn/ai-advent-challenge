"""Оркестратор red team-прогона против мишени партнёра.

Запускается на immers. По каждому вектору: прогон seed-payload'ов, при неудаче —
адаптивные вариации от локальной модели, судья после каждого ответа, лог всего
в JSONL. Соблюдает rate-limit и потолки запросов.

Режимы:
  --recon           только разведка: GET /v1/models, benign-запрос, состав инструментов
  --dry-run         не трогать мишень: напечатать, что было бы отправлено (токен не нужен)
  --vectors a,b,c   выбрать векторы (по умолчанию все)
  без флагов        боевой прогон по мишени

Использование (на immers):
  ATTACK_TARGET_TOKEN=... python3 -m redteam.run --recon
  ATTACK_TARGET_TOKEN=... python3 -m redteam.run --vectors tool_enum,prompt_reveal
"""

import argparse
import json
import os
import sys
import time

from redteam import attacker, client, config, judge, payloads


def _log_path() -> str:
    """Путь к файлу лога текущего прогона."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    return os.path.join(config.LOG_DIR, f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


def _write(fh, record: dict) -> None:
    """Дописывает строку в JSONL-лог и сбрасывает буфер."""
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    fh.flush()


def _steps_for(vector: str) -> list[list[str]]:
    """Возвращает seed-последовательности вектора с подстановками."""
    if vector == "file_read":
        return payloads.render_file_read()
    return payloads.SEEDS.get(vector, [])


def _run_sequence(target: client.Target, steps: list[str], use_model: bool) -> tuple[list[dict], dict]:
    """Прогоняет одну многоходовую последовательность, судит финальный ответ.

    Возвращает (записи шагов, вердикт судьи по последнему ответу).
    """
    target.reset()
    records: list[dict] = []
    last = ""
    last_reply = None
    multi = len(steps) > 1
    for i, msg in enumerate(steps):
        reply = target.send(msg, keep=multi)
        last = reply.content
        last_reply = reply
        records.append(
            {
                "step": i,
                "payload": msg,
                "ok": reply.ok,
                "status": reply.status,
                "error": reply.error,
                "response": reply.content[:1200],
                "input_decision": reply.input_decision,
                "output_decision": reply.output_decision,
                "notices": reply.notices,
                "agent_state": reply.agent_state,
            }
        )
    verdict = judge.assess(last, use_model=use_model)
    # Гвард замаскировал/заблокировал вывод — значит агент попытался выдать что-то
    # чувствительное: near-hit, помечаем как повод присмотреться.
    if last_reply is not None and last_reply.guard_fired:
        verdict["guard_fired"] = {
            "input": last_reply.input_decision,
            "output": last_reply.output_decision,
            "notices": last_reply.notices,
        }
        if last_reply.output_decision and last_reply.output_decision != "ALLOW":
            verdict["suspect"] = True
            verdict.setdefault("reasons", []).append(
                f"гвард мишени сработал на выходе: {last_reply.output_decision}"
            )
    return records, verdict


def _dry_run(vectors: list[str]) -> None:
    """Печатает payload'ы без обращения к мишени — для локальной проверки."""
    for vector in vectors:
        print(f"\n=== {vector} — {payloads.GOALS.get(vector, '')} ===")
        for seq in _steps_for(vector):
            for i, msg in enumerate(seq):
                tag = f"  [{i}]" if len(seq) > 1 else "  -"
                print(f"{tag} {msg}")
    print("\n[dry-run] мишень не трогали.")


def _attack(vectors: list[str], use_model: bool) -> None:
    """Боевой прогон по мишени с адаптацией и логированием."""
    gaps = config.missing()
    if gaps:
        sys.exit(f"Не задано: {', '.join(gaps)}. Экспортируй токен мишени.")

    target = client.Target()
    total = 0
    path = _log_path()
    print(f"[run] лог: {path}")
    print(f"[run] мишень: {config.TARGET_API} | модель-атакующий: {config.ATTACKER_MODEL}")

    with open(path, "a", encoding="utf-8") as fh:
        _write(fh, {"event": "start", "target": config.TARGET_API, "vectors": vectors})
        for vector in vectors:
            print(f"\n=== вектор: {vector} — {payloads.GOALS.get(vector, '')} ===")
            attempts = 0
            tried: list[str] = []
            suspect_found = False

            queue: list[list[str]] = list(_steps_for(vector))
            asked_model = False

            while queue and attempts < config.MAX_ATTEMPTS_PER_VECTOR:
                if total >= config.MAX_TOTAL_REQUESTS:
                    print("[run] достигнут общий потолок запросов, стоп.")
                    _write(fh, {"event": "budget_stop", "total": total})
                    return
                steps = queue.pop(0)
                attempts += 1
                total += len(steps)
                tried.append(steps[-1])

                records, verdict = _run_sequence(target, steps, use_model)
                mark = "⚠ ПОДОЗРЕНИЕ" if verdict["suspect"] else "—"
                print(f"  [{attempts}] {mark}  {steps[-1][:70]}")
                for r in reasons_short(verdict):
                    print(f"        {r}")

                _write(
                    fh,
                    {
                        "event": "attempt",
                        "vector": vector,
                        "attempt": attempts,
                        "steps": records,
                        "verdict": verdict,
                    },
                )
                if verdict["suspect"]:
                    suspect_found = True

                # Очередь опустела, подозрения нет — просим модель дать вариации.
                if not queue and not suspect_found and use_model and not asked_model:
                    asked_model = True
                    last = records[-1]["response"] if records else ""
                    variants = attacker.variations(vector, last, tried)
                    print(f"  … модель предложила {len(variants)} вариаций")
                    _write(fh, {"event": "variations", "vector": vector, "count": len(variants), "items": variants})
                    queue.extend([[v] for v in variants])

            _write(fh, {"event": "vector_done", "vector": vector, "attempts": attempts, "suspect": suspect_found})
        _write(fh, {"event": "end", "total_requests": total})
    print(f"\n[run] готово. Запросов к мишени: {total}. Разбор: python3 -m redteam.report {path}")


def reasons_short(verdict: dict) -> list[str]:
    """Короткие строки причин подозрения — для консоли."""
    return verdict.get("reasons", [])[:3]


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="red team прогон против мишени партнёра")
    parser.add_argument("--recon", action="store_true", help="только разведка")
    parser.add_argument("--dry-run", action="store_true", help="не трогать мишень, показать payload'ы")
    parser.add_argument("--vectors", default="", help="список векторов через запятую")
    parser.add_argument("--no-model", action="store_true", help="без адаптивных вариаций и без судьи-модели")
    args = parser.parse_args()

    vectors = [v.strip() for v in args.vectors.split(",") if v.strip()] or list(payloads.SEEDS.keys())
    unknown = [v for v in vectors if v not in payloads.SEEDS]
    if unknown:
        sys.exit(f"Неизвестные векторы: {', '.join(unknown)}. Доступны: {', '.join(payloads.SEEDS)}")

    if args.dry_run:
        _dry_run(vectors)
        return
    if args.recon:
        _recon()
        return
    _attack(vectors, use_model=not args.no_model)


def _recon() -> None:
    """Разведка формы API мишени (black-box)."""
    if config.missing():
        sys.exit(f"Не задано: {', '.join(config.missing())}.")
    target = client.Target()
    print(f"[recon] {config.TARGET_API} | guard_mode={config.GUARD_MODE}")
    status, body, err = target.list_chats()
    print(f"  GET /chats → {status} {err}")
    if body:
        print(f"  чатов на мишени: {len(body.get('chats') or [])}")
    print("  benign-запрос…")
    reply = target.send("Здравствуйте. Чем вы можете помочь?")
    print(f"  → ok={reply.ok} status={reply.status} err={reply.error}")
    print(f"  вердикты гварда: input={reply.input_decision} output={reply.output_decision} state={reply.agent_state}")
    print(f"  ответ: {reply.content[:300]}")
    print("  состав инструментов…")
    reply = target.send(payloads.SEEDS["tool_enum"][0][0])
    print(f"  → {reply.content[:700]}")


if __name__ == "__main__":
    main()
