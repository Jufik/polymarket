"""Tests for the proportional-copy strategy (S1)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
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
    _compute_grading_stats,
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
    assert cfg.max_sizing_mult == 3.0
    assert cfg.max_entry_price is None
    assert cfg.price_slippage == 0.05


def test_proportional_copy_config_custom() -> None:
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1", "0xtrader2"},
        capital_per_trader_usd=100.0,
        contradiction_filter=False,
        sizing="proportional",
        max_sizing_mult=5.0,
        max_entry_price=0.40,
        price_slippage=0.03,
    )
    assert len(cfg.pool_traders) == 2
    assert cfg.capital_per_trader_usd == 100.0
    assert cfg.contradiction_filter is False
    assert cfg.sizing == "proportional"
    assert cfg.max_sizing_mult == 5.0
    assert cfg.max_entry_price == 0.40
    assert cfg.price_slippage == 0.03


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
# Proportional sizing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proportional_sizing_scales_by_relative_size() -> None:
    """Proportional sizing: larger-than-average trades get larger copy bets."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1"},
        capital_per_trader_usd=50.0,
        sizing="proportional",
        max_sizing_mult=3.0,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # First trade: $100 — no prior avg, uses base bet
    t1 = _make_trade(
        maker="0xtrader1", condition_id="0xmkt1", amount_usd=100.0, price=0.25
    )
    r1 = await strategy.on_trade(t1, ctx)
    assert r1 is not None
    assert r1[0].size_usd == 50.0  # no prior avg → base bet

    # Second trade: $200 — 2x the avg ($100), so 2x sizing
    t2 = _make_trade(
        maker="0xtrader1", condition_id="0xmkt2", amount_usd=200.0, price=0.30
    )
    r2 = await strategy.on_trade(t2, ctx)
    assert r2 is not None
    assert r2[0].size_usd == pytest.approx(100.0, abs=1.0)  # 50 * (200/100)

    # Third trade: $600 — 4x the avg ($150), but capped at 3.0x
    t3 = _make_trade(
        maker="0xtrader1", condition_id="0xmkt3", amount_usd=600.0, price=0.20
    )
    r3 = await strategy.on_trade(t3, ctx)
    assert r3 is not None
    assert r3[0].size_usd == pytest.approx(150.0, abs=1.0)  # 50 * 3.0 (capped)


@pytest.mark.asyncio
async def test_equal_sizing_ignores_trade_amount() -> None:
    """Equal sizing: always uses capital_per_trader_usd regardless of trade size."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1"},
        capital_per_trader_usd=50.0,
        sizing="equal",
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Seed prior average
    t1 = _make_trade(
        maker="0xtrader1", condition_id="0xmkt1", amount_usd=100.0, price=0.25
    )
    r1 = await strategy.on_trade(t1, ctx)
    assert r1 is not None
    assert r1[0].size_usd == 50.0

    # 10x trade — still base bet in equal mode
    t2 = _make_trade(
        maker="0xtrader1", condition_id="0xmkt2", amount_usd=1000.0, price=0.25
    )
    r2 = await strategy.on_trade(t2, ctx)
    assert r2 is not None
    assert r2[0].size_usd == 50.0


# ---------------------------------------------------------------------------
# Max price tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_price_set_on_yes_intent() -> None:
    """BUY YES intent: max_price = YES entry price + slippage."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1"},
        price_slippage=0.05,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    trade = _make_trade(maker="0xtrader1", side="BUY", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert result[0].max_price == pytest.approx(0.30, abs=0.001)  # 0.25 + 0.05


@pytest.mark.asyncio
async def test_max_price_set_on_no_intent() -> None:
    """BUY NO intent: max_price = (1 - YES price) + slippage."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1"},
        price_slippage=0.05,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Trader sells YES at 0.80 → NO entry = 0.20
    trade = _make_trade(maker="0xtrader1", side="SELL", price=0.80)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert result[0].outcome == "NO"
    assert result[0].max_price == pytest.approx(0.25, abs=0.001)  # 0.20 + 0.05


@pytest.mark.asyncio
async def test_max_entry_price_caps_slippage() -> None:
    """max_entry_price should cap the slippage-based max_price."""
    cfg = ProportionalCopyConfig(
        pool_traders={"0xtrader1"},
        price_slippage=0.10,
        max_entry_price=0.30,
    )
    strategy = ProportionalCopyStrategy(config=cfg)
    ctx = _MockCtx(features={"pool_traders": cfg.pool_traders})

    # Trade at 0.25, slippage would give 0.35, but cap at 0.30
    trade = _make_trade(maker="0xtrader1", side="BUY", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert result[0].max_price == pytest.approx(0.30, abs=0.001)


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
                "price": [0.25, 0.30, 0.20],
                "amount_usd": [100.0, 200.0, 300.0],
                "published_at": [1.0, 2.0, 3.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 2
        makers = set(
            trades.filter(pl.col("maker").is_in(["0xA", "0xB"]))
            .collect()["maker"]
            .to_list()
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
                "price": [0.25, 0.75, 0.20],
                "amount_usd": [100.0, 100.0, 100.0],
                "published_at": [1.0, 2.0, 3.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2"]})

        result = strategy.compute_signals(trades, markets)
        # m1 should be filtered (contradiction), m2 should remain
        assert len(result) == 1
        assert result["condition_id"][0] == "0xm2"

    def test_vectorized_has_max_price(self) -> None:
        """Vectorized signals should include max_price column."""
        cfg = ProportionalCopyConfig(
            pool_traders={"0xA"},
            contradiction_filter=False,
            price_slippage=0.05,
        )
        strategy = ProportionalCopyStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "maker": ["0xA"],
                "condition_id": ["0xm1"],
                "side": ["BUY"],
                "price": [0.25],
                "amount_usd": [100.0],
                "published_at": [1.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1"]})

        result = strategy.compute_signals(trades, markets)
        assert "max_price" in result.columns
        assert result["max_price"][0] == pytest.approx(0.30, abs=0.001)

    def test_vectorized_proportional_sizing(self) -> None:
        """Vectorized proportional sizing scales by trader avg."""
        cfg = ProportionalCopyConfig(
            pool_traders={"0xA"},
            capital_per_trader_usd=50.0,
            contradiction_filter=False,
            sizing="proportional",
            max_sizing_mult=3.0,
        )
        strategy = ProportionalCopyStrategy(config=cfg)

        # 3 trades from 0xA: $100, $200, $300. avg=$200.
        # Unique by (maker, cid) keeps first per market.
        # mkt1: $100 → mult=100/200=0.5 → $25
        # mkt2: $200 → mult=200/200=1.0 → $50
        # mkt3: $300 → mult=300/200=1.5 → $75
        trades = pl.LazyFrame(
            {
                "maker": ["0xA", "0xA", "0xA"],
                "condition_id": ["0xm1", "0xm2", "0xm3"],
                "side": ["BUY", "BUY", "BUY"],
                "price": [0.25, 0.30, 0.20],
                "amount_usd": [100.0, 200.0, 300.0],
                "published_at": [1.0, 2.0, 3.0],
            }
        )
        markets = pl.LazyFrame({"condition_id": ["0xm1", "0xm2", "0xm3"]})

        result = strategy.compute_signals(trades, markets)
        sizes = result.sort("condition_id")["size_usd"].to_list()
        assert sizes[0] == pytest.approx(25.0, abs=1.0)  # 50 * 0.5
        assert sizes[1] == pytest.approx(50.0, abs=1.0)  # 50 * 1.0
        assert sizes[2] == pytest.approx(75.0, abs=1.0)  # 50 * 1.5


# ---------------------------------------------------------------------------
# Provider tests — legacy mode
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
            "price": [0.50] * 100,
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
            "price": [0.50] * 50,
            "published_at": [float(i) for i in range(50)],
        }
    )
    trades_v2 = pl.DataFrame(
        {
            "maker": ["0xB"] * 50,
            "condition_id": [f"0xmkt{i}" for i in range(50)],
            "side": ["BUY"] * 50,
            "price": [0.50] * 50,
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


# ---------------------------------------------------------------------------
# Provider tests — legacy grading filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graded_pool_filters_by_longshot_yes_fraction() -> None:
    """Only traders with longshot_yes_fraction > 0.15 should pass."""
    # Trader A: 20 markets, 5 are YES buys at <0.50 → longshot_yes_frac = 0.25 (passes)
    # Trader B: 20 markets, 1 is YES buy at <0.50 → longshot_yes_frac = 0.05 (fails)
    rows_a_longshot = [
        {
            "maker": "0xA",
            "condition_id": f"0xm{i}",
            "side": "BUY",
            "price": 0.30,
            "published_at": float(i),
        }
        for i in range(5)
    ]
    rows_a_normal = [
        {
            "maker": "0xA",
            "condition_id": f"0xm{i}",
            "side": "SELL",
            "price": 0.70,
            "published_at": float(i),
        }
        for i in range(5, 20)
    ]
    rows_b_longshot = [
        {
            "maker": "0xB",
            "condition_id": "0xn0",
            "side": "BUY",
            "price": 0.25,
            "published_at": 0.0,
        }
    ]
    rows_b_normal = [
        {
            "maker": "0xB",
            "condition_id": f"0xn{i}",
            "side": "SELL",
            "price": 0.75,
            "published_at": float(i),
        }
        for i in range(1, 20)
    ]

    trades_df = pl.DataFrame(
        rows_a_longshot + rows_a_normal + rows_b_longshot + rows_b_normal
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(min_markets=10, min_longshot_yes_frac=0.15)
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xA" in pool  # 0.25 >= 0.15
    assert "0xB" not in pool  # 0.05 < 0.15


@pytest.mark.asyncio
async def test_graded_pool_excludes_high_no_fraction() -> None:
    """Traders with no_fraction > 0.60 should be excluded."""
    # Trader C: 20 markets, 15 are SELL (NO), no_frac = 0.75 → excluded
    rows_c = [
        {
            "maker": "0xC",
            "condition_id": f"0xp{i}",
            "side": "SELL",
            "price": 0.80,
            "published_at": float(i),
        }
        for i in range(15)
    ] + [
        {
            "maker": "0xC",
            "condition_id": f"0xp{i}",
            "side": "BUY",
            "price": 0.30,
            "published_at": float(i),
        }
        for i in range(15, 20)
    ]
    # Trader D: 20 markets, 8 SELL, 12 BUY (4 longshot YES) → no_frac=0.40, longshot=0.20 → passes
    rows_d = (
        [
            {
                "maker": "0xD",
                "condition_id": f"0xq{i}",
                "side": "SELL",
                "price": 0.70,
                "published_at": float(i),
            }
            for i in range(8)
        ]
        + [
            {
                "maker": "0xD",
                "condition_id": f"0xq{i}",
                "side": "BUY",
                "price": 0.30,
                "published_at": float(i),
            }
            for i in range(8, 12)
        ]
        + [
            {
                "maker": "0xD",
                "condition_id": f"0xq{i}",
                "side": "BUY",
                "price": 0.60,
                "published_at": float(i),
            }
            for i in range(12, 20)
        ]
    )

    trades_df = pl.DataFrame(rows_c + rows_d)

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    provider = GradedPoolProvider(
        min_markets=10, min_longshot_yes_frac=0.15, max_no_fraction=0.60
    )
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xC" not in pool  # no_frac 0.75 > 0.60
    assert "0xD" in pool  # no_frac 0.40, longshot_yes 0.20


@pytest.mark.asyncio
async def test_graded_pool_backward_compat_no_grading() -> None:
    """When no grading params given, behaves like before (market count only)."""
    trades_df = pl.DataFrame(
        {
            "maker": ["0xA"] * 60 + ["0xB"] * 30,
            "condition_id": [f"0xmkt{i}" for i in range(60)]
            + [f"0xmkt{i}" for i in range(30)],
            "side": ["BUY"] * 90,
            "price": [0.50] * 90,
            "published_at": [float(i) for i in range(90)],
        }
    )

    backend = AsyncMock()
    backend.query_trades = AsyncMock(return_value=trades_df)

    # No grading params → old behavior
    provider = GradedPoolProvider(min_markets=20)
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xA" in pool
    assert "0xB" in pool


# ---------------------------------------------------------------------------
# Provider tests — consistency mode
# ---------------------------------------------------------------------------


def _make_consistency_data(
    n_months: int = 9,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Create synthetic PnL, resolved, and MVF data for consistency mode tests.

    Generates two traders:
    - 0xGOOD: Profitable every month, pure taker, 60% longshot YES positions
    - 0xBAD: Profitable every month, pure taker, but 80% NO positions
    """
    rows_pnl = []
    rows_resolved = []

    for m in range(n_months):
        month_dt = datetime(2025, 4 + m, 15, tzinfo=UTC)
        # 0xGOOD: 10 markets per month, mix of YES longshot + some NO
        for i in range(10):
            cid = f"0xcid_good_{m}_{i}"
            is_longshot_yes = i < 6  # 60% longshot YES
            rows_pnl.append(
                {
                    "trader": "0xGOOD",
                    "condition_id": cid,
                    "market_pnl": 10.0,  # always profitable
                    "net_yes_tokens": 1.0 if is_longshot_yes else -1.0,
                    "wavg_yes_entry_price": 0.30 if is_longshot_yes else 0.80,
                }
            )
            rows_resolved.append(
                {"condition_id": cid, "resolved_at": month_dt}
            )

        # 0xBAD: 10 markets per month, 80% NO positions
        for i in range(10):
            cid = f"0xcid_bad_{m}_{i}"
            is_no = i < 8  # 80% NO
            rows_pnl.append(
                {
                    "trader": "0xBAD",
                    "condition_id": cid,
                    "market_pnl": 10.0,
                    "net_yes_tokens": -1.0 if is_no else 1.0,
                    "wavg_yes_entry_price": 0.85 if is_no else 0.70,
                }
            )
            rows_resolved.append(
                {"condition_id": cid, "resolved_at": month_dt}
            )

    pnl_df = pl.DataFrame(rows_pnl)
    resolved_df = pl.DataFrame(rows_resolved)
    mvf_df = pl.DataFrame(
        {"trader": ["0xGOOD", "0xBAD"], "mvf": [0.05, 0.05]}
    )

    return pnl_df, resolved_df, mvf_df


@pytest.mark.asyncio
async def test_consistency_mode_filters_correctly() -> None:
    """Consistency mode: 0xGOOD passes (longshot YES), 0xBAD fails (too much NO)."""
    pnl_df, resolved_df, mvf_df = _make_consistency_data(n_months=9)

    provider = GradedPoolProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2026, 2, 1, tzinfo=UTC),
        min_periods=9,
        min_consistency_markets=20,
        max_mvf=0.10,
        max_median_entry=0.90,
        min_longshot_yes_frac=0.15,
        max_no_fraction=0.60,
    )

    backend = AsyncMock()
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xGOOD" in pool  # 60% longshot YES, 40% NO
    assert "0xBAD" not in pool  # 80% NO > 60% cap


@pytest.mark.asyncio
async def test_consistency_mode_empty_when_too_few_months() -> None:
    """If traders have fewer months than required, pool should be empty."""
    pnl_df, resolved_df, mvf_df = _make_consistency_data(n_months=3)

    provider = GradedPoolProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2026, 2, 1, tzinfo=UTC),
        min_periods=9,  # requires 9 but only 3 available
        min_consistency_markets=20,
        max_mvf=0.10,
        max_median_entry=0.90,
    )

    backend = AsyncMock()
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert len(pool) == 0


@pytest.mark.asyncio
async def test_consistency_mode_relaxed_grading_includes_more() -> None:
    """Relaxed grading (no longshot/NO filters) includes more traders."""
    pnl_df, resolved_df, mvf_df = _make_consistency_data(n_months=9)

    provider = GradedPoolProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        train_end=datetime(2026, 2, 1, tzinfo=UTC),
        min_periods=9,
        min_consistency_markets=20,
        max_mvf=0.10,
        max_median_entry=0.90,
        min_longshot_yes_frac=0.0,  # disabled
        max_no_fraction=1.0,  # disabled
    )

    backend = AsyncMock()
    await provider.compute(backend)

    pool = provider.get_features()["pool_traders"]
    assert "0xGOOD" in pool
    assert "0xBAD" in pool  # passes without grading filters


# ---------------------------------------------------------------------------
# Grading stats helper test
# ---------------------------------------------------------------------------


def test_compute_grading_stats() -> None:
    """_compute_grading_stats should compute correct per-trader fractions."""
    pnl_df = pl.DataFrame(
        {
            "trader": ["0xA"] * 4 + ["0xB"] * 4,
            "condition_id": [f"c{i}" for i in range(8)],
            "net_yes_tokens": [1.0, 1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 1.0],
            "wavg_yes_entry_price": [0.30, 0.60, 0.70, 0.80, 0.85, 0.90, 0.75, 0.40],
        }
    )
    resolved_df = pl.DataFrame(
        {
            "condition_id": [f"c{i}" for i in range(8)],
            "resolved_at": [
                datetime(2025, 6, 1, tzinfo=UTC)
            ]
            * 8,
        }
    )

    result = _compute_grading_stats(
        pnl_df,
        resolved_df,
        frozenset({"0xA", "0xB"}),
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    a_row = result.filter(pl.col("trader") == "0xA")
    b_row = result.filter(pl.col("trader") == "0xB")

    # 0xA: 4 positions. longshot YES = (net_yes>0 AND entry<0.50): only first. frac=1/4=0.25
    # NO: (net_yes<=0): 2 out of 4. frac=0.50
    assert a_row["longshot_yes_frac"][0] == pytest.approx(0.25)
    assert a_row["no_frac"][0] == pytest.approx(0.50)

    # 0xB: 4 positions. longshot YES = last one (net_yes>0 AND entry=0.40<0.50): 1/4=0.25
    # NO: 3 out of 4. frac=0.75
    assert b_row["longshot_yes_frac"][0] == pytest.approx(0.25)
    assert b_row["no_frac"][0] == pytest.approx(0.75)
