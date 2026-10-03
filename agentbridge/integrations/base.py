"""The contract every integration follows.

An integration is a subclass of Integration that declares:
  - `settings`: the env vars it reads (one place to see what to configure)
  - `routes()`: the HTTP endpoints it exposes under /api/integrations/<name>/
  - optionally `public_info()` (non-secret facts the frontend may need) and
    `check()` (a live call that proves the credentials work)

The blueprint in web.py does everything else uniformly -- 503 when a
setting is missing, error-to-JSON translation, rate limiting, CORS -- so a
new integration is just its own API calls, not another copy of that plumbing.
"""

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class IntegrationError(RuntimeError):
    """A failure that's safe to report to the caller.

    `message` is what the visitor sees. `detail` is the raw upstream
    response, which can contain account names, file IDs or token errors, so
    it only ever goes to the server log -- never into the HTTP response.
    """

    def __init__(self, message: str, status: int = 502, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


class NotConfiguredError(IntegrationError):
    def __init__(self, integration: str, missing: list[str]):
        super().__init__(f"{integration} integration is not configured on this server", status=503)
        self.missing = missing


@dataclass(frozen=True)
class Setting:
    env: str
    description: str
    required: bool = True
    # Non-secret settings (file IDs, an inbox address) may be printed by the
    # CLI; secret ones are only ever reported as set/unset.
    secret: bool = True
    default: str | None = None


@dataclass(frozen=True)
class Route:
    path: str
    handler: Callable[..., Any]
    methods: tuple[str, ...] = ("GET",)
    # (max_requests, window_seconds) per client IP, or None for no limit.
    rate_limit: tuple[int, float] | None = None
    # Cache-Control for JSON responses. Handlers returning their own Response
    # set their own headers.
    cache_control: str = "no-store"


class Integration:
    name: str = ""
    title: str = ""
    settings: tuple[Setting, ...] = ()

    def __init__(self, environ: Mapping[str, str] | None = None):
        # Read lazily from os.environ by default so env vars changed after
        # import (tests, a reloaded .env) are picked up.
        self._environ = environ

    @property
    def environ(self) -> Mapping[str, str]:
        return self._environ if self._environ is not None else os.environ

    def get(self, env: str) -> str | None:
        for s in self.settings:
            if s.env == env:
                value = self.environ.get(env, "").strip()
                return value or s.default
        raise KeyError(f"{env} is not a declared setting of {self.name}")

    def require(self, env: str) -> str:
        value = self.get(env)
        if not value:
            raise NotConfiguredError(self.title, [env])
        return value

    def number(self, env: str) -> float:
        """A numeric setting, with a clear error for a typo like "5m"."""
        value = self.get(env)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise IntegrationError(
                f"{self.title} is misconfigured on this server",
                status=500,
                detail=f"{env} must be a number of seconds, got {value!r}",
            ) from None

    def missing(self) -> list[str]:
        return [s.env for s in self.settings if s.required and not self.get(s.env)]

    def is_configured(self) -> bool:
        return not self.missing()

    def ensure_configured(self) -> None:
        missing = self.missing()
        if missing:
            raise NotConfiguredError(self.title, missing)

    def routes(self) -> list[Route]:
        return []

    def public_info(self) -> dict:
        """Non-secret details the frontend can use (e.g. which Drive file
        aliases exist). Only called when the integration is configured."""
        return {}

    def check(self) -> str:
        """Makes a real API call to verify credentials; returns a short
        human-readable success message or raises IntegrationError."""
        self.ensure_configured()
        return "configured (no live check implemented)"


@dataclass
class TTLCache:
    """Tiny per-instance cache so repeated page views don't each hit a
    third-party API (and its rate limits). Per serverless instance only;
    the Cache-Control headers set by each route let Vercel's CDN do the
    cross-instance caching."""

    ttl_seconds: float
    _data: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_or_set(self, key: str, compute: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = compute()
        with self._lock:
            self._data[key] = (now + self.ttl_seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def parse_mapping(raw: str | None) -> dict[str, str]:
    """Parses "resume=1AbC,portfolio=2DeF" into {"resume": "1AbC", ...}."""
    result = {}
    for part in (raw or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            result[key] = value
    return result


def parse_list(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]
