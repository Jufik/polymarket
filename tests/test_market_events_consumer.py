"""Tests for market events consumer (debounce + PG upsert)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

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


# ---------------------------------------------------------------------------
# Voided market resolution (C2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voided_resolution_calls_settle_voided(consumer: MarketEventsConsumer) -> None:
    """market_resolved with empty resolution should call settle_voided_market."""
    event = {
        "type": "market_resolved",
        "condition_id": "0xvoided",
        "payload": {"resolution": ""},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)

    consumer._runner.settle_voided_market.assert_called_once_with("0xvoided", 0.5)
    consumer._runner.settle_resolved_market.assert_not_called()
    consumer._runner.request_refresh.assert_called()


def _make_pg_mock() -> tuple[AsyncMock, AsyncMock]:
    """Create a mock PG pool + connection for testing _upsert_resolution."""
    mock_conn = AsyncMock()
    # asyncpg pool.acquire() returns an async context manager
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_cm
    return mock_pool, mock_conn


@pytest.mark.asyncio
async def test_voided_resolution_pg_upsert_uses_minus_one() -> None:
    """Voided resolution should upsert resolution_value=-1 in PG."""
    mock_pool, mock_conn = _make_pg_mock()

    runner = MagicMock()
    runner.request_refresh = MagicMock()

    consumer = MarketEventsConsumer(
        pg_pool=mock_pool,
        runner=runner,
        debounce_s=0.05,
    )

    event = {
        "type": "market_resolved",
        "condition_id": "0xvoided",
        "payload": {"resolution": ""},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)

    # Check the execute call used resolution_value = -1
    mock_conn.execute.assert_called_once()
    # The positional args: (sql, resolution_value, winner_outcome, condition_id)
    args = mock_conn.execute.call_args[0]
    assert args[1] == -1  # resolution_value
    assert args[3] == "0xvoided"  # condition_id


@pytest.mark.asyncio
async def test_normal_resolution_pg_upsert_uses_one() -> None:
    """Normal YES/NO resolution should upsert resolution_value=1 in PG."""
    mock_pool, mock_conn = _make_pg_mock()

    runner = MagicMock()
    runner.request_refresh = MagicMock()

    consumer = MarketEventsConsumer(
        pg_pool=mock_pool,
        runner=runner,
        debounce_s=0.05,
    )

    event = {
        "type": "market_resolved",
        "condition_id": "0xresolved",
        "payload": {"resolution": "YES"},
        "timestamp": 1700000000.0,
    }
    await consumer.handle(json.dumps(event))
    await asyncio.sleep(0.1)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]
    # resolution_value should be 1 for normal resolution
    assert args[1] == 1  # resolution_value
