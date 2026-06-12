from agents.jarvis import JarvisAgent


def run_cli_interface():
    """Простой CLI-интерфейс для взаимодействия с агентом."""

    try:
        # Создаём агента
        agent = JarvisAgent(
            model="ai-sage/GigaChat3-10B-A1.8B",
            temperature=0.5,
            system_prompt="Ты — полезный AI-ассистент. Отвечай кратко и по делу."
        )
        print("=" * 70)
        print("🤖 ПРИВЕТ! Я Jarvis - самый умный ИИ-агент. А ещё скромный.")
        print("=" * 70)
        print("Команды:")
        print("  • /reset  - очистить историю разговора")
        print("  • /stats  - показать статистику")
        print("  • /quit   - выйти из программы")
        print("=" * 70)

    except ValueError as e:
        print(f"❌ Ошибка инициализации: {e}")
        return

    # Основной цикл взаимодействия
    while True:
        try:
            # Получаем ввод от пользователя
            user_input = input("\n👤 Вы: ").strip()

            # Обрабатываем команды
            if user_input.lower() in ['/quit', '/exit', '/q']:
                print("\n👋 До свидания!")
                break
            elif user_input.lower() == '/reset':
                agent.reset_conversation()
                continue
            elif user_input.lower() == '/stats':
                print(f"\n{agent.get_stats()}")
                continue
            elif not user_input:
                continue

            # Отправляем запрос агенту
            print("\n⏳ Агент думает...")
            response = agent.chat(user_input)

            # Выводим ответ
            print(f"\n🤖 Агент: {response}")

        except KeyboardInterrupt:
            print("\n\n👋 До свидания!")
            break
        except Exception as e:
            print(f"\n❌ Произошла ошибка: {e}")


if __name__ == "__main__":
    run_cli_interface()
