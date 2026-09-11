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

document.querySelectorAll(".agent-btn[data-provider]").forEach((btn) => {
  const emoji = PROVIDER_EMOJI[btn.dataset.provider];
  const slot = btn.querySelector(".agent-emoji");
  if (emoji && slot && !slot.textContent) slot.textContent = emoji;
});

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
        body: JSON.stringify({ agent }),
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
