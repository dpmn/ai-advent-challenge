import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.jarvis import JarvisAgent

app = Flask(__name__)

DEFAULT_MODELS = [
    "Qwen/Qwen3-Coder-Next",
    "MiniMaxAI/MiniMax-M2.5",
    "Qwen/Qwen3.5-397B-A17B",
]
AVAILABLE_MODELS = json.loads(os.getenv("AVAILABLE_MODELS", json.dumps(DEFAULT_MODELS)))

agent = JarvisAgent(
    model=AVAILABLE_MODELS[0],
    temperature=0.3,
    system_prompt="Ты — полезный AI-ассистент по имени Jarvis. Отвечай кратко и по делу.",
    context_limit=40000,
    max_tokens=5000,
)


# ──────── HTML ──────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


# ──────── Sessions ──────────────────────────────────────────────


@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    sessions = agent.list_sessions()
    current_id = agent.current_session["id"]
    return jsonify({"sessions": sessions, "current_id": current_id})


@app.route("/api/sessions", methods=["POST"])
def create_session():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    sm_enabled = data.get("sm_enabled", False)
    agent.create_session(name, sm_enabled=sm_enabled)
    return jsonify({"session": agent.current_session}), 201


@app.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def delete_session(session_id):
    ok = agent.delete_session(session_id)
    if ok:
        return jsonify({"session": agent.current_session})
    return jsonify({"error": "Session not found"}), 404


@app.route("/api/sessions/<int:session_id>/switch", methods=["POST"])
def switch_session(session_id):
    ok = agent.switch_session(session_id)
    if ok:
        return jsonify({"session": agent.current_session})
    return jsonify({"error": "Session not found"}), 404


@app.route("/api/sessions/<int:session_id>/messages", methods=["GET"])
def get_messages(session_id):
    if session_id != agent.current_session["id"]:
        return jsonify({"error": "Switch to this session first"}), 400
    messages = [
        m for m in agent.conversation_history if m["role"] != "system"
    ]
    return jsonify({"messages": messages})


# ──────── Chat ──────────────────────────────────────────────────


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    text = data.get("message", "").strip()
    if not text:
        return jsonify({"error": "Empty message"}), 400

    response = agent.chat(text)

    messages = [
        m for m in agent.conversation_history if m["role"] != "system"
    ]
    return jsonify({"response": response, "messages": messages})


# ──────── Settings & Models ─────────────────────────────────────


@app.route("/api/models", methods=["GET"])
def list_models():
    return jsonify({"models": AVAILABLE_MODELS, "current": agent.model})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    sm_info = None
    if agent.pipeline:
        sm_info = {
            "enabled": True,
            "current_state": agent.pipeline.current_state.value,
            "validation_enabled": agent.pipeline.validation_enabled,
            "artifacts_count": len(agent.pipeline.artifacts),
        }
    else:
        sm_enabled = agent.current_session.get("sm_enabled", False)
        sm_info = {"enabled": bool(sm_enabled)} if sm_enabled else {"enabled": False}

    return jsonify({
        "model": agent.model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "context_limit": agent.context_limit,
        "context_strategy": agent.context_strategy,
        "compression_enabled": agent.compression_enabled,
        "profile_name": agent.profile.profile_name,
        "task_context": agent.task_context.to_dict(),
        "sm": sm_info,
        "invariants_enabled": agent.invariants_enabled,
        "invariants": [
            {"name": inv.name, "enabled": inv.enabled}
            for inv in agent._invariants
        ],
    })


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json(silent=True) or {}
    if "model" in data:
        m = data["model"]
        if m in AVAILABLE_MODELS:
            agent.model = m
    if "temperature" in data:
        agent.temperature = float(data["temperature"])
    if "max_tokens" in data:
        agent.max_tokens = int(data["max_tokens"])
    if "context_limit" in data:
        agent.context_limit = int(data["context_limit"])
    if "invariants_enabled" in data:
        val = bool(data["invariants_enabled"])
        if val:
            agent._handle_command("/invariant on")
        else:
            agent._handle_command("/invariant off")
    return jsonify({
        "model": agent.model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "context_limit": agent.context_limit,
    })


# ──────── Stats ─────────────────────────────────────────────────


@app.route("/api/stats", methods=["GET"])
def stats():
    return jsonify({"stats": agent.get_stats()})


# ──────── MCP ───────────────────────────────────────────────────


@app.route("/api/mcp", methods=["GET"])
def mcp_list():
    """Возвращает статус MCP и список настроенных серверов."""
    return jsonify({
        "enabled": bool(agent.mcp_enabled),
        "servers": agent.mcp_manager.list_servers(),
        "tools": [
            {"name": t["function"]["name"],
             "description": t["function"].get("description", "")}
            for t in agent.mcp_manager.get_openai_tools()
        ],
    })


@app.route("/api/mcp/toggle", methods=["POST"])
def mcp_toggle():
    """Включает/выключает использование MCP-инструментов для текущей сессии."""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", not agent.mcp_enabled))
    if enabled:
        result = agent._handle_command("/mcp on")
    else:
        result = agent._handle_command("/mcp off")
    return jsonify({"enabled": agent.mcp_enabled, "message": result})


@app.route("/api/mcp/add", methods=["POST"])
def mcp_add():
    """Добавляет новый MCP-сервер в конфиг."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    transport = (data.get("transport") or "http").strip()
    if not name or not url:
        return jsonify({"error": "name и url обязательны"}), 400
    try:
        entry = agent.mcp_manager.add_server(name, transport, url,
                                             headers=data.get("headers"),
                                             enabled=data.get("enabled", True))
        return jsonify({"server": entry}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/mcp/<name>", methods=["DELETE"])
def mcp_remove(name):
    """Удаляет MCP-сервер."""
    if agent.mcp_manager.remove_server(name):
        return jsonify({"removed": name})
    return jsonify({"error": "not found"}), 404


@app.route("/api/mcp/<name>/connect", methods=["POST"])
def mcp_connect(name):
    """Подключается к указанному MCP-серверу."""
    try:
        info = agent.mcp_manager.connect_server(name)
        return jsonify({"connected": name, "info": info})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mcp/<name>/disconnect", methods=["POST"])
def mcp_disconnect(name):
    """Отключает MCP-сервер."""
    if agent.mcp_manager.disconnect_server(name):
        return jsonify({"disconnected": name})
    return jsonify({"error": "not connected"}), 404


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, threaded=True)
