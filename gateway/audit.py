"""
Аудит LLM Gateway (день 48).

Одна JSON-строка на запрос, файл на день: `logs/gateway-YYYY-MM-DD.jsonl`.
Каталог `logs/` в `.gitignore`.

Главное правило этого модуля: в лог не попадает ни одного секрета. Гейтвей
существует ради того, чтобы секрет не уехал в чужую систему; если он при этом
складывает ключи в файл на диске открытым текстом, утечка просто переехала.
Поэтому пишется только маска, тип и отпечаток sha256 (первые 12 символов) —
по нему видно, что ключ из вчерашнего запроса и ключ из сегодняшнего один и тот
же, а восстановить значение нельзя.

Тексты запроса и ответа пишутся уже после маскирования и обрезаются по длине.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"

_TEXT_LIMIT = 2000


class GatewayAudit:
    """Пишет аудит-лог запросов гейтвея в JSONL и читает его хвост."""

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR

    def _current_file(self) -> Path:
        """Возвращает путь к файлу лога за сегодня, создавая каталог при нужде."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"gateway-{datetime.now():%Y-%m-%d}.jsonl"

    def log(
            self,
            *,
            request_id: str,
            client_ip: str = "",
            model: str = "",
            upstream: str = "",
            action: str = "",
            input_verdict: Optional[dict] = None,
            output_verdict: Optional[dict] = None,
            prompt_preview: str = "",
            response_preview: str = "",
            usage: Optional[dict] = None,
            duration_ms: int = 0,
            upstream_status: Optional[int] = None,
            error: Optional[str] = None,
    ) -> dict:
        """Записывает один запрос строкой JSON и возвращает записанную структуру."""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "request_id": request_id,
            "client_ip": client_ip,
            "model": model,
            "upstream": upstream,
            "action": action,
            "input": input_verdict or {"action": "pass", "findings": []},
            "output": output_verdict or {"action": "pass", "findings": []},
            "prompt_preview": prompt_preview[:_TEXT_LIMIT],
            "response_preview": response_preview[:_TEXT_LIMIT],
            "usage": usage or {},
            "duration_ms": duration_ms,
            "upstream_status": upstream_status,
            "error": error,
        }
        try:
            with open(self._current_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[GATEWAY][AUDIT] Не удалось записать лог: {e}")
        return record

    def tail(self, limit: int = 20) -> list[dict]:
        """Возвращает последние записи сегодняшнего лога (свежие — первыми)."""
        path = self.log_dir / f"gateway-{datetime.now():%Y-%m-%d}.jsonl"
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            print(f"[GATEWAY][AUDIT] Не удалось прочитать лог: {e}")
            return []
        out = []
        for line in reversed(lines[-limit:]):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
