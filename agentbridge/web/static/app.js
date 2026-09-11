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
let keysLoadedFromServer = false;

if (keyAnthropic && keyOpenai) {
  const stored = loadStoredKeys();
  keyAnthropic.value = stored.anthropic || "";
  keyOpenai.value = stored.openai || "";

  saveKeysBtn.addEventListener("click", async () => {
    const keys = { anthropic: keyAnthropic.value.trim(), openai: keyOpenai.value.trim() };
    saveStoredKeys(keys);
    if (isSignedIn) {
      try {
        await fetch("/api/keys", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ keys }),
        });
        keysSavedNote.textContent = "saved to your account — synced across devices.";
      } catch (e) {
        keysSavedNote.textContent = "saved in this browser only (couldn't reach your account).";
      }
    } else {
      keysSavedNote.textContent = "saved in this browser only.";
    }
    keysSavedNote.classList.add("show");
    setTimeout(() => keysSavedNote.classList.remove("show"), 2200);
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
      try {
        const res = await fetch("/api/keys");
        const data = await res.json();
        if (data.ok) {
          if (data.keys.anthropic) keyAnthropic.value = data.keys.anthropic;
          if (data.keys.openai) keyOpenai.value = data.keys.openai;
        }
      } catch (e) {
        // fall back silently to whatever localStorage already populated
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
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
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
  lastEntryCount = entries.length;

  if (entries.length === 0) {
    ledgerEntries.innerHTML = `<div class="empty-state">No turns yet. Set a task above and give an agent the mic.</div>`;
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
          `<span class="file-chip" data-path="${escapeHtml(f.path)}">${f.action}: ${escapeHtml(f.path)}</span>`
      )
      .join("") || `<span class="empty-state">no files touched</span>`;

    const emoji = PROVIDER_EMOJI[entry.provider] || "\u{1F916}";

    div.innerHTML = `
      <div class="entry-head">
        <span>${emoji} <span class="entry-agent">${escapeHtml(entry.agent)}</span> · ${escapeHtml(entry.provider)}/${escapeHtml(entry.model)}</span>
        <span>#${entry.id} · ${escapeHtml(entry.timestamp)}</span>
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
}

async function refresh() {
  try {
    const res = await fetch("/api/state");
    const state = await res.json();
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
  await fetch("/api/task", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ task: taskInput.value }),
  });
  taskDirty = false;
  taskSavedNote.textContent = "saved.";
  taskSavedNote.classList.add("show");
  setTimeout(() => taskSavedNote.classList.remove("show"), 1500);
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
      const res = await fetch("/api/turn", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ agent, keys: currentCredentials() }),
      });
      const data = await res.json();
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
