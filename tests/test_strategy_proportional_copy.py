"""Tests for the proportional-copy strategy (S1)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import polars as pl
import pytest

from polymarket_pipeline.strategies.types import MarketInfo
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)
from polymarket_pipeline.strategies_impl.proportional_copy.providers import (
    GradedPoolProvider,
)
from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
    ProportionalCopyStrategy,
)

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_proportional_copy_config_defaults() -> None:
    cfg = ProportionalCopyConfig()
    assert cfg.pool_traders == frozenset()
    assert cfg.capital_per_trader_usd == 50.0
    assert cfg.max_position_pct == 0.05
    assert cfg.contradiction_filter is True
    assert cfg.sizing == "equal"


def test_proportional_copy_config_custom() -> None:
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        capital_per_trader_usd=100.0,
        contradiction_filter=False,
    )
    assert len(cfg.pool_traders) == 2
    assert cfg.capital_per_trader_usd == 100.0
    assert cfg.contradiction_filter is False


def test_proportional_copy_config_frozen() -> None:
    cfg = ProportionalCopyConfig()
    with pytest.raises(AttributeError):
        cfg.capital_per_trader_usd = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_trade(
    *,
    condition_id: str = "0xmkt1",
    maker: str = "0xtrader1",
    side: str = "BUY",
    price: float = 0.25,
    amount_usd: float = 100.0,
    published_at: float | None = None,
) -> Any:
    from datetime import UTC, datetime
    from decimal import Decimal

    from polymarket_pipeline.models import NormalizedTrade

    return NormalizedTrade(
        trade_id=f"test:{condition_id}:{maker}:{time.time()}",
        condition_id=condition_id,
        asset_id="0xasset",
        side=side,
        price=Decimal(str(price)),
        size=Decimal("10"),
        amount_usd=Decimal(str(amount_usd)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker=None,
        timestamp=datetime.now(UTC),
        source="websocket",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
        published_at=published_at or time.time(),
    )


class _MockCtx:
    def __init__(self, features: dict[str, Any] | None = None) -> None:
        self._features = features or {}

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return MarketInfo(
            condition_id=condition_id,
            question="Will X happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        )

    async def get_features(self, key: str) -> Any:
        return self._features.get(key)

    async def get_position(self, condition_id: str) -> None:
        return None

    async def get_orderbook(self, condition_id: str) -> None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Event-driven tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copies_pool_trader_entry() -> None:
    """Should emit intent when a pool trader enters a market."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        capital_per_trader_usd=50.0,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xtrader1", side="BUY", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 1
    intent = result[0]
    assert intent.strategy == "proportional_copy"
    assert intent.side == "BUY"
    assert intent.outcome == "YES"  # BUY side = buying YES
    assert intent.size_usd == 50.0


@pytest.mark.asyncio
async def test_ignores_non_pool_trader() -> None:
    """Trades from non-pool traders should be ignored."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xrandom")
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_ignores_duplicate_trader_in_same_market() -> None:
    """Same trader trading again in same market should be ignored."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    t1 = _make_trade(maker="0xtrader1", condition_id="0xmkt1")
    t2 = _make_trade(maker="0xtrader1", condition_id="0xmkt1")

    r1 = await strategy.on_trade(t1, ctx)
    r2 = await strategy.on_trade(t2, ctx)
    assert r1 is not None
    assert r2 is None


@pytest.mark.asyncio
async def test_contradiction_filter_skips_conflicted_market() -> None:
    """When contradiction_filter=True, skip if traders disagree."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        contradiction_filter=True,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Trader1 buys YES
    t1 = _make_trade(maker="0xtrader1", condition_id="0xmkt1", side="BUY")
    r1 = await strategy.on_trade(t1, ctx)
    assert r1 is not None  # first trader, no contradiction yet

    # Trader2 sells YES (bets NO) — contradiction
    t2 = _make_trade(maker="0xtrader2", condition_id="0xmkt1", side="SELL")
    r2 = await strategy.on_trade(t2, ctx)
    assert r2 is None  # contradicted, skip


@pytest.mark.asyncio
async def test_sell_side_maps_to_no() -> None:
    """SELL side (selling YES tokens) = betting NO."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xtrader1", side="SELL", price=0.75)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    intent = result[0]
    assert intent.side == "BUY"
    assert intent.outcome == "NO"


@pytest.mark.asyncio
async def test_on_market_update_returns_none() -> None:
    cfg = ProportionalCopyConfig()
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx()
    result = await strategy.on_market_update({"price": 0.5}, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_on_timer_returns_none() -> None:
    cfg = ProportionalCopyConfig()
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx()
    result = await strategy.on_timer(1700000000.0, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_falls_back_to_config_pool() -> None:
    """When no pool_traders feature, should use config pool."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx()  # no features

    trade = _make_trade(maker="0xtrader1", side="BUY")
    result = await strategy.on_trade(trade, ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_ignores_trade_with_no_maker() -> None:
    """Trades with maker=None should be ignored."""
    cfg = ProportionalCopyConfig(pool_traders={"0xtrader1"})
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Create a trade with maker=None directly
    from datetime import UTC, datetime
    from decimal import Decimal

    from polymarket_pipeline.models import NormalizedTrade

    no_maker_trade = NormalizedTrade(
        trade_id="test:no_maker",
        condition_id="0xmkt1",
        asset_id="0xasset",
        side="BUY",
        price=Decimal("0.25"),
        size=Decimal("10"),
        amount_usd=Decimal("100"),
        fee_usd=Decimal("0"),
        maker=None,
        taker=None,
        timestamp=datetime.now(UTC),
        source="websocket",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
        published_at=time.time(),
    )
    result = await strategy.on_trade(no_maker_trade, ctx)
    assert result is None


# ---------------------------------------------------------------------------
# Vectorized tests
# ---------------------------------------------------------------------------


class TestProportionalCopyVectorized:
    def test_filters_to_pool_traders(self) -> None:
        """Only pool trader trades should generate signals."""
        cfg = ProportionalCopyConfig(
            pool_traders={"0xA", "0xB"},
            contradiction_filter=False,
        )
        strategy = ProportionalCopyStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "maker": ["0xA", "0xB", "0xC"],
                "condition_id": ["0xm1", "0xm1", "0xm2"],
                "side": ["BUY", "BUY", "BUY"],
                "published_at": [1.0, 2.0, 3.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 2
        makers = set(
            trades.filter(pl.col("maker").is_in(["0xA", "0xB"])).collect()["maker"].to_list()
        )
        assert "0xC" not in makers

    def test_contradiction_filter_removes_conflicted(self) -> None:
        """Markets with both BUY and SELL from pool should be filtered."""
        cfg = ProportionalCopyConfig(
            pool_traders={"0xA", "0xB"},
            contradiction_filter=True,
        )
        strategy = ProportionalCopyStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "maker": ["0xA", "0xB", "0xA"],
                "condition_id": ["0xm1", "0xm1", "0xm2"],
                "side": ["BUY", "SELL", "BUY"],  # m1 is conflicted
                "published_at": [1.0, 2.0, 3.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})

        result = strategy.compute_signals(trades, markets)
        # m1 should be filtered (contradiction), m2 should remain
        assert len(result) == 1
        assert result["condition_id"][0] == "0xm2"


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graded_pool_provider_basic() -> None:
    """Provider should expose pool_traders frozenset."""
    trades_df = pl.DataFrame(
        {
            "maker": ["0xA"] * 60 + ["0xB"] * 30 + ["0xC"] * 10,
            "condition_id": [f"0xmkt{i}" for i in range(60)]
            + [f"0xmkt{i}" for i in range(30)]
            + [f"0xmkt{i}" for i in range(10)],
            "side": ["BUY"] * 100,
            "published_at": [float(i) for i in range(100)],
        }
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)

    features = provider.get_features()
    pool = features["pool_traders"]

    # 0xA has 60 markets (passes), 0xB has 30 (passes), 0xC has 10 (fails)
    assert "0xA" in pool
    assert "0xB" in pool
    assert "0xC" not in pool


@pytest.mark.asyncio
async def test_graded_pool_provider_empty_trades() -> None:
    """Provider should handle empty trades gracefully."""
    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=pl.DataFrame())

    provider = GradedPoolProvider()
    await provider.compute(backend)

    features = provider.get_features()
    assert features["pool_traders"] == frozenset()


@pytest.mark.asyncio
async def test_graded_pool_provider_refresh_swaps_atomically() -> None:
    """Refresh should replace the pool without intermediate empty state."""
    trades_v1 = pl.DataFrame(
        {
            "maker": ["0xA"] * 50,
            "condition_id": [f"0xmkt{i}" for i in range(50)],
            "side": ["BUY"] * 50,
            "published_at": [float(i) for i in range(50)],
        }
    )
    trades_v2 = pl.DataFrame(
        {
            "maker": ["0xB"] * 50,
            "condition_id": [f"0xmkt{i}" for i in range(50)],
            "side": ["BUY"] * 50,
            "published_at": [float(i) for i in range(50)],
        }
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_v1)

    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)
    assert "0xA" in provider.get_features()["pool_traders"]

    # Refresh with new data
    backend.query_trades = AsyncMock(return_value=trades_v2)
    await provider.refresh(backend)
    pool = provider.get_features()["pool_traders"]
    assert "0xB" in pool
    assert "0xA" not in pool
