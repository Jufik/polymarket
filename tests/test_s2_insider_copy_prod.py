"""Tests for production s2_insider_copy strategy + provider."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies_impl.s2_insider_copy.provider import (
    InsiderCopyProvider,
)
from polymarket_pipeline.strategies_impl.s2_insider_copy.strategy import (
    InsiderCopyConfig,
    InsiderCopyStrategy,
    create_insider_copy_strategy,
)


def _make_trade(
    maker: str = "alice",
    condition_id: str = "cid_1",
    asset_id: str = "asset_yes_1",
    side: str = "BUY",
    price: float = 0.30,
    amount_usd: float = 100.0,
    ts: float = 1700000000.0,
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{maker}:{condition_id}:{ts}",
        condition_id=condition_id,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal(str(round(amount_usd / price, 4))),
        amount_usd=Decimal(str(amount_usd)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="taker_1",
        timestamp=datetime.datetime.fromtimestamp(ts, tz=datetime.UTC),
        source="goldsky_subgraph",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestFactory:
    def test_creates_strategy_with_defaults(self) -> None:
        config = AsyncMock()
        config.params = {}
        config.name = "s2_insider_fast"

        strategy = create_insider_copy_strategy(config)
        assert strategy.name == "s2_insider_fast"
        assert strategy._cfg.min_consensus == 3
        assert strategy._cfg.max_entry_price == 0.65

    def test_creates_strategy_with_custom_params(self) -> None:
        config = AsyncMock()
        config.params = {
            "min_consensus": 5,
            "size_usd": 100.0,
            "max_entry_price": 0.55,
            "categories": ["politics"],
            "take_profit_daily_pct": 0.03,
            "min_days_to_take_profit": 7,
        }
        config.name = "s2_insider_slow"

        strategy = create_insider_copy_strategy(config)
        assert strategy._cfg.min_consensus == 5
        assert strategy._cfg.size_usd == 100.0
        assert strategy._cfg.max_entry_price == 0.55
        assert strategy._cfg.categories == ["politics"]
        assert strategy._cfg.take_profit_daily_pct == 0.03


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class TestProvider:
    @pytest.mark.asyncio
    async def test_loads_pool_from_backend(self) -> None:
        backend = AsyncMock()
        backend.query_custom.return_value = pl.DataFrame({
            "trader": ["0xalice", "0xbob"],
            "effective_hr": [0.82, 0.78],
            "best_direction": ["NO", "YES"],
            "hr_excess": [0.20, 0.15],
            "high_pct": [0.30, 0.25],
            "total_positions": [10, 8],
        })

        provider = InsiderCopyProvider(
            lookback_months=12, min_positions=3, min_bayesian_hr=0.75,
        )
        await provider.compute(backend)

        feats = provider.get_features()
        assert feats["pool_size"] == 2
        assert "0xalice" in feats["insider_pool"]
        assert feats["insider_pool"]["0xalice"]["direction"] == "NO"

    @pytest.mark.asyncio
    async def test_tracks_insider_buy_trades(self) -> None:
        provider = InsiderCopyProvider()
        # Manually set pool
        provider._pool = {"alice": {"score": 0.8, "direction": "YES"}}

        trade = _make_trade(maker="alice", side="BUY")
        await provider.on_trade(trade)

        sigs = provider.get_features()["insider_signals"]
        assert "cid_1" in sigs
        assert sigs["cid_1"]["consensus_count"] == 1

    @pytest.mark.asyncio
    async def test_ignores_sell_trades(self) -> None:
        provider = InsiderCopyProvider()
        provider._pool = {"alice": {"score": 0.8, "direction": "YES"}}

        await provider.on_trade(_make_trade(maker="alice", side="SELL"))
        assert "cid_1" not in provider.get_features()["insider_signals"]

    @pytest.mark.asyncio
    async def test_ignores_non_pool_traders(self) -> None:
        provider = InsiderCopyProvider()
        provider._pool = {"alice": {"score": 0.8, "direction": "YES"}}

        await provider.on_trade(_make_trade(maker="random", side="BUY"))
        assert "cid_1" not in provider.get_features()["insider_signals"]

    @pytest.mark.asyncio
    async def test_consensus_counts_unique_traders(self) -> None:
        provider = InsiderCopyProvider()
        provider._pool = {
            "alice": {"score": 0.8, "direction": "YES"},
            "bob": {"score": 0.7, "direction": "YES"},
        }

        await provider.on_trade(_make_trade(maker="alice", side="BUY"))
        await provider.on_trade(_make_trade(maker="alice", side="BUY", ts=2.0))
        await provider.on_trade(_make_trade(maker="bob", side="BUY", ts=3.0))

        sig = provider.get_features()["insider_signals"]["cid_1"]
        assert sig["consensus_count"] == 2  # alice counted once


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------


class TestStrategy:
    @pytest.fixture
    def ctx(self) -> AsyncMock:
        ctx = AsyncMock()
        ctx.get_position.return_value = None
        ctx.get_features.return_value = {
            "insider_pool": {
                "alice": {"score": 0.9, "direction": "YES"},
                "bob": {"score": 0.8, "direction": "YES"},
                "charlie": {"score": 0.7, "direction": "YES"},
            },
            "insider_signals": {
                "cid_1": {
                    "direction": "YES",
                    "insiders": {"alice", "bob", "charlie"},
                    "consensus_count": 3,
                },
            },
        }
        return ctx

    @pytest.mark.asyncio
    async def test_entry_on_insider_buy(self, ctx: AsyncMock) -> None:
        cfg = InsiderCopyConfig(min_consensus=1, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)

        trade = _make_trade(maker="alice", price=0.30)
        intents = await strategy.on_trade(trade, ctx)

        assert intents is not None
        assert len(intents) == 1
        assert intents[0].side == "BUY"
        assert intents[0].outcome == "YES"

    @pytest.mark.asyncio
    async def test_rejects_above_max_entry_price(self, ctx: AsyncMock) -> None:
        cfg = InsiderCopyConfig(min_consensus=1, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)

        trade = _make_trade(maker="alice", price=0.70)
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_rejects_below_consensus_threshold(self, ctx: AsyncMock) -> None:
        cfg = InsiderCopyConfig(min_consensus=5, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)

        # Only 3 insiders in signals, need 5
        trade = _make_trade(maker="alice", price=0.30)
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_ignores_sell_trades(self, ctx: AsyncMock) -> None:
        cfg = InsiderCopyConfig(min_consensus=1, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)

        trade = _make_trade(maker="alice", side="SELL", price=0.30)
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_stop_loss(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position

        cfg = InsiderCopyConfig(stop_loss_pct=0.50, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)
        strategy._entries["cid_1"] = {
            "entry_price": 0.30, "outcome": "YES", "entry_time": 1700000000.0,
        }

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.30,
        )
        ctx.get_price.return_value = 0.10  # 67% drop → triggers 50% stop

        trade = _make_trade(maker="random", price=0.10, condition_id="cid_1")
        intents = await strategy.on_trade(trade, ctx)

        assert intents is not None
        assert intents[0].side == "SELL"

    @pytest.mark.asyncio
    async def test_no_stop_loss_within_threshold(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position

        cfg = InsiderCopyConfig(stop_loss_pct=0.50, max_entry_price=0.65)
        strategy = InsiderCopyStrategy(cfg)
        strategy._entries["cid_1"] = {
            "entry_price": 0.30, "outcome": "YES", "entry_time": 1700000000.0,
        }

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.30,
        )
        ctx.get_price.return_value = 0.20  # 33% drop → within 50% stop

        trade = _make_trade(maker="random", price=0.20, condition_id="cid_1")
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None


# ---------------------------------------------------------------------------
# Take-profit tests (on_timer)
# ---------------------------------------------------------------------------


class TestTakeProfit:
    @pytest.fixture
    def ctx(self) -> AsyncMock:
        ctx = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_take_profit_fires(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position

        cfg = InsiderCopyConfig(
            take_profit_daily_pct=0.03, min_days_to_take_profit=7,
        )
        strategy = InsiderCopyStrategy(cfg)
        strategy._entries["cid_1"] = {
            "entry_price": 0.20,
            "outcome": "YES",
            "entry_time": 1700000000.0,
        }

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.20,
        )
        ctx.get_price.return_value = 0.50  # +150% over 10 days = 15%/day

        now = 1700000000.0 + 10 * 86400  # 10 days later
        intents = await strategy.on_timer(now, ctx)

        assert intents is not None
        assert intents[0].side == "SELL"
        assert "take-profit" in intents[0].reason

    @pytest.mark.asyncio
    async def test_no_take_profit_before_min_days(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position

        cfg = InsiderCopyConfig(
            take_profit_daily_pct=0.03, min_days_to_take_profit=7,
        )
        strategy = InsiderCopyStrategy(cfg)
        strategy._entries["cid_1"] = {
            "entry_price": 0.20,
            "outcome": "YES",
            "entry_time": 1700000000.0,
        }

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.20,
        )
        ctx.get_price.return_value = 0.50

        now = 1700000000.0 + 3 * 86400  # 3 days — below min_days
        intents = await strategy.on_timer(now, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_no_take_profit_when_disabled(self, ctx: AsyncMock) -> None:
        cfg = InsiderCopyConfig(take_profit_daily_pct=0.0)
        strategy = InsiderCopyStrategy(cfg)
        strategy._entries["cid_1"] = {
            "entry_price": 0.20, "outcome": "YES", "entry_time": 1700000000.0,
        }

        intents = await strategy.on_timer(1700000000.0 + 30 * 86400, ctx)
        assert intents is None


# ---------------------------------------------------------------------------
# Multi-instance tests
# ---------------------------------------------------------------------------


class TestMultiInstance:
    @pytest.mark.asyncio
    async def test_two_instances_independent(self) -> None:
        ctx1 = AsyncMock()
        ctx1.get_position.return_value = None
        ctx1.get_features.return_value = {
            "insider_pool": {"alice": {"score": 0.9, "direction": "YES"}},
            "insider_signals": {
                "cid_1": {"direction": "YES", "insiders": {"alice"},
                          "consensus_count": 1},
            },
        }

        ctx2 = AsyncMock()
        ctx2.get_position.return_value = None
        ctx2.get_features.return_value = ctx1.get_features.return_value

        fast = InsiderCopyStrategy(
            InsiderCopyConfig(min_consensus=1, max_entry_price=0.65),
            name="s2_insider_fast",
        )
        slow = InsiderCopyStrategy(
            InsiderCopyConfig(min_consensus=1, max_entry_price=0.65,
                              take_profit_daily_pct=0.03),
            name="s2_insider_slow",
        )

        trade = _make_trade(maker="alice", price=0.30)

        fast_intents = await fast.on_trade(trade, ctx1)
        slow_intents = await slow.on_trade(trade, ctx2)

        assert fast_intents is not None
        assert fast_intents[0].strategy == "s2_insider_fast"
        assert slow_intents is not None
        assert slow_intents[0].strategy == "s2_insider_slow"
