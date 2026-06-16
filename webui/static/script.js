let currentSessionId = null;

// ──── Init ─────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadSessions();
  loadModels();
  loadSettings();

  document.getElementById("new-session-btn").onclick = createSession;
  document.getElementById("send-btn").onclick = sendMessage;

  document.getElementById("message-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
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
});

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
    div.innerHTML = `
      <span class="session-name">${escHtml(s.name)}</span>
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
  scrollToBottom();
}

async function createSession() {
  const name = prompt("Session name:", "");
  if (name === null) return;
  await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || undefined }),
  });
  loadSessions();
}

async function deleteSession(id) {
  if (!confirm("Delete this session?")) return;
  await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  loadSessions();
}

// ──── Messages ─────────────────────────────────────────────────

async function loadMessages() {
  if (!currentSessionId) return;
  const res = await fetch(`/api/sessions/${currentSessionId}/messages`);
  if (!res.ok) return;
  const data = await res.json();
  renderMessages(data.messages);
}

function renderMessages(messages) {
  const container = document.getElementById("messages-container");
  container.innerHTML = "";
  if (messages.length === 0) {
    container.innerHTML =
      '<div class="empty-chat">Start a conversation</div>';
    return;
  }
  for (const m of messages) {
    container.appendChild(createBubble(m.role, m.content));
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
  const btn = document.getElementById("send-btn");
  btn.disabled = true;
  btn.textContent = "···";

  // Optimistically add user bubble
  const container = document.getElementById("messages-container");
  const emptyChat = container.querySelector(".empty-chat");
  if (emptyChat) emptyChat.remove();
  container.appendChild(createBubble("user", text));
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
  } catch (err) {
    container.appendChild(
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
