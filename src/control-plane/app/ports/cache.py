"""Cache / rate-limit port.

The current adapter is Redis (``RedisRateLimiter``). Tests use ``InMemoryRateLimiter``.
"""

from __future__ import annotations

from typing import Protocol


class RateLimiter(Protocol):
    def allow(self, identifier: str) -> bool:
        """Return True if a request for ``identifier`` is within the limit."""
        ...
