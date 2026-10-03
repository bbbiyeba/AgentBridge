const statusDot = document.getElementById("status-dot");
const taskInput = document.getElementById("task-input");
const saveTaskBtn = document.getElementById("save-task");
const taskSavedNote = document.getElementById("task-saved");
const ledgerEntries = document.getElementById("ledger-entries");
const fileTree = document.getElementById("file-tree");
const fileView = document.getElementById("file-view");
const turnStatus = document.getElementById("turn-status");
const taglineEl = document.getElementById("tagline");

let taskDirty = false;
let lastEntryCount = -1;
let turnInFlight = false;

const PROVIDER_EMOJI = {
  anthropic: "\u{1F7E0}", // orange circle
  openai: "\u{1F7E2}", // green circle
  ollama: "\u{1F999}", // llama
  auto: "\u{1F501}", // repeat
};

const TAGLINES = [
  "Multiple agents, one codebase, minimal adult supervision.",
  "Claude, GPT, and a local llama, taking turns like it's a group project.",
  "Turn-based pair programming, except there are three pairs and no eye contact.",
  "The ledger remembers everything, even the mistakes.",
  "Handing off code so you don't have to copy-paste it yourself.",
];

const THINKING_LINES = [
  "reading the ledger like ancient scripture...",
  "staring at the file tree, formulating opinions...",
  "definitely not making this up as it goes...",
  "arguing with itself about naming conventions...",
  "drafting a JSON response, allegedly...",
  "consulting the task, then ignoring half of it...",
];

taglineEl.textContent = TAGLINES[Math.floor(Math.random() * TAGLINES.length)];

document.querySelectorAll("[data-provider]").forEach((el) => {
  const emoji = PROVIDER_EMOJI[el.dataset.provider];
  const slot = el.querySelector(".agent-emoji");
  if (emoji && slot && !slot.textContent) slot.textContent = emoji;
});

// Every API call goes through here so failures are reported the same way:
// a non-2xx status, an {ok:false} body, a network error, or a non-JSON
// reply (e.g. the platform's HTML 504 page when a request runs too long)
// all come back as {ok:false, error}, never as a thrown SyntaxError or a
// silently ignored response.
async function api(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (e) {
    return { ok: false, error: "couldn't reach the server" };
  }
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    const hint = res.status === 504 ? " (the request took too long)" : "";
    return { ok: false, error: `server returned HTTP ${res.status} instead of data${hint}` };
  }
  if (!res.ok || !data || data.ok === false) {
    return { ok: false, error: (data && data.error) || `server returned HTTP ${res.status}` };
  }
  return { ok: true, ...data };
}

function postJson(url, body) {
  return api(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}

function flash(el, text, isError = false, ms = 2200) {
  el.textContent = text;
  el.classList.toggle("err", isError);
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), isError ? ms * 3 : ms);
}

const KEYS_STORAGE_KEY = "agentbridge:keys";

function loadStoredKeys() {
  try {
    return JSON.parse(localStorage.getItem(KEYS_STORAGE_KEY) || "{}");
  } catch (e) {
    return {};
  }
}

function saveStoredKeys(keys) {
  try {
    localStorage.setItem(KEYS_STORAGE_KEY, JSON.stringify(keys));
  } catch (e) {
    // localStorage unavailable (private mode, etc.) - keys just won't persist across reloads
  }
}

const keyAnthropic = document.getElementById("key-anthropic");
const keyOpenai = document.getElementById("key-openai");
const saveKeysBtn = document.getElementById("save-keys");
const keysSavedNote = document.getElementById("keys-saved");
const authRow = document.getElementById("auth-row");
const authStatus = document.getElementById("auth-status");
const authLoginLink = document.getElementById("auth-login-link");
const authLogoutLink = document.getElementById("auth-logout-link");

let isSignedIn = false;

// Sign-out is a POST (so other sites can't trigger it with a plain link);
// the link keeps its href for semantics, and this does the actual request.
if (authLogoutLink) {
  authLogoutLink.addEventListener("click", async (e) => {
    e.preventDefault();
    const result = await postJson("/auth/logout", {});
    if (result.ok) window.location.reload();
    else authStatus.textContent = `Couldn't sign out: ${result.error}`;
  });
}
let keysLoadedFromServer = false;

if (keyAnthropic && keyOpenai) {
  const stored = loadStoredKeys();
  keyAnthropic.value = stored.anthropic || "";
  keyOpenai.value = stored.openai || "";

  saveKeysBtn.addEventListener("click", async () => {
    const keys = { anthropic: keyAnthropic.value.trim(), openai: keyOpenai.value.trim() };
    saveStoredKeys(keys);
    if (!isSignedIn) return flash(keysSavedNote, "saved in this browser only.");
    const result = await postJson("/api/keys", { keys });
    if (result.ok) {
      flash(keysSavedNote, "saved to your account — synced across devices.");
    } else {
      flash(keysSavedNote, `saved in this browser only — your account wasn't updated: ${result.error}`, true);
    }
  });
}

async function refreshAuthUI(state) {
  if (!authRow) return;
  if (!state.google_login_available) {
    authRow.hidden = true;
    return;
  }
  authRow.hidden = false;

  if (state.user) {
    isSignedIn = true;
    authStatus.textContent = `Signed in as ${state.user.email}.`;
    authLoginLink.hidden = true;
    authLogoutLink.hidden = false;

    if (!keysLoadedFromServer && keyAnthropic && keyOpenai) {
      keysLoadedFromServer = true;
      const result = await api("/api/keys");
      if (result.ok) {
        if (result.keys.anthropic) keyAnthropic.value = result.keys.anthropic;
        if (result.keys.openai) keyOpenai.value = result.keys.openai;
      } else {
        // Say so, and try again on the next refresh, rather than leaving the
        // fields blank as if no keys were ever saved.
        keysLoadedFromServer = false;
        authStatus.textContent = `Signed in as ${state.user.email}. Couldn't load your saved keys: ${result.error}`;
      }
    }
  } else {
    isSignedIn = false;
    keysLoadedFromServer = false;
    authStatus.textContent = "";
    authLoginLink.hidden = false;
    authLogoutLink.hidden = true;
  }
}

function currentCredentials() {
  if (keyAnthropic && keyOpenai) {
    return { anthropic: keyAnthropic.value.trim(), openai: keyOpenai.value.trim() };
  }
  return {};
}

function escapeHtml(s) {
  // String(): ledger fields come from AI output, so never assume their type.
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderDiff(diff) {
  if (!diff) return "(no textual diff)";
  return diff
    .split("\n")
    .map((line) => {
      const esc = escapeHtml(line);
      if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="diff-add">${esc}</span>`;
      if (line.startsWith("-") && !line.startsWith("---")) return `<span class="diff-del">${esc}</span>`;
      return esc;
    })
    .join("\n");
}

function renderLedger(state) {
  const entries = state.entries || [];
  if (entries.length === lastEntryCount) return;

  if (entries.length === 0) {
    ledgerEntries.innerHTML = `<div class="empty-state">No turns yet. Set a task above and give an agent the mic.</div>`;
    lastEntryCount = 0;
    return;
  }

  ledgerEntries.innerHTML = "";
  [...entries].reverse().forEach((entry) => {
    const div = document.createElement("div");
    div.className = "entry";
    div.dataset.provider = entry.provider;

    const files = (entry.files || [])
      .map(
        (f) =>
          `<span class="file-chip" data-path="${escapeHtml(f.path)}">${escapeHtml(f.action)}: ${escapeHtml(f.path)}</span>`
      )
      .join("") || `<span class="empty-state">no files touched</span>`;

    const emoji = PROVIDER_EMOJI[entry.provider] || "\u{1F916}";

    div.innerHTML = `
      <div class="entry-head">
        <span>${emoji} <span class="entry-agent">${escapeHtml(entry.agent)}</span> · ${escapeHtml(entry.provider)}/${escapeHtml(entry.model)}</span>
        <span>#${escapeHtml(entry.id)} · ${escapeHtml(entry.timestamp)}</span>
      </div>
      <div class="entry-message">${escapeHtml(entry.message)}</div>
      <div class="entry-files">${files}</div>
      ${entry.handoff ? `<div class="entry-head">handoff → <b>${escapeHtml(entry.handoff)}</b></div>` : ""}
    `;

    div.querySelectorAll(".file-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const fileEntry = (entry.files || []).find((f) => f.path === chip.dataset.path);
        fileView.textContent = "";
        fileView.innerHTML = renderDiff(fileEntry ? fileEntry.diff : "");
      });
    });

    ledgerEntries.appendChild(div);
  });
  // Only after a successful render, so a failure is retried next refresh
  // instead of freezing the ledger.
  lastEntryCount = entries.length;
}

async function refresh() {
  try {
    const state = await api("/api/state");
    if (!state.ok) throw new Error(state.error);
    if (!turnInFlight) statusDot.className = "dot ok";

    if (!taskDirty) taskInput.value = state.task || "";
    fileTree.textContent = state.file_tree || "(workspace is an empty canvas, or just empty)";
    renderLedger(state);
    await refreshAuthUI(state);
  } catch (e) {
    if (!turnInFlight) statusDot.className = "dot err";
  }
}

taskInput.addEventListener("input", () => {
  taskDirty = true;
});

saveTaskBtn.addEventListener("click", async () => {
  const result = await postJson("/api/task", { task: taskInput.value });
  if (result.ok) {
    taskDirty = false;
    flash(taskSavedNote, "saved.", false, 1500);
  } else {
    // taskDirty stays true so the next refresh doesn't overwrite the unsaved text.
    flash(taskSavedNote, `not saved: ${result.error}`, true);
  }
});

document.querySelectorAll(".agent-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (turnInFlight) return;
    const agent = btn.dataset.agent || null;
    turnInFlight = true;

    document.querySelectorAll(".agent-btn").forEach((b) => (b.disabled = true));
    btn.classList.add("running");
    statusDot.className = "dot busy";
    turnStatus.className = "";

    let lineIndex = 0;
    const label = agent || "the next agent";
    turnStatus.textContent = `${label} is ${THINKING_LINES[lineIndex]}`;
    const rotator = setInterval(() => {
      lineIndex = (lineIndex + 1) % THINKING_LINES.length;
      turnStatus.textContent = `${label} is ${THINKING_LINES[lineIndex]}`;
    }, 1400);

    try {
      const data = await postJson("/api/turn", { agent, keys: currentCredentials() });
      clearInterval(rotator);
      if (!data.ok) {
        turnStatus.textContent = `nope: ${data.error}`;
        turnStatus.className = "err";
      } else {
        turnStatus.textContent = `turn #${data.entry.id} done — ${data.entry.agent} wrapped up.`;
        turnStatus.className = "ok";
        lastEntryCount = -1;
        await refresh();
      }
    } catch (e) {
      clearInterval(rotator);
      turnStatus.textContent = `something broke: ${e}`;
      turnStatus.className = "err";
    } finally {
      turnInFlight = false;
      btn.classList.remove("running");
      document.querySelectorAll(".agent-btn").forEach((b) => (b.disabled = false));
      statusDot.className = "dot ok";
    }
  });
});

refresh();
setInterval(refresh, 2000);
