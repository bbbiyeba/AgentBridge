"""mailboard.json read/write — the shared ledger agents coordinate through."""

import json
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Mailboard:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            self._write({"task": "", "next_agent": None, "entries": []})

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(self.path)

    @property
    def task(self) -> str:
        return self._read().get("task", "")

    def set_task(self, task: str) -> None:
        data = self._read()
        data["task"] = task
        self._write(data)

    @property
    def next_agent(self) -> str | None:
        return self._read().get("next_agent")

    def entries(self) -> list[dict]:
        return self._read().get("entries", [])

    def recent(self, n: int) -> list[dict]:
        return self.entries()[-n:]

    def append(
        self,
        agent: str,
        provider: str,
        model: str,
        message: str,
        files: list[dict],
        handoff: str | None,
    ) -> dict:
        data = self._read()
        entry = {
            "id": len(data["entries"]) + 1,
            "timestamp": _now(),
            "agent": agent,
            "provider": provider,
            "model": model,
            "message": message,
            "files": files,
            "handoff": handoff,
        }
        data["entries"].append(entry)
        data["next_agent"] = handoff
        self._write(data)
        return entry

    def state(self) -> dict:
        return self._read()
