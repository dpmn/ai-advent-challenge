"""
Скрипт для проверки 3 стратегий управления контекстом.
Запускает диалог по каждой стратегии и сохраняет LOG в md.
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.jarvis import JarvisAgent


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log_dialog(strategy_name: str, messages: list):
    """Сохраняет диалог в md-файл."""
    filename = LOG_DIR / f"dialog_{strategy_name}.md"
    lines = [
        f"# Диалог: стратегия «{strategy_name}»",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for msg in messages:
        role = msg["role"].upper()
        content = msg["content"]
        branch = msg.get("branch", "")
        header = f"## {role}"
        if branch:
            header += f" [{branch}]"
        lines.append(header)
        lines.append("")
        lines.append(content)
        lines.append("")
    lines.append("---")
    lines.append("*Конец диалога*")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  📝 LOG сохранён: {filename}")


def run_strategy_test(strategy: str, scenario_messages: list) -> list:
    """
    Запускает тест для одной стратегии.
    Возвращает список всех сообщений (включая ответы агента).
    """
    db_path = str(LOG_DIR / f"test_{strategy}.db")

    agent = JarvisAgent(
        model="Qwen/Qwen3-30B-A3B",
        temperature=0.7,
        system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай кратко и по делу.",
        context_limit=40000,
        max_tokens=2500,
        db_path=db_path,
        compression_enabled=False
    )

    # Создаём новую сессию
    agent.create_session(f"Тест стратегии: {strategy}")
    agent.set_strategy(strategy)

    full_log = []
    strat_name = strategy if strategy else "off"

    print(f"\n{'='*60}")
    print(f"🔀 Тестирование стратегии: {strat_name}")
    print(f"{'='*60}")

    for i, user_msg in enumerate(scenario_messages, 1):
        print(f"\n  [{i}/{len(scenario_messages)}] 👤 → {user_msg[:60]}...")
        full_log.append({"role": "user", "content": user_msg, "turn": i})

        response = agent.chat(user_msg)
        full_log.append({"role": "assistant", "content": response, "turn": i})

        # Статистика
        if agent.last_usage:
            u = agent.last_usage
            print(f"  🤖 ← (prompt: {u.get('prompt_tokens', '?')} + completion: {u.get('completion_tokens', '?')} = {u.get('total_tokens', '?')} tok)")

        # Показываем факты если sticky_facts
        if strategy == "sticky_facts":
            print(f"  📌 Факты: {len(agent.sticky_facts)}")
            for k, v in agent.sticky_facts.items():
                print(f"      {k}: {v[:70]}...")

        # Показываем историю если sliding_window
        if strategy == "sliding_window":
            msg_count = len([m for m in agent.conversation_history if m["role"] != "system"])
            print(f"  🪟 Сообщений в истории: {msg_count}")

    # Итоговая статистика
    print(f"\n  📊 Итог сессии:")
    print(f"     Всего токенов: {agent.session_total_tokens}")
    print(f"     Сообщений: {len([m for m in agent.conversation_history if m['role'] != 'system'])}")
    if strategy == "sticky_facts":
        print(f"     Фактов: {len(agent.sticky_facts)}")
        for k, v in agent.sticky_facts.items():
            print(f"       {k}: {v[:80]}...")

    log_dialog(strat_name, full_log)
    return full_log


def run_branching_test() -> list:
    """
    Специальный тест для branching: показывает реальное расхождение диалога.
    Две ветки от одного checkpoint — с разными вопросами.
    """
    db_path = str(LOG_DIR / "test_branching.db")

    agent = JarvisAgent(
        model="Qwen/Qwen3-30B-A3B",
        temperature=0.7,
        system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай кратко и по делу.",
        context_limit=40000,
        max_tokens=2500,
        db_path=db_path,
        compression_enabled=False
    )

    agent.create_session("Тест branching: расхождение веток")
    agent.set_strategy("branching")

    full_log = []
    turn_counter = 0

    def chat(msg):
        nonlocal turn_counter
        turn_counter += 1
        print(f"\n  [{turn_counter}] 👤 → {msg[:60]}...")
        resp = agent.chat(msg)
        print(f"  🤖 ← {resp[:60]}...")
        full_log.append({"role": "user", "content": msg, "turn": turn_counter, "branch": agent.branches[agent.current_branch_id]["name"]})
        full_log.append({"role": "assistant", "content": resp, "turn": turn_counter, "branch": agent.branches[agent.current_branch_id]["name"]})
        return resp

    print(f"\n{'='*60}")
    print(f"🔀 Тестирование стратегии: branching (с расхождением)")
    print(f"{'='*60}")

    # ── Общая часть (обе ветки) ──
    chat("Привет! Хочу разработать телеграм-бота для изучения "
         "разговорного английского с использованием ИИ. "
         "Цель — помочь пользователям преодолеть языковой барьер.")

    chat("Основные требования: пользователь отправляет голосовые сообщения, "
         "бот транскрибирует их через ASR, проверяет грамматику и произношение "
         "с помощью LLM, даёт обратную связь. Нужна поддержка уровня A1-C1.")

    # ── Checkpoint ──
    print("\n  📍 Сохраняем checkpoint...")
    agent.save_checkpoint()
    full_log.append({"role": "system", "content": "📍 CHECKPOINT СОХРАНЁН — далее диалог расходится", "turn": 0, "branch": "—"})

    # ── Ветка 1: бюджет/стек ──
    print("\n  🌿 Создаём ветку «Бюджет и стек»...")
    agent.create_branch("Бюджет и стек")
    full_log.append({"role": "system", "content": "🌿 ПЕРЕКЛЮЧЕНО В ВЕТКУ «Бюджет и стек»", "turn": 0, "branch": "—"})

    chat("Какие технологии стека предложишь? Мне интересно: "
         "запускать всё через облако Cloud.ru, использовать их же GPU для LLM. "
         "Бюджет ограничен — не больше 50 000 руб/мес. Сколько будет стоить?")

    chat("Сравни по цене Whisper и Google Speech-to-Text для ASR "
         "в рамках этого бюджета. Какой выгоднее?")

    # ── Ветка 0: архитектура ──
    print("\n  🔄 Переключаемся обратно в основную ветку...")
    agent.switch_branch(0)
    full_log.append({"role": "system", "content": "🔄 ПЕРЕКЛЮЧЕНО В ОСНОВНУЮ ВЕТКУ", "turn": 0, "branch": "—"})

    chat("Опиши архитектуру: как будут связаны Telegram Bot API, "
         "ASR-сервис (например Whisper), LLM и база данных. "
         "Как хранить прогресс пользователя?")

    chat("Какую БД посоветуешь для хранения прогресса пользователя? "
         "Нужно хранить уровень, историю диалогов, статистику ошибок.")

    # ── Итог ──
    print(f"\n  📊 Итог сессии:")
    print(f"     Всего токенов: {agent.session_total_tokens}")
    print(f"     Всего веток: {len(agent.branches)}")
    for bid, branch in agent.branches.items():
        msgs = len([m for m in branch["messages"] if m["role"] != "system"])
        print(f"       Ветка '{branch['name']}' (ID {bid}): {msgs} сообщений")

    log_dialog("branching", full_log)
    return full_log


def main():
    # Сценарий для sliding_window и sticky_facts
    scenario = [
        (
            "Привет! Хочу разработать телеграм-бота для изучения "
            "разговорного английского с использованием ИИ. "
            "Цель — помочь пользователям преодолеть языковой барьер."
        ),
        (
            "Основные требования: пользователь отправляет голосовые сообщения, "
            "бот транскрибирует их через ASR, проверяет грамматику и произношение "
            "с помощью LLM, даёт обратную связь. Нужна поддержка уровня A1-C1."
        ),
        (
            "Какие технологии стек предложишь? Мне интересно: "
            "запускать всё через облако Cloud.ru, использовать их же GPU для LLM. "
            "Бюджет ограничен — не больше 50 000 руб/мес."
        ),
        (
            "Опиши архитектуру: как будут связаны Telegram Bot API, "
            "ASR-сервис (например Whisper), LLM и база данных. "
            "Как хранить прогресс пользователя?"
        ),
    ]

    strategies = ["sliding_window", "sticky_facts"]
    all_results = {}

    for strategy in strategies:
        try:
            log = run_strategy_test(strategy, scenario)
            all_results[strategy] = log
        except Exception as e:
            print(f"❌ Ошибка при тестировании стратегии {strategy}: {e}")
            import traceback
            traceback.print_exc()

    # Branching — отдельный тест с расхождением
    try:
        log = run_branching_test()
        all_results["branching"] = log
    except Exception as e:
        print(f"❌ Ошибка при тестировании branching: {e}")
        import traceback
        traceback.print_exc()

    # Сводка
    print(f"\n\n{'='*60}")
    print("📊 СВОДКА ПО СТРАТЕГИЯМ")
    print(f"{'='*60}")

    print("\n✅ Все LOG-файлы сохранены в week-02/day-10/logs/")
    print("   " + ", ".join(str(LOG_DIR / f"dialog_{s}.md") for s in ["sliding_window", "sticky_facts", "branching"]))


if __name__ == "__main__":
    main()
