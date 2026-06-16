#!/usr/bin/env python3
"""
День 11 — Демонстрация трёхуровневой модели памяти.
Тема: Text-to-SQL агент для аналитики.

Демонстрирует:
  - Short-term: диалог внутри сессии
  - Working: TaskContext (текущая задача: схема БД, описание таблиц)
  - Long-term: Profile (стиль SQL, предпочтения, именование)

Запуск:
  python3 week-03/day-11/w03d01.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.jarvis import JarvisAgent


SQL_SYSTEM_PROMPT = """Ты — SQL-ассистент для аналитики.
Помогаешь пользователю превращать текстовые запросы в SQL.
Правила:
  - Отвечай ТОЛЬКО SQL-запросом и кратким пояснением.
  - Используй информацию из профиля пользователя (стиль, соглашения).
  - Учитывай данные текущей задачи (схему БД, описание таблиц).
  - Если данных недостаточно — запроси уточнение.
"""


def print_separator(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    agent = JarvisAgent(
        system_prompt=SQL_SYSTEM_PROMPT,
        temperature=0.3,
    )
    # Создаём новую сессию для демо, чтобы не мешала история прошлых запусков
    agent.create_session("День 11 — Text-to-SQL Demo")

    # ── 1. Настройка долговременной памяти (Profile) ──────────
    print_separator("ШАГ 1: Долговременная память (Profile)")
    print("Зададим профиль пользователя — стиль SQL, предпочтения.")
    agent.profile.set("dbms", "PostgreSQL")
    agent.profile.set("naming_convention", "snake_case, lower_case_table_names")
    agent.profile.set(
        "sql_style",
        "WITH clauses для сложных запросов, явные JOIN, "
        "алиасы таблиц по первой букве, группировка в конце",
    )
    agent.profile.set("date_format", "YYYY-MM-DD")
    print(f"  • dbms: PostgreSQL")
    print(f"  • naming_convention: snake_case, lower_case_table_names")
    print(f"  • sql_style: WITH clauses, явные JOIN, алиасы")
    print(f"  • date_format: YYYY-MM-DD")
    print(f"\n  Сохранено в файле: agents/memory/profiles/default.md")

    # ── 2. Настройка рабочей памяти (TaskContext) ─────────────
    print_separator("ШАГ 2: Рабочая память (TaskContext)")
    print("Зададим контекст задачи — схему таблиц базы данных.")
    agent.task_context.set("database", "sales_analytics")
    agent.task_context.set(
        "table: orders",
        "id (INT PK), customer_id (INT FK), order_date (DATE), "
        "total_amount (DECIMAL), status (TEXT)",
    )
    agent.task_context.set(
        "table: customers",
        "id (INT PK), name (TEXT), email (TEXT), "
        "registration_date (DATE), country (TEXT)",
    )
    agent.task_context.set(
        "table: order_items",
        "id (INT PK), order_id (INT FK), product_name (TEXT), "
        "quantity (INT), unit_price (DECIMAL)",
    )
    agent.task_context.set("current_task", "Подготовить дашборд продаж за текущий месяц")
    print(f"  • database: sales_analytics")
    print(f"  • table: orders — колонки с типами")
    print(f"  • table: customers — колонки с типами")
    print(f"  • table: order_items — колонки с типами")
    print(f"  • current_task: Подготовить дашборд продаж")

    # ── 3. Чат (Short-term память) ───────────────────────────
    print_separator("ШАГ 3: Чат — краткосрочная память (диалог)")
    print("Теперь отправим запросы агенту и посмотрим, как он использует")
    print("все три слоя памяти.\n")

    queries = [
        "Покажи 10 самых активных клиентов по сумме заказов",
        "А теперь то же самое, но только за последний месяц",
        "Какие товары чаще всего покупают вместе с ноутбуками?",
    ]

    for i, q in enumerate(queries, 1):
        print(f"\n{'─' * 50}")
        print(f"Запрос {i}: {q}")
        print(f"{'─' * 50}")
        response = agent.chat(q)
        print(f"Ответ:\n{response}")

    # ── 4. Итог — что в каждом слое ──────────────────────────
    print_separator("ИТОГ: Что попало в каждый слой памяти")

    print("1. КРАТКОСРОЧНАЯ (Short-term) — история диалога в сессии:")
    non_system = [m for m in agent.conversation_history if m["role"] != "system"]
    print(f"   Сообщений в сессии: {len(non_system)}")
    print(f"   Хранится в: SQLite (messages), память (conversation_history)")
    for m in non_system[-4:]:
        role = m["role"].upper()
        text = m["content"][:80]
        print(f"     [{role}] {text}...")

    print(f"\n2. РАБОЧАЯ (Working) — данные текущей задачи:")
    print(f"   Поля TaskContext: {list(agent.task_context.keys())}")
    print(f"   Хранится в: память (TaskContext), SQLite (sessions.task_context)")
    print(f"   Как используется: инжектируется в system prompt перед запросом")

    print(f"\n3. ДОЛГОВРЕМЕННАЯ (Long-term) — профиль и знания:")
    print(f"   Профиль: {agent.profile.profile_name}")
    print(f"   Поля профиля: {list(agent.profile._data.keys())}")
    print(f"   Хранится в: agents/memory/profiles/{agent.profile.profile_name}.md")
    print(f"   Как используется: загружается при старте, инжектируется в system prompt")

    print(f"\n{'=' * 60}")
    print(f"  Демонстрация завершена.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
