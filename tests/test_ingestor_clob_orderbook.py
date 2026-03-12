"""Tests for CLOBOrderbookIngestor."""

from __future__ import annotations

import json
from typing import Any


class MockBroker:
    """Captures published messages."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, bytes]] = []  # (message, topic, key)

    async def publish(self, message: str, topic: str, key: bytes) -> None:
        self.messages.append((message, topic, key))


class _StubRegistry:
    """Minimal stub — tests don't exercise the registry."""

    async def get_desired(self) -> set[str]:
        return set()


def _make_ingestor(
    broker: Any = None, token_map: dict[str, tuple[str, str]] | None = None
) -> Any:
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    return CLOBOrderbookIngestor(
        broker=broker or MockBroker(),
        registry=_StubRegistry(),  # type: ignore[arg-type]
        topic="orderbooks.raw",
        status_topic="pipeline.status",
        token_market_map=token_map or {},
    )


async def test_price_change_publishes_snapshot() -> None:
    """A price_change with level detail applies to the book and publishes."""
    broker = MockBroker()
    token_map: dict[str, tuple[str, str]] = {"asset_123": ("0xcond_abc", "YES")}
    ingestor = _make_ingestor(broker, token_map)

    msg = json.dumps(
        {
            "market": "0xcond_abc",
            "price_changes": [
                {
                    "asset_id": "asset_123",
                    "price": "0.55",
                    "size": "100",
                    "side": "BUY",
                }
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["condition_id"] == "0xcond_abc"
    assert data["asset_id"] == "asset_123"
    assert data["best_bid"] == 0.55
    assert data["bids"] == [[0.55, 100.0]]
    assert data["asks"] == []
    assert broker.messages[0][1] == "orderbooks.raw"
    assert broker.messages[0][2] == b"0xcond_abc"


async def test_price_change_old_style_bbo_only() -> None:
    """Old-style price_change with only best_bid/best_ask (no level detail) still works."""
    broker = MockBroker()
    ingestor = _make_ingestor(broker)

    msg = json.dumps(
        {
            "market": "0xfallback",
            "price_changes": [
                {
                    "asset_id": "unknown_asset",
                    "best_bid": "0.40",
                    "best_ask": "0.45",
                }
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["condition_id"] == "0xfallback"
    assert data["best_bid"] == 0.40
    assert data["best_ask"] == 0.45


async def test_non_price_change_ignored() -> None:
    """Unrecognised messages should be silently ignored."""
    broker: Any = MockBroker()
    ingestor = _make_ingestor(broker)

    msg = json.dumps({"event_type": "trade", "data": "something"})
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_missing_asset_id_skipped() -> None:
    """price_changes entry with missing asset_id should be skipped."""
    broker: Any = MockBroker()
    ingestor = _make_ingestor(broker)

    msg = json.dumps(
        {
            "market": "0xabc",
            "price_changes": [
                {"price": "0.50", "size": "100", "side": "BUY"}
                # No asset_id
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_orderbook_snapshot_list() -> None:
    """Initial orderbook snapshots (list with bids/asks) publish with L2 depth."""
    broker: Any = MockBroker()
    ingestor = _make_ingestor(broker)

    msg = json.dumps(
        [
            {
                "market": "0xmarket1",
                "asset_id": "asset_1",
                "bids": [
                    {"price": "0.30", "size": "200"},
                    {"price": "0.29", "size": "100"},
                ],
                "asks": [
                    {"price": "0.35", "size": "150"},
                    {"price": "0.36", "size": "80"},
                ],
            },
        ]
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["asset_id"] == "asset_1"
    assert data["best_bid"] == 0.30
    assert data["best_ask"] == 0.35
    assert len(data["bids"]) == 2
    assert len(data["asks"]) == 2
    # Bids descending
    assert data["bids"][0][0] == 0.30
    assert data["bids"][1][0] == 0.29
    # Asks ascending
    assert data["asks"][0][0] == 0.35
    assert data["asks"][1][0] == 0.36
    assert data["bid_depth_usd"] > 0
    assert data["ask_depth_usd"] > 0


async def test_multiple_price_changes_in_one_message() -> None:
    """A price_changes message with multiple assets publishes one per asset."""
    broker: Any = MockBroker()
    ingestor = _make_ingestor(broker)

    msg = json.dumps(
        {
            "market": "0xabc",
            "price_changes": [
                {"asset_id": "a1", "price": "0.60", "size": "50", "side": "BUY"},
                {"asset_id": "a2", "price": "0.30", "size": "50", "side": "SELL"},
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 2
    ids = {json.loads(m[0])["asset_id"] for m in broker.messages}
    assert ids == {"a1", "a2"}


async def test_empty_list_ignored() -> None:
    """Empty list (WS ack) should be silently ignored."""
    broker: Any = MockBroker()
    ingestor = _make_ingestor(broker)

    await ingestor._handle_message("[]")
    assert len(broker.messages) == 0


async def test_unchanged_dedup() -> None:
    """Publishing the same snapshot twice should skip the second (dedup)."""
    broker = MockBroker()
    ingestor = _make_ingestor(broker)

    snapshot_msg = json.dumps(
        [
            {
                "market": "0xm",
                "asset_id": "a1",
                "bids": [{"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.55", "size": "100"}],
            }
        ]
    )
    # First: publishes (force=True for snapshots)
    await ingestor._handle_message(snapshot_msg)
    assert len(broker.messages) == 1

    # Same snapshot again: force=True, so still publishes
    await ingestor._handle_message(snapshot_msg)
    assert len(broker.messages) == 2

    # Now a price_change that doesn't change the top-N
    pc_msg = json.dumps(
        {
            "market": "0xm",
            "price_changes": [
                {"asset_id": "a1", "price": "0.50", "size": "100", "side": "BUY"}
            ],
        }
    )
    await ingestor._handle_message(pc_msg)
    # Still 2 — the top-N didn't change so it was skipped
    assert len(broker.messages) == 2
    assert ingestor._skipped_unchanged >= 1


async def test_size_zero_removes_level() -> None:
    """A price_change with size=0 should remove a level from the book."""
    broker = MockBroker()
    ingestor = _make_ingestor(broker)

    # Seed the book with a snapshot
    snapshot = json.dumps(
        [
            {
                "market": "0xm",
                "asset_id": "a1",
                "bids": [
                    {"price": "0.50", "size": "100"},
                    {"price": "0.49", "size": "50"},
                ],
                "asks": [{"price": "0.55", "size": "80"}],
            }
        ]
    )
    await ingestor._handle_message(snapshot)
    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert len(data["bids"]) == 2

    # Remove the top bid level
    pc = json.dumps(
        {
            "market": "0xm",
            "price_changes": [
                {"asset_id": "a1", "price": "0.50", "size": "0", "side": "BUY"}
            ],
        }
    )
    await ingestor._handle_message(pc)
    assert len(broker.messages) == 2
    data = json.loads(broker.messages[1][0])
    assert data["best_bid"] == 0.49
    assert len(data["bids"]) == 1


async def test_snapshot_clears_book() -> None:
    """A new snapshot should replace all prior levels in the book."""
    broker = MockBroker()
    ingestor = _make_ingestor(broker)

    # First snapshot: 2 bid levels
    s1 = json.dumps(
        [
            {
                "market": "0xm",
                "asset_id": "a1",
                "bids": [
                    {"price": "0.50", "size": "100"},
                    {"price": "0.49", "size": "50"},
                ],
                "asks": [{"price": "0.55", "size": "80"}],
            }
        ]
    )
    await ingestor._handle_message(s1)
    data1 = json.loads(broker.messages[0][0])
    assert len(data1["bids"]) == 2

    # Second snapshot with only 1 bid level — should not carry over old levels
    s2 = json.dumps(
        [
            {
                "market": "0xm",
                "asset_id": "a1",
                "bids": [{"price": "0.48", "size": "200"}],
                "asks": [{"price": "0.52", "size": "120"}],
            }
        ]
    )
    await ingestor._handle_message(s2)
    data2 = json.loads(broker.messages[1][0])
    assert len(data2["bids"]) == 1
    assert data2["best_bid"] == 0.48


async def test_heartbeat_fields() -> None:
    """Heartbeat should include book tracking and dedup metrics."""
    broker = MockBroker()
    ingestor = _make_ingestor(broker)

    fields = ingestor._heartbeat_fields()
    assert "assets_tracked" in fields
    assert "skipped_unchanged" in fields
    assert fields["assets_tracked"] == 0

    # Seed a book
    s = json.dumps(
        [
            {
                "market": "0xm",
                "asset_id": "a1",
                "bids": [{"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.55", "size": "80"}],
            }
        ]
    )
    await ingestor._handle_message(s)
    fields = ingestor._heartbeat_fields()
    assert fields["assets_tracked"] == 1
