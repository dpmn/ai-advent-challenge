from agents.jarvis import JarvisAgent


def run_cli():
    """CLI-интерфейс с поддержкой сессий."""

    try:
        agent = JarvisAgent(
            model="GigaChat/GigaChat-2-Max",
            temperature=0.7,
            system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай кратко и по делу."
        )
        print("=" * 70)
        print("🤖 ПРИВЕТ! Я Jarvis - самый умный ИИ-агент. А ещё скромный.")
        print("=" * 70)
        print("Команды:")
        print("  • /new [имя]      - создать новую сессию")
        print("  • /sessions       - список всех сессий")
        print("  • /switch <id>    - переключиться на сессию по ID")
        print("  • /delete <id>    - удалить сессию по ID")
        print("  • /reset          - очистить историю текущей сессии")
        print("  • /stats          - показать статистику")
        print("  • /quit           - выйти из программы")
        print("=" * 70)
        print(f"✅ Текущая сессия: {agent.current_session['name']}")
        print(f"📝 Сообщений в истории: {len([m for m in agent.conversation_history if m['role'] != 'system'])}")

    except ValueError as e:
        print(f"❌ Ошибка инициализации: {e}")
        return

    while True:
        try:
            user_input = input(f"\n👤 [{agent.current_session['name']}] Вы: ").strip()

            if not user_input:
                continue

            # Обработка команд
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

            # Обычный диалог
            print("\n⏳ Jarvis думает...")
            response = agent.chat(user_input)
            print(f"\n🤖 Jarvis: {response}")

        except KeyboardInterrupt:
            print("\n\n👋 До свидания!")
            break
        except Exception as e:
            print(f"\n❌ Произошла ошибка: {e}")


if __name__ == "__main__":
    run_cli()
