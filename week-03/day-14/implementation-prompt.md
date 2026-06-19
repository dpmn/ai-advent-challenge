# Промпт для реализации инвариантов (день 14)

## Задача

Реализовать систему инвариантов для JarvisAgent: жёсткие ограничения, которые агент не может нарушать. Хранение — файлы + БД. Механизм — гибрид (инжекция в system prompt + кодовая валидация с retry). Без интеграции в State Machine (только обычный чат).

## 1. Создать файл `agents/invariants.py`

Модуль с тремя классами:

**a) `Invariant` (ABC)**
- `check(text: str) -> bool` — возвращает `True` если нарушений нет
- `get_error_message() -> str` — сообщение об ошибке для retry
- `get_prompt_block() -> str` — текстовый блок для инжекции в system prompt
- `name: str` — название инварианта
- `enabled: bool` — флаг активности

**b) `ForbiddenLibrariesInvariant`**
- Конструктор: `__init__(self, name: str, libraries: list[str])`
- `check(text)` — проверяет, не содержит ли ответ упоминания запрещённых библиотек (поиск подстроки, регистронезависимый)
- `get_error_message()` — возвращает: `"Ошибка: использование библиотек {libs} запрещено! Переделай ответ без них."`
- `get_prompt_block()` — возвращает: `"⚠️ Инвариант: {name}\nЗапрещённые библиотеки: {libraries}"`

**c) `RequiredTechStackInvariant`**
- Конструктор: `__init__(self, name: str, techs: list[str], requirement: str)` — где `requirement` — описание требования (например "проект должен использовать чистый Python без сторонних зависимостей")
- `check(text)` — проверяет, что текст не нарушает требование (regex- или LLM-проверка). Для простоты: проверяет, что текст не предлагает явного нарушения (например, не содержит фраз вроде "установите pandas", "pip install" если требование — без сторонних библиотек). Допускается простая текстовая проверка.
- `get_error_message()` — возвращает описание нарушения
- `get_prompt_block()` — возвращает требование для инжекции

**d) `AgentValidator`**
- `__init__(self, invariants: list[Invariant])`
- `validate(response: str) -> Optional[str]` — прогоняет ответ через все enabled-инварианты, возвращает первую ошибку или `None`
- `get_prompt_blocks() -> str` — собирает `get_prompt_block()` всех enabled-инвариантов в один блок

**e) `InvariantManager`**
- Управляет загрузкой/сохранением инвариантов из файлов
- Метод `load_all(invariants_dir: Path) -> list[Invariant]` — загружает все `.md` файлы из директории
- Метод `save(invariant: Invariant, invariants_dir: Path)` — сохраняет в `.md` файл
- Метод `list_invariants(invariants_dir: Path) -> list[str]` — список имён загруженных инвариантов

**Формат файла инварианта** (`agents/memory/invariants/<name>.md`):

```markdown
# Invariant: <name>
type: forbidden_library | required_tech
enabled: true

## libraries
numpy
pandas
requests

## message
Запрещённые библиотеки: numpy, pandas, requests
```

или для required_tech:

```markdown
# Invariant: <name>
type: required_tech
enabled: true

## requirement
Проект должен использовать чистый Python без сторонних зависимостей

## techs
pure-python

## message
Требование: чистый Python без сторонних библиотек
```

Парсинг аналогичен `Profile._load()` — по `## section`.

## 2. Дополнить `agents/jarvis.py`

**a) Импорт**

```python
from agents.invariants import InvariantManager, AgentValidator, ForbiddenLibrariesInvariant, RequiredTechStackInvariant
```

**b) Константа**

```python
_INVARIANTS_DIR = _AGENTS_DIR / "memory" / "invariants"
```

**c) DB-миграция** (в `_init_db()`)

Добавить колонки после SM-миграций:

```python
for inv_col in [
    ("invariants_enabled", "INTEGER DEFAULT 1"),
    ("invariants_config", "TEXT DEFAULT '{}'"),
]:
    try:
        conn.execute(f"ALTER TABLE sessions ADD COLUMN {inv_col[0]} {inv_col[1]}")
    except sqlite3.OperationalError:
        pass
```

`invariants_config` хранит JSON вида `{"enabled_ids": ["libs", "techs"]}` — какие инварианты активны в этой сессии.

**d) В `__init__()` после инициализации профиля (~строка 225)**

Добавить загрузку инвариантов:

```python
self._invariants_dir = _INVARIANTS_DIR
self._invariants_dir.mkdir(parents=True, exist_ok=True)
self._invariants: list = []
self._validator: Optional[AgentValidator] = None
self.invariants_enabled = bool(self.current_session.get("invariants_enabled", True))
self._load_invariants()
```

**e) Метод `_load_invariants()`**

```python
def _load_invariants(self):
    all_invariants = InvariantManager.load_all(self._invariants_dir)
    config_raw = self.current_session.get("invariants_config", "{}")
    try:
        config = json.loads(config_raw) if config_raw else {}
    except (json.JSONDecodeError, TypeError):
        config = {}
    enabled_ids = config.get("enabled_ids", [])
    if enabled_ids:
        for inv in all_invariants:
            inv.enabled = inv.name in enabled_ids
    self._invariants = all_invariants
    self._validator = AgentValidator(self._invariants) if self.invariants_enabled else None
```

**f) Метод `_save_invariants_state()`**

```python
def _save_invariants_state(self):
    enabled_ids = [inv.name for inv in self._invariants if inv.enabled]
    config = json.dumps({"enabled_ids": enabled_ids}, ensure_ascii=False)
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            "UPDATE sessions SET invariants_enabled = ?, invariants_config = ? WHERE id = ?",
            (int(self.invariants_enabled), config, self.current_session["id"])
        )
        conn.commit()
    self.current_session["invariants_enabled"] = int(self.invariants_enabled)
    self.current_session["invariants_config"] = config
```

**g) Инжекция в system prompt** (в `chat()`, в блок `memory_blocks`, после profile_block и task_block, перед `if memory_blocks:`)

```python
if self.invariants_enabled and self._validator:
    inv_block = self._validator.get_prompt_blocks()
    if inv_block:
        memory_blocks.append(inv_block)
```

**h) Валидация ответа с retry** (в `chat()`, после `response["content"]`, перед сохранением в историю)

Заменить:

```python
if response["success"]:
    assistant_message = response["content"]
    self.conversation_history.append({"role": "assistant", "content": assistant_message})
    self._save_message("assistant", assistant_message)
```

На:

```python
if response["success"]:
    assistant_message = response["content"]

    # Валидация инвариантов (с retry)
    if self.invariants_enabled and self._validator:
        max_retries = 2
        for attempt in range(max_retries + 1):
            violation = self._validator.validate(assistant_message)
            if not violation:
                break
            if attempt < max_retries:
                retry_messages = messages.copy()
                retry_messages.append({"role": "user", "content": assistant_message})
                retry_messages.append({
                    "role": "system",
                    "content": f"⚠️ Нарушение инварианта: {violation}\nИсправь ответ."
                })
                retry_response = self._call_api(retry_messages)
                if retry_response["success"]:
                    assistant_message = retry_response["content"]
                else:
                    break
        else:
            assistant_message += (
                f"\n\n⚠️ Инвариант нарушен. Не удалось исправить: {violation}"
            )

    self.conversation_history.append({"role": "assistant", "content": assistant_message})
    self._save_message("assistant", assistant_message)
```

**i) Команды `/invariant` и `/invariants`** (добавить в `_handle_command`, перед `return f"❌ Неизвестная команда"`)

```python
if cmd == "/invariant":
    if not arg:
        if not self._invariants:
            return "Инварианты не загружены."
        lines = ["⚠️ Инварианты:"]
        for inv in self._invariants:
            status = "✅" if inv.enabled else "❌"
            lines.append(f"  {status} {inv.name}")
        return "\n".join(lines)

    parts = arg.strip().split(maxsplit=2)
    sub = parts[0].lower()

    if sub == "toggle" and len(parts) >= 2:
        name = parts[1]
        for inv in self._invariants:
            if inv.name == name:
                inv.enabled = not inv.enabled
                self._validator = AgentValidator(self._invariants) if self.invariants_enabled else None
                self._save_invariants_state()
                status = "включён" if inv.enabled else "выключен"
                return f"✅ Инвариант '{name}' {status}."
        return f"❌ Инвариант '{name}' не найден."

    elif sub == "show" and len(parts) >= 2:
        name = parts[1]
        for inv in self._invariants:
            if inv.name == name:
                return inv.get_prompt_block()
        return f"❌ Инвариант '{name}' не найден."

    elif sub == "on":
        self.invariants_enabled = True
        self._validator = AgentValidator(self._invariants)
        self._save_invariants_state()
        return "✅ Проверка инвариантов включена."

    elif sub == "off":
        self.invariants_enabled = False
        self._validator = None
        self._save_invariants_state()
        return "✅ Проверка инвариантов выключена."

    return "❌ Используйте: /invariant, /invariant toggle <name>, /invariant show <name>, /invariant on, /invariant off"

if cmd == "/invariants":
    return self._handle_command("/invariant")
```

**j) Обновить `/help`** — добавить строки:

```python
"  /invariant [on|off] — вкл/выкл проверку инвариантов\n"
"  /invariant toggle <name> — вкл/выкл конкретный инвариант\n"
"  /invariant show <name> — показать инвариант\n"
"  /invariants — список инвариантов\n"
```

**k) Обновить `create_session()`** — добавить в возвращаемый словарь:

```python
"invariants_enabled": 1,
"invariants_config": "{}",
```

**l) Обновить `switch_session()`** — после загрузки профиля (после `self.profile = Profile(profile_name)`) вызвать:

```python
self._load_invariants()
```

**m) Обновить `_load_session()` и `_get_last_session()`** — добавить выборку полей `invariants_enabled` и `invariants_config` в SQL и маппинг результата (аналогично существующим полям).

**n) В `reset_conversation()`** — сбросить инварианты:

```python
self._load_invariants()
```

## 3. Обновить `webui/app.py`

**a) GET `/api/settings`** — добавить в возвращаемый JSON:

```python
"invariants_enabled": agent.invariants_enabled,
"invariants": [
    {"name": inv.name, "enabled": inv.enabled}
    for inv in agent._invariants
],
```

**b) POST `/api/settings`** — добавить поддержку:

```python
if "invariants_enabled" in data:
    val = bool(data["invariants_enabled"])
    if val:
        agent._handle_command("/invariant on")
    else:
        agent._handle_command("/invariant off")
```

## 4. Создать тестовые файлы инвариантов

**a) `agents/memory/invariants/no-external-libs.md`**

```markdown
# Invariant: no-external-libs
type: required_tech
enabled: true

## requirement
Используй только стандартную библиотеку Python. Никаких сторонних библиотек (numpy, pandas, requests, flask, django и т.д.)

## techs
stdlib-only

## message
Требование: только стандартная библиотека Python, без сторонних зависимостей
```

**b) `agents/memory/invariants/no-numpy-pandas.md`**

```markdown
# Invariant: no-numpy-pandas
type: forbidden_library
enabled: false

## libraries
numpy
pandas

## message
Запрещённые библиотеки: numpy, pandas
```

## 5. Обновить `docs/database-schema.md`

Добавить описание новых колонок:
- `sessions.invariants_enabled` (INTEGER DEFAULT 1) — глобальный флаг проверки
- `sessions.invariants_config` (TEXT DEFAULT '{}') — JSON с enabled_ids

## 6. Проверка

Ничего не ломать. Проверить:
- `python webui/app.py` — запускается без ошибок
- Обычный чат без инвариантов работает как раньше
- При `invariants_enabled = 0` — поведение не меняется
- При включённых инвариантах — учитываются в промпте и валидируются
