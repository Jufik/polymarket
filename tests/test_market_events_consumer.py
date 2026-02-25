"""Tests for market events consumer (debounce + PG upsert)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer


@pytest.fixture
def consumer() -> MarketEventsConsumer:
    runner = MagicMock()
    runner.request_refresh = MagicMock()
    return MarketEventsConsumer(
        pg_pool=None,
        runner=runner,
        debounce_s=0.05,
    )


@pytest.mark.asyncio
async def test_market_resolved_triggers_refresh(consumer: MarketEventsConsumer) -> None:
    """A market_resolved event should eventually trigger request_refresh."""
    event = {
        "type": "market_resolved",
        "condition_id": "0xabc",
        "payload": {"resolution": "YES"},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)
    consumer._runner.request_refresh.assert_called()


@pytest.mark.asyncio
async def test_debounce_batches_events(consumer: MarketEventsConsumer) -> None:
    """Multiple rapid events should produce a single refresh call."""
    for i in range(5):
        event = {
            "type": "market_resolved",
            "condition_id": f"0x{i:04x}",
            "payload": {},
            "timestamp": 1700000000.0 + i,
        }
        await consumer.handle(json.dumps(event))

    await asyncio.sleep(0.1)
    assert consumer._runner.request_refresh.call_count == 1


@pytest.mark.asyncio
async def test_new_market_does_not_trigger_refresh(consumer: MarketEventsConsumer) -> None:
    """new_market events should not trigger pool refresh (no PnL impact)."""
    event = {
        "type": "new_market",
        "condition_id": "0xnew",
        "payload": {},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)
    consumer._runner.request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json_ignored(consumer: MarketEventsConsumer) -> None:
    """Invalid JSON should be silently ignored."""
    await consumer.handle("not json")
