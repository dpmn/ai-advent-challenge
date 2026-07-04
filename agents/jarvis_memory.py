import json
from typing import Dict, Any
from pathlib import Path


_PROFILES_DIR = Path(__file__).parent.resolve() / "memory" / "profiles"


class TaskContext:
    """Рабочая память (Working Memory) — данные текущей задачи."""

    TASK_STATE_KEYS = {
        "goal": "цель диалога",
        "constraints": "ограничения",
        "terms": "уточнённые термины",
        "last_focus": "последняя тема",
        "progress": "что сделано / что осталось",
    }

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def clear(self):
        self._data.clear()

    def to_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["📋 Текущая задача (TaskContext):"]
        for k, v in self._data.items():
            if isinstance(v, list):
                items = "\n".join(f"    - {i}" for i in v)
                lines.append(f"  • {k}:\n{items}")
            elif isinstance(v, dict):
                items = "\n".join(f"    - {sk}: {sv}" for sk, sv in v.items())
                lines.append(f"  • {k}:\n{items}")
            else:
                lines.append(f"  • {k}: {v}")
        return "\n".join(lines)

    def extract_and_update(
        self,
        user_input: str,
        assistant_response: str,
        api_key: str,
        model: str = "Qwen/Qwen3-30B-A3B",
        base_url: str = "https://foundation-models.api.cloud.ru/v1",
    ) -> dict:
        """Вызывает LLM для извлечения task state из последнего обмена, обновляет _data.

        Args:
            user_input: Последнее сообщение пользователя.
            assistant_response: Последний ответ ассистента.
            api_key: API-ключ Cloud.ru.
            model: ID модели для извлечения (дешёвая).
            base_url: Базовый URL API.

        Returns:
            Обновлённый _data (dict).
        """
        current = json.dumps(self._data, ensure_ascii=False, indent=2) if self._data else "пусто"

        prompt = (
            "Ты — анализатор диалога. Проанализируй последний обмен и обнови состояние задачи.\n\n"
            "Текущее состояние задачи:\n"
            f"{current}\n\n"
            "Последнее сообщение пользователя:\n"
            f"{user_input}\n\n"
            "Последний ответ ассистента:\n"
            f"{assistant_response}\n\n"
            "Верни JSON с обновлённым состоянием задачи. "
            "Поля:\n"
            '  "goal" — цель диалога (строка)\n'
            '  "constraints" — список ограничений (массив строк)\n'
            '  "terms" — словарь уточнённых терминов (объект)\n'
            '  "last_focus" — о чём последний обмен (строка)\n'
            '  "progress" — что сделано / что осталось (строка)\n\n'
            "Правила:\n"
            "- Если информации для поля нет — используй пустую строку или пустой список/объект.\n"
            "- Сохраняй предыдущие данные, если новая информация их не отменяет.\n"
            "- Ответ верни СТРОГО в виде JSON без markdown-обёртки."
        )

        import urllib.request

        payload = {
            "model": model,
            "max_tokens": 512,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            print(f"[TASK_EXTRACT] LLM error: {e}")
            return self._data

        extracted = self._try_parse_json(content)
        if extracted and isinstance(extracted, dict):
            for key in self.TASK_STATE_KEYS:
                if key in extracted and extracted[key]:
                    self._data[key] = extracted[key]
        return self._data

    @staticmethod
    def _try_parse_json(content: str) -> dict | None:
        """Пытается извлечь JSON из ответа LLM."""
        content = content.strip()
        # прямой парсинг
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # ```json блок
        import re
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        # первый { }
        m = re.search(r"(\{[\s\S]*\})", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def to_dict(self) -> dict:
        return dict(self._data)

    def load_dict(self, data: dict):
        self._data = dict(data)

    def remove(self, key: str):
        self._data.pop(key, None)

    def keys(self):
        return self._data.keys()


class Profile:
    """Долговременная память (Long-term Memory) — профиль пользователя."""

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self._data: Dict[str, str] = {}
        self._profiles_dir = _PROFILES_DIR
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _file_path(self) -> Path:
        return self._profiles_dir / f"{self.profile_name}.md"

    def _load(self):
        path = self._file_path()
        if path.exists():
            content = path.read_text(encoding="utf-8")
            current_key = None
            current_value = []
            for line in content.split("\n"):
                if line.startswith("## "):
                    if current_key:
                        self._data[current_key] = "\n".join(current_value).strip()
                    current_key = line[3:].strip()
                    current_value = []
                elif current_key:
                    current_value.append(line)
            if current_key:
                self._data[current_key] = "\n".join(current_value).strip()

    def save(self):
        path = self._file_path()
        lines = [f"# Profile: {self.profile_name}"]
        for k, v in self._data.items():
            lines.append(f"\n## {k}")
            lines.append(v)
        path.write_text("\n".join(lines), encoding="utf-8")

    def set(self, key: str, value: str):
        self._data[key] = value
        self.save()

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def to_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["\U0001f464 Профиль пользователя (Profile):"]
        for k, v in self._data.items():
            first_line = v.split("\n")[0] if v else ""
            lines.append(f"  \u2022 {k}: {first_line[:120]}")
        return "\n".join(lines)

    def to_full_prompt_block(self) -> str:
        if not self._data:
            return ""
        lines = ["\U0001f464 Профиль пользователя:"]
        for k, v in self._data.items():
            lines.append(f"\n[{k}]\n{v}")
        return "\n".join(lines)

    def list_profiles(self) -> list:
        return sorted(
            p.stem for p in self._profiles_dir.glob("*.md")
        ) or ["default"]
