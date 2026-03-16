"""Shared memory orderbook reader/writer implementing pm_core protocols.

BookWriterImpl and BookReaderImpl use a shared memory region to store orderbook
snapshots with sub-microsecond read latency. Torn-write detection via sequence
numbers ensures readers never see partial updates.
"""

from __future__ import annotations

import struct
import time
from typing import Any

from pm_store.shmem.book import (
    LEVEL_SIZE,
    MAX_LEVELS,
    SLOT_FIXED_FMT,
    SLOT_FIXED_SIZE,
    SLOT_SIZE,  # noqa: F401
    BookRegion,
)
from pm_store.shmem.index import SlotIndex


class BookWriterImpl:
    """Shared memory orderbook writer implementing pm_core.protocols.BookWriter.

    Protocol::

        def update(self, asset_id, bid, ask, bids, asks, bid_depth, ask_depth) -> None
    """

    def __init__(self, region: BookRegion, index: SlotIndex) -> None:
        self._region = region
        self._index = index

    def update(
        self,
        asset_id: str,
        bid: float,
        ask: float,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        bid_depth: float,
        ask_depth: float,
    ) -> None:
        """Write an orderbook snapshot to the shared memory slot for this asset.

        Uses a sequence number protocol for torn-write detection:
        1. Write seq = odd (signals "write in progress")
        2. Write data
        3. Write seq = even (signals "write complete")
        Readers check seq before and after reading — mismatch means torn read.
        """
        slot = self._index.insert_deterministic(asset_id)
        offset = self._region.slot_offset(slot)
        buf = self._region.buf

        # Read current sequence
        current_seq = struct.unpack_from("<Q", buf, offset)[0]
        new_seq = current_seq + 2  # Always increment by 2 (even = complete)

        # Step 1: Write odd seq (write in progress)
        struct.pack_into("<Q", buf, offset, new_seq - 1)

        # Step 2: Write fixed fields
        timestamp_ns = time.time_ns()
        struct.pack_into(
            SLOT_FIXED_FMT,
            buf,
            offset,
            new_seq - 1,  # will be overwritten in step 3
            timestamp_ns,
            bid,
            ask,
            bid_depth,
            ask_depth,
        )

        # Step 2b: Write bid levels
        levels_offset = offset + SLOT_FIXED_SIZE
        n_bids = min(len(bids), MAX_LEVELS)
        for i in range(n_bids):
            struct.pack_into(
                "<dd",
                buf,
                levels_offset + i * LEVEL_SIZE,
                bids[i][0],
                bids[i][1],
            )
        # Zero remaining bid slots
        for i in range(n_bids, MAX_LEVELS):
            struct.pack_into(
                "<dd",
                buf,
                levels_offset + i * LEVEL_SIZE,
                0.0,
                0.0,
            )

        # Step 2c: Write ask levels
        asks_offset = levels_offset + MAX_LEVELS * LEVEL_SIZE
        n_asks = min(len(asks), MAX_LEVELS)
        for i in range(n_asks):
            struct.pack_into(
                "<dd",
                buf,
                asks_offset + i * LEVEL_SIZE,
                asks[i][0],
                asks[i][1],
            )
        for i in range(n_asks, MAX_LEVELS):
            struct.pack_into(
                "<dd",
                buf,
                asks_offset + i * LEVEL_SIZE,
                0.0,
                0.0,
            )

        # Step 2d: Write level counts
        tail_offset = asks_offset + MAX_LEVELS * LEVEL_SIZE
        struct.pack_into("<HH", buf, tail_offset, n_bids, n_asks)

        # Step 3: Write even seq (write complete)
        struct.pack_into("<Q", buf, offset, new_seq)


class BookReaderImpl:
    """Shared memory orderbook reader implementing pm_core.protocols.BookReader.

    Protocol::

        def get(self, asset_id) -> dict | None
        def stale_ns(self, asset_id) -> int | None
    """

    def __init__(self, region: BookRegion, index: SlotIndex) -> None:
        self._region = region
        self._index = index

    def get(self, asset_id: str) -> dict[str, Any] | None:
        """Read the orderbook snapshot for an asset.

        Returns None if the asset is not found or if a torn write is detected.
        Torn writes are detected by checking the sequence number before and after
        reading — if it changed (or is odd), the read is invalid.
        """
        slot = self._index.lookup(asset_id)
        if slot is None:
            return None

        offset = self._region.slot_offset(slot)
        buf = self._region.buf

        # Read seq before
        seq_before = struct.unpack_from("<Q", buf, offset)[0]
        if seq_before % 2 != 0:
            return None  # Write in progress

        # Read fixed fields
        _, timestamp_ns, best_bid, best_ask, bid_depth, ask_depth = struct.unpack_from(
            SLOT_FIXED_FMT, buf, offset
        )

        # Read level counts
        levels_offset = offset + SLOT_FIXED_SIZE
        asks_start = levels_offset + MAX_LEVELS * LEVEL_SIZE
        tail_offset = asks_start + MAX_LEVELS * LEVEL_SIZE
        n_bids, n_asks = struct.unpack_from("<HH", buf, tail_offset)

        # Read bid levels
        bids = []
        for i in range(min(n_bids, MAX_LEVELS)):
            price, size = struct.unpack_from("<dd", buf, levels_offset + i * LEVEL_SIZE)
            bids.append((price, size))

        # Read ask levels
        asks = []
        for i in range(min(n_asks, MAX_LEVELS)):
            price, size = struct.unpack_from("<dd", buf, asks_start + i * LEVEL_SIZE)
            asks.append((price, size))

        # Read seq after (torn write check)
        seq_after = struct.unpack_from("<Q", buf, offset)[0]
        if seq_after != seq_before:
            return None  # Torn write detected

        if timestamp_ns == 0:
            return None  # Never written

        return {
            "asset_id": asset_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bids": bids,
            "asks": asks,
            "bid_depth_usd": bid_depth,
            "ask_depth_usd": ask_depth,
            "timestamp_ns": timestamp_ns,
            "seq": seq_after,
        }

    def stale_ns(self, asset_id: str) -> int | None:
        """Return nanoseconds since last update, or None if asset not found."""
        slot = self._index.lookup(asset_id)
        if slot is None:
            return None

        offset = self._region.slot_offset(slot)
        buf = self._region.buf

        # Read timestamp_ns (at offset + 8)
        timestamp_ns = struct.unpack_from("<Q", buf, offset + 8)[0]
        if timestamp_ns == 0:
            return None

        return time.time_ns() - timestamp_ns


# Re-export for convenience

__all__ = ["BookReaderImpl", "BookWriterImpl", "BookRegion", "SlotIndex"]
