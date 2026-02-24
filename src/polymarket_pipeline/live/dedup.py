"""Trade deduplication with TTL-based eviction."""

from __future__ import annotations

import time


class TradeDedup:
    """Set-based dedup with TTL eviction.

    Entries older than ttl_s are evicted on each check.
    """

    __slots__ = ("_seen", "_ttl_s")

    def __init__(self, ttl_s: float = 300.0) -> None:
        self._seen: dict[str, float] = {}
        self._ttl_s = ttl_s

    def is_duplicate(self, trade_id: str) -> bool:
        """Return True if trade_id was seen within TTL."""
        now = time.monotonic()
        self._evict(now)
        if trade_id in self._seen:
            return True
        self._seen[trade_id] = now
        return False

    def _evict(self, now: float) -> None:
        """Remove entries older than TTL."""
        cutoff = now - self._ttl_s
        expired = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in expired:
            del self._seen[k]

    def __len__(self) -> int:
        return len(self._seen)
