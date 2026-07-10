/* Отмазочная: генерация отмазки + чат доработки. */

const el = (id) => document.getElementById(id);
let history = [];
let busy = false;

/** Показывает статус (пустая строка — скрыть, isError — красным). */
function setStatus(text, isError = false) {
    const s = el("status");
    s.textContent = text;
    s.className = "status" + (isError ? " error" : "");
}

/** POST-запрос к API, возвращает JSON или бросает ошибку с текстом сервера. */
async function api(path, body) {
    const resp = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `Ошибка ${resp.status}`);
    return data;
}

/** Блокирует кнопки на время запроса. */
function setBusy(value) {
    busy = value;
    el("generate").disabled = value;
    el("send").disabled = value;
}

/** Добавляет реплику в чат-ленту. */
function addMsg(role, content) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = content;
    el("chat").appendChild(div);
}

el("generate").addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    setStatus("Придумываю отмазку… (модель маленькая, сервер слабый — до минуты)");
    try {
        const data = await api("/api/excuse", {
            role: el("role").value,
            genre: el("genre").value,
            context: el("context").value.trim(),
        });
        el("result").textContent = data.excuse;
        el("chat").innerHTML = "";
        history = [{ role: "assistant", content: data.excuse }];
        el("result-card").classList.remove("hidden");
        setStatus("");
    } catch (e) {
        setStatus(e.message, true);
    } finally {
        setBusy(false);
    }
});

async function sendChat() {
    const input = el("chat-message");
    const message = input.value.trim();
    if (!message || busy) return;
    setBusy(true);
    addMsg("user", message);
    input.value = "";
    setStatus("Дорабатываю…");
    try {
        const data = await api("/api/chat", { history, message });
        addMsg("assistant", data.reply);
        history.push({ role: "user", content: message }, { role: "assistant", content: data.reply });
        setStatus("");
    } catch (e) {
        setStatus(e.message, true);
    } finally {
        setBusy(false);
    }
}

el("send").addEventListener("click", sendChat);
el("chat-message").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChat();
});
