"""
State Machine module for pipeline-based task processing.

Provides StageAgent (individual stage) and PipelineAgent (orchestrator)
for implementing a deterministic state machine workflow with isolated
histories, per-stage LLM configs, and auto/manual transition modes.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
import json
import sqlite3
import urllib.request
import urllib.error


class AgentState(Enum):
    """Допустимые этапы пайплайна задачи."""
    PLANNING = "PLANNING"
    EXECUTION = "EXECUTION"
    VALIDATION = "VALIDATION"
    DONE = "DONE"


ALLOWED_TRANSITIONS: Dict[AgentState, List[AgentState]] = {
    AgentState.PLANNING: [AgentState.EXECUTION],
    AgentState.EXECUTION: [AgentState.VALIDATION, AgentState.PLANNING],
    AgentState.VALIDATION: [AgentState.DONE, AgentState.EXECUTION],
    AgentState.DONE: [AgentState.PLANNING],
}

STAGE_SYSTEM_PROMPTS: Dict[AgentState, str] = {
    AgentState.PLANNING: (
        "Ты — агент планирования. Твоя задача — составить детальный план "
        "выполнения задачи пользователя. Определи шаги, необходимые ресурсы "
        "и ожидаемые результаты. Не переходи к выполнению."
    ),
    AgentState.EXECUTION: (
        "Ты — агент выполнения. Твоя задача — реализовать план, созданный "
        "на этапе планирования. Следуй плану и создавай требуемые артефакты "
        "(код, документацию, конфиги)."
    ),
    AgentState.VALIDATION: (
        "Ты — агент валидации. Проверь результаты выполнения на соответствие "
        "плану. Выяви ошибки, проблемы безопасности, несоответствия. "
        "Предложи конкретные исправления."
    ),
    AgentState.DONE: (
        "Ты — агент завершения. Подведи итог выполненной работы, опиши что "
        "было сделано на каждом этапе, зафиксируй результаты."
    ),
}

STAGE_DEFAULT_MODELS: Dict[AgentState, str] = {
    AgentState.PLANNING: "Qwen/Qwen3-30B-A3B",
    AgentState.EXECUTION: "Qwen/Qwen3-Coder-Next",
    AgentState.VALIDATION: "Qwen/Qwen3-30B-A3B",
    AgentState.DONE: "Qwen/Qwen3-30B-A3B",
}


def call_llm(
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    messages: list,
) -> dict:
    """Отправляет запрос к LLM API и возвращает ответ."""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
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
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            choice = result.get("choices", [{}])[0]
            message = choice.get("message", {})
            return {
                "success": True,
                "content": message.get("content", ""),
                "usage": result.get("usage", {}),
                "finish_reason": choice.get("finish_reason", "unknown"),
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "No error body"
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}", "details": error_body}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"URL Error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


class StageAgent:
    """
    Агент для работы с отдельным этапом пайплайна.

    Имеет изолированную историю сообщений и собственные параметры LLM
    (model, temperature, max_tokens). При повторном входе на этап
    через PipelineAgent история автоматически восстанавливается.
    """

    def __init__(
        self,
        stage: AgentState,
        api_key: str,
        base_url: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.stage = stage
        self.api_key = api_key
        self.base_url = base_url
        self.system_prompt = system_prompt or STAGE_SYSTEM_PROMPTS[stage]
        self.model = model or STAGE_DEFAULT_MODELS[stage]
        self.temperature = temperature if temperature is not None else 0.6
        self.max_tokens = max_tokens or 2500
        self.messages: List[dict] = []

    def chat(self, user_input: str, artifacts: Optional[dict] = None) -> str:
        """
        Отправляет запрос в LLM с учётом изолированной истории этапа.

        Args:
            user_input: запрос пользователя или системный триггер.
            artifacts: артефакты из других этапов (plan, code, validation).

        Returns:
            ответ модели.
        """
        system_content = self.system_prompt
        if artifacts:
            artifact_lines = ["\n\nКонтекст из других этапов:"]
            for key, value in artifacts.items():
                if value:
                    artifact_lines.append(f"\n[{key.upper()}]\n{value}")
            system_content += "\n".join(artifact_lines)

        messages = [{"role": "system", "content": system_content}]
        messages.extend(self.messages)
        messages.append({"role": "user", "content": user_input})

        response = call_llm(
            self.api_key, self.base_url,
            self.model, self.temperature, self.max_tokens,
            messages,
        )

        if response["success"]:
            content = response["content"]
            self.messages.append({"role": "user", "content": user_input})
            self.messages.append({"role": "assistant", "content": content})
            return content
        else:
            error_msg = (
                f"Ошибка [{self.stage.value}]: "
                f"{response.get('error', 'Неизвестная ошибка')}"
            )
            details = response.get("details", "")
            if details:
                error_msg += f"\n{details}"
            return error_msg

    def to_dict(self) -> dict:
        """Сериализует состояние StageAgent для сохранения."""
        return {
            "stage": self.stage.value,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": list(self.messages),
        }

    @classmethod
    def from_dict(cls, data: dict, api_key: str, base_url: str) -> "StageAgent":
        """Восстанавливает StageAgent из словаря."""
        agent = cls(
            stage=AgentState(data["stage"]),
            api_key=api_key,
            base_url=base_url,
            model=data.get("model"),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
        )
        agent.messages = data.get("messages", [])
        return agent


class PipelineAgent:
    """
    Оркестратор пайплайна из 4 StageAgent'ов.

    Отвечает за:
    - Маршрутизацию запросов текущему StageAgent'у.
    - Управление переходами между этапами (ALLOWED_TRANSITIONS).
    - Хранение и передачу артефактов между этапами.
    - Режим валидации: ручной (/step) или автоматический.
    """

    def __init__(
        self,
        session_id: int,
        api_key: str,
        base_url: str,
        db_path: str,
        current_state: str = "PLANNING",
        artifacts: Optional[dict] = None,
        validation_enabled: bool = True,
        stage_configs: Optional[dict] = None,
    ):
        self.session_id = session_id
        self.api_key = api_key
        self.base_url = base_url
        self.db_path = db_path
        self.validation_enabled = validation_enabled

        self.current_state = AgentState(current_state)
        self.artifacts: Dict[str, str] = artifacts or {}

        self.stage_agents: Dict[AgentState, StageAgent] = {}
        for state in AgentState:
            config = (stage_configs or {}).get(state.value, {})
            self.stage_agents[state] = StageAgent(
                stage=state,
                api_key=api_key,
                base_url=base_url,
                model=config.get("model"),
                temperature=config.get("temperature"),
                max_tokens=config.get("max_tokens"),
            )

        self._load_stage_messages()

    def _load_stage_messages(self):
        """Загружает историю сообщений каждого этапа из БД."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                for state in AgentState:
                    cursor = conn.execute(
                        "SELECT role, content FROM stage_messages "
                        "WHERE session_id = ? AND stage = ? ORDER BY timestamp ASC",
                        (self.session_id, state.value),
                    )
                    msgs = [{"role": r[0], "content": r[1]} for r in cursor.fetchall()]
                    self.stage_agents[state].messages = msgs
        except sqlite3.OperationalError:
            pass

    def _save_all_stage_messages(self):
        """Сохраняет историю сообщений всех этапов в БД."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM stage_messages WHERE session_id = ?",
                (self.session_id,),
            )
            for state, agent in self.stage_agents.items():
                for msg in agent.messages:
                    conn.execute(
                        "INSERT INTO stage_messages (session_id, stage, role, content) "
                        "VALUES (?, ?, ?, ?)",
                        (self.session_id, state.value, msg["role"], msg["content"]),
                    )
            conn.commit()

    def _get_current_agent(self) -> StageAgent:
        return self.stage_agents[self.current_state]

    def _save_artifact(self, state: AgentState, content: str):
        """Сохраняет артефакт для указанного этапа."""
        self.artifacts[state.value.lower()] = content

    def _get_next_state(self) -> Optional[AgentState]:
        """Возвращает следующее состояние по умолчанию (первое из разрешённых)."""
        allowed = ALLOWED_TRANSITIONS[self.current_state]
        return allowed[0] if allowed else None

    def transition_to(self, target: AgentState) -> str:
        """
        Выполняет переход в целевое состояние, если переход разрешён картой.

        Args:
            target: состояние для перехода.

        Returns:
            сообщение о результате перехода.
        """
        if target == self.current_state:
            return f"Вы уже на этапе {target.value}."

        allowed = ALLOWED_TRANSITIONS[self.current_state]
        if target not in allowed:
            allowed_names = ", ".join(s.value for s in allowed)
            return (
                f"Переход из {self.current_state.value} в {target.value} "
                f"запрещён. Допустимые: {allowed_names}"
            )

        self.current_state = target
        return f"Переход на этап {target.value} выполнен."

    def chat(self, user_input: str):
        """
        Принимает запрос, маршрутизирует текущему StageAgent'у, управляет переходами.

        Для команд возвращает str.
        Для обычного чата возвращает list[(AgentState, str)] — каждый этап отдельно.

        Args:
            user_input: сообщение пользователя или команда.
        """
        if user_input.startswith("/step"):
            return self._handle_step_command(user_input)
        if user_input.startswith("/artifact"):
            return self._handle_artifact_command()
        if user_input.startswith("/sm validate"):
            return self._handle_sm_validate_command(user_input)

        stages: list = []

        agent = self._get_current_agent()
        response = agent.chat(user_input, self.artifacts)
        self._save_artifact(self.current_state, response)
        stages.append((self.current_state, response))

        if not self.validation_enabled:
            progress_count = 0
            while self.current_state != AgentState.DONE and progress_count < 10:
                next_state = self._get_next_state()
                if not next_state:
                    break
                self.current_state = next_state
                next_agent = self._get_current_agent()
                auto_prompt = (
                    "[auto] Продолжите выполнение с учётом "
                    "результатов предыдущих этапов."
                )
                next_response = next_agent.chat(auto_prompt, self.artifacts)
                self._save_artifact(self.current_state, next_response)
                stages.append((self.current_state, next_response))
                progress_count += 1

        self._save_all_stage_messages()
        return stages

    def _handle_step_command(self, user_input: str) -> str:
        """Обрабатывает /step — просмотр и смену этапа."""
        parts = user_input.strip().split(maxsplit=1)
        if len(parts) == 1:
            return f"Текущий этап: {self.current_state.value}"

        target_name = parts[1].strip().upper()
        try:
            target = AgentState(target_name)
        except ValueError:
            valid = ", ".join(s.value for s in AgentState)
            return f"Неизвестный этап {target_name}. Допустимые: {valid}"

        return self.transition_to(target)

    def _handle_artifact_command(self) -> str:
        """Обрабатывает /artifact — просмотр артефактов."""
        if not self.artifacts:
            return "Артефакты отсутствуют."
        lines = ["Артефакты этапов:"]
        for key, value in self.artifacts.items():
            if value:
                preview = value[:200].replace("\n", "\\n")
                lines.append(f"  {key}: {preview}...")
        return "\n".join(lines)

    def _handle_sm_validate_command(self, user_input: str) -> str:
        """Обрабатывает /sm validate [on|off]."""
        parts = user_input.strip().split()
        if len(parts) < 3:
            return "Используйте: /sm validate [on|off]"
        val = parts[2].lower()
        if val in ("on", "вкл", "1"):
            self.validation_enabled = True
            return "Валидация включена."
        elif val in ("off", "выкл", "0"):
            self.validation_enabled = False
            return "Валидация выключена."
        return "Используйте: /sm validate [on|off]"

    def to_dict(self) -> dict:
        """Сериализует состояние PipelineAgent для сохранения в сессии."""
        return {
            "current_state": self.current_state.value,
            "artifacts": dict(self.artifacts),
            "validation_enabled": self.validation_enabled,
        }

    def load_dict(self, data: dict):
        """Восстанавливает состояние PipelineAgent из словаря."""
        self.current_state = AgentState(data.get("current_state", "PLANNING"))
        self.artifacts = data.get("artifacts", {})
        self.validation_enabled = data.get("validation_enabled", True)
