"""Интеграционный тест Composer MCP сервера."""
import json
import time
import urllib.request

URL = "http://127.0.0.1:8767/mcp"
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
    assert sid, "No session ID received"
    print(f"Session: {sid}")

    # 2. notifications/initialized
    body, _ = _post({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, session_id=sid)
    print(f"Initialized: {body[:50] if body else 'ok'}")

    # 3. List tools
    print("\n=== tools/list ===")
    result = _call("tools/list", {}, session_id=sid)
    tools = result.get("result", {}).get("tools", [])
    for t in tools:
        print(f"  - {t['name']}: {t.get('description', '')[:80]}")
    assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}"
    print("  3 tools OK")

    # 4. save_to_file (stateless, doesn't need NASA API)
    print("\n=== save_to_file ===")
    result = _call("tools/call", {
        "name": "save_to_file",
        "arguments": {"filename": "test_output.txt", "content": "Hello from MCP Composer!"},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)
    assert "Saved:" in text and "test_output.txt" in text
    assert "bytes" in text

    # 5. summarize_text (APOD-style)
    print("\n=== summarize_text (APOD) ===")
    result = _call("tools/call", {
        "name": "summarize_text",
        "arguments": {
            "text": "Title: Galaxy Glow\nDate: 2025-01-01\n"
                    "Description: A beautiful view of the Andromeda galaxy captured "
                    "by the Hubble Space Telescope. The image shows millions of stars "
                    "in remarkable detail across the entire galaxy.",
            "max_words": 15,
        },
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)
    assert "Title:" in text and "Galaxy" in text

    # 6. Path traversal protection
    print("\n=== save_to_file (path traversal) ===")
    result = _call("tools/call", {
        "name": "save_to_file",
        "arguments": {"filename": "../../etc/passwd", "content": "secret"},
    }, session_id=sid)
    text = result.get("result", {}).get("content", [{}])[0].get("text", "")
    print(text)
    assert "Error:" in text and "invalid filename" in text

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
