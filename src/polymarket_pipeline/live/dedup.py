"""Trade deduplication with TTL-based eviction."""

from __future__ import annotations

import time
from collections import OrderedDict


class TradeDedup:
    """Set-based dedup with TTL eviction.

    Uses OrderedDict for O(1) amortized eviction — entries are ordered by
    insertion time, so we only evict from the front until hitting a non-expired
    entry.
    """

    __slots__ = ("_seen", "_ttl_s")

    def __init__(self, ttl_s: float = 300.0) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
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
        """Remove entries older than TTL from the front of the OrderedDict."""
        cutoff = now - self._ttl_s
        while self._seen:
            # Peek at the oldest entry (front of OrderedDict)
            oldest_key, oldest_ts = next(iter(self._seen.items()))
            if oldest_ts >= cutoff:
                break
            del self._seen[oldest_key]

    def __len__(self) -> int:
        return len(self._seen)
