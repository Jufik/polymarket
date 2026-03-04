"""Tests for RedisContext -- Redis-backed StrategyContext."""

from __future__ import annotations

from typing import Any

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
)


def _make_ob(condition_id: str = "cond1", best_bid: float = 0.50) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        condition_id=condition_id,
        best_bid=best_bid,
        best_ask=best_bid + 0.05,
        bid_depth=100.0,
        ask_depth=100.0,
        timestamp=1700000000.0,
        bids=((best_bid, 100.0),),
        asks=((best_bid + 0.05, 100.0),),
    )


def _make_redis_context() -> Any:
    """Create a RedisContext with fakeredis backend."""
    fakeredis = pytest.importorskip("fakeredis")

    from polymarket_pipeline.strategies.context.redis import RedisContext

    redis = fakeredis.aioredis.FakeRedis()
    inner = InMemoryContext()
    return RedisContext(redis=redis, inner=inner), redis, inner


async def test_get_orderbook_from_redis() -> None:
    """Redis has data → returns it directly."""
    from polymarket_pipeline.live.redis_orderbook import write_orderbook

    ctx, redis, _inner = _make_redis_context()
    snap = {
        "condition_id": "cond1",
        "asset_id": "asset1",
        "best_bid": 0.55,
        "best_ask": 0.60,
        "bids": [[0.55, 100.0]],
        "asks": [[0.60, 80.0]],
        "bid_depth_usd": 55.0,
        "ask_depth_usd": 48.0,
        "timestamp": 1700000000.0,
    }
    await write_orderbook(redis, "cond1", "asset1", snap, ttl_s=60)

    ob = await ctx.get_orderbook("cond1")
    assert ob is not None
    assert ob.best_bid == 0.55
    assert ob.condition_id == "cond1"


async def test_get_orderbook_fallback_to_inner() -> None:
    """Redis empty → falls back to InMemoryContext."""
    ctx, _redis, inner = _make_redis_context()

    ob = _make_ob("cond1", best_bid=0.45)
    inner.set_orderbook("cond1", ob)

    result = await ctx.get_orderbook("cond1")
    assert result is not None
    assert result.best_bid == 0.45


async def test_get_orderbook_by_asset_async() -> None:
    """Async get_orderbook_by_asset works with Redis data."""
    from polymarket_pipeline.live.redis_orderbook import write_orderbook

    ctx, redis, _inner = _make_redis_context()
    snap = {
        "condition_id": "cond1",
        "asset_id": "asset1",
        "best_bid": 0.55,
        "best_ask": 0.60,
        "bids": [[0.55, 100.0]],
        "asks": [[0.60, 80.0]],
        "bid_depth_usd": 55.0,
        "ask_depth_usd": 48.0,
        "timestamp": 1700000000.0,
    }
    await write_orderbook(redis, "cond1", "asset1", snap, ttl_s=60)

    ob = await ctx.get_orderbook_by_asset("asset1")
    assert ob is not None
    assert ob.best_ask == 0.60


async def test_get_orderbook_by_asset_fallback() -> None:
    """Asset lookup falls back to inner when Redis misses."""
    ctx, _redis, inner = _make_redis_context()

    ob = _make_ob("cond1", best_bid=0.40)
    inner.set_orderbook("cond1", ob, asset_id="asset1")

    result = await ctx.get_orderbook_by_asset("asset1")
    assert result is not None
    assert result.best_bid == 0.40


async def test_position_delegates_to_inner() -> None:
    """Non-orderbook ops use inner context."""
    ctx, _redis, _inner = _make_redis_context()

    pos = Position(
        condition_id="cond1",
        strategy="test",
        qty_yes=10.0,
        qty_no=0.0,
        avg_entry_yes=0.55,
        avg_entry_no=0.0,
        cost_basis=5.5,
        realized_pnl=0.0,
    )
    ctx.set_position("cond1", pos)

    result = await ctx.get_position("cond1")
    assert result is not None
    assert result.qty_yes == 10.0


async def test_set_orderbook_populates_inner() -> None:
    """set_orderbook writes to inner context for Kafka fallback path."""
    ctx, _redis, inner = _make_redis_context()

    ob = _make_ob("cond1", best_bid=0.50)
    ctx.set_orderbook("cond1", ob, asset_id="asset1")

    # Inner should have it
    assert inner.get_orderbook_by_asset("asset1") is not None
    assert (await inner.get_orderbook("cond1")) is not None
