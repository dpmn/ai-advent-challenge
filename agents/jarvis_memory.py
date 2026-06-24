from typing import Dict, Any
from pathlib import Path


_PROFILES_DIR = Path(__file__).parent.resolve() / "memory" / "profiles"


class TaskContext:
    """Рабочая память (Working Memory) — данные текущей задачи."""

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
        lines = ["\U0001f4cb Текущая задача (TaskContext):"]
        for k, v in self._data.items():
            lines.append(f"  \u2022 {k}: {v}")
        return "\n".join(lines)

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
