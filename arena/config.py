"""Настройки сервиса arena: доступ, модели, адреса, лимиты, пути.

Всё читается из окружения (`.env` в корне репозитория) с разумными
умолчаниями. Единственное, у чего умолчания нет, — `ARENA_TOKEN`: сервис,
который пускает кого угодно, для red team challenge не годится.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Доступ ────────────────────────────────────────────────────────
# Shared-токен: выдаётся партнёру, проверяется на каждом боевом запросе.
ARENA_TOKEN = os.getenv("ARENA_TOKEN", "")

ARENA_HOST = os.getenv("ARENA_HOST", "127.0.0.1")
ARENA_PORT = int(os.getenv("ARENA_PORT", "5010"))

# ── LLM ───────────────────────────────────────────────────────────
# Все вызовы модели идут через LLM Gateway дня 48 — единая точка контроля.
GATEWAY_URL = os.getenv("ARENA_GATEWAY_URL", "http://127.0.0.1:5001/v1")
API_KEY = os.getenv("CLOUDRU_SECRET_KEY", "")

# Бот: tool calling у Cloud.ru живёт только на Coder-Next и MiniMax.
BOT_MODEL = os.getenv("ARENA_BOT_MODEL", "Qwen/Qwen3-Coder-Next")
# Судья: инструменты ему не нужны, вызывается до трёх раз на запрос — берём
# самую дешёвую модель. Лекция недели ровно это и советует (микромодель).
JUDGE_MODEL = os.getenv("ARENA_JUDGE_MODEL", "Qwen/Qwen3-30B-A3B")

BOT_TEMPERATURE = float(os.getenv("ARENA_BOT_TEMPERATURE", "0.3"))
BOT_MAX_TOKENS = int(os.getenv("ARENA_BOT_MAX_TOKENS", "1200"))
LLM_TIMEOUT = float(os.getenv("ARENA_LLM_TIMEOUT", "120"))

# ── Лимиты ────────────────────────────────────────────────────────
# Сервис открыт наружу и крутит LLM за наши деньги — потолки обязательны.
MAX_MESSAGE_CHARS = int(os.getenv("ARENA_MAX_MESSAGE_CHARS", "8000"))
MAX_FILE_BYTES = int(os.getenv("ARENA_MAX_FILE_BYTES", "65536"))
MAX_HISTORY_MESSAGES = int(os.getenv("ARENA_MAX_HISTORY", "20"))
MAX_TOOL_STEPS = int(os.getenv("ARENA_MAX_TOOL_STEPS", "5"))
RATE_LIMIT_PER_MIN = int(os.getenv("ARENA_RATE_LIMIT", "20"))
BUDGET_RUB = float(os.getenv("ARENA_BUDGET_RUB", "100"))

# Расширения, которые разрешено загружать. Бинарные форматы (PDF, DOCX) не
# берём: парсинг стоит дня, а вектор атаки ровно тот же.
ALLOWED_EXTENSIONS = {".txt", ".md", ".html", ".json"}

# ── Пути ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARENA_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "arena.db"
UPLOAD_DIR = DATA_DIR / "uploads"


def ensure_dirs() -> None:
    """Создаёт каталоги данных и загрузок, если их ещё нет."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def missing() -> list[str]:
    """Возвращает список незаданных обязательных настроек (для /health и старта)."""
    gaps = []
    if not ARENA_TOKEN:
        gaps.append("ARENA_TOKEN")
    if not API_KEY:
        gaps.append("CLOUDRU_SECRET_KEY")
    return gaps
