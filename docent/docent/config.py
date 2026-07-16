"""Конфигурация docent: пути `.docent/`, чтение ключа, реестр моделей.

Реестр моделей — это задел под будущий opencode-style выбор модели: логические
имена (`coder`, `base`, `heavy`) отображаются на конкретные id провайдера, а
конфиг проекта хранит только логическое имя. Роутинг под задачу пока не
реализован — `resolve_model` просто возвращает id по имени/id.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Каталог с индексом и настройками, создаётся `docent init` в корне репозитория.
DOCENT_DIR = ".docent"
CONFIG_FILE = "config.json"

# Провайдер: Cloud.ru Foundation Models (OpenAI-совместимый API).
BASE_URL = "https://foundation-models.api.cloud.ru/v1"
API_KEY_ENV = "DOCENT_API_KEY"

# Реестр моделей. Ключ — логическое имя, значение — id у провайдера и описание.
# Задел под выбор модели: сюда добавляются модели, конфиг ссылается по имени.
MODELS: dict[str, dict[str, str]] = {
    "coder": {"id": "Qwen/Qwen3-Coder-Next", "desc": "средняя сложность, дефолт"},
    "base": {"id": "Qwen/Qwen3-30B-A3B", "desc": "простые задачи, дёшево"},
    "heavy": {"id": "MiniMaxAI/MiniMax-M2.5", "desc": "тяжёлые задачи"},
}
# В конфиге храним реальный id провайдера (как embed_model), а не логический
# алиас — так однообразнее и понятнее. resolve_model() понимает и алиас, и id.
DEFAULT_MODEL = MODELS["coder"]["id"]
EMBED_MODEL = "openai/text-embedding-3-small"


@dataclass
class Config:
    """Настройки индексации и генерации для одного репозитория."""

    model: str = DEFAULT_MODEL
    # Модель ревью: тяжёлая — ревью гоняется раз на PR, качество важнее цены.
    review_model: str = MODELS["heavy"]["id"]
    # Запасная модель для ревью, если основная недоступна (retry/fallback).
    fallback_model: str = MODELS["base"]["id"]
    embed_model: str = EMBED_MODEL
    base_url: str = BASE_URL
    # Glob-паттерны документации (относительно корня репо) для индекса.
    # README-файлы берутся по всему дереву (не только корневой): иначе доки
    # вложенных пакетов (например docent/README.md) не попадают в индекс.
    index_globs: list[str] = field(
        default_factory=lambda: ["**/README*", "docs/**/*.md", "docs/**/*.markdown"]
    )
    # Glob-паттерны кода: индексируем docstring-и и сигнатуры (см. rag/code.py).
    code_globs: list[str] = field(
        default_factory=lambda: [
            "agents/**/*.py",
            "ragger/**/*.py",
            "docent/**/*.py",
            "mcp_servers/**/*.py",
            "webui/*.py",
        ]
    )
    top_k: int = 5
    # Лимиты ревью (символы/файлы). Переопределяются через .docent/config.json.
    max_diff_chars: int = 12000
    # 12k символов ≈ 300 строк: файлы меньших размеров попадают целиком —
    # усечённый файл провоцирует фактические ошибки ревьюера (day-34).
    max_file_chars: int = 12000
    max_context_files: int = 20
    # Пасс верификации: второй LLM-вызов отсеивает недоказуемые находки.
    review_verify: bool = True
    # Файл осознанных решений проекта (в корне репо, коммитится) — гасит
    # повторные находки по уже принятым решениям.
    review_notes: str = ".docent-review-notes.md"

    def to_dict(self) -> dict:
        """Сериализует конфиг в словарь для записи в JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Строит конфиг из словаря, игнорируя незнакомые ключи."""
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def docent_dir(root: Path) -> Path:
    """Возвращает путь к каталогу `.docent/` внутри корня репозитория."""
    return root / DOCENT_DIR


def config_path(root: Path) -> Path:
    """Возвращает путь к файлу конфига внутри `.docent/`."""
    return docent_dir(root) / CONFIG_FILE


def is_initialized(root: Path) -> bool:
    """Проверяет, что в корне уже выполнен `docent init`."""
    return config_path(root).exists()


def load_config(root: Path) -> Config:
    """Читает конфиг из `.docent/config.json`; при отсутствии — дефолтный."""
    path = config_path(root)
    if not path.exists():
        return Config()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Config.from_dict(data)


def save_config(root: Path, config: Config) -> None:
    """Сохраняет конфиг в `.docent/config.json`, создавая каталог при нужде."""
    docent_dir(root).mkdir(parents=True, exist_ok=True)
    config_path(root).write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_model(name_or_id: str) -> str:
    """По логическому имени из реестра возвращает id модели; иначе — как есть.

    Задел под opencode-style выбор: сейчас это просто lookup. Позже здесь
    появится выбор модели под конкретную задачу/стоимость.
    """
    entry = MODELS.get(name_or_id)
    return entry["id"] if entry else name_or_id


def get_api_key() -> str | None:
    """Возвращает ключ Cloud.ru из переменной окружения или None."""
    return os.getenv(API_KEY_ENV)
