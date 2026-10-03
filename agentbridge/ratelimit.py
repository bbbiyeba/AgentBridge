"""Rate limiting for the app's routes.

SlidingWindowLimiter counts in process memory: free and dependency-free,
but each serverless instance (Vercel) keeps its own counts, so the real
limit is "N per instance", and counts reset on every cold start.

SharedLimiter (via make_limiter) counts in Upstash Redis instead when
RATE_LIMIT_STORE=upstash is set, so every instance shares one count per
client. It's opt-in, and it fails open to the in-memory limiter if
Upstash is unreachable: a Redis outage shouldn't take the site down.
"""

import logging
import os
import threading
import time
from collections import deque

from . import upstash

log = logging.getLogger(__name__)

# Every this-many hits, drop the buckets of clients that have gone quiet, so
# a long-lived process (PythonAnywhere) doesn't keep one entry per IP forever.
SWEEP_EVERY = 1000


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._hits_since_sweep = 0

    def hit(self, key: str) -> bool:
        """Records one request for `key`; returns True if it's over the limit."""
        now = time.monotonic()
        with self._lock:
            self._hits_since_sweep += 1
            if self._hits_since_sweep >= SWEEP_EVERY:
                self._sweep(now)
            bucket = self._buckets.setdefault(key, deque())
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return True
            bucket.append(now)
            return False

    def _sweep(self, now: float) -> None:
        self._hits_since_sweep = 0
        stale = [k for k, b in self._buckets.items() if not b or now - b[-1] > self.window_seconds]
        for k in stale:
            del self._buckets[k]


class SharedLimiter:
    """Fixed-window counter in Upstash: one INCR per request, in a key that
    names the current window, which expires on its own. A fixed window
    can admit up to 2x the limit across a window boundary -- an accepted
    trade for one round trip per request and no cleanup."""

    _last_warning = 0.0  # class-wide, so an outage logs about once a minute

    def __init__(self, name: str, max_requests: int, window_seconds: float):
        self.name = name
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.local = SlidingWindowLimiter(max_requests, window_seconds)

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("RATE_LIMIT_STORE", "").strip().lower() == "upstash" and upstash.is_configured()

    def hit(self, key: str) -> bool:
        if not self.enabled():
            return self.local.hit(key)
        window = int(time.time() // self.window_seconds)
        redis_key = f"agentbridge:rl:{self.name}:{key}:{window}"
        try:
            # Short timeout: this sits in front of every limited request.
            results = upstash.pipeline(
                [["INCR", redis_key], ["EXPIRE", redis_key, int(self.window_seconds) * 2 + 1]], timeout=2
            )
            count = int(results[0]["result"])
        except (upstash.UpstashError, KeyError, TypeError, ValueError) as e:
            now = time.monotonic()
            if now - SharedLimiter._last_warning > 60:
                SharedLimiter._last_warning = now
                log.warning("shared rate limiter unavailable, using per-instance limits: %s", e)
            return self.local.hit(key)
        return count > self.max_requests


def make_limiter(name: str, max_requests: int, window_seconds: float) -> SharedLimiter:
    """A limiter that's shared across instances when RATE_LIMIT_STORE=upstash
    (and Upstash is configured), and per-instance otherwise. `name` keeps
    different routes' counts apart in Redis."""
    return SharedLimiter(name, max_requests, window_seconds)
