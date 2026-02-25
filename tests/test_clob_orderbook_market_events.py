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
    )


@pytest.mark.asyncio
async def test_market_resolved_published_to_events_topic(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """market_resolved events should be published to markets.events topic."""
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
async def test_new_market_published_to_events_topic(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """new_market events should be published to markets.events topic."""
    msg = json.dumps({
        "event_type": "new_market",
        "condition_id": "0xdef456",
        "question": "Will BTC hit 100k?",
        "timestamp": 1700000000.0,
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    payload = json.loads(broker.publish.call_args.kwargs["message"])
    assert payload["type"] == "new_market"
    assert payload["condition_id"] == "0xdef456"


@pytest.mark.asyncio
async def test_price_change_still_works(
    ingestor: CLOBOrderbookIngestor, broker: AsyncMock
) -> None:
    """price_change events should still go to orderbooks.raw."""
    msg = json.dumps({
        "event_type": "price_change",
        "asset_id": "tok123",
        "best_bid": 0.55,
        "best_ask": 0.57,
    })
    await ingestor._handle_message(msg)

    broker.publish.assert_called_once()
    assert broker.publish.call_args.kwargs["topic"] == "orderbooks.raw"


@pytest.mark.asyncio
async def test_subscription_includes_custom_feature(
    ingestor: CLOBOrderbookIngestor,
) -> None:
    """WS subscription payload should include custom_feature_enabled."""
    payload = ingestor._subscription_payload()
    assert payload["custom_feature_enabled"] is True
