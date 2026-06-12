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
            system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай развёрнуто.",
            context_limit=40000,
            max_tokens=10000
        )
        print("=" * 70)
        print("🤖 Jarvis — агент с подсчётом токенов")
        print("=" * 70)
        print("Команды:")
        print("  • /new [имя]      - создать новую сессию")
        print("  • /sessions       - список всех сессий (с токенами)")
        print("  • /switch <id>    - переключиться на сессию по ID")
        print("  • /delete <id>    - удалить сессию по ID")
        print("  • /trim [N]       - обрезать историю до N сообщений (по умолч. 10)")
        print("  • /reset          - очистить историю текущей сессии")
        print("  • /stats          - показать статистику агента")
        print("  • /quit           - выйти из программы")
        print("=" * 70)
        print("💡 Enter — отправить команду /... | Alt+Enter — отправить сообщение")
        print("=" * 70)
        print(f"✅ Текущая сессия: {agent.current_session['name']} (ID: {agent.current_session['id']})")
        msg_count = len([m for m in agent.conversation_history if m["role"] != "system"])
        print(f"📝 Сообщений в истории: {msg_count}")

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
                        print(f"  ID {s['id']:>3}: {s['name']}{marker}")
                        print(f"       токенов: {s['total_tokens']}  (prompt: {s['prompt_tokens']}, completion: {s['completion_tokens']})")
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

        except KeyboardInterrupt:
            print("\n\n👋 До свидания!")
            break
        except Exception as e:
            print(f"\n❌ Произошла ошибка: {e}")


if __name__ == "__main__":
    run_cli()
