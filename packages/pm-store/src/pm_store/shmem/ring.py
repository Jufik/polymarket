"""Generic SPMC (single-producer, multi-consumer) ring buffer over shared memory.

Used for broadcasting events (e.g., trades) from a single writer to multiple
reader processes without kernel IPC overhead.
"""

from __future__ import annotations

import struct
from multiprocessing import shared_memory

# Ring header: magic(u32) + version(u16) + capacity(u16) + item_size(u32) +
#              write_seq(u64) + pad(12)
RING_MAGIC = 0x504D5247  # "PMRG"
RING_VERSION = 1
RING_HEADER_FMT = "<IHHIQxxxxxxxxxxxx"  # 32 bytes
RING_HEADER_SIZE = struct.calcsize(RING_HEADER_FMT)


class RingBuffer:
    """SPMC ring buffer over shared memory.

    Layout::

        [HEADER: 32 bytes]
        [ITEM 0: item_size bytes]  — each item prefixed with seq(u64)
        ...
        [ITEM N-1: item_size bytes]

    The writer increments write_seq atomically after writing each item.
    Readers track their own read position and detect overwrite by comparing
    sequence numbers.
    """

    def __init__(
        self,
        shm: shared_memory.SharedMemory,
        capacity: int,
        item_size: int,
    ) -> None:
        self._shm = shm
        self._capacity = capacity
        self._item_size = item_size
        # Each slot has an 8-byte sequence prefix
        self._slot_size = 8 + item_size

    @classmethod
    def create(
        cls,
        name: str,
        capacity: int = 1024,
        item_size: int = 256,
    ) -> RingBuffer:
        """Create a new ring buffer in shared memory."""
        total_size = RING_HEADER_SIZE + capacity * (8 + item_size)
        shm = shared_memory.SharedMemory(name=name, create=True, size=total_size)

        # Write header
        struct.pack_into(
            RING_HEADER_FMT,
            shm.buf,
            0,
            RING_MAGIC,
            RING_VERSION,
            capacity,
            item_size,
            0,
        )

        return cls(shm, capacity, item_size)

    @classmethod
    def attach(cls, name: str) -> RingBuffer:
        """Attach to an existing ring buffer."""
        shm = shared_memory.SharedMemory(name=name, create=False)

        magic, version, capacity, item_size, _ = struct.unpack_from(RING_HEADER_FMT, shm.buf, 0)
        if magic != RING_MAGIC:
            raise ValueError(f"Invalid ring magic: 0x{magic:08X}")
        if version != RING_VERSION:
            raise ValueError(f"Unsupported ring version: {version}")

        return cls(shm, capacity, item_size)

    def _slot_offset(self, index: int) -> int:
        return RING_HEADER_SIZE + index * self._slot_size

    @property
    def write_seq(self) -> int:
        """Current write sequence number (from header)."""
        return struct.unpack_from("<Q", self._shm.buf, 16)[0]

    def write(self, data: bytes) -> int:
        """Write data to the next slot. Returns the sequence number written.

        Data must be <= item_size bytes (padded with zeros if shorter).
        """
        if len(data) > self._item_size:
            raise ValueError(f"Data too large: {len(data)} > {self._item_size}")

        seq = self.write_seq + 1
        index = (seq - 1) % self._capacity
        offset = self._slot_offset(index)

        # Write sequence number
        struct.pack_into("<Q", self._shm.buf, offset, seq)
        # Write data (zero-padded)
        padded = data.ljust(self._item_size, b"\x00")
        self._shm.buf[offset + 8 : offset + 8 + self._item_size] = padded

        # Update header write_seq
        struct.pack_into("<Q", self._shm.buf, 16, seq)

        return seq

    def read(self, seq: int) -> tuple[int, bytes] | None:
        """Read the item at the given sequence number.

        Returns (actual_seq, data) or None if the slot has been overwritten
        (i.e., reader is too slow).
        """
        index = (seq - 1) % self._capacity
        offset = self._slot_offset(index)

        # Read sequence number (check for torn read)
        actual_seq = struct.unpack_from("<Q", self._shm.buf, offset)[0]
        if actual_seq != seq:
            return None  # Overwritten or not yet written

        data = bytes(self._shm.buf[offset + 8 : offset + 8 + self._item_size])
        return actual_seq, data

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def item_size(self) -> int:
        return self._item_size

    def close(self) -> None:
        self._shm.close()

    def unlink(self) -> None:
        self._shm.unlink()
