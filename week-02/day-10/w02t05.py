import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.jarvis import JarvisAgent
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings

kb = KeyBindings()


@kb.add('enter')
def _(event):
    buf = event.app.current_buffer
    if buf.text.startswith('/'):
        buf.accept_action.validate_and_handle(event.app, buf)
    else:
        buf.insert_text('\n')


session_pt = PromptSession(key_bindings=kb)


def run_cli():
    try:
        agent = JarvisAgent(
            model="Qwen/Qwen3-30B-A3B",
            temperature=0.7,
            system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай кратко и по делу.",
            context_limit=40000,
            max_tokens=2500
        )
        print("=" * 70)
        print("🤖 Jarvis — управление контекстом: 3 стратегии (day 10)")
        print("=" * 70)
        print("Команды:")
        print("  • /strategy               - показать текущую стратегию")
        print("  • /strategy <name>        - установить стратегию (sliding_window | sticky_facts | branching | off)")
        print("  • /checkpoint             - сохранить checkpoint (для branching)")
        print("  • /branch <name>          - создать ветку от checkpoint")
        print("  • /branches               - список всех веток")
        print("  • /switch-branch <id>     - переключиться на ветку")
        print("  • /compress-on            - включить сжатие")
        print("  • /compress-off           - выключить сжатие")
        print("  • /new [имя]              - создать новую сессию")
        print("  • /sessions               - список всех сессий")
        print("  • /switch <id>            - переключиться на сессию по ID")
        print("  • /delete <id>            - удалить сессию по ID")
        print("  • /trim [N]               - обрезать историю до N сообщений")
        print("  • /reset                  - очистить историю текущей сессии")
        print("  • /stats                  - показать статистику агента")
        print("  • /quit                   - выйти из программы")
        print("=" * 70)
        print("💡 Enter — отправить команду /... | Alt+Enter — отправить сообщение")
        print("=" * 70)
        strat_name = agent.context_strategy if agent.context_strategy else "выкл"
        print(f"✅ Текущая сессия: {agent.current_session['name']} (ID: {agent.current_session['id']})")
        print(f"🔀 Стратегия: {strat_name} | Сжатие: {'вкл' if agent.compression_enabled else 'выкл'}")
        msg_count = len([m for m in agent.conversation_history if m["role"] != "system"])
        print(f"📝 Сообщений в истории: {msg_count}")
        if agent.compression_history:
            print(f"📦 Сжатых фрагментов: {len(agent.compression_history)}")
        if agent.context_strategy == "sticky_facts" and agent.sticky_facts:
            print(f"📌 Фактов: {len(agent.sticky_facts)}")

    except ValueError as e:
        print(f"❌ Ошибка инициализации: {e}")
        return

    while True:
        try:
            prompt_text = f"👤 [{agent.current_session['name']}] Вы: "
            user_input = session_pt.prompt(
                f"\n{prompt_text}",
                multiline=True
            ).strip()

            if not user_input:
                continue

            # ─── Команды ──────────────────────────────────────────
            if user_input.lower() in ['/quit', '/exit', '/q']:
                print("\n👋 До свидания!")
                break

            elif user_input.lower() == '/stats':
                print(f"\n{agent.get_stats()}")
                continue

            elif user_input.lower() == '/reset':
                agent.reset_conversation()
                continue

            elif user_input.lower() == '/sessions':
                sessions = agent.list_sessions()
                if not sessions:
                    print("📭 Нет сохранённых сессий")
                else:
                    print("\n📋 Все сессии:")
                    for s in sessions:
                        marker = " 👈 (текущая)" if s["id"] == agent.current_session["id"] else ""
                        comp = "вкл" if s.get("compression_enabled", 1) else "выкл"
                        strat = s.get("context_strategy") or "выкл"
                        print(f"  ID {s['id']:>3}: {s['name']}{marker}")
                        print(f"       токенов: {s['total_tokens']}  (prompt: {s['prompt_tokens']}, completion: {s['completion_tokens']}) | Сжатие: {comp} | Стратегия: {strat}")
                continue

            elif user_input.lower().startswith('/new'):
                parts = user_input.split(maxsplit=1)
                name = parts[1] if len(parts) > 1 else None
                agent.create_session(name)
                continue

            elif user_input.lower().startswith('/switch'):
                parts = user_input.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    print("❌ Используйте: /switch <id>")
                    continue
                agent.switch_session(int(parts[1]))
                continue

            elif user_input.lower().startswith('/delete'):
                parts = user_input.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    print("❌ Используйте: /delete <id>")
                    continue
                if agent.delete_session(int(parts[1])):
                    print("✅ Сессия удалена")
                else:
                    print("❌ Сессия не найдена")
                continue

            elif user_input.lower().startswith('/trim'):
                parts = user_input.split()
                if len(parts) == 1:
                    agent.trim_context()
                elif len(parts) == 2 and parts[1].isdigit():
                    agent.trim_context(int(parts[1]))
                else:
                    print("❌ Используйте: /trim [N]")
                continue

            elif user_input.lower() == '/compress-on':
                agent.enable_compression()
                continue

            elif user_input.lower() == '/compress-off':
                agent.disable_compression()
                continue

            # ─── Команды стратегий ────────────────────────────────

            elif user_input.lower() == '/strategy':
                s = agent.context_strategy if agent.context_strategy else "выкл"
                print(f"\n🔀 Текущая стратегия: {s}")
                continue

            elif user_input.lower().startswith('/strategy '):
                parts = user_input.split(maxsplit=1)
                if len(parts) != 2:
                    print("❌ Используйте: /strategy sliding_window | sticky_facts | branching | off")
                    continue
                raw = parts[1].strip().lower()
                strategy_map = {
                    "sliding_window": "sliding_window",
                    "sticky_facts": "sticky_facts",
                    "sticky": "sticky_facts",
                    "branching": "branching",
                    "branch": "branching",
                    "off": None,
                    "none": None,
                    "выкл": None,
                }
                mapped = strategy_map.get(raw)
                if mapped is None and raw not in ("off", "none", "выкл"):
                    # Allow the exact string as well
                    if raw in ("sliding_window", "sticky_facts", "branching"):
                        mapped = raw
                    else:
                        print("❌ Неизвестная стратегия. Допустимые: sliding_window, sticky_facts, branching, off")
                        continue
                result = agent.set_strategy(mapped)
                print(f"\n{result}")
                continue

            elif user_input.lower() == '/checkpoint':
                result = agent.save_checkpoint()
                print(f"\n{result}")
                continue

            elif user_input.lower().startswith('/branch '):
                parts = user_input.split(maxsplit=1)
                if len(parts) != 2:
                    print("❌ Используйте: /branch <имя ветки>")
                    continue
                result = agent.create_branch(parts[1].strip())
                print(f"\n{result}")
                continue

            elif user_input.lower() == '/branches':
                branches = agent.list_branches()
                if not branches:
                    print("📭 Нет веток (ветвление не активно)")
                else:
                    print("\n🌿 Ветки:")
                    for b in branches:
                        print(f"  ID {b['id']:>3}: {b['name']} — {b['messages']} сообщений{b['current']}")
                continue

            elif user_input.lower().startswith('/switch-branch '):
                parts = user_input.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    print("❌ Используйте: /switch-branch <id>")
                    continue
                result = agent.switch_branch(int(parts[1]))
                print(f"\n{result}")
                continue

            elif user_input.lower().startswith('/branch') and not user_input.lower().startswith('/branches'):
                # /branch without argument
                print("❌ Используйте: /branch <имя ветки>")
                continue

            # ─── Обычный диалог ───────────────────────────────────
            print("⏳ Jarvis думает...")
            response = agent.chat(user_input)
            print(f"\n🤖 Jarvis: {response}")

            # Показываем токены последнего запроса
            if agent.last_usage:
                u = agent.last_usage
                print(
                    f"\n📊 Последний запрос: prompt {u.get('prompt_tokens', '?')} "
                    f"+ completion {u.get('completion_tokens', '?')} "
                    f"= {u.get('total_tokens', '?')} токенов"
                )
                total = agent.session_prompt_tokens + agent.session_completion_tokens
                pct = round(total / agent.context_limit * 100, 1)
                print(
                    f"📈 Сессия всего: {agent.session_total_tokens} токенов "
                    f"| Контекст занят на {pct}%"
                )

            # Показываем информацию о стратегии
            strat_name = agent.context_strategy if agent.context_strategy else "выкл"
            comp_status = "вкл" if agent.compression_enabled else "выкл"
            print(f"🔀 Стратегия: {strat_name} | Сжатие: {comp_status}")

            if agent.context_strategy == "sticky_facts" and agent.sticky_facts:
                print(f"📌 Фактов: {len(agent.sticky_facts)}")

            if agent.context_strategy == "branching":
                current_branch = agent.branches.get(agent.current_branch_id, {})
                print(f"🌿 Ветка: '{current_branch.get('name', '?')}' (ID: {agent.current_branch_id}) | Всего веток: {len(agent.branches)}")

            if agent.compression_history:
                total_saved = sum(
                    item["tokens_before"] - item["tokens_after"]
                    for item in agent.compression_history
                )
                print(
                    f"📦 Сжатых фрагментов: {len(agent.compression_history)}, "
                    f"всего сэкономлено ~{total_saved} токенов"
                )

        except KeyboardInterrupt:
            print("\n\n👋 До свидания!")
            break
        except Exception as e:
            print(f"\n❌ Произошла ошибка: {e}")


if __name__ == "__main__":
    run_cli()
