"""Immutable TokenMap for atomic refresh of asset_id -> (condition_id, outcome) lookups.

Usage::

    # Build a new snapshot and swap atomically (single reference assignment).
    new_map = TokenMap(fresh_entries)
    runner.token_map = new_map          # atomic in CPython (GIL)

The class is intentionally read-only: no mutation methods, no setters.
"""

from __future__ import annotations


class TokenMap:
    """Read-only mapping from ``asset_id`` to ``(condition_id, outcome)``.

    Designed for concurrent readers: construct a new instance and swap the
    reference instead of mutating a shared dict.
    """

    __slots__ = ("_map",)

    def __init__(self, entries: dict[str, tuple[str, str]]) -> None:
        # Defensive copy so callers cannot mutate our internal state.
        self._map: dict[str, tuple[str, str]] = dict(entries)

    def lookup(self, asset_id: str) -> tuple[str, str] | None:
        """Return ``(condition_id, outcome)`` for *asset_id*, or ``None``."""
        return self._map.get(asset_id)

    def lookup_yes_asset(self, condition_id: str) -> str | None:
        """Return the YES asset_id for a condition_id by scanning entries."""
        for asset_id, (cid, outcome) in self._map.items():
            if cid == condition_id and outcome.upper() in ("YES", "0"):
                return asset_id
        return None

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, asset_id: object) -> bool:
        return asset_id in self._map
