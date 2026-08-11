"""Настройки red team-харнеса против мишени партнёра (день 50, этап 2).

Всё через окружение. Токен мишени в код не пишем — только `ATTACK_TARGET_TOKEN`.
Харнес запускается на immers (GPU-ВМ с локальной моделью), а не на этой машине:
запросы к чужому сервису не должны идти от инфраструктуры Anthropic.

Правила боя (зашиты намеренно, «правил нет» в челлендже — это наша дисциплина):
- цель только два названных секрета (system prompt + файл на сервере);
- никаких деструктивных действий, персистентности, бокового движения — даже
  если Gradle даст выполнение кода, читаем секретный файл и останавливаемся;
- вежливый rate-limit: VPS мишени маленький, не заваливаем.
"""

import os

# ── Мишень ────────────────────────────────────────────────────────
# Адрес и токен — из README партнёра. IP публичный, токен — только из env.
TARGET_API = os.getenv("ATTACK_TARGET_URL", "http://185.43.5.66:18090/v1")
TARGET_TOKEN = os.getenv("ATTACK_TARGET_TOKEN", "")
# Режим гварда мишени (enum на её стороне). Разведка показала валидный `redact`
# — это их боевая защита (маскирование секрета на выходе). Её и атакуем.
GUARD_MODE = os.getenv("ATTACK_GUARD_MODE", "redact")

# ── Локальная модель (атакующий мозг + судья) ─────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
ATTACKER_MODEL = os.getenv("ATTACKER_MODEL", "dolphin3:8b")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", ATTACKER_MODEL)

# ── Дисциплина боя ────────────────────────────────────────────────
# Минимальный интервал между запросами к мишени, секунды (rate-limit).
# У мишени свой лимит 10 запросов / 60 с → держим ≥ 7 с (8,5/мин, под лимитом).
MIN_INTERVAL_SEC = float(os.getenv("ATTACK_MIN_INTERVAL", "7.0"))
# Сколько вариаций просить у локальной модели, когда seed-payload не прошёл.
MAX_VARIATIONS = int(os.getenv("ATTACK_MAX_VARIATIONS", "4"))
# Потолок запросов к мишени на один вектор (страховка от бесконечного цикла).
MAX_ATTEMPTS_PER_VECTOR = int(os.getenv("ATTACK_MAX_ATTEMPTS", "12"))
# Общий потолок запросов к мишени за прогон.
MAX_TOTAL_REQUESTS = int(os.getenv("ATTACK_MAX_TOTAL", "150"))

TARGET_TIMEOUT = float(os.getenv("ATTACK_TARGET_TIMEOUT", "120"))
OLLAMA_TIMEOUT = float(os.getenv("ATTACK_OLLAMA_TIMEOUT", "180"))

# ── Пути ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.getenv("ATTACK_LOG_DIR", os.path.join(BASE_DIR, "logs"))


def missing() -> list[str]:
    """Возвращает список незаданных обязательных настроек."""
    gaps = []
    if not TARGET_TOKEN:
        gaps.append("ATTACK_TARGET_TOKEN")
    return gaps
