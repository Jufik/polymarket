"""Orderbook shared memory layout — struct definitions and region lifecycle."""

from __future__ import annotations

import struct
from multiprocessing import shared_memory

import structlog

log = structlog.get_logger()

# Region constants
REGION_NAME = "pm_orderbook"
MAGIC = 0x504D424B  # "PMBK"
VERSION = 1

# Slot layout
MAX_LEVELS = 10  # max bid/ask levels per slot
# Each level: (price: f64, size: f64) = 16 bytes
LEVEL_SIZE = 16
# Slot: seq(u64) + timestamp_ns(u64) + best_bid(f64) + best_ask(f64) +
#        bid_depth(f64) + ask_depth(f64) + bids(MAX_LEVELS * LEVEL_SIZE) +
#        asks(MAX_LEVELS * LEVEL_SIZE) + n_bids(u16) + n_asks(u16) + pad(4)
SLOT_HEADER_SIZE = 8 + 8 + 8 + 8 + 8 + 8  # 48 bytes
SLOT_LEVELS_SIZE = 2 * MAX_LEVELS * LEVEL_SIZE  # 320 bytes
SLOT_TAIL_SIZE = 2 + 2 + 4  # n_bids, n_asks, padding
SLOT_SIZE = SLOT_HEADER_SIZE + SLOT_LEVELS_SIZE + SLOT_TAIL_SIZE  # 376 bytes

# Region header: magic(u32) + version(u16) + max_slots(u16) + slot_size(u32) + pad(4)
HEADER_SIZE = 4 + 2 + 2 + 4 + 4  # 16 bytes

# Struct formats
HEADER_FMT = "<IHHIxxxx"  # magic, version, max_slots, slot_size, pad (little-endian)
SLOT_FMT = f"<QQdddd{MAX_LEVELS}d{MAX_LEVELS}d{MAX_LEVELS}d{MAX_LEVELS}dHHxxxx"

# Simplified slot pack/unpack (just the fixed fields, levels handled separately)
SLOT_FIXED_FMT = "<QQdddd"  # seq, timestamp_ns, best_bid, best_ask, bid_depth, ask_depth
SLOT_FIXED_SIZE = struct.calcsize(SLOT_FIXED_FMT)


class BookRegion:
    """Shared memory region for orderbook data.

    Layout::

        [HEADER: 16 bytes]
        [SLOT 0: SLOT_SIZE bytes]
        [SLOT 1: SLOT_SIZE bytes]
        ...
        [SLOT N-1: SLOT_SIZE bytes]
    """

    def __init__(self, shm: shared_memory.SharedMemory, max_slots: int) -> None:
        self._shm = shm
        self._max_slots = max_slots

    @classmethod
    def create(cls, max_slots: int = 4096, name: str = REGION_NAME) -> BookRegion:
        """Create a new shared memory region."""
        total_size = HEADER_SIZE + max_slots * SLOT_SIZE
        shm = shared_memory.SharedMemory(name=name, create=True, size=total_size)

        # Write header
        struct.pack_into(HEADER_FMT, shm.buf, 0, MAGIC, VERSION, max_slots, SLOT_SIZE)

        log.info("shmem.created", name=name, slots=max_slots, size=total_size)
        return cls(shm, max_slots)

    @classmethod
    def attach(cls, name: str = REGION_NAME) -> BookRegion:
        """Attach to an existing shared memory region."""
        shm = shared_memory.SharedMemory(name=name, create=False)

        # Validate header
        magic, version, max_slots, slot_size = struct.unpack_from(HEADER_FMT, shm.buf, 0)
        if magic != MAGIC:
            raise ValueError(f"Invalid magic: 0x{magic:08X} (expected 0x{MAGIC:08X})")
        if version != VERSION:
            raise ValueError(f"Unsupported version: {version}")
        if slot_size != SLOT_SIZE:
            raise ValueError(f"Slot size mismatch: {slot_size} != {SLOT_SIZE}")

        return cls(shm, max_slots)

    def slot_offset(self, slot_index: int) -> int:
        """Get the byte offset of a slot."""
        if slot_index < 0 or slot_index >= self._max_slots:
            raise IndexError(f"Slot index {slot_index} out of range [0, {self._max_slots})")
        return HEADER_SIZE + slot_index * SLOT_SIZE

    @property
    def buf(self) -> memoryview:
        return self._shm.buf

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def name(self) -> str:
        return self._shm.name

    def close(self) -> None:
        """Close (but don't unlink) the shared memory."""
        self._shm.close()

    def unlink(self) -> None:
        """Unlink (destroy) the shared memory region."""
        self._shm.unlink()
