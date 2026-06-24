"""Интеграционный тест Space Monitor MCP сервера."""

import json
import time
import urllib.request

URL = "http://127.0.0.1:8766/mcp"
BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _post(payload: dict, session_id: str | None = None, timeout: int = 30) -> tuple[str, str | None]:
    headers = dict(BASE_HEADERS)
    if session_id:
        headers["MCP-Session-Id"] = session_id
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        sid = resp.headers.get("MCP-Session-Id") or resp.headers.get("mcp-session-id")
        body = resp.read().decode()
    return body, sid


def _call(method: str, params: dict, session_id: str) -> dict:
    body, _ = _post({
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 100000,
        "method": method,
        "params": params,
    }, session_id=session_id)
    if "data:" in body:
        data_line = [l for l in body.split("\n") if l.startswith("data:")][0]
        return json.loads(data_line[5:].strip())
    return json.loads(body)


def main():
    # 1. Initialize
    print("=== Initialize ===")
    body, sid = _post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    })
    if not sid:
        print("ERROR: no session ID")
        return
    print(f"Session: {sid}")

    # 2. notifications/initialized
    body, _ = _post({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, session_id=sid)
    print(f"notifications: {body[:50] if body else 'ok'}")

    # 3. Start monitor with 10s interval (NEO only — APOD is slow from this network)
    print("\n=== monitor_start(10, [neo]) ===")
    result = _call("tools/call", {
        "name": "monitor_start",
        "arguments": {"interval_seconds": 10, "sources": ["neo"]},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)

    # 4. Wait for 2 collections
    print("\nWaiting 25s for 2 collection ticks...")
    time.sleep(25)

    # 5. Status
    print("\n=== monitor_status ===")
    result = _call("tools/call", {
        "name": "monitor_status", "arguments": {},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)

    # 6. Summary — should show 2 NEO entries
    print("\n=== monitor_summary ===")
    result = _call("tools/call", {
        "name": "monitor_summary", "arguments": {},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)

    # 7. Stop
    print("\n=== monitor_stop ===")
    result = _call("tools/call", {
        "name": "monitor_stop", "arguments": {},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)


if __name__ == "__main__":
    main()
