"""The turn loop: pick an agent, give it context, apply its writes, log it, repeat."""

import difflib
import json
import re
from pathlib import Path

from .config import Config
from .mailboard import Mailboard
from .providers import chat

# Non-greedy and tried block by block: a reply can contain several fenced
# blocks, or file contents that themselves contain ``` fences.
FENCE_RE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.DOTALL)

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
Base edits on the file contents shown in the prompt. Don't rewrite a file whose contents
weren't shown (marked "not shown") unless you mean to replace it entirely.
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


def _visible_files(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]


def build_file_contents(root: Path, budget: int, priority: list[str] | None = None) -> str:
    """The workspace's text files, wrapped in <file> tags, within `budget`
    characters. Paths in `priority` (recently changed) go first so the files
    an agent is most likely to touch are the ones it actually sees."""
    root = Path(root)
    files = {p.relative_to(root).as_posix(): p for p in _visible_files(root)}
    ordered = [r for r in dict.fromkeys(priority or []) if r in files]
    ordered += [r for r in files if r not in ordered]

    shown, omitted, used = [], [], 0
    for rel in ordered:
        try:
            text = files[rel].read_text(encoding="utf-8")
        except UnicodeDecodeError:
            omitted.append(f"{rel} (binary)")
            continue
        except OSError:
            continue
        if used + len(text) > budget:
            omitted.append(f"{rel} ({len(text)} chars, over the context budget)")
            continue
        used += len(text)
        shown.append(f'<file path="{rel}">\n{text}\n</file>')
    if not shown and not omitted:
        return "(no files yet)"
    parts = shown
    if omitted:
        parts = parts + ["Not shown: " + "; ".join(omitted)]
    return "\n\n".join(parts)


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


def _json_candidates(text: str):
    yield text
    for match in FENCE_RE.finditer(text):
        yield match.group(1)
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        yield text[first : last + 1]


def parse_response(text) -> dict:
    """Extracts and type-checks the agent's JSON reply. Raises ValueError
    (shown to the user) for anything that doesn't fit the contract."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Agent returned an empty response.")
    text = text.strip()
    data, error = None, None
    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate.strip())
        except json.JSONDecodeError as e:
            error = error or e
            continue
        if isinstance(data, dict) and "message" in data:
            break
    if not isinstance(data, dict):
        reason = f"not valid JSON: {error}" if error and data is None else "JSON, but not an object"
        raise ValueError(f"Agent response was {reason}. Raw response:\n{text[:2000]}")
    if "message" not in data:
        raise ValueError(f"Agent response is missing 'message'. Raw response:\n{text[:2000]}")
    if not isinstance(data["message"], str):
        raise ValueError("Agent response's 'message' must be a string.")
    files = data.get("files")
    if files is None:
        files = []
    if not isinstance(files, list):
        raise ValueError("Agent response's 'files' must be a list.")
    handoff = data.get("handoff")
    if handoff is not None and not isinstance(handoff, str):
        raise ValueError("Agent response's 'handoff' must be an agent name or null.")
    return {"message": data["message"], "files": files, "handoff": handoff or None}


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

    def _find_agent(self, name):
        if not isinstance(name, str):
            return None
        for agent in self.config.agents:
            if agent.name.lower() == name.strip().lower():
                return agent
        return None

    def _resolve_in_workspace(self, rel_path: str) -> Path:
        root = self.workspace.resolve()
        target = (self.workspace / rel_path.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Path escapes the workspace root: {rel_path}")
        return target

    def prepare_turn(self, agent_name: str | None = None) -> dict:
        """Build the prompt for the next turn, without calling any provider or
        touching the filesystem. Used both by take_turn() and by remote
        workers that want to make the LLM call themselves."""
        if agent_name:
            agent = self.config.get_agent(agent_name)
        else:
            # A next_agent saved by an older version (or a since-removed
            # agent) must not wedge every "next in queue" turn.
            queued = self.mailboard.next_agent
            agent = self._find_agent(queued) or self.config.agents[0]

        task = self.mailboard.task
        recent = self.mailboard.recent(self.config.settings.max_ledger_context)
        ledger_context = build_ledger_context(recent)
        file_tree = build_file_tree(self.workspace)
        recently_changed = [f["path"] for e in reversed(recent) for f in e.get("files", []) if "path" in f]
        file_contents = build_file_contents(
            self.workspace, self.config.settings.max_file_context_chars, recently_changed
        )

        system = f"{agent.role.strip()}\n\n{RESPONSE_CONTRACT}"
        user_prompt = (
            f"TASK:\n{task or '(no task set)'}\n\n"
            f"RECENT LEDGER ENTRIES:\n{ledger_context}\n\n"
            f"CURRENT WORKSPACE FILE TREE ({self.workspace}/):\n{file_tree}\n\n"
            f"CURRENT FILE CONTENTS:\n{file_contents}\n"
        )
        return {
            "agent": {"name": agent.name, "provider": agent.provider, "model": agent.model},
            "system": system,
            "user_prompt": user_prompt,
        }

    def apply_turn(
        self,
        agent_name: str,
        message: str,
        files: list[dict],
        handoff: str | None,
    ) -> dict:
        """Apply an already-obtained agent response: write files, diff them,
        and append the ledger entry. Used both by take_turn() and by remote
        workers submitting a turn they ran locally."""
        agent = self.config.get_agent(agent_name)
        if not isinstance(message, str):
            raise ValueError("'message' must be a string")
        writes = self._validate_files(files)

        # Models often get the case wrong ("Reviewer"); an unknown name is
        # dropped (and noted) rather than stored, since a stored bad name
        # would make every following "next in queue" turn fail.
        next_agent = self._find_agent(handoff) if handoff else None
        if handoff and next_agent is None:
            message = f"{message}\n(handoff to {handoff!r} ignored: no agent by that name)"
        handoff = next_agent.name if next_agent else None

        root = self.workspace.resolve()
        applied = []
        try:
            for target, rel, content in writes:
                old_content = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                applied.append(
                    {
                        "path": target.relative_to(root).as_posix(),
                        "action": "modified" if old_content is not None else "created",
                        "diff": diff_for(old_content, content, rel),
                    }
                )
        finally:
            # Even if a write fails partway (disk full), whatever did land on
            # disk gets a ledger entry, so the workspace never changes
            # without a record of it.
            if applied or not writes:
                entry = self.mailboard.append(
                    agent=agent.name,
                    provider=agent.provider,
                    model=agent.model,
                    message=message,
                    files=applied,
                    handoff=handoff,
                )
        return entry

    def _validate_files(self, files) -> list[tuple[Path, str, str]]:
        """Checks every file entry before anything is written, so a bad
        entry halfway through can't leave a half-applied turn behind."""
        if files is None:
            return []
        if not isinstance(files, list):
            raise ValueError("'files' must be a list")
        root = self.workspace.resolve()
        writes, seen = [], set()
        for i, f in enumerate(files):
            if not isinstance(f, dict):
                raise ValueError(f"files[{i}] must be an object with 'path' and 'content'")
            rel, content = f.get("path"), f.get("content")
            if not isinstance(rel, str) or not rel.strip("/ ."):
                raise ValueError(f"files[{i}] needs a non-empty 'path'")
            if not isinstance(content, str):
                raise ValueError(f"files[{i}] ({rel}) needs string 'content'")
            target = self._resolve_in_workspace(rel)
            if target == root or target.is_dir():
                raise ValueError(f"{rel} is a directory, not a file")
            if target in seen:
                raise ValueError(f"{rel} appears more than once in this turn")
            for parent in target.parents:
                if parent == root:
                    break
                if parent.is_file() or parent in seen:
                    raise ValueError(f"{rel} would need {parent.relative_to(root).as_posix()} to be a directory, but it's a file")
            seen.add(target)
            writes.append((target, rel, content))
        for target, rel, _ in writes:
            if any(target in other.parents for other, _, _ in writes):
                raise ValueError(f"{rel} is used as both a file and a directory in this turn")
        return writes

    def take_turn(self, agent_name: str | None = None, credentials: dict[str, str] | None = None) -> dict:
        credentials = credentials or {}
        prepared = self.prepare_turn(agent_name)
        agent = prepared["agent"]

        credential = credentials.get(agent["provider"])
        if (
            self.config.settings.require_client_keys
            and agent["provider"] in KEYED_PROVIDERS
            and not credential
        ):
            raise ValueError(
                f"This deployment requires your own {agent['provider']} API key — "
                f"add it above before running '{agent['name']}'."
            )

        raw = chat(
            agent["provider"],
            agent["model"],
            prepared["system"],
            [{"role": "user", "content": prepared["user_prompt"]}],
            credential=credential,
        )
        parsed = parse_response(raw)

        return self.apply_turn(agent["name"], parsed["message"], parsed["files"], parsed.get("handoff"))

    def run(self, max_turns: int | None = None, start_agent: str | None = None) -> list[dict]:
        if max_turns is None:
            max_turns = self.config.settings.max_turns
        results = []
        next_agent = start_agent
        for _ in range(max_turns):
            entry = self.take_turn(next_agent)
            results.append(entry)
            next_agent = entry.get("handoff")
            if not next_agent:
                break
        return results
