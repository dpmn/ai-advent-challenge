---
description: >
  Синхронизирует AGENTS.md (дерево проекта), progress.md, database-schema.md
  после завершения задания. Добавляет новые файлы в структуру
mode: subagent
permission:
  read: allow
  glob: allow
  grep: allow
  edit: allow
  bash: allow
---

## Что ты делаешь

После выполнения задания ты актуализируешь проектные документы.

## Порядок действий

1. Прочитай `docs/progress.md` — определи текущий статус.

2. Обнови `docs/progress.md`:
   - установи текущее задание как done
   - добавь новое todo на следующее задание (если применимо)

3. Прочитай README выполненного задания (week-XX/day-YY/README.md) — найди раздел с описанием изменённых файлов.

4. Обнови `AGENTS.md` — структура проекта (раздел "## Структура проекта"):
   - только **верхнеуровневые директории** с однострочным описанием
   - **не перечисляй файлы внутри директорий**
   - если появились новые корневые директории — добавь их
   - если директории были удалены — убери из дерева
   - поддерживай формат: `├─ dirname/    # краткое описание`
   - пример:
     ```
     ├─ agents/              # Ядро агента и его модули
     ├─ mcp_servers/         # MCP-серверы
     ```
   - готовый шаблон на основе состояния сейчас:
     ```
     /
     ├─ agents/              # Ядро агента и его модули
     ├─ mcp_servers/         # MCP-серверы (FastMCP, streamable-http)
     ├─ webui/               # Flask + SPA (vanilla JS), Claude-inspired theme + light/dark toggle
     ├─ docs/                # Документация, конспекты лекций, прогресс
     ├─ week-NN/             # Задания по дням
     └─ .opencode/
        ├─ agents/           # Субагенты opencode
        └─ skills/           # Модули знаний opencode
     ```
   - **НЕ используй `rtk tree` или другие рекурсивные обходы** — только ручное добавление/удаление верхнеуровневых записей

5. Обнови `docs/database-schema.md` если были изменения в БД:
   - проверь `agents/memory/jarvis_history.db` — если изменилась схема
   - проверь `agents/jarvis.py` на новые таблицы/колонки

6. Проверь актуальность Skills в `.opencode/skills/`:

   | Если менялись | Проверь skill |
   |---|---|
   | `agents/jarvis.py` или `agents/state_machine.py` или `agents/invariants.py` | `.opencode/skills/backend-rules/SKILL.md` |
   | `webui/app.py` или `webui/static/script.js` или `webui/static/style.css` или `webui/templates/index.html` | `.opencode/skills/webui-rules/SKILL.md` |

   Для каждого затронутого skill:
   - прочитай его SKILL.md
   - прочитай изменённые файлы
   - если появились новые классы/методы/роуты/компоненты которых нет в SKILL.md — добавь их
   - если изменилась сигнатура или поведение — обнови описание
   - удали упоминания удалённых сущностей

Без комментариев. Только фактические изменения.
