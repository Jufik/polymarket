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


async def test_price_change_publishes_snapshot() -> None:
    """A price_changes message should produce an orderbook snapshot."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker = MockBroker()
    token_map: dict[str, tuple[str, str]] = {"asset_123": ("0xcond_abc", "YES")}
    ingestor = CLOBOrderbookIngestor(
        broker=broker,
        topic="orderbooks.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
    )

    msg = json.dumps(
        {
            "market": "0xcond_abc",
            "price_changes": [
                {
                    "asset_id": "asset_123",
                    "price": "0.55",
                    "size": "100",
                    "side": "BUY",
                    "best_bid": "0.55",
                    "best_ask": "0.58",
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
    assert data["best_ask"] == 0.58
    assert broker.messages[0][1] == "orderbooks.raw"
    assert broker.messages[0][2] == b"0xcond_abc"


async def test_non_price_change_ignored() -> None:
    """Unrecognised messages should be silently ignored."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps({"event_type": "trade", "data": "something"})
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_missing_prices_skipped() -> None:
    """price_changes entry with missing bid/ask should be skipped."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        {
            "market": "0xabc",
            "price_changes": [
                {"asset_id": "asset_123", "price": "0.50", "size": "100", "side": "BUY"}
                # No best_bid or best_ask
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_asset_id_resolution_fallback() -> None:
    """When asset_id not in token_map, use market field as condition_id."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker, token_market_map={})

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


async def test_orderbook_snapshot_list() -> None:
    """Initial orderbook snapshots (list with bids/asks) go to orderbooks.raw."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        [
            {
                "market": "0xmarket1",
                "asset_id": "asset_1",
                "bids": [{"price": "0.30", "size": "200"}],
                "asks": [{"price": "0.35", "size": "150"}],
            },
        ]
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["asset_id"] == "asset_1"
    assert data["best_bid"] == 0.30
    assert data["best_ask"] == 0.35


async def test_multiple_price_changes_in_one_message() -> None:
    """A price_changes message with multiple entries publishes one per asset."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        {
            "market": "0xabc",
            "price_changes": [
                {"asset_id": "a1", "best_bid": "0.60", "best_ask": "0.65"},
                {"asset_id": "a2", "best_bid": "0.30", "best_ask": "0.35"},
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 2
    ids = {json.loads(m[0])["asset_id"] for m in broker.messages}
    assert ids == {"a1", "a2"}


async def test_empty_list_ignored() -> None:
    """Empty list (WS ack) should be silently ignored."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    await ingestor._handle_message("[]")
    assert len(broker.messages) == 0
