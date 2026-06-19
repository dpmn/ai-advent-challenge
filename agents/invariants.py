import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class Invariant(ABC):
    """Абстрактный базовый класс для инвариантов."""

    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def check(self, text: str) -> bool:
        """Возвращает True если нарушений нет."""

    @abstractmethod
    def get_error_message(self) -> str:
        """Сообщение об ошибке для retry."""

    @abstractmethod
    def get_prompt_block(self) -> str:
        """Текстовый блок для инжекции в system prompt."""


class ForbiddenLibrariesInvariant(Invariant):
    """Проверяет, не содержит ли ответ упоминания запрещённых библиотек."""

    def __init__(self, name: str, libraries: list[str], enabled: bool = True):
        super().__init__(name, enabled)
        self.libraries = libraries

    def check(self, text: str) -> bool:
        text_lower = text.lower()
        for lib in self.libraries:
            if lib.lower() in text_lower:
                return False
        return True

    def get_error_message(self) -> str:
        libs = ", ".join(self.libraries)
        return f"Ошибка: использование библиотек {libs} запрещено! Переделай ответ без них."

    def get_prompt_block(self) -> str:
        libs = ", ".join(self.libraries)
        return f"⚠️ Инвариант: {self.name}\nЗапрещённые библиотеки: {libs}"


class RequiredTechStackInvariant(Invariant):
    """Проверяет, что текст не нарушает требование к технологическому стеку."""

    def __init__(self, name: str, techs: list[str], requirement: str, enabled: bool = True):
        super().__init__(name, enabled)
        self.techs = techs
        self.requirement = requirement

    def check(self, text: str) -> bool:
        text_lower = text.lower()
        violation_phrases = [
            "pip install",
            "npm install",
            "gem install",
            "apt-get install",
            "brew install",
            "установите",
            "установка",
            "скачайте",
        ]
        for phrase in violation_phrases:
            if phrase in text_lower:
                return False
        return True

    def get_error_message(self) -> str:
        return f"Нарушение требования: {self.requirement}"

    def get_prompt_block(self) -> str:
        techs_str = ", ".join(self.techs)
        return f"⚠️ Инвариант: {self.name}\nТребование: {self.requirement}\nТехнологии: {techs_str}"


class AgentValidator:
    """Прогоняет ответ через все включённые инварианты."""

    def __init__(self, invariants: list[Invariant]):
        self.invariants = invariants

    def validate(self, response: str) -> Optional[str]:
        for inv in self.invariants:
            if not inv.enabled:
                continue
            if not inv.check(response):
                return inv.get_error_message()
        return None

    def get_prompt_blocks(self) -> str:
        blocks = []
        for inv in self.invariants:
            if inv.enabled:
                block = inv.get_prompt_block()
                if block:
                    blocks.append(block)
        return "\n\n".join(blocks)


class InvariantManager:
    """Управляет загрузкой/сохранением инвариантов из файлов."""

    @staticmethod
    def load_all(invariants_dir: Path) -> list[Invariant]:
        invariants = []
        if not invariants_dir.exists():
            return invariants
        for path in sorted(invariants_dir.glob("*.md")):
            inv = InvariantManager._load(path)
            if inv:
                invariants.append(inv)
        return invariants

    @staticmethod
    def _load(path: Path) -> Optional[Invariant]:
        content = path.read_text(encoding="utf-8")
        name = None
        inv_type = None
        enabled = True
        libraries = []
        techs = []
        requirement = ""
        message = ""

        current_section = None
        current_lines = []

        for line in content.split("\n"):
            if line.startswith("# Invariant: "):
                name = line[13:].strip()
                continue
            if line.startswith("type: "):
                inv_type = line[6:].strip()
                continue
            if line.startswith("enabled: "):
                enabled = line[9:].strip().lower() == "true"
                continue
            if line.startswith("## "):
                if current_section:
                    section_value = "\n".join(current_lines).strip()
                    if current_section == "libraries":
                        libraries = [l.strip() for l in section_value.split("\n") if l.strip()]
                    elif current_section == "techs":
                        techs = [t.strip() for t in section_value.split("\n") if t.strip()]
                    elif current_section == "requirement":
                        requirement = section_value
                    elif current_section == "message":
                        message = section_value
                current_section = line[3:].strip()
                current_lines = []
            elif current_section:
                current_lines.append(line)

        if current_section:
            section_value = "\n".join(current_lines).strip()
            if current_section == "libraries":
                libraries = [l.strip() for l in section_value.split("\n") if l.strip()]
            elif current_section == "techs":
                techs = [t.strip() for t in section_value.split("\n") if t.strip()]
            elif current_section == "requirement":
                requirement = section_value
            elif current_section == "message":
                message = section_value

        if not name or not inv_type:
            return None

        if inv_type == "forbidden_library":
            return ForbiddenLibrariesInvariant(name, libraries, enabled=enabled)
        elif inv_type == "required_tech":
            return RequiredTechStackInvariant(name, techs, requirement, enabled=enabled)

        return None

    @staticmethod
    def save(invariant: Invariant, invariants_dir: Path):
        invariants_dir.mkdir(parents=True, exist_ok=True)
        path = invariants_dir / f"{invariant.name}.md"

        if isinstance(invariant, ForbiddenLibrariesInvariant):
            lines = [
                f"# Invariant: {invariant.name}",
                "type: forbidden_library",
                f"enabled: {'true' if invariant.enabled else 'false'}",
                "",
                "## libraries",
            ]
            lines.extend(invariant.libraries)
            lines.extend([
                "",
                "## message",
                invariant.get_error_message(),
            ])
        elif isinstance(invariant, RequiredTechStackInvariant):
            lines = [
                f"# Invariant: {invariant.name}",
                "type: required_tech",
                f"enabled: {'true' if invariant.enabled else 'false'}",
                "",
                "## requirement",
                invariant.requirement,
                "",
                "## techs",
            ]
            lines.extend(invariant.techs)
            lines.extend([
                "",
                "## message",
                invariant.get_error_message(),
            ])
        else:
            return

        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def list_invariants(invariants_dir: Path) -> list[str]:
        if not invariants_dir.exists():
            return []
        names = []
        for path in sorted(invariants_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                if line.startswith("# Invariant: "):
                    names.append(line[13:].strip())
                    break
        return names
