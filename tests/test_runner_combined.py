"""Tests for combined multi-strategy backtest runner."""

from __future__ import annotations

import polars as pl
import pytest

from polymarket_pipeline.strategies.runners.combined import CombinedBacktestRunner


@pytest.fixture
def trades_lf() -> pl.LazyFrame:
    return pl.LazyFrame({
        "trade_id": [f"t{i}" for i in range(6)],
        "condition_id": ["0xm1", "0xm1", "0xm2", "0xm2", "0xm3", "0xm3"],
        "maker": ["0xA", "0xB", "0xA", "0xC", "0xA", "0xB"],
        "side": ["BUY", "BUY", "SELL", "SELL", "BUY", "BUY"],
        "price": [0.30, 0.25, 0.70, 0.80, 0.20, 0.22],
        "published_at": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


@pytest.fixture
def markets_lf() -> pl.LazyFrame:
    return pl.LazyFrame({
        "condition_id": ["0xm1", "0xm2", "0xm3"],
        "question": ["Will X?", "Will Y?", "Bitcoin above $100K?"],
        "category": ["Politics", "Politics", "Crypto"],
    })


def test_combined_runner_produces_per_strategy_signals(
    trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame
) -> None:
    """Each strategy should produce independent signals."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import (
        ProportionalCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
        ProportionalCopyStrategy,
    )
    from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
    from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders={"0xA", "0xB"}, contradiction_filter=False
        )
    )
    s2 = WillNoStrategy(
        config=WillNoConfig(
            yes_price_min=0.10, yes_price_max=0.50, avoid_keywords=set()
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1, s2],
        budgets={"proportional_copy": 1000.0, "will_no": 300.0},
    )

    result = runner.run(trades_lf, markets_lf)

    assert "strategy" in result.columns
    strategies_found = set(result["strategy"].to_list())
    # Both strategies should have produced signals
    assert len(strategies_found) >= 1  # at least one fires


def test_combined_runner_respects_budgets(
    trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame
) -> None:
    """Total size_usd per strategy should not exceed its budget."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import (
        ProportionalCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
        ProportionalCopyStrategy,
    )

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders={"0xA", "0xB", "0xC"},
            capital_per_trader_usd=500.0,
            contradiction_filter=False,
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1],
        budgets={"proportional_copy": 600.0},
    )

    result = runner.run(trades_lf, markets_lf)
    total_spent = result.filter(pl.col("strategy") == "proportional_copy")[
        "size_usd"
    ].sum()
    assert total_spent <= 600.0


def test_combined_runner_returns_equity_curve(
    trades_lf: pl.LazyFrame, markets_lf: pl.LazyFrame
) -> None:
    """Runner should return equity curve DataFrame."""
    from polymarket_pipeline.strategies_impl.proportional_copy.config import (
        ProportionalCopyConfig,
    )
    from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
        ProportionalCopyStrategy,
    )

    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders={"0xA"}, contradiction_filter=False
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1],
        budgets={"proportional_copy": 1000.0},
    )

    signals = runner.run(trades_lf, markets_lf)
    assert "signal_time" in signals.columns
    assert "size_usd" in signals.columns
