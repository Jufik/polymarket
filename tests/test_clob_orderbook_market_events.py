"""Tests for CLOBOrderbookIngestor market event forwarding."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor


@pytest.fixture
def broker() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def ingestor(broker: AsyncMock) -> CLOBOrderbookIngestor:
    return CLOBOrderbookIngestor(
        broker=broker,
        topic="orderbooks.raw",
        markets_events_topic="markets.events",
        token_market_map={"tok123": ("cond_abc", "YES")},
    )


@pytest.mark.asyncio
async def test_market_resolved_published_to_events_topic(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """market_resolved events (event_type field) go to markets.events topic."""
    msg = json.dumps({
        "event_type": "market_resolved",
        "condition_id": "0xabc123",
        "resolution": "YES",
        "timestamp": 1700000000.0,
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    published = broker.publish.call_args
    payload = json.loads(published.kwargs["message"])
    assert payload["type"] == "market_resolved"
    assert payload["condition_id"] == "0xabc123"
    assert published.kwargs["topic"] == "markets.events"


@pytest.mark.asyncio
async def test_new_market_broadcast_published(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """New market broadcasts (question + market fields) go to markets.events."""
    msg = json.dumps({
        "id": "12345",
        "question": "Will BTC hit 100k?",
        "market": "0xdef456",
        "slug": "btc-100k",
        "description": "Resolves YES if...",
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    payload = json.loads(broker.publish.call_args.kwargs["message"])
    assert payload["type"] == "new_market"
    assert payload["condition_id"] == "0xdef456"
    assert broker.publish.call_args.kwargs["topic"] == "markets.events"


@pytest.mark.asyncio
async def test_price_change_publishes_orderbook(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """price_changes messages (real WS format) go to orderbooks.raw."""
    msg = json.dumps({
        "market": "0xcond_abc",
        "price_changes": [{
            "asset_id": "tok123",
            "price": "0.55",
            "size": "100",
            "side": "BUY",
            "best_bid": "0.55",
            "best_ask": "0.57",
        }],
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    published = broker.publish.call_args
    assert published.kwargs["topic"] == "orderbooks.raw"
    payload = json.loads(published.kwargs["message"])
    assert payload["condition_id"] == "cond_abc"
    assert payload["best_bid"] == 0.55
    assert payload["best_ask"] == 0.57


@pytest.mark.asyncio
async def test_orderbook_snapshot_publishes(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """Initial orderbook snapshots (list with bids/asks) go to orderbooks.raw."""
    msg = json.dumps([{
        "market": "0xcond_abc",
        "asset_id": "tok123",
        "bids": [{"price": "0.54", "size": "200"}],
        "asks": [{"price": "0.56", "size": "150"}],
    }])
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    payload = json.loads(broker.publish.call_args.kwargs["message"])
    assert payload["best_bid"] == 0.54
    assert payload["best_ask"] == 0.56


@pytest.mark.asyncio
async def test_empty_list_ignored(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """Empty list ack from WS should be silently ignored."""
    await ingestor._handle_message("[]")
    broker.publish.assert_not_called()


@pytest.mark.asyncio
async def test_price_change_missing_bid_ignored(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """price_changes with missing best_bid/best_ask are silently dropped."""
    msg = json.dumps({
        "market": "0xabc",
        "price_changes": [{"asset_id": "tok123", "best_bid": "0.50"}],
    })
    await ingestor._handle_message(msg)
    broker.publish.assert_not_called()
