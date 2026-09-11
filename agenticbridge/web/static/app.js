const statusDot = document.getElementById("status-dot");
const taskInput = document.getElementById("task-input");
const saveTaskBtn = document.getElementById("save-task");
const ledgerEntries = document.getElementById("ledger-entries");
const fileTree = document.getElementById("file-tree");
const fileView = document.getElementById("file-view");
const turnStatus = document.getElementById("turn-status");

let taskDirty = false;
let lastEntryCount = -1;

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

  ledgerEntries.innerHTML = "";
  [...entries].reverse().forEach((entry) => {
    const div = document.createElement("div");
    div.className = "entry";

    const files = (entry.files || [])
      .map(
        (f) =>
          `<span class="file-chip" data-path="${escapeHtml(f.path)}" data-diff="${entry.id}-${escapeHtml(f.path)}">${f.action}: ${escapeHtml(f.path)}</span>`
      )
      .join("");

    div.innerHTML = `
      <div class="entry-head">
        <span><span class="entry-agent">${escapeHtml(entry.agent)}</span> · ${escapeHtml(entry.provider)}/${escapeHtml(entry.model)}</span>
        <span>#${entry.id} · ${escapeHtml(entry.timestamp)}</span>
      </div>
      <div class="entry-message">${escapeHtml(entry.message)}</div>
      <div class="entry-files">${files}</div>
      ${entry.handoff ? `<div class="entry-head">handoff → <b>${escapeHtml(entry.handoff)}</b></div>` : ""}
    `;

    div.querySelectorAll(".file-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const fileEntry = (entry.files || []).find((f) => f.path === chip.dataset.path);
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
    statusDot.className = "dot ok";

    if (!taskDirty) taskInput.value = state.task || "";
    fileTree.textContent = state.file_tree || "(empty)";
    renderLedger(state);
  } catch (e) {
    statusDot.className = "dot err";
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
});

document.querySelectorAll(".agent-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const agent = btn.dataset.agent || null;
    document.querySelectorAll(".agent-btn").forEach((b) => (b.disabled = true));
    turnStatus.textContent = `Running turn${agent ? " for " + agent : ""}...`;
    try {
      const res = await fetch("/api/turn", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ agent }),
      });
      const data = await res.json();
      if (!data.ok) {
        turnStatus.textContent = `Error: ${data.error}`;
      } else {
        turnStatus.textContent = `Turn #${data.entry.id} done (${data.entry.agent}).`;
        lastEntryCount = -1;
        await refresh();
      }
    } catch (e) {
      turnStatus.textContent = `Error: ${e}`;
    } finally {
      document.querySelectorAll(".agent-btn").forEach((b) => (b.disabled = false));
    }
  });
});

refresh();
setInterval(refresh, 2000);
