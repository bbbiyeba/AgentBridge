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

Then open http://127.0.0.1:5050/app (the root `/` redirects to the public
marketing site, which isn't useful for local dev — go straight to `/app`).
Set a task, click an agent to give it a turn (or "Next in queue" to follow
the previous agent's handoff), and watch the ledger and file diffs update.

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
but it does transit the network and exist briefly in server memory. For a
setup where your key **never** leaves your own machine at all, see the
worker mode below.

### Zero-trust mode: run the agent yourself with `agentbridge worker`

Instead of sending your key to the hosted server at all, you can run a
turn's provider call on your own machine and only send the (non-secret)
result up to the shared server. The server exposes two endpoints for this:
`GET /api/turn/prepare` returns the prompt for a turn (task, ledger, file
tree — nothing secret), and `POST /api/turn/submit` accepts the finished
result (message, files, handoff — also nothing secret) and applies it to
the shared workspace and ledger. Neither endpoint ever sees an API key.

```bash
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # stays on your machine, never sent to the server
python -m agentbridge worker --server https://your-host --agent architect
```

This polls the hosted server and, whenever it's `architect`'s turn, fetches
the prompt, calls Anthropic **directly from your machine** using your local
key, and posts just the result back. Add `--once` to run a single turn
immediately regardless of whose turn it currently is (handy for testing, or
for scripting a specific agent's turn from your own shell), or
`--poll-interval 10` to change how often it checks (default: 5 seconds).

Run one worker per agent you want to execute locally — e.g. you run
`--agent architect` on your machine with your Anthropic key, someone else
runs `--agent reviewer` on theirs with their OpenAI key, and the handoffs
between them still flow through the shared server exactly like normal.

The CLI (`agentbridge run`) always keeps using your local env vars
regardless of this setting — it's meant for you, running trusted, not for
public visitors.

### Deploying to PythonAnywhere (free tier)

PythonAnywhere's free tier gives you a real public URL
(`yourname.pythonanywhere.com`) with persistent disk storage — your ledger
and workspace survive restarts — and its free-tier allowlist already
includes `api.anthropic.com` and `api.openai.com`, so both keyed providers
work out of the box. Ollama won't: PythonAnywhere's servers can't reach a
`localhost` Ollama instance running on your own machine, so drop that
agent for a hosted deployment (`config.deploy.yaml`, committed in this
repo, already does — it only has `architect` and `reviewer`, and has
`require_client_keys: true` set so visitors bring their own key).

1. Sign up free at [pythonanywhere.com](https://www.pythonanywhere.com) — no card required.
2. Open a **Bash console** from the Dashboard and clone the repo:
   ```bash
   git clone https://github.com/bbbiyeba/AgentBridge.git
   cd AgentBridge
   python3.10 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Go to the **Web** tab → **Add a new web app** → **Manual configuration** → pick the same Python version as your venv.
4. On that web app's config page, set:
   - **Source code**: `/home/yourname/AgentBridge`
   - **Working directory**: `/home/yourname/AgentBridge`
   - **Virtualenv**: `/home/yourname/AgentBridge/.venv`
5. Click the **WSGI configuration file** link (PythonAnywhere generates one per web app) and replace its contents with:
   ```python
   import sys
   path = "/home/yourname/AgentBridge"
   if path not in sys.path:
       sys.path.insert(0, path)

   from agentbridge.config import load_config
   from agentbridge.web.app import create_app

   config = load_config(f"{path}/config.deploy.yaml")
   application = create_app(config)
   ```
   (This is the same logic as [wsgi_pythonanywhere.py](wsgi_pythonanywhere.py) in the repo — PythonAnywhere needs the content pasted into *its* generated file specifically, not a path to ours.)
6. No API keys need setting on the server at all — `require_client_keys: true` means visitors supply their own via the panel or the `X-Anthropic-Key`/`X-Openai-Key` headers.
7. Hit the green **Reload** button on the Web tab, then visit `https://yourname.pythonanywhere.com/app` (the dashboard — `/` redirects to the separate marketing site).

Free-tier web apps go to sleep if untouched for 3 months — the Web tab shows a button to extend that with one click, no action needed otherwise. To ship a code update later: `git pull` in the same Bash console, then hit **Reload** again.

Because this is a stateful app (it reads and writes `workspace/` and
`mailboard.json` on disk), it needs a host with a persistent filesystem and
a long-running process — a small VPS, Render, Railway, or Fly.io ordinarily,
not a serverless platform like Vercel (see the caveat in `config.vercel.yaml`
if you deploy there anyway — the ledger/workspace won't reliably persist,
though saved API keys will, via the feature below).

### Optional: "Sign in with Google" to save keys across devices

By default, keys entered in the panel live in that one browser's
`localStorage` — convenient, but they don't follow you to a different
browser or device. Setting a few extra environment variables turns on
Google sign-in, after which saved keys are stored server-side (encrypted)
and tied to your Google account instead, so they follow you anywhere.

This is a bigger commitment than everything else in this README: **your
server now holds other people's live API keys**, even encrypted, so treat
`KEY_ENCRYPTION_SECRET` with the same care as an API key itself. If that
tradeoff isn't worth it, skip this section entirely — plain `localStorage`
keys (the default) never touch your server's storage at all.

**1. Register a Google OAuth app** (free, needs a Google account, no billing):
   - Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project (or use an existing one)
   - **APIs & Services → OAuth consent screen**: set it up as "External," add your own email as a test user if it stays in testing mode
   - **APIs & Services → Credentials → Create Credentials → OAuth client ID** → Application type: **Web application**
   - Under **Authorized redirect URIs**, add: `https://<your-deployed-domain>/auth/callback` (must match exactly, including `https://`)
   - Save, then copy the **Client ID** and **Client secret**

**2. Create a free Upstash Redis database** (this is where encrypted keys are stored):
   - Sign up free at [upstash.com](https://upstash.com) — no card required
   - Create a Redis database (any region)
   - From its dashboard, copy the **REST URL** and **REST Token**

**3. Set these environment variables on your host** (Vercel: Project Settings → Environment Variables; PythonAnywhere: there's an Environment Variables section on the Web tab):

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | from step 1 |
| `GOOGLE_CLIENT_SECRET` | from step 1 |
| `UPSTASH_REDIS_REST_URL` | from step 2 |
| `UPSTASH_REDIS_REST_TOKEN` | from step 2 |
| `KEY_ENCRYPTION_SECRET` | any long random string you generate yourself — e.g. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_SECRET_KEY` | another long random string (same command) — signs the login session cookie |

Redeploy after setting these. The "Sign in with Google" link appears automatically once `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set — nothing to change in `config.yaml`. Without Upstash configured, saved keys just won't persist even if login works; without Google configured, the whole feature stays hidden and everything behaves exactly as before.

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
  web/               Flask app + the dashboard UI (templates/static), served at /app
  worker.py          Local worker: runs a turn's provider call on your own machine
  auth.py            Optional "Sign in with Google" (see README section below)
  keystore.py        Optional encrypted per-user key storage (Upstash Redis)
  config.py          Loads config.yaml into AgentConfig/Settings
  mailboard.py       mailboard.json ledger read/write
  orchestrator.py    The turn loop: prompt building, response parsing, applying writes
  __main__.py        CLI: `agentbridge run` / `agentbridge web` / `agentbridge worker`
landing/             Marketing site (Vite/React) - a separate deployment, see below
workspace/           The shared codebase agents read and write (gitignored)
mailboard.json       The ledger (generated at runtime, gitignored)
config.example.yaml  Template — copy to config.yaml (gitignored) and edit
config.deploy.yaml   Committed public-hosting config (used by the PythonAnywhere path)
config.vercel.yaml   Committed public-hosting config (used by the Vercel path)
```

### The `landing/` marketing site

`landing/` is a separate Vite/React app (originally generated in Figma Make)
deployed as its **own** Vercel project, independent from the Flask app. The
Flask app's `/` route just redirects there (`/app` is the actual dashboard).
It has its own `package.json`/`npm install`/`npm run build` — it's not part
of the Python app at all, just living in the same repo for convenience.

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
- Google login only verifies identity via Google's `tokeninfo` endpoint
  (no JWT library, matching the rest of this app's stdlib-only approach) —
  fine for this app's purposes (an account handle to key saved API keys
  to), but don't lean on it as a hardened auth system for anything more
  sensitive.
