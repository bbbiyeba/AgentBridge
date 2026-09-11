"""The turn loop: pick an agent, give it context, apply its writes, log it, repeat."""

import difflib
import json
import re
from pathlib import Path

from .config import Config
from .mailboard import Mailboard
from .providers import chat

FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

# Providers that use a secret API key (as opposed to ollama's plain host URL).
# When settings.require_client_keys is on, turns for these providers must
# come with a caller-supplied credential and never fall back to the
# server's own environment variable.
KEYED_PROVIDERS = {"anthropic", "openai"}

RESPONSE_CONTRACT = """
Respond with a single JSON object and nothing else (no prose outside it), matching:
{
  "message": "<a short summary of what you did and why, for the ledger>",
  "files": [{"path": "<relative path from the workspace root>", "content": "<full new file content>"}],
  "handoff": "<name of the next agent that should act, or null to end the run>"
}
Always include the FULL new content of any file you create or modify, not a diff or a snippet.
Use forward slashes in paths and never write outside the workspace root.
If you have nothing to change, return an empty "files" list.
"""


def build_file_tree(root: Path, max_entries: int = 200) -> str:
    root = Path(root)
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            lines.append(f"{rel}/")
        else:
            lines.append(f"{rel} ({path.stat().st_size} bytes)")
        if len(lines) >= max_entries:
            lines.append("... (truncated)")
            break
    return "\n".join(lines) if lines else "(empty)"


def build_ledger_context(entries: list[dict]) -> str:
    if not entries:
        return "(no turns yet)"
    chunks = []
    for e in entries:
        files = ", ".join(f["path"] for f in e.get("files", [])) or "(no files changed)"
        chunks.append(
            f"[{e['timestamp']}] {e['agent']} ({e['provider']}/{e['model']}):\n"
            f"  {e['message']}\n"
            f"  files: {files}\n"
            f"  handoff -> {e.get('handoff') or '(none)'}"
        )
    return "\n\n".join(chunks)


def parse_response(text: str) -> dict:
    text = text.strip()
    match = FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Agent response was not valid JSON ({e}). Raw response:\n{text[:2000]}"
        ) from e
    if "message" not in data:
        raise ValueError(f"Agent response is missing 'message': {data}")
    data.setdefault("files", [])
    data.setdefault("handoff", None)
    return data


def diff_for(old: str | None, new: str, path: str) -> str:
    old_lines = (old or "").splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}")
    return "".join(diff)


class Orchestrator:
    def __init__(self, config: Config, mailboard: Mailboard):
        self.config = config
        self.mailboard = mailboard
        self.workspace = Path(config.settings.workspace_dir)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _resolve_in_workspace(self, rel_path: str) -> Path:
        root = self.workspace.resolve()
        target = (self.workspace / rel_path.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Path escapes the workspace root: {rel_path}")
        return target

    def take_turn(self, agent_name: str | None = None, credentials: dict[str, str] | None = None) -> dict:
        credentials = credentials or {}
        name = agent_name or self.mailboard.next_agent or self.config.agents[0].name
        agent = self.config.get_agent(name)

        credential = credentials.get(agent.provider)
        if (
            self.config.settings.require_client_keys
            and agent.provider in KEYED_PROVIDERS
            and not credential
        ):
            raise ValueError(
                f"This deployment requires your own {agent.provider} API key — "
                f"add it above before running '{agent.name}'."
            )

        task = self.mailboard.task
        ledger_context = build_ledger_context(
            self.mailboard.recent(self.config.settings.max_ledger_context)
        )
        file_tree = build_file_tree(self.workspace)

        system = f"{agent.role.strip()}\n\n{RESPONSE_CONTRACT}"
        user_prompt = (
            f"TASK:\n{task or '(no task set)'}\n\n"
            f"RECENT LEDGER ENTRIES:\n{ledger_context}\n\n"
            f"CURRENT WORKSPACE FILE TREE ({self.workspace}/):\n{file_tree}\n"
        )

        raw = chat(
            agent.provider,
            agent.model,
            system,
            [{"role": "user", "content": user_prompt}],
            credential=credential,
        )
        parsed = parse_response(raw)

        applied = []
        for f in parsed["files"]:
            target = self._resolve_in_workspace(f["path"])
            old_content = target.read_text(encoding="utf-8") if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"], encoding="utf-8")
            applied.append(
                {
                    "path": target.relative_to(self.workspace.resolve()).as_posix(),
                    "action": "modified" if old_content is not None else "created",
                    "diff": diff_for(old_content, f["content"], f["path"]),
                }
            )

        return self.mailboard.append(
            agent=agent.name,
            provider=agent.provider,
            model=agent.model,
            message=parsed["message"],
            files=applied,
            handoff=parsed.get("handoff"),
        )

    def run(self, max_turns: int | None = None, start_agent: str | None = None) -> list[dict]:
        max_turns = max_turns or self.config.settings.max_turns
        results = []
        next_agent = start_agent
        for _ in range(max_turns):
            entry = self.take_turn(next_agent)
            results.append(entry)
            next_agent = entry.get("handoff")
            if not next_agent:
                break
        return results
