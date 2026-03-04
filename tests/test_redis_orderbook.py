"""Tests for Redis orderbook cache helpers."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from polymarket_pipeline.live.redis_orderbook import (
    _deserialize,
    _serialize,
    read_by_asset,
    read_by_condition,
    write_orderbook,
)
from polymarket_pipeline.strategies.types import OrderbookSnapshot


def _sample_snapshot() -> dict[str, Any]:
    return {
        "condition_id": "0xcond_abc",
        "asset_id": "tok123",
        "best_bid": 0.55,
        "best_ask": 0.58,
        "bids": [[0.55, 100.0], [0.54, 50.0]],
        "asks": [[0.58, 80.0], [0.59, 120.0]],
        "bid_depth_usd": 82.0,
        "ask_depth_usd": 117.4,
        "timestamp": 1700000000.123,
    }


def test_serialize_deserialize_roundtrip() -> None:
    """msgpack round-trip preserves all OrderbookSnapshot fields."""
    snap = _sample_snapshot()
    raw = _serialize(snap)
    ob = _deserialize(raw)

    assert isinstance(ob, OrderbookSnapshot)
    assert ob.condition_id == "0xcond_abc"
    assert ob.best_bid == 0.55
    assert ob.best_ask == 0.58
    assert ob.bid_depth == 82.0
    assert ob.ask_depth == 117.4
    assert ob.timestamp == 1700000000.123
    assert len(ob.bids) == 2
    assert len(ob.asks) == 2
    assert ob.bids[0] == (0.55, 100.0)
    assert ob.asks[1] == (0.59, 120.0)


async def test_write_and_read_by_condition() -> None:
    """Dual-key write allows read by condition_id."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.aioredis.FakeRedis()

    snap = _sample_snapshot()
    await write_orderbook(redis, "0xcond_abc", "tok123", snap, ttl_s=60)

    ob = await read_by_condition(redis, "0xcond_abc")
    assert ob is not None
    assert ob.condition_id == "0xcond_abc"
    assert ob.best_bid == 0.55


async def test_write_and_read_by_asset() -> None:
    """Dual-key write allows read by asset_id."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.aioredis.FakeRedis()

    snap = _sample_snapshot()
    await write_orderbook(redis, "0xcond_abc", "tok123", snap, ttl_s=60)

    ob = await read_by_asset(redis, "tok123")
    assert ob is not None
    assert ob.best_ask == 0.58
    assert len(ob.bids) == 2


async def test_read_missing_returns_none() -> None:
    """Reading a non-existent key returns None."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.aioredis.FakeRedis()

    assert await read_by_condition(redis, "nonexistent") is None
    assert await read_by_asset(redis, "nonexistent") is None


async def test_ttl_expires() -> None:
    """Keys expire after TTL."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.aioredis.FakeRedis()

    snap = _sample_snapshot()
    await write_orderbook(redis, "0xcond_abc", "tok123", snap, ttl_s=1)

    # Immediately readable
    assert await read_by_condition(redis, "0xcond_abc") is not None

    # Wait for TTL
    await asyncio.sleep(1.5)
    assert await read_by_condition(redis, "0xcond_abc") is None


async def test_write_error_silent() -> None:
    """Write errors are swallowed (fire-and-forget)."""
    redis = AsyncMock()
    redis.pipeline.side_effect = RuntimeError("connection lost")

    # Should not raise
    await write_orderbook(redis, "0xcond", "tok", _sample_snapshot())


async def test_read_error_returns_none() -> None:
    """Read errors return None instead of raising."""
    redis = AsyncMock()
    redis.get.side_effect = RuntimeError("connection lost")

    assert await read_by_condition(redis, "cond") is None
    assert await read_by_asset(redis, "asset") is None
