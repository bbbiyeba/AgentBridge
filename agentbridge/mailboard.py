"""mailboard.json read/write — the shared ledger agents coordinate through."""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # POSIX: Vercel, PythonAnywhere, macOS, Linux
except ImportError:  # Windows: only the in-process lock below applies
    fcntl = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Mailboard:
    """Every change is a read-modify-write of one JSON file, so two writers
    at once (two workers, a worker and the web UI, a multi-process host)
    would otherwise both read the same state and the second write would
    silently drop the first one's entry -- or, sharing one temp file,
    corrupt the ledger outright. Writers therefore hold an exclusive lock
    for the whole read-modify-write, and each write goes to its own temp
    file before an atomic rename, so readers never see a partial file."""

    # Per-path, process-wide: flock() alone doesn't serialize threads on
    # every platform, and on Windows it's all we have.
    _thread_locks: dict[str, threading.Lock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        with self._thread_locks_guard:
            self._thread_lock = self._thread_locks.setdefault(str(self.path.resolve()), threading.Lock())
        with self._locked():
            if not self.path.exists():
                self._write({"task": "", "next_agent": None, "entries": []})

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            if fcntl is None:
                yield
                return
            with open(self._lock_path, "a") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    @property
    def task(self) -> str:
        return self._read().get("task", "")

    def set_task(self, task: str) -> None:
        with self._locked():
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
        with self._locked():
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
