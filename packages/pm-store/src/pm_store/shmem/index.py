"""Open-addressing hash index: asset_id -> slot index in shared memory."""

from __future__ import annotations

import hashlib


class SlotIndex:
    """In-memory open-addressing hash table mapping asset_id to slot index.

    Not stored in shared memory itself — each process builds its own from
    the set of known asset_ids. The slot assignment is deterministic
    (hash-based) so all processes agree on which slot holds which asset.
    """

    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = capacity
        self._table: dict[str, int] = {}
        self._next_slot = 0

    @staticmethod
    def _hash(asset_id: str) -> int:
        """Deterministic hash of an asset_id."""
        return int.from_bytes(hashlib.sha256(asset_id.encode()).digest()[:8], "little")

    def lookup(self, asset_id: str) -> int | None:
        """Look up the slot index for an asset_id. Returns None if not found."""
        return self._table.get(asset_id)

    def insert(self, asset_id: str) -> int:
        """Insert an asset_id and return its slot index.

        If the asset_id is already present, returns its existing slot.
        New assets get the next available slot index.
        """
        if asset_id in self._table:
            return self._table[asset_id]

        if self._next_slot >= self._capacity:
            raise RuntimeError(f"SlotIndex full: {self._next_slot} >= {self._capacity}")

        slot = self._next_slot
        self._table[asset_id] = slot
        self._next_slot += 1
        return slot

    def insert_deterministic(self, asset_id: str) -> int:
        """Insert using deterministic hash-based slot assignment.

        All processes calling this with the same asset_id get the same slot.
        Collisions are resolved via linear probing.
        """
        if asset_id in self._table:
            return self._table[asset_id]

        base = self._hash(asset_id) % self._capacity
        for i in range(self._capacity):
            slot = (base + i) % self._capacity
            # Check if slot is free (not in values)
            if slot not in self._table.values():
                self._table[asset_id] = slot
                return slot

        raise RuntimeError("SlotIndex full — no free slots")

    def remove(self, asset_id: str) -> bool:
        """Remove an asset_id from the index. Returns True if it was present."""
        if asset_id in self._table:
            del self._table[asset_id]
            return True
        return False

    @property
    def size(self) -> int:
        return len(self._table)

    @property
    def capacity(self) -> int:
        return self._capacity

    def all_assets(self) -> dict[str, int]:
        """Return a copy of the asset_id -> slot mapping."""
        return dict(self._table)
