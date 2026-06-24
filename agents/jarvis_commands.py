from agents.invariants import AgentValidator
from agents.jarvis_memory import Profile


class CommandMixin:
    """Mixin-класс для обработки команд (/help, /stats, /session и т.д.)."""

    def _handle_command(self, cmd_input: str) -> str:
        parts = cmd_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd == "/help":
            return (
                "📋 Доступные команды:\n\n"
                "  /help         — показать этот список\n"
                "  /stats        — статистика агента\n"
                "  /sessions     — список сессий\n"
                "  /session <id> — переключиться на сессию\n"
                "  /new [name]   — создать новую сессию (/new sm — с SM)\n"
                "  /clear        — очистить историю\n"
                "  /model [name] — показать/сменить модель\n"
                "  /temp [value] — показать/сменить температуру\n"
                "  /strategy [type] — показать/сменить стратегию\n"
                "  /compression [on|off|toggle] — управление сжатием\n"
                "  /context      — информация о контексте\n"
                "  /checkpoint   — сохранить checkpoint (branching)\n"
                "  /branch <name> — создать ветку\n"
                "  /branches     — список веток\n"
                "  /switch <id>  — переключить ветку\n"
                "  /sm           — статус State Machine\n"
                "  /sm validate [on|off] — вкл/выкл валидацию\n"
                "  /step [STAGE] — показать/сменить этап SM\n"
                "  /artifact     — артефакты этапов SM\n"
                "  /task [key value] — показать/задать рабочую память\n"
                "  /task clear   — очистить рабочую память\n"
                "  /profile [name] — показать/сменить профиль\n"
                "  /profile set <k> <v> — задать поле профиля\n"
                "  /profile new <name> — создать новый профиль\n"
                "  /profiles     — список доступных профилей\n"
                "  /invariant [on|off] — вкл/выкл проверку инвариантов\n"
                "  /invariant toggle <name> — вкл/выкл конкретный инвариант\n"
                "  /invariant show <name> — показать инвариант\n"
                "  /invariants — список инвариантов\n"
                "  /mcp          — статус MCP-серверов\n"
                "  /mcp on|off   — вкл/выкл tool-calling через MCP\n"
                "  /mcp connect <name> — подключить сервер\n"
                "  /mcp disconnect <name> — отключить сервер\n"
                "  /mcp add <name> <url> [transport] — добавить сервер\n"
                "  /mcp remove <name> — удалить сервер\n"
                "  /mcp tools    — список инструментов"
            )

        if cmd == "/stats":
            return self.get_stats()

        if cmd == "/sessions":
            sessions = self.list_sessions()
            if not sessions:
                return "Нет сессий."
            lines = ["📂 Сессии:"]
            for s in sessions:
                marker = " 👈" if s["id"] == self.current_session["id"] else ""
                strategy = f" | {s['context_strategy']}" if s.get("context_strategy") else ""
                lines.append(f"  ID {s['id']}: {s['name']}{marker}{strategy}")
            return "\n".join(lines)

        if cmd == "/session":
            if not arg:
                return f"Текущая сессия: {self.current_session['name']} (ID: {self.current_session['id']})"
            try:
                sid = int(arg.strip())
                if self.switch_session(sid):
                    return f"✅ Переключено на сессию: {self.current_session['name']}"
                return f"❌ Сессия с ID {sid} не найдена."
            except ValueError:
                return "❌ Укажите числовой ID сессии."

        if cmd == "/new":
            sm_enabled = False
            if arg and arg.strip().lower().startswith("sm "):
                sm_enabled = True
                sm_name = arg.strip()[3:].strip()
                self.create_session(sm_name if sm_name else None, sm_enabled=True)
            elif arg and arg.strip().lower() == "sm":
                self.create_session(None, sm_enabled=True)
            else:
                name = arg.strip() if arg else None
                self.create_session(name)
            tag = " [SM]" if sm_enabled else ""
            return f"✅ Создана новая сессия: {self.current_session['name']}{tag}"

        if cmd == "/clear":
            self.reset_conversation()
            return "✅ История диалога очищена."

        if cmd == "/model":
            if not arg:
                return f"Текущая модель: {self.model}"
            new_model = arg.strip()
            self.model = new_model
            return f"✅ Модель изменена: {new_model}"

        if cmd == "/temp":
            if not arg:
                return f"Текущая температура: {self.temperature}"
            try:
                val = float(arg.strip())
                if 0 <= val <= 2:
                    self.temperature = val
                    return f"✅ Температура изменена: {val}"
                return "❌ Температура должна быть от 0 до 2."
            except ValueError:
                return "❌ Укажите числовое значение (0.0 – 2.0)."

        if cmd == "/strategy":
            if not arg:
                cur = self.context_strategy or "выкл"
                return f"Текущая стратегия: {cur}"
            strategy_map = {
                "off": None, "выкл": None,
                "sliding_window": "sliding_window", "sliding": "sliding_window",
                "sticky_facts": "sticky_facts", "sticky": "sticky_facts",
                "branching": "branching", "branch": "branching",
            }
            arg_lower = arg.strip().lower()
            if arg_lower in strategy_map:
                return self.set_strategy(strategy_map[arg_lower])
            return "❌ Допустимые стратегии: off, sliding_window, sticky_facts, branching"

        if cmd == "/compression":
            if not arg:
                status = "вкл" if self.compression_enabled else "выкл"
                return f"Сжатие истории: {status}"
            arg_lower = arg.strip().lower()
            if arg_lower in ("on", "вкл", "1"):
                self.enable_compression()
                return "✅ Сжатие истории включено"
            if arg_lower in ("off", "выкл", "0"):
                self.disable_compression()
                return "✅ Сжатие истории выключено"
            if arg_lower in ("toggle", "switch"):
                self.toggle_compression()
                status = "вкл" if self.compression_enabled else "выкл"
                return f"✅ Сжатие истории: {status}"
            return "❌ Используйте: /compression [on|off|toggle]"

        if cmd == "/context":
            lines = ["📐 Контекст диалога:"]
            total = self.session_prompt_tokens + self.session_completion_tokens
            pct = round(total / self.context_limit * 100, 1) if self.context_limit > 0 else 0
            lines.append(f"  Токенов в сессии: {total} / {self.context_limit} ({pct}%)")
            non_system = [m for m in self.conversation_history if m["role"] != "system"]
            lines.append(f"  Сообщений: {len(non_system)}")
            lines.append(f"  Модель: {self.model}")
            lines.append(f"  Стратегия: {self.context_strategy or 'выкл'}")
            lines.append(f"  Сжатие: {'вкл' if self.compression_enabled else 'выкл'}")
            if self.context_strategy == "sliding_window":
                lines.append("  Окно: последние 5 сообщений")
            elif self.context_strategy == "sticky_facts":
                lines.append(f"  Фактов: {len(self.sticky_facts)}")
            elif self.context_strategy == "branching":
                lines.append(f"  Веток: {len(self.branches)}")
                cur = self.branches.get(self.current_branch_id, {})
                lines.append(f"  Текущая ветка: '{cur.get('name', '?')}'")
            if self.pipeline:
                lines.append("")
                lines.append("🤖 State Machine:")
                lines.append(f"  Этап: {self.pipeline.current_state.value}")
                lines.append(f"  Валидация: {'вкл' if self.pipeline.validation_enabled else 'выкл'}")
                lines.append(f"  Артефактов: {len(self.pipeline.artifacts)}")
            lines.append("")
            lines.append("🧠 Модель памяти:")
            tc = self.task_context.to_dict()
            lines.append(f"  Working (TaskContext): {len(tc)} полей")
            lines.append(f"  Long-term (Profile): '{self.profile.profile_name}' — {len(self.profile._data)} полей")
            return "\n".join(lines)

        if cmd == "/checkpoint":
            return self.save_checkpoint()

        if cmd == "/branch":
            if not arg:
                return "❌ Укажите имя ветки: /branch <name>"
            return self.create_branch(arg.strip())

        if cmd == "/branches":
            branches = self.list_branches()
            if not branches:
                return "Ветвление не активно. Используйте: /strategy branching"
            lines = ["🌿 Ветки:"]
            for b in branches:
                lines.append(f"  ID {b['id']}: '{b['name']}' — {b['messages']} сообщений{b['current']}")
            return "\n".join(lines)

        if cmd == "/switch":
            if not arg:
                return "❌ Укажите ID ветки: /switch <id>"
            try:
                bid = int(arg.strip())
                return self.switch_branch(bid)
            except ValueError:
                return "❌ Укажите числовой ID ветки."

        # ── State Machine ────────────────────────────────────

        if cmd == "/sm":
            if not self.pipeline:
                return (
                    "State Machine не активен.\n"
                    "Создайте SM-сессию: /new sm [name]"
                )
            parts = cmd_input.strip().split(maxsplit=2)
            if len(parts) == 1:
                val_status = "вкл" if self.pipeline.validation_enabled else "выкл"
                return (
                    f"🤖 State Machine:\n"
                    f"  Этап: {self.pipeline.current_state.value}\n"
                    f"  Валидация: {val_status}\n"
                    f"  Артефактов: {len(self.pipeline.artifacts)}\n"
                    f"Команды: /sm validate [on|off], /step, /artifact"
                )
            if parts[1] == "validate" and len(parts) == 3:
                resp = self.pipeline._handle_sm_validate_command(cmd_input)
                self._save_sm_state()
                return resp
            return "Используйте: /sm, /sm validate [on|off]"

        if cmd == "/step":
            if not self.pipeline:
                return "State Machine не активен. Создайте SM-сессию: /new sm [name]"
            resp = self.pipeline._handle_step_command(cmd_input)
            self._save_sm_state()
            self._save_message("command", resp)
            return resp

        if cmd == "/artifact":
            if not self.pipeline:
                return "State Machine не активен."
            return self.pipeline._handle_artifact_command()

        # ── Рабочая память (TaskContext) ─────────────────────

        if cmd == "/task":
            if not arg:
                data = self.task_context.to_dict()
                if not data:
                    return "📋 Рабочая память пуста."
                lines = ["📋 Рабочая память (TaskContext):"]
                for k, v in data.items():
                    lines.append(f"  \u2022 {k}: {v}")
                return "\n".join(lines)
            parts = arg.strip().split(maxsplit=1)
            sub = parts[0].lower()
            if sub == "clear":
                self.task_context.clear()
                self._save_memory_state()
                return "✅ Рабочая память очищена."
            if len(parts) < 2:
                return "❌ Используйте: /task <key> <value> или /task clear"
            self.task_context.set(parts[0], parts[1])
            self._save_memory_state()
            return f"✅ Задано: {parts[0]} = {parts[1]}"

        # ── Долговременная память (Profile) ──────────────────

        if cmd == "/profile":
            if not arg:
                data = self.profile._data
                if not data:
                    return "👤 Профиль пуст. Используйте /profile set <key> <value>"
                lines = [f"👤 Профиль: {self.profile.profile_name}"]
                for k, v in data.items():
                    first = v.split("\n")[0][:100] if v else ""
                    lines.append(f"  \u2022 {k}: {first}")
                return "\n".join(lines)
            parts = arg.strip().split(maxsplit=2)
            sub = parts[0].lower()
            if sub == "set" and len(parts) >= 3:
                self.profile.set(parts[1], parts[2])
                self._save_memory_state()
                return f"✅ Профиль обновлён: {parts[1]} = {parts[2]}"
            elif sub == "set" and len(parts) < 3:
                return "❌ Используйте: /profile set <key> <value>"
            elif sub == "new" and len(parts) >= 2:
                name = parts[1]
                profiles = self.profile.list_profiles()
                if name in profiles:
                    return f"❌ Профиль '{name}' уже существует."
                self.profile = Profile(name)
                self._save_memory_state()
                return f"✅ Создан и активирован профиль: {name}"
            else:
                name = parts[0]
                profiles = self.profile.list_profiles()
                if name not in profiles:
                    return f"❌ Профиль '{name}' не найден. Доступны: {', '.join(profiles)}"
                self.profile = Profile(name)
                self._save_memory_state()
                return f"✅ Переключено на профиль: {name}"

        if cmd == "/profiles":
            profiles = self.profile.list_profiles()
            current = self.profile.profile_name
            lines = ["📂 Доступные профили:"]
            for p in profiles:
                marker = " 👈" if p == current else ""
                lines.append(f"  \u2022 {p}{marker}")
            return "\n".join(lines)

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

        # ── MCP ───────────────────────────────────────────────
        if cmd == "/mcp":
            if not arg:
                status = "вкл" if self.mcp_enabled else "выкл"
                servers = self.mcp_manager.list_servers()
                lines = [f"🔌 MCP: {status}"]
                if not servers:
                    lines.append("  Серверы не настроены. Используйте /mcp add <name> <url>")
                else:
                    for s in servers:
                        mark = "🟢" if s["connected"] else "⚪"
                        tools = f" ({s['tools_count']} tools)" if s["connected"] else ""
                        lines.append(f"  {mark} {s['name']} [{s['transport']}] {s['url']}{tools}")
                lines.append("")
                lines.append("Команды: /mcp connect <name>, /mcp disconnect <name>,")
                lines.append("         /mcp add <name> <url> [http], /mcp remove <name>, /mcp tools,")
                lines.append("         /mcp on|off (явное управление)")
                return "\n".join(lines)

            parts = arg.strip().split()
            sub = parts[0].lower()

            if sub == "on":
                self.mcp_enabled = True
                self._save_mcp_state()
                results = self.mcp_manager.connect_all_enabled()
                lines = ["✅ MCP включён."]
                for name, status in results.items():
                    lines.append(f"  \u2022 {name}: {status}")
                return "\n".join(lines) if results else "✅ MCP включён. Нет серверов для подключения."

            if sub == "off":
                self.mcp_enabled = False
                self._save_mcp_state()
                self.mcp_manager.disconnect_all()
                return "✅ MCP выключен. Серверы отключены."

            if sub == "connect" and len(parts) >= 2:
                name = parts[1]
                try:
                    info = self.mcp_manager.connect_server(name)
                    self.mcp_enabled = True
                    self._save_mcp_state()
                    return f"✅ '{name}' подключён. Инструментов: {info['tools_count']}."
                except Exception as e:
                    return f"❌ Не удалось подключить '{name}': {e}"

            if sub == "disconnect" and len(parts) >= 2:
                name = parts[1]
                if self.mcp_manager.disconnect_server(name):
                    if not self.mcp_manager.has_active_tools():
                        self.mcp_enabled = False
                        self._save_mcp_state()
                        return f"✅ '{name}' отключён. MCP автоматически выключен — нет активных серверов."
                    return f"✅ '{name}' отключён."
                return f"❌ '{name}' не был подключён."

            if sub == "add" and len(parts) >= 3:
                name = parts[1]
                url = parts[2]
                transport = parts[3] if len(parts) >= 4 else "http"
                entry = self.mcp_manager.add_server(name, transport, url)
                return f"✅ Сервер '{entry['name']}' добавлен ({entry['transport']} → {entry['url']})."

            if sub == "remove" and len(parts) >= 2:
                name = parts[1]
                if self.mcp_manager.remove_server(name):
                    return f"✅ Сервер '{name}' удалён."
                return f"❌ Сервер '{name}' не найден."

            if sub == "tools":
                tools = self.mcp_manager.get_openai_tools()
                if not tools:
                    return "Нет активных инструментов. Подключите сервер через /mcp connect <name>."
                lines = [f"🛠 Доступные инструменты ({len(tools)}):"]
                for t in tools:
                    fn = t["function"]
                    desc = (fn.get("description") or "").split("\n")[0][:80]
                    lines.append(f"  \u2022 {fn['name']}: {desc}")
                return "\n".join(lines)

            return ("❌ Используйте: /mcp, /mcp on|off, /mcp connect <name>, "
                    "/mcp disconnect <name>, /mcp add <name> <url> [transport], "
                    "/mcp remove <name>, /mcp tools")

        return f"❌ Неизвестная команда: {cmd}. Используйте /help для списка команд."
