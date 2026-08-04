"""
JSONL-лог обменов Jarvis — то, чего нет в SQLite-истории.

История диалога уже пишется в agents/memory/jarvis_history.db (таблица messages),
но там нет вызовов MCP-инструментов с их результатами, активной персоны и модели
на момент запроса. Для разбора prompt injection (день 46) это ключевое: надо видеть,
что агент реально прочитал отравленный тикет, а не выдумал текст сам.

Формат — одна JSON-строка на обмен, файл на день: logs/jarvis-YYYY-MM-DD.jsonl.
Каталог logs/ в .gitignore: в промпте-жертве лежит (выдуманный) секрет, и диалогам
с ним в репозитории не место.

Логирование не должно ломать чат: любая ошибка записи гасится и печатается.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"


class JarvisLogger:
    """Пишет обмены агента (запрос, вызовы инструментов, ответ) в JSONL-файл."""

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR

    def _current_file(self) -> Path:
        """Возвращает путь к файлу лога за сегодня, создавая каталог при нужде."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"jarvis-{datetime.now():%Y-%m-%d}.jsonl"

    def log_exchange(
            self,
            *,
            session_id: Optional[int] = None,
            model: str = "",
            persona: str = "",
            user_input: str = "",
            response: str = "",
            tool_calls: Optional[list] = None,
            duration_ms: int = 0,
            error: Optional[str] = None,
            extra: Optional[dict] = None
    ) -> None:
        """Записывает один обмен строкой JSON. Ошибки записи не пробрасывает."""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "model": model,
            "persona": persona,
            "user_input": user_input,
            "tool_calls": tool_calls or [],
            "response": response,
            "duration_ms": duration_ms,
            "error": error,
        }
        if extra:
            record.update(extra)

        try:
            with open(self._current_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[JARVIS][LOG] Не удалось записать лог: {e}")
