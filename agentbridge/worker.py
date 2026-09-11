"""A local worker that runs turns for one agent using a LOCAL API key.

The key lives only in this process's environment and is used to call the
provider (Anthropic/OpenAI/Ollama) directly from this machine. Only the
non-secret prompt (fetched from the server) and the non-secret result
(sent back to the server) ever cross the network to the hosted app — the
key itself never does.
"""

import json
import os
import sys
import time
import urllib.error
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


def _get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise WorkerError(f"{url} returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise WorkerError(f"Could not reach {url}: {e.reason}") from e


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise WorkerError(f"{url} returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise WorkerError(f"Could not reach {url}: {e.reason}") from e


def credential_for(provider: str) -> str | None:
    env_var = CREDENTIAL_ENV.get(provider)
    return os.environ.get(env_var) if env_var else None


def run_one_turn(server: str, agent_name: str) -> dict:
    server = server.rstrip("/")
    prepared = _get(f"{server}/api/turn/prepare?agent={agent_name}")
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
    print(f"({CREDENTIAL_ENV.get(agent_name, '')} never leaves this machine.) Ctrl+C to stop.\n")

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
