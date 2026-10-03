"""In-memory sliding-window rate limiter shared by the app's routes.

Resets on every cold start and isn't shared across serverless instances,
so it's not a hard guarantee on platforms like Vercel -- but it's free,
dependency-free, and still meaningfully slows down abuse within one warm
instance, which is the realistic threat for the endpoints that use it.
"""

import threading
import time
from collections import deque

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
