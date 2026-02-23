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
    """A valid price_change event should produce an orderbook snapshot."""
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
            "event_type": "price_change",
            "asset_id": "asset_123",
            "best_bid": "0.55",
            "best_ask": "0.58",
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
    """Non-price_change events should be silently ignored."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps({"event_type": "trade", "data": "something"})
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_missing_prices_skipped() -> None:
    """price_change with missing bid/ask should be skipped."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        {
            "event_type": "price_change",
            "asset_id": "asset_123",
            # No bid or ask fields
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 0


async def test_asset_id_resolution_fallback() -> None:
    """When asset_id not in token_map, use asset_id as condition_id."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker, token_market_map={})

    msg = json.dumps(
        {
            "event_type": "price_change",
            "asset_id": "unknown_asset",
            "best_bid": "0.40",
            "best_ask": "0.45",
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["condition_id"] == "unknown_asset"


async def test_list_message_format() -> None:
    """CLOB WS may send messages as JSON arrays."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        [
            {
                "event_type": "price_change",
                "asset_id": "asset_1",
                "best_bid": "0.30",
                "best_ask": "0.35",
            },
            {
                "event_type": "something_else",
                "data": "ignored",
            },
        ]
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["asset_id"] == "asset_1"


async def test_price_changes_array_format() -> None:
    """price_change event with nested price_changes array."""
    from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

    broker: Any = MockBroker()
    ingestor = CLOBOrderbookIngestor(broker=broker)

    msg = json.dumps(
        {
            "event_type": "price_change",
            "asset_id": "asset_1",
            "price_changes": [
                {"best_bid": "0.60", "best_ask": "0.65"},
            ],
        }
    )
    await ingestor._handle_message(msg)

    assert len(broker.messages) == 1
    data = json.loads(broker.messages[0][0])
    assert data["best_bid"] == 0.60
    assert data["best_ask"] == 0.65
