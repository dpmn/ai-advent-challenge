"""
Менеджер MCP-серверов для Jarvis.

Подключается к MCP-серверам по протоколу JSON-RPC 2.0 поверх Streamable HTTP.
Конвертирует MCP-инструменты в OpenAI tool-calling формат, выполняет вызовы
инструментов синхронно (через urllib), парсит ответы (включая SSE).

Конфиг серверов хранится в agents/mcp/servers.json.
"""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Any

_AGENTS_DIR = Path(__file__).parent.resolve()
_DEFAULT_CONFIG_PATH = _AGENTS_DIR / "mcp" / "servers.json"

# Версия MCP-протокола, которую запрашивает клиент.
MCP_PROTOCOL_VERSION = "2025-06-18"


def _parse_response(body: str) -> dict:
    """Парсит ответ MCP-сервера. Поддерживает application/json и text/event-stream."""
    body = body.strip()
    if not body:
        return {}
    # SSE: ищем строку "data: {...}"
    if body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        return {}
    # Чистый JSON
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


class McpConnection:
    """Одно активное соединение с MCP-сервером (HTTP-транспорт)."""

    def __init__(self, name: str, url: str, headers: Optional[dict] = None, timeout: int = 30):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.server_info: dict = {}
        self.tools: list[dict] = []  # raw MCP-схемы инструментов
        self._req_id = 0
        self.connected = False

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict, expect_response: bool = True) -> dict:
        """Отправляет JSON-RPC запрос. Возвращает распарсенный response (или {} для нотификаций)."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.headers)
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Перехватываем session-id (регистр заголовков может отличаться)
                sid = resp.headers.get("MCP-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                body = resp.read().decode("utf-8")
                if not expect_response:
                    return {}
                return _parse_response(body)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(f"MCP HTTP {e.code} {e.reason}: {err_body[:300]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"MCP connection error: {e.reason}")

    def connect(self) -> dict:
        """Выполняет MCP handshake: initialize → notifications/initialized → tools/list."""
        # 1. initialize
        init_resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "1.0"},
            },
        })
        if "error" in init_resp:
            raise RuntimeError(f"initialize failed: {init_resp['error']}")
        self.server_info = init_resp.get("result", {}).get("serverInfo", {})

        # 2. notifications/initialized (без id, без ожидания тела)
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        except RuntimeError:
            # Некоторые серверы могут вернуть 202/пустоту — это нормально
            pass

        # 3. tools/list
        self.tools = self._fetch_tools()
        self.connected = True
        return {"server_info": self.server_info, "tools_count": len(self.tools)}

    def _fetch_tools(self) -> list[dict]:
        """Запрашивает у сервера список инструментов (с пагинацией)."""
        all_tools: list[dict] = []
        cursor: Optional[str] = None
        for _ in range(20):  # safety: не более 20 страниц
            params: dict = {}
            if cursor:
                params["cursor"] = cursor
            resp = self._post({
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": params,
            })
            result = resp.get("result", {})
            all_tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return all_tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Вызывает инструмент на сервере, возвращает текстовый результат."""
        if not self.connected:
            raise RuntimeError(f"MCP server '{self.name}' not connected")
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        if "error" in resp:
            err = resp["error"]
            return f"[MCP error] {err.get('code')}: {err.get('message', 'unknown')}"

        result = resp.get("result", {})
        # MCP вернёт content: [{type: 'text', text: '...'}, ...]
        contents = result.get("content", [])
        is_error = result.get("isError", False)
        parts: list[str] = []
        for item in contents:
            t = item.get("type", "")
            if t == "text":
                parts.append(item.get("text", ""))
            else:
                # Другие типы (image, resource) — просто сериализуем
                parts.append(json.dumps(item, ensure_ascii=False))
        text = "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
        return ("[tool error] " + text) if is_error else text

    def disconnect(self):
        """Помечает соединение как закрытое (HTTP — stateless, активного сокета нет)."""
        self.connected = False
        self.session_id = None
        self.tools = []


# ─────────────────────── Менеджер серверов ──────────────────────────


class McpServerManager:
    """
    Управляет набором MCP-серверов: загрузка конфига, подключение,
    выдача OpenAI-совместимых tool-схем, маршрутизация вызовов инструментов.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.connections: dict[str, McpConnection] = {}
        self._tool_routes: dict[str, str] = {}  # safe_tool_name -> server_name
        self._tool_real_names: dict[str, str] = {}  # safe -> original MCP name
        self.config: dict = {"servers": []}
        self.load_config()

    # ── Конфиг ──────────────────────────────────────────────────────

    def load_config(self) -> dict:
        """Загружает servers.json. Если файла нет — создаёт пустой конфиг."""
        if not self.config_path.exists():
            self.config = {"servers": []}
            self.save_config()
        else:
            try:
                self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.config = {"servers": []}
        if "servers" not in self.config:
            self.config["servers"] = []
        return self.config

    def save_config(self):
        """Сохраняет текущий конфиг в JSON-файл."""
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_servers(self) -> list[dict]:
        """Возвращает список серверов с признаком connected."""
        result = []
        for s in self.config["servers"]:
            entry = dict(s)
            entry["connected"] = s["name"] in self.connections and self.connections[s["name"]].connected
            entry["tools_count"] = len(self.connections[s["name"]].tools) if entry["connected"] else 0
            result.append(entry)
        return result

    def add_server(self, name: str, transport: str, url: str,
                   headers: Optional[dict] = None, enabled: bool = True) -> dict:
        """Добавляет новый сервер в конфиг (или обновляет существующий)."""
        if not name or not url:
            raise ValueError("name и url обязательны")
        for s in self.config["servers"]:
            if s["name"] == name:
                s.update({"transport": transport, "url": url,
                          "headers": headers or {}, "enabled": enabled})
                self.save_config()
                return s
        entry = {"name": name, "transport": transport, "url": url,
                 "headers": headers or {}, "enabled": enabled}
        self.config["servers"].append(entry)
        self.save_config()
        return entry

    def remove_server(self, name: str) -> bool:
        """Удаляет сервер из конфига и отключает его."""
        before = len(self.config["servers"])
        self.config["servers"] = [s for s in self.config["servers"] if s["name"] != name]
        if len(self.config["servers"]) < before:
            self.disconnect_server(name)
            self.save_config()
            return True
        return False

    def _get_server_config(self, name: str) -> Optional[dict]:
        for s in self.config["servers"]:
            if s["name"] == name:
                return s
        return None

    # ── Подключение ─────────────────────────────────────────────────

    def connect_server(self, name: str) -> dict:
        """Подключается к одному серверу, возвращает {server_info, tools_count}."""
        cfg = self._get_server_config(name)
        if not cfg:
            raise ValueError(f"Сервер '{name}' не найден в конфиге")
        transport = cfg.get("transport", "http")
        if transport != "http":
            raise NotImplementedError(f"Транспорт '{transport}' пока не поддержан (только http)")
        # Если уже подключён — переподключаемся (свежий list tools)
        if name in self.connections:
            self.connections[name].disconnect()
        conn = McpConnection(name=name, url=cfg["url"], headers=cfg.get("headers", {}))
        info = conn.connect()
        self.connections[name] = conn
        self._rebuild_tool_routes()
        return info

    def disconnect_server(self, name: str) -> bool:
        """Отключает сервер (помечает соединение закрытым)."""
        if name in self.connections:
            self.connections[name].disconnect()
            del self.connections[name]
            self._rebuild_tool_routes()
            return True
        return False

    def connect_all_enabled(self) -> dict[str, str]:
        """Подключает все серверы с enabled=True. Возвращает {name: status}."""
        results: dict[str, str] = {}
        for s in self.config["servers"]:
            if not s.get("enabled", True):
                continue
            try:
                info = self.connect_server(s["name"])
                results[s["name"]] = f"ok ({info['tools_count']} tools)"
            except Exception as e:
                results[s["name"]] = f"error: {e}"
        return results

    def disconnect_all(self):
        """Отключает все активные серверы."""
        for name in list(self.connections.keys()):
            self.disconnect_server(name)

    # ── OpenAI tool-calling ─────────────────────────────────────────

    @staticmethod
    def _safe_name(server_name: str, tool_name: str) -> str:
        """Делает имя инструмента совместимым с OpenAI (^[a-zA-Z0-9_-]{1,64}$).

        Префикс с именем сервера нужен, чтобы избежать коллизий между серверами
        и чтобы LLM могла однозначно отличать инструменты.
        """
        raw = f"{server_name}__{tool_name}"
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
        return safe[:64]

    def _rebuild_tool_routes(self):
        """Перестраивает таблицу маршрутизации инструмент → сервер."""
        self._tool_routes.clear()
        self._tool_real_names.clear()
        for server_name, conn in self.connections.items():
            if not conn.connected:
                continue
            for tool in conn.tools:
                tname = tool.get("name", "")
                if not tname:
                    continue
                safe = self._safe_name(server_name, tname)
                self._tool_routes[safe] = server_name
                self._tool_real_names[safe] = tname

    def get_openai_tools(self) -> list[dict]:
        """Возвращает список инструментов в формате OpenAI tools для chat/completions."""
        openai_tools: list[dict] = []
        for server_name, conn in self.connections.items():
            if not conn.connected:
                continue
            for tool in conn.tools:
                tname = tool.get("name", "")
                if not tname:
                    continue
                safe = self._safe_name(server_name, tname)
                params = tool.get("inputSchema") or {"type": "object", "properties": {}}
                # Гарантируем минимальный валидный JSON-Schema объект
                if "type" not in params:
                    params = {**params, "type": "object"}
                description = tool.get("description", "") or tname
                # OpenAI ограничивает description; обрежем для безопасности
                if len(description) > 1024:
                    description = description[:1020] + "..."
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": safe,
                        "description": description,
                        "parameters": params,
                    },
                })
        return openai_tools

    def execute_tool(self, openai_tool_name: str, arguments: Any) -> str:
        """Выполняет вызов инструмента по OpenAI-имени, возвращает текстовый результат."""
        if openai_tool_name not in self._tool_routes:
            return f"[error] Инструмент '{openai_tool_name}' не найден среди подключённых MCP-серверов."
        server_name = self._tool_routes[openai_tool_name]
        real_name = self._tool_real_names[openai_tool_name]
        conn = self.connections.get(server_name)
        if not conn or not conn.connected:
            return f"[error] Сервер '{server_name}' не подключён."
        # arguments из OpenAI приходит как JSON-строка
        if isinstance(arguments, str):
            try:
                args_dict = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return f"[error] Некорректный JSON в аргументах: {arguments[:200]}"
        elif isinstance(arguments, dict):
            args_dict = arguments
        else:
            args_dict = {}
        try:
            return conn.call_tool(real_name, args_dict)
        except Exception as e:
            return f"[error] Ошибка вызова '{real_name}' на '{server_name}': {e}"

    def has_active_tools(self) -> bool:
        """True, если хотя бы один сервер подключён и предоставляет инструменты."""
        return bool(self._tool_routes)
