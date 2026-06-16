import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.jarvis import JarvisAgent

app = Flask(__name__)

DEFAULT_MODELS = [
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen3-Coder-Next",
    "MiniMaxAI/MiniMax-M2.5",
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
    agent.create_session(name)
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
    return jsonify({
        "model": agent.model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "context_limit": agent.context_limit,
        "context_strategy": agent.context_strategy,
        "compression_enabled": agent.compression_enabled,
        "profile_name": agent.profile.profile_name,
        "task_context": agent.task_context.to_dict(),
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


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, threaded=False)
