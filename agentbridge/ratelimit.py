"""In-memory sliding-window rate limiter shared by the app's routes.

Resets on every cold start and isn't shared across serverless instances,
so it's not a hard guarantee on platforms like Vercel -- but it's free,
dependency-free, and still meaningfully slows down abuse within one warm
instance, which is the realistic threat for the endpoints that use it.
"""

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        """Records one request for `key`; returns True if it's over the limit."""
        now = time.monotonic()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return True
        bucket.append(now)
        return False
