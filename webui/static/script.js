let currentSessionId = null;

// ──── Init ─────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadSessions();
  loadModels();
  loadSettings();
  loadMcp();

  // Restore theme
  const saved = localStorage.getItem("jarvis-theme");
  if (saved === "light") setTheme(true);

  document.getElementById("new-session-btn").onclick = createSession;
  document.getElementById("send-btn").onclick = sendMessage;
  document.getElementById("sidebar-toggle").onclick = toggleSidebar;
  document.getElementById("theme-toggle").onclick = toggleTheme;

  document.getElementById("message-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Auto-resize textarea like Claude
  document.getElementById("message-input").addEventListener("input", (e) => {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
  });

  // Settings live updates
  document.getElementById("temp-slider").oninput = (e) => {
    document.getElementById("temp-value").textContent = parseFloat(
      e.target.value
    ).toFixed(2);
    updateSettings();
  };
  document.getElementById("model-select").onchange = updateSettings;
  document.getElementById("max-tokens-input").onchange = updateSettings;
  document.getElementById("context-limit-input").onchange = updateSettings;

  // MCP wiring
  document.getElementById("mcp-enabled-checkbox").onchange = toggleMcp;
  document.getElementById("mcp-add-btn").onclick = addMcpServer;
});

// ──── Sidebar ──────────────────────────────────────────────────

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("hidden");
}

// ──── Theme ────────────────────────────────────────────────────

function setTheme(isLight) {
  document.body.classList.toggle("light", isLight);
  document.getElementById("theme-toggle").textContent = isLight ? "☀" : "☾";
  localStorage.setItem("jarvis-theme", isLight ? "light" : "dark");
}

function toggleTheme() {
  setTheme(!document.body.classList.contains("light"));
}

// ──── Sessions ─────────────────────────────────────────────────

async function loadSessions() {
  const res = await fetch("/api/sessions");
  const data = await res.json();
  currentSessionId = data.current_id;
  renderSessions(data.sessions, data.current_id);
  if (data.current_id) loadMessages();
}

function renderSessions(sessions, currentId) {
  const list = document.getElementById("session-list");
  list.innerHTML = "";
  if (sessions.length === 0) {
    list.innerHTML =
      '<div style="padding:20px;color:#555;text-align:center">No sessions</div>';
    return;
  }
  for (const s of sessions) {
    const div = document.createElement("div");
    div.className = "session-item" + (s.id === currentId ? " active" : "");
    div.title = escHtml(s.name);
    const smBadge = s.sm_enabled ? ' <span class="sm-badge">SM</span>' : "";
    div.innerHTML = `
      <span class="session-name">${escHtml(s.name)}${smBadge}</span>
      <button class="session-delete" data-id="${s.id}">✕</button>
    `;
    div.onclick = (e) => {
      if (e.target.classList.contains("session-delete")) return;
      switchSession(s.id);
    };
    div.querySelector(".session-delete").onclick = (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    };
    list.appendChild(div);
  }
}

async function switchSession(id) {
  if (id === currentSessionId) return;
  const res = await fetch(`/api/sessions/${id}/switch`, { method: "POST" });
  if (!res.ok) return;
  currentSessionId = id;
  loadSessions();
  loadMcp();
  scrollToBottom();
}

async function createSession() {
  const name = prompt("Session name (append ' sm' for State Machine):", "");
  if (name === null) return;
  let smEnabled = false;
  let cleanName = name.trim();
  if (cleanName.toLowerCase().endsWith(" sm")) {
    smEnabled = true;
    cleanName = cleanName.slice(0, -3).trim();
  }
  await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: cleanName || undefined, sm_enabled: smEnabled }),
  });
  loadSessions();
}

async function deleteSession(id) {
  if (!confirm("Delete this session?")) return;
  await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  loadSessions();
}

// ──── Messages ─────────────────────────────────────────────────

function getMsgContainer() {
  return document.querySelector("#messages-container .inner");
}

async function loadMessages() {
  if (!currentSessionId) return;
  const res = await fetch(`/api/sessions/${currentSessionId}/messages`);
  if (!res.ok) return;
  const data = await res.json();
  renderMessages(data.messages);
}

function renderMessages(messages) {
  const inner = getMsgContainer();
  inner.innerHTML = "";
  if (messages.length === 0) {
    inner.innerHTML = `
      <div class="empty-chat">
        <div class="emoji">💬</div>
        <div>Start a conversation</div>
        <div class="hint">Type a message below to begin</div>
      </div>`;
    return;
  }
  for (const m of messages) {
    inner.appendChild(createBubble(m.role, m.content));
  }
  scrollToBottom();
}

function createBubble(role, content) {
  const div = document.createElement("div");
  div.className = "message " + role;
  div.textContent = content;
  return div;
}

// ──── Chat ─────────────────────────────────────────────────────

async function sendMessage() {
  const input = document.getElementById("message-input");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  input.style.height = "auto";
  const btn = document.getElementById("send-btn");
  btn.disabled = true;
  btn.textContent = "···";

  // Optimistically add user bubble
  const inner = getMsgContainer();
  const emptyChat = inner.querySelector(".empty-chat");
  if (emptyChat) emptyChat.remove();
  inner.appendChild(createBubble("user", text));
  scrollToBottom();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    // Replace all messages with server state (ensures consistency)
    renderMessages(data.messages);
    loadSettings(); // Refresh SM/AI settings
    loadSessions(); // Refresh session list
    loadMcp(); // Refresh MCP status (may have changed via slash commands)
  } catch (err) {
    inner.appendChild(
      createBubble("system", "Error: " + err.message)
    );
    scrollToBottom();
  } finally {
    btn.disabled = false;
    btn.textContent = "Send →";
    input.focus();
  }
}

// ──── Settings & Models ────────────────────────────────────────

async function loadModels() {
  const res = await fetch("/api/models");
  const data = await res.json();
  const select = document.getElementById("model-select");
  select.innerHTML = "";
  for (const m of data.models) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    if (m === data.current) opt.selected = true;
    select.appendChild(opt);
  }
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  const data = await res.json();
  document.getElementById("temp-slider").value = data.temperature;
  document.getElementById("temp-value").textContent = parseFloat(
    data.temperature
  ).toFixed(2);
  document.getElementById("max-tokens-input").value = data.max_tokens;
  document.getElementById("context-limit-input").value = data.context_limit;

  // SM status
  const smSection = document.getElementById("sm-section");
  const smStatus = document.getElementById("sm-status");
  const sm = data.sm || { enabled: false };
  if (sm.enabled) {
    smSection.style.display = "block";
    if (sm.current_state) {
      const valStatus = sm.validation_enabled ? "val on" : "val off";
      smStatus.textContent = `Stage: ${sm.current_state} | ${valStatus}`;
    } else {
      smStatus.textContent = "Active";
    }
  } else {
    smSection.style.display = "none";
  }
}

async function updateSettings() {
  const model = document.getElementById("model-select").value;
  const temperature = parseFloat(document.getElementById("temp-slider").value);
  const max_tokens = parseInt(
    document.getElementById("max-tokens-input").value
  );
  const context_limit = parseInt(
    document.getElementById("context-limit-input").value
  );

  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, temperature, max_tokens, context_limit }),
  });
}

// ──── Utils ────────────────────────────────────────────────────

function escHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function scrollToBottom() {
  setTimeout(() => {
    const c = document.getElementById("messages-container");
    c.scrollTop = c.scrollHeight;
  }, 50);
}

// ──── MCP ──────────────────────────────────────────────────────

async function loadMcp() {
  try {
    const res = await fetch("/api/mcp");
    const data = await res.json();
    renderMcp(data);
  } catch (e) {
    console.error("MCP load failed:", e);
  }
}

function renderMcp(data) {
  const checkbox = document.getElementById("mcp-enabled-checkbox");
  const label = document.getElementById("mcp-enabled-label");
  checkbox.checked = !!data.enabled;
  label.textContent = data.enabled ? "on" : "off";

  const list = document.getElementById("mcp-servers-list");
  list.innerHTML = "";
  if (!data.servers || data.servers.length === 0) {
    list.innerHTML = '<div class="mcp-empty">No servers configured</div>';
  } else {
    for (const s of data.servers) {
      const item = document.createElement("div");
      item.className = "mcp-server-item";
      const dot = `<span class="mcp-dot${s.connected ? " connected" : ""}"></span>`;
      const tools = s.connected ? ` (${s.tools_count} tools)` : "";
      item.innerHTML = `
        <div class="mcp-server-info">
          <div class="mcp-server-name">${dot}${escHtml(s.name)}${tools}</div>
          <div class="mcp-server-url">${escHtml(s.url)}</div>
        </div>
        <div class="mcp-server-actions">
          <button data-act="${s.connected ? "disconnect" : "connect"}" data-name="${escHtml(s.name)}">
            ${s.connected ? "✕" : "▶"}
          </button>
          <button class="danger" data-act="remove" data-name="${escHtml(s.name)}">🗑</button>
        </div>
      `;
      list.appendChild(item);
    }
    list.querySelectorAll("button").forEach((btn) => {
      btn.onclick = () => mcpServerAction(btn.dataset.act, btn.dataset.name);
    });
  }

  const info = document.getElementById("mcp-tools-info");
  if (data.tools && data.tools.length > 0) {
    info.textContent = `${data.tools.length} active tool(s): ${data.tools
      .map((t) => t.name)
      .join(", ")}`;
  } else {
    info.textContent = data.enabled
      ? "MCP enabled, but no tools active. Connect a server."
      : "";
  }
}

async function toggleMcp(e) {
  const enabled = e.target.checked;
  document.getElementById("mcp-enabled-label").textContent = enabled ? "on" : "off";
  const res = await fetch("/api/mcp/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = await res.json();
  console.log("MCP toggle:", data.message);
  await loadMcp();
}

async function addMcpServer() {
  const name = document.getElementById("mcp-add-name").value.trim();
  const url = document.getElementById("mcp-add-url").value.trim();
  if (!name || !url) {
    alert("Укажите name и url");
    return;
  }
  const res = await fetch("/api/mcp/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, url, transport: "http" }),
  });
  if (!res.ok) {
    const err = await res.json();
    alert("Error: " + (err.error || res.statusText));
    return;
  }
  document.getElementById("mcp-add-name").value = "";
  document.getElementById("mcp-add-url").value = "";
  await loadMcp();
}

async function mcpServerAction(action, name) {
  if (action === "remove") {
    if (!confirm(`Удалить сервер '${name}'?`)) return;
    await fetch(`/api/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
  } else if (action === "connect") {
    const res = await fetch(`/api/mcp/${encodeURIComponent(name)}/connect`, {
      method: "POST",
    });
    if (!res.ok) {
      const err = await res.json();
      alert("Connect failed: " + (err.error || res.statusText));
    }
  } else if (action === "disconnect") {
    await fetch(`/api/mcp/${encodeURIComponent(name)}/disconnect`, {
      method: "POST",
    });
  }
  await loadMcp();
}
