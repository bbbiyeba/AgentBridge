"""A local worker that runs turns for one agent using a LOCAL API key.

The key lives only in this process's environment and is used to call the
provider (Anthropic/OpenAI/Ollama) directly from this machine. Only the
non-secret prompt (fetched from the server) and the non-secret result
(sent back to the server) ever cross the network to the hosted app — the
key itself never does.
"""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .orchestrator import parse_response
from .providers import ProviderError, chat

CREDENTIAL_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "OLLAMA_HOST",
}


class WorkerError(RuntimeError):
    """Raised for anything that goes wrong talking to the hosted server or
    running a turn locally."""


def _request(req: urllib.request.Request | str, timeout: float) -> dict:
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise WorkerError(f"{url} returned HTTP {e.code}: {detail}") from e
    except TimeoutError as e:
        raise WorkerError(f"{url} didn't respond within {timeout}s") from e
    except (OSError, http.client.HTTPException, ValueError) as e:
        # URLError, dropped connections, and urllib's ValueError/InvalidURL
        # for a malformed --server -- all reported, none crash watch().
        raise WorkerError(f"Could not reach {url}: {getattr(e, 'reason', e)}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # e.g. a login wall or platform error page served with HTTP 200
        raise WorkerError(f"{url} returned a page that isn't JSON -- is --server pointing at AgentBridge?") from e


def _get(url: str) -> dict:
    return _request(url, timeout=30)


def _post(url: str, payload: dict) -> dict:
    headers = {"content-type": "application/json"}
    worker_token = os.environ.get("WORKER_SUBMIT_TOKEN")
    if worker_token:
        headers["X-Worker-Token"] = worker_token
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return _request(req, timeout=180)


def credential_for(provider: str) -> str | None:
    env_var = CREDENTIAL_ENV.get(provider)
    return os.environ.get(env_var) if env_var else None


def run_one_turn(server: str, agent_name: str) -> dict:
    server = server.rstrip("/")
    # Encoded: a name with a space, & or # would otherwise crash or
    # silently ask for a different agent.
    prepared = _get(f"{server}/api/turn/prepare?{urllib.parse.urlencode({'agent': agent_name})}")
    if not prepared.get("ok"):
        raise WorkerError(prepared.get("error", "prepare failed"))

    agent = prepared["agent"]
    credential = credential_for(agent["provider"])
    if agent["provider"] in ("anthropic", "openai") and not credential:
        raise WorkerError(
            f"{CREDENTIAL_ENV[agent['provider']]} is not set locally — this worker "
            f"needs it to act as '{agent_name}' ({agent['provider']}/{agent['model']})."
        )

    try:
        raw = chat(
            agent["provider"],
            agent["model"],
            prepared["system"],
            [{"role": "user", "content": prepared["user_prompt"]}],
            credential=credential,
        )
    except ProviderError as e:
        raise WorkerError(str(e)) from e

    try:
        parsed = parse_response(raw)
    except ValueError as e:
        raise WorkerError(str(e)) from e

    result = _post(
        f"{server}/api/turn/submit",
        {
            "agent": agent_name,
            "message": parsed["message"],
            "files": parsed["files"],
            "handoff": parsed.get("handoff"),
        },
    )
    if not result.get("ok"):
        raise WorkerError(result.get("error", "submit failed"))
    return result["entry"]


def watch(server: str, agent_name: str, poll_interval: float) -> None:
    server = server.rstrip("/")
    print(f"Watching {server} — will act as '{agent_name}' whenever it's that agent's turn.")
    print("(Your provider API key is read from this machine's environment and never leaves it.) Ctrl+C to stop.\n")

    while True:
        try:
            state = _get(f"{server}/api/state")
        except WorkerError as e:
            print(f"  ! {e}", file=sys.stderr)
            time.sleep(poll_interval)
            continue

        next_agent = state.get("next_agent")
        entries = state.get("entries") or []
        agents = state.get("agents") or []
        first_agent_name = agents[0]["name"] if agents else None
        is_my_turn = next_agent == agent_name or (
            not entries and next_agent is None and agent_name == first_agent_name
        )

        if not is_my_turn:
            time.sleep(poll_interval)
            continue

        try:
            entry = run_one_turn(server, agent_name)
            print(f"[{entry['id']}] ran turn: {entry['message']}")
            for f in entry["files"]:
                print(f"    {f['action']}: {f['path']}")
        except WorkerError as e:
            print(f"  ! turn failed: {e}", file=sys.stderr)
            time.sleep(poll_interval)
