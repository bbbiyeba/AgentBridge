# AgentBridge

A local multi-agent orchestration tool. Multiple AI agents — Claude, ChatGPT,
and local Ollama models (just to name a few) — take turns working on a shared codebase without you
copy-pasting between them.

## How it works

- **Shared workspace.** `workspace/` holds the code the agents are actually
  writing. `mailboard.json` is the ledger they coordinate through: each turn,
  an agent reads the task + recent ledger entries + the workspace file tree,
  writes files, and appends a ledger entry (with a diff per file and an
  optional handoff to a named agent). The next turn picks up from there.
- **Provider-agnostic.** `agentbridge/providers/chat(provider, model, system,
  messages)` is the one interface every agent goes through. Claude, OpenAI,
  and Ollama are swappable per-agent via `config.yaml` — no SDKs, just
  `urllib` against each provider's HTTP API.
- **Turn loop.** `agentbridge/orchestrator.py` builds the prompt, calls the
  agent, parses its JSON response (`message`, `files`, `handoff`), applies the
  file writes inside `workspace/`, and appends the ledger entry.
- **Web UI.** A local Flask app polls the ledger, shows per-file diffs, lets
  you edit the task, and trigger a turn for any configured agent (or "next in
  queue" to follow the last agent's handoff).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy config.example.yaml config.yaml   # Windows
# cp config.example.yaml config.yaml   # macOS/Linux
```

Edit `config.yaml` to define your agents — each needs a `name`, `provider`
(`anthropic` | `openai` | `ollama`), `model`, and `role` (its system prompt).

Set whichever provider API keys you're actually using as environment
variables (PowerShell shown; only set the ones you need):

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:OPENAI_API_KEY = "sk-..."
$env:OLLAMA_HOST = "http://localhost:11434"   # optional, this is the default
```

Ollama itself needs to be running locally (`ollama serve`) with the model
you referenced in `config.yaml` pulled (`ollama pull llama3`).

## Run

**Web UI** (recommended — lets you watch the ledger live and trigger turns):

```bash
python -m agentbridge web
```

Then open http://127.0.0.1:5050. Set a task, click an agent to give it a
turn (or "Next in queue" to follow the previous agent's handoff), and watch
the ledger and file diffs update.

**Terminal**, for a scripted run:

```bash
python -m agentbridge run --task "Build a CLI todo app in Python with tests" --turns 6
```

Omit `--task` to reuse whatever task is already saved in `mailboard.json`.
Omit `--agent` to start from the mailboard's `next_agent` (or the first
configured agent on a fresh run). The loop stops early if an agent sets
`handoff` to `null`.

## Hosting this somewhere other than localhost

The web UI runs fine on your own machine as-is, but if you deploy it
somewhere reachable by other people, add auth in front of it (this app has
none) and think about API keys: by default every turn uses whatever key is
set as an env var on the server — meaning any visitor who finds the URL
could trigger turns on your dime.

To stop that, set `require_client_keys: true` in `config.yaml`. When it's
on:

- the web UI shows a "Your API keys" panel where each visitor pastes in
  their own Anthropic/OpenAI key
- keys are kept in that browser's `localStorage` only, sent to the server
  with each turn request, and never logged, stored in the mailboard, or
  written to disk
- the server **never** falls back to its own env-var keys for those
  requests — a turn on `anthropic` or `openai` is refused outright if the
  visitor hasn't supplied a key, so nobody can spend your credits just by
  finding the URL

This only applies to keyed providers (Anthropic, OpenAI). Ollama has no
secret key — the server still needs Ollama itself reachable, which
generally means running it on the same host.

**Prefer a terminal over the browser panel?** The browser panel is
convenient, but the key does sit in that tab's `localStorage` while you're
using it — exposed to browser extensions, clipboard managers, or an XSS bug
if one is ever found in this app or a dependency. If you'd rather your key
never touch a web page at all, send it as a request header instead of
using the UI:

```bash
curl -X POST https://your-host/api/turn \
  -H "content-type: application/json" \
  -H "X-Anthropic-Key: $ANTHROPIC_API_KEY" \
  -d '{"agent": "architect"}'
```

(`X-Openai-Key` works the same way for an OpenAI-backed agent.) The key
comes straight from your own shell's environment variable, goes out over
HTTPS, and is never written to disk, logged, or stored anywhere server-side
— identical handling to the browser panel, just without ever existing
inside a browser tab.

One caveat either way: the key still has to reach the server so *it* can
call Anthropic/OpenAI on your behalf — this app doesn't log or persist it,
but a truly zero-trust setup (key never leaves your own machine, period)
would need a different architecture, where your machine makes the provider
call itself instead of the hosted server. Ask if you want that instead —
it's a bigger change but doable.

The CLI (`agentbridge run`) always keeps using your local env vars
regardless of this setting — it's meant for you, running trusted, not for
public visitors.

Because this is a stateful app (it reads and writes `workspace/` and
`mailboard.json` on disk), it needs a host with a persistent filesystem and
a long-running process — a small VPS, Render, Railway, or Fly.io, not a
serverless platform like Vercel.

## The agent response contract

Every turn, the orchestrator asks the agent to respond with exactly one JSON
object:

```json
{
  "message": "short summary of what was done, for the ledger",
  "files": [{"path": "app.py", "content": "<full new file contents>"}],
  "handoff": "reviewer"
}
```

Agents must return the **full** contents of any file they touch (not a
diff) — the orchestrator computes and stores the diff itself for the ledger
and the web UI's file viewer. `handoff` names the next agent to act, or
`null` to end an automatic `run`.

## Project layout

```
agentbridge/
  providers/        Anthropic / OpenAI / Ollama HTTP clients + the chat() dispatcher
  web/               Flask app + the single-page UI (templates/static)
  config.py          Loads config.yaml into AgentConfig/Settings
  mailboard.py       mailboard.json ledger read/write
  orchestrator.py    The turn loop: prompt building, response parsing, applying writes
  __main__.py        CLI: `agentbridge run` / `agentbridge web`
workspace/           The shared codebase agents read and write (gitignored)
mailboard.json       The ledger (generated at runtime, gitignored)
config.example.yaml  Template — copy to config.yaml (gitignored) and edit
```

## Notes and limitations

- Single-process, no locking: this is built for one person driving turns
  from the web UI or CLI, not concurrent orchestrators writing the same
  mailboard.
- The response contract is enforced by prompting, not a schema validator —
  if a model wraps its JSON in extra prose beyond a single fenced code
  block, parsing will fail and the turn errors out instead of silently
  guessing.
- No sandboxing of file writes beyond staying inside `workspace/`. Don't
  point an agent at a task that requires running arbitrary shell commands —
  this tool only ever writes files it's told to write.
