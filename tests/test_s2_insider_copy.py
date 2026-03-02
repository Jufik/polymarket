# tests/test_s2_insider_copy.py
"""Tests for s2_insider_copy strategy components."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import polars as pl
import pytest

from polymarket_pipeline.models import NormalizedTrade


class TestMarketSusceptibility:
    """Test market susceptibility classification."""

    def test_political_is_high(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will Trump win the 2024 election?",
            category="Politics",
        )
        assert tier == "HIGH"

    def test_sports_is_medium(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will the Lakers win tonight?",
            category="Sports",
        )
        assert tier == "MEDIUM"

    def test_crypto_up_or_down_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will Bitcoin go Up or Down in the next 5 minutes?",
            category="Crypto",
        )
        assert tier == "LOW"

    def test_gambling_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Coin flip: heads or tails?",
            category="Gambling",
        )
        assert tier == "LOW"

    def test_regulatory_is_high(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will the SEC approve the Bitcoin ETF?",
            category="Crypto",
        )
        assert tier == "HIGH"

    def test_entertainment_is_medium(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Who will win Best Picture at the Oscars?",
            category="Entertainment",
        )
        assert tier == "MEDIUM"

    def test_weather_is_low(self) -> None:
        from research.strategies.s2_insider_copy import classify_market_susceptibility

        tier = classify_market_susceptibility(
            question="Will it snow in NYC tomorrow?",
            category="Weather",
        )
        assert tier == "LOW"

    def test_is_susceptible_helper(self) -> None:
        from research.strategies.s2_insider_copy import is_susceptible

        assert is_susceptible("HIGH") is True
        assert is_susceptible("MEDIUM") is True
        assert is_susceptible("LOW") is False


class TestBayesianHitRate:
    """Test Bayesian-shrunk hit rate computation."""

    def test_uniform_prior_mean(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # No evidence → posterior mean equals prior mean
        hr = bayesian_hit_rate(wins=0, total=0, prior_alpha=3.81, prior_beta=6.19)
        assert abs(hr - 0.381) < 0.001

    def test_strong_evidence_overrides_prior(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # 30 wins out of 35 → posterior near 0.857, barely shrunk
        hr = bayesian_hit_rate(wins=30, total=35, prior_alpha=3.81, prior_beta=6.19)
        assert hr > 0.70  # well above prior
        assert hr < 0.90  # slightly shrunk from 30/35 = 0.857

    def test_weak_evidence_shrunk_to_prior(self) -> None:
        from research.strategies.s2_insider_copy import bayesian_hit_rate

        # 3 wins out of 3 → still heavily shrunk toward 0.381
        hr = bayesian_hit_rate(wins=3, total=3, prior_alpha=3.81, prior_beta=6.19)
        assert hr < 0.70  # shrunk from 1.0 toward 0.381
        assert hr > 0.381  # but above prior since 3/3

    def test_effective_hr_picks_best_direction(self) -> None:
        from research.strategies.s2_insider_copy import compute_effective_hr

        # Trader with 8 YES wins / 10 YES total, 2 NO wins / 5 NO total
        hr, direction = compute_effective_hr(
            yes_wins=8, yes_total=10,
            no_wins=2, no_total=5,
        )
        assert direction == "YES"
        assert hr > 0.55  # YES posterior (8/10 shrunk toward 0.381) beats NO

    def test_effective_hr_no_direction(self) -> None:
        from research.strategies.s2_insider_copy import compute_effective_hr

        # Trader better at NO
        hr, direction = compute_effective_hr(
            yes_wins=1, yes_total=5,
            no_wins=9, no_total=10,
        )
        assert direction == "NO"


class TestInsiderScorer:
    """Test composite insider scoring from trader stats DataFrame."""

    def _make_trader_stats(self) -> pl.DataFrame:
        """Create sample trader stats for testing."""
        return pl.DataFrame({
            "trader": ["alice", "bob", "charlie", "dave"],
            # F1 inputs: directional positions
            "yes_wins": [8, 2, 15, 5],
            "yes_total": [10, 5, 20, 10],
            "no_wins": [2, 7, 3, 1],
            "no_total": [3, 10, 5, 2],
            # F2: avg bet size (USD)
            "avg_position_usd": [5000.0, 200.0, 1000.0, 50.0],
            # F3: markets per month
            "markets_per_month": [1.5, 20.0, 5.0, 2.0],
            # F5: timing edge (avg price delta after entry)
            "avg_timing_edge": [0.15, 0.02, 0.08, -0.05],
            # F6: high susceptibility ratio
            "high_market_ratio": [0.8, 0.3, 0.6, 0.1],
        })

    def test_score_returns_all_traders(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        assert len(result) == 4
        assert "insider_score" in result.columns

    def test_alice_scores_highest(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        scores = dict(zip(
            result["trader"].to_list(),
            result["insider_score"].to_list(),
            strict=True,
        ))
        # Alice: high HR, big bets, selective, good timing, high susceptibility
        assert scores["alice"] > scores["bob"]
        assert scores["alice"] > scores["dave"]

    def test_score_has_feature_columns(self) -> None:
        from research.strategies.s2_insider_copy import compute_insider_scores

        df = self._make_trader_stats()
        result = compute_insider_scores(df)
        for col in ["f1_bayesian_hr", "f2_conviction", "f3_selectivity",
                     "f4_anomaly", "f5_timing", "f6_susceptibility"]:
            assert col in result.columns, f"Missing column: {col}"


def _make_trade(
    maker: str = "alice",
    condition_id: str = "cid_1",
    asset_id: str = "asset_yes_1",
    side: str = "BUY",
    price: float = 0.65,
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


class TestInsiderProvider:
    """Test the InsiderProvider feature provider."""

    def test_provider_tracks_insider_trades(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" in features["insider_signals"]
        signal = features["insider_signals"]["cid_1"]
        assert "alice" in signal["insiders"]
        assert signal["direction"] == "YES"

    def test_provider_ignores_non_insiders(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="bob", condition_id="cid_1", side="BUY")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" not in features["insider_signals"]

    def test_provider_ignores_sell_trades(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        trade = _make_trade(maker="alice", condition_id="cid_1", side="SELL")
        asyncio.run(provider.on_trade(trade))

        features = provider.get_features()
        assert "cid_1" not in features["insider_signals"]

    def test_provider_consensus_count(self) -> None:
        from research.strategies.s2_insider_copy import InsiderProvider

        pool = {
            "alice": {"score": 0.9, "direction": "YES"},
            "bob": {"score": 0.8, "direction": "YES"},
        }
        provider = InsiderProvider(insider_pool=pool)

        import asyncio
        asyncio.run(provider.on_trade(
            _make_trade(maker="alice", condition_id="cid_1", side="BUY")
        ))
        asyncio.run(provider.on_trade(
            _make_trade(maker="bob", condition_id="cid_1", side="BUY")
        ))

        features = provider.get_features()
        signal = features["insider_signals"]["cid_1"]
        assert signal["consensus_count"] == 2


class TestInsiderCopyStrategy:
    """Test the InsiderCopyStrategy event-driven strategy."""

    @pytest.fixture
    def ctx(self) -> AsyncMock:
        """Mock StrategyContext."""
        ctx = AsyncMock()
        ctx.get_position.return_value = None  # no existing position
        ctx.get_features.return_value = {
            "insider_signals": {
                "cid_1": {
                    "direction": "YES",
                    "insiders": {"alice"},
                    "consensus_count": 1,
                    "first_signal_time": 1700000000.0,
                    "max_score": 0.9,
                },
            },
        }
        ctx.now.return_value = 1700000100.0
        return ctx

    @pytest.mark.asyncio
    async def test_emits_intent_on_insider_trade(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        intents = await strategy.on_trade(trade, ctx)
        assert intents is not None
        assert len(intents) == 1
        assert intents[0].condition_id == "cid_1"
        assert intents[0].side == "BUY"
        assert intents[0].outcome == "YES"
        assert intents[0].size_usd == 50.0

    @pytest.mark.asyncio
    async def test_skips_if_already_positioned(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy", qty_yes=10.0,
        )
        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_requires_consensus_threshold(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=2, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="BUY", price=0.65)

        # Only 1 insider → below consensus threshold of 2
        intents = await strategy.on_trade(trade, ctx)
        assert intents is None

    @pytest.mark.asyncio
    async def test_stop_loss_emits_sell(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.65,
        )
        ctx.get_features.return_value = {"insider_signals": {}}
        ctx.get_price.return_value = 0.30  # 54% drop from 0.65 entry → triggers stop

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        # Track that we entered cid_1
        strategy._entries["cid_1"] = {"entry_price": 0.65, "outcome": "YES"}

        trade = _make_trade(maker="random", condition_id="cid_1", side="BUY", price=0.30)
        intents = await strategy.on_trade(trade, ctx)

        assert intents is not None
        assert len(intents) == 1
        assert intents[0].side == "SELL"
        assert intents[0].outcome == "YES"

    @pytest.mark.asyncio
    async def test_no_stop_loss_within_threshold(self, ctx: AsyncMock) -> None:
        from polymarket_pipeline.strategies.types import Position
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        ctx.get_position.return_value = Position(
            condition_id="cid_1", strategy="s2_insider_copy",
            qty_yes=50.0, avg_entry_yes=0.65,
        )
        ctx.get_features.return_value = {"insider_signals": {}}
        ctx.get_price.return_value = 0.45  # 31% drop from 0.65 → within 50% stop

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        strategy._entries["cid_1"] = {"entry_price": 0.65, "outcome": "YES"}

        trade = _make_trade(maker="random", condition_id="cid_1", side="BUY", price=0.45)
        intents = await strategy.on_trade(trade, ctx)

        assert intents is None

    @pytest.mark.asyncio
    async def test_ignores_sell_trades(self, ctx: AsyncMock) -> None:
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        pool = {"alice": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=50.0, stop_loss_pct=0.50,
        )
        trade = _make_trade(maker="alice", condition_id="cid_1", side="SELL")

        intents = await strategy.on_trade(trade, ctx)
        assert intents is None


class TestInsiderCopyIntegration:
    """Integration test: run strategy through BacktestRunner."""

    @pytest.mark.asyncio
    async def test_backtest_runs_without_error(self) -> None:
        from polymarket_pipeline.strategies.config import StrategyConfig
        from polymarket_pipeline.strategies.context.memory import InMemoryContext
        from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
        from polymarket_pipeline.strategies.execution.realistic import (
            FillModelConfig,
            RealisticFillSimulator,
        )
        from polymarket_pipeline.strategies.runners.backtest import BacktestRunner
        from polymarket_pipeline.strategies.types import ExecutionMode
        from research.strategies.s2_insider_copy import InsiderCopyStrategy

        # Minimal insider pool: one "insider" who happens to be the maker
        pool = {"maker_1": {"score": 0.9, "direction": "YES"}}
        strategy = InsiderCopyStrategy(
            insider_pool=pool, min_consensus=1, size_usd=10.0, stop_loss_pct=0.50,
        )

        config = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000,
            max_position_usd=100,
            max_open_positions=10,
            cooldown_s=0,
        )

        ctx = InMemoryContext()
        executor = RealisticFillSimulator(config=FillModelConfig())
        gateway = ExecutionGateway(executor)

        runner = BacktestRunner(
            strategy=strategy,
            ctx=ctx,
            gateway=gateway,
            config=config,
        )

        # Create trades where maker_1 is the insider
        trades = [
            _make_trade(
                maker="maker_1", condition_id="cid_A", side="BUY",
                price=0.60, amount_usd=500.0, ts=1000.0,
            ),
            _make_trade(
                maker="random", condition_id="cid_A", side="BUY",
                price=0.62, amount_usd=100.0, ts=1100.0,
            ),
            _make_trade(
                maker="maker_1", condition_id="cid_B", side="BUY",
                price=0.40, amount_usd=300.0, ts=1200.0,
            ),
        ]

        result = await runner.run(trades)

        # Should have attempted to fill at least the first insider trade
        assert result.total_trades == 3
        assert result.total_intents >= 1
        assert result.total_fills >= 1
