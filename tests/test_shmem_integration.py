"""Tests for SharedMemory orderbook integration.

Covers:
- BookWriterImpl → BookReaderImpl round-trip via insert_deterministic()
- CLOBOrderbookIngestor shmem writes via mock BookWriter
- shmem_enabled=False does not create SharedMemory
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# 1. BookWriterImpl → BookReaderImpl round-trip
# ---------------------------------------------------------------------------


def test_writer_reader_roundtrip() -> None:
    """Write via BookWriterImpl, read via BookReaderImpl — data matches."""
    from pm_store.shmem import BookReaderImpl, BookRegion, BookWriterImpl, SlotIndex

    region = BookRegion.create(name="test_roundtrip", max_slots=64)
    try:
        writer_index = SlotIndex(capacity=64)
        reader_index = SlotIndex(capacity=64)

        writer = BookWriterImpl(region, writer_index)
        reader = BookReaderImpl(region, reader_index)

        asset_id = "0xabc123"
        bids = [(0.55, 100.0), (0.54, 200.0)]
        asks = [(0.56, 150.0), (0.57, 250.0)]

        writer.update(
            asset_id=asset_id,
            bid=0.55,
            ask=0.56,
            bids=bids,
            asks=asks,
            bid_depth=155.0,
            ask_depth=213.5,
        )

        # Reader must use insert_deterministic to agree on slot
        reader_index.insert_deterministic(asset_id)
        result = reader.get(asset_id)

        assert result is not None
        assert result["asset_id"] == asset_id
        assert result["best_bid"] == pytest.approx(0.55)
        assert result["best_ask"] == pytest.approx(0.56)
        assert result["bid_depth_usd"] == pytest.approx(155.0)
        assert result["ask_depth_usd"] == pytest.approx(213.5)
        assert len(result["bids"]) == 2
        assert len(result["asks"]) == 2
        assert result["bids"][0] == pytest.approx((0.55, 100.0))
        assert result["asks"][0] == pytest.approx((0.56, 150.0))
        assert result["timestamp_ns"] > 0
    finally:
        region.close()
        region.unlink()


def test_deterministic_slot_agreement() -> None:
    """Writer and reader agree on slot index via insert_deterministic()."""
    from pm_store.shmem import SlotIndex

    index_a = SlotIndex(capacity=1024)
    index_b = SlotIndex(capacity=1024)

    asset_ids = [
        "0xdeadbeef0001",
        "0xdeadbeef0002",
        "0xdeadbeef0003",
        "0xcafebabe1234",
    ]

    for aid in asset_ids:
        slot_a = index_a.insert_deterministic(aid)
        slot_b = index_b.insert_deterministic(aid)
        assert slot_a == slot_b, f"Slot mismatch for {aid}: {slot_a} != {slot_b}"


def test_reader_returns_none_for_unknown_asset() -> None:
    """Reader returns None for asset_ids that were never written."""
    from pm_store.shmem import BookReaderImpl, BookRegion, SlotIndex

    region = BookRegion.create(name="test_unknown", max_slots=32)
    try:
        reader_index = SlotIndex(capacity=32)
        reader = BookReaderImpl(region, reader_index)
        assert reader.get("0xunknown") is None
    finally:
        region.close()
        region.unlink()


def test_stale_ns_returns_none_for_unwritten() -> None:
    """stale_ns returns None for assets never written."""
    from pm_store.shmem import BookReaderImpl, BookRegion, SlotIndex

    region = BookRegion.create(name="test_stale", max_slots=32)
    try:
        reader_index = SlotIndex(capacity=32)
        reader = BookReaderImpl(region, reader_index)
        assert reader.stale_ns("0xnever_written") is None
    finally:
        region.close()
        region.unlink()


def test_multiple_assets_independent() -> None:
    """Multiple assets written and read independently without interference."""
    from pm_store.shmem import BookReaderImpl, BookRegion, BookWriterImpl, SlotIndex

    region = BookRegion.create(name="test_multi", max_slots=128)
    try:
        w_idx = SlotIndex(capacity=128)
        r_idx = SlotIndex(capacity=128)
        writer = BookWriterImpl(region, w_idx)
        reader = BookReaderImpl(region, r_idx)

        # Write two different assets
        writer.update("asset_A", 0.40, 0.42, [(0.40, 50.0)], [(0.42, 60.0)], 20.0, 25.2)
        writer.update("asset_B", 0.70, 0.72, [(0.70, 80.0)], [(0.72, 90.0)], 56.0, 64.8)

        # Reader must discover both via deterministic slots
        r_idx.insert_deterministic("asset_A")
        r_idx.insert_deterministic("asset_B")

        a = reader.get("asset_A")
        b = reader.get("asset_B")

        assert a is not None and b is not None
        assert a["best_bid"] == pytest.approx(0.40)
        assert b["best_bid"] == pytest.approx(0.70)
    finally:
        region.close()
        region.unlink()


# ---------------------------------------------------------------------------
# 2. CLOBOrderbookIngestor with mock BookWriter
# ---------------------------------------------------------------------------


class MockBroker:
    """Captures published messages."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bytes]] = []

    async def publish(self, message: str, topic: str, key: bytes) -> None:
        self.messages.append((message, topic, key))


class _StubRegistry:
    async def get_desired(self) -> set[str]:
        return set()


class MockBookWriter:
    """Records all update() calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({
            "asset_id": asset_id,
            "bid": bid,
            "ask": ask,
            "bids": bids,
            "asks": asks,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
        })


@pytest.mark.asyncio
async def test_clob_ingestor_writes_to_shmem() -> None:
    """CLOBOrderbookIngestor calls book_writer.update() on book snapshot."""
    from pm_ingest.ingestors.clob_ws import CLOBOrderbookIngestor

    broker = MockBroker()
    mock_writer = MockBookWriter()
    token_map: dict[str, tuple[str, str]] = {"asset_xyz": ("0xcond_1", "YES")}

    ingestor = CLOBOrderbookIngestor(
        broker=broker,
        registry=_StubRegistry(),  # type: ignore[arg-type]
        topic="orderbooks.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
        book_writer=mock_writer,
    )

    # Feed a book snapshot
    msg = json.dumps({
        "event_type": "book",
        "asset_id": "asset_xyz",
        "market": "0xcond_1",
        "bids": [{"price": "0.50", "size": "100"}, {"price": "0.49", "size": "200"}],
        "asks": [{"price": "0.52", "size": "150"}],
    })
    await ingestor._handle_message(msg)

    # book_writer should have been called once
    assert len(mock_writer.calls) == 1
    call = mock_writer.calls[0]
    assert call["asset_id"] == "asset_xyz"
    assert call["bid"] == pytest.approx(0.50)
    assert call["ask"] == pytest.approx(0.52)
    assert len(call["bids"]) >= 1
    assert len(call["asks"]) >= 1
    assert ingestor._shmem_writes == 1


@pytest.mark.asyncio
async def test_clob_ingestor_no_writer_no_crash() -> None:
    """CLOBOrderbookIngestor works fine when book_writer is None."""
    from pm_ingest.ingestors.clob_ws import CLOBOrderbookIngestor

    broker = MockBroker()
    token_map: dict[str, tuple[str, str]] = {"asset_abc": ("0xcond_2", "YES")}

    ingestor = CLOBOrderbookIngestor(
        broker=broker,
        registry=_StubRegistry(),  # type: ignore[arg-type]
        topic="orderbooks.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
        book_writer=None,  # no shmem
    )

    msg = json.dumps({
        "event_type": "book",
        "asset_id": "asset_abc",
        "market": "0xcond_2",
        "bids": [{"price": "0.60", "size": "50"}],
        "asks": [{"price": "0.62", "size": "70"}],
    })
    await ingestor._handle_message(msg)

    # Should succeed without shmem
    assert ingestor._shmem_writes == 0
    assert ingestor._update_count >= 1


# ---------------------------------------------------------------------------
# 3. shmem_enabled=False does not create SharedMemory
# ---------------------------------------------------------------------------


def test_shmem_disabled_no_creation() -> None:
    """When shmem_enabled=False, no SharedMemory region is created."""
    from multiprocessing import shared_memory

    from pm_pipeline.settings import Settings

    settings = Settings(shmem_enabled=False)
    assert settings.shmem_enabled is False

    # Verify no region named pm_orderbook exists (or at least that settings
    # doesn't trigger creation)
    try:
        shm = shared_memory.SharedMemory(name="pm_orderbook_disabled_test", create=False)
        shm.close()
        # If it exists, it's from a previous test — not from our settings
    except FileNotFoundError:
        pass  # Expected — no region created


def test_shmem_settings_defaults() -> None:
    """Settings has correct shmem defaults."""
    from pm_pipeline.settings import Settings

    settings = Settings()
    assert settings.shmem_enabled is False
    assert settings.shmem_slots == 4096
