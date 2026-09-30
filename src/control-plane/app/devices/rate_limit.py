"""Fixed-window rate limiting for device registration.

The limiter is keyed by source IP and backed by Redis in production. A simple
in-memory implementation is provided for tests and local development, and both
share the ``RateLimiter`` port so the dependency can be overridden.
"""

from __future__ import annotations

import time

from app.ports.cache import RateLimiter

__all__ = ["InMemoryRateLimiter", "RateLimiter", "RedisRateLimiter"]


class RedisRateLimiter:
    """Fixed-window counter stored in Redis.

    A key of the form ``prefix:identifier:window`` is incremented on each call
    and expires after the window elapses. Requests are allowed while the count
    stays at or below ``limit``. If Redis is unreachable the request is allowed
    (fail-open) so a limiter outage cannot block legitimate registrations.
    """

    def __init__(
        self,
        redis_client,
        *,
        limit: int,
        window_seconds: int,
        prefix: str = "reg_rl",
    ) -> None:
        self._redis = redis_client
        self._limit = limit
        self._window = window_seconds
        self._prefix = prefix

    def allow(self, identifier: str) -> bool:
        if self._limit <= 0:
            return True
        window_index = int(time.time()) // self._window
        key = f"{self._prefix}:{identifier}:{window_index}"
        try:
            count = self._redis.incr(key)
            if count == 1:
                self._redis.expire(key, self._window)
        except Exception:
            # Fail open: never let a limiter outage block registration.
            return True
        return int(count) <= self._limit


class InMemoryRateLimiter:
    """Process-local fixed-window limiter for tests and local development."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._counters: dict[tuple[str, int], int] = {}

    def allow(self, identifier: str) -> bool:
        if self._limit <= 0:
            return True
        window_index = int(time.time()) // self._window
        key = (identifier, window_index)
        count = self._counters.get(key, 0) + 1
        self._counters[key] = count
        return count <= self._limit
