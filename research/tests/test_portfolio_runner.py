"""Tests for portfolio replication backtester."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 6, 1, hour, minute, tzinfo=timezone.utc)


def _make_holdout_pnl() -> pl.DataFrame:
    """Two traders, multiple markets resolving in holdout window.

    t1: markets A (+10), B (-5), C (+20)  -> pnl=+25, 2/3 wins, wr=66.7%
    t2: markets A (-3), D (+8)            -> pnl=+5,  1/2 wins, wr=50.0%
    t3 (not in pool): market A (+100)     -> should be excluded
    """
    return pl.DataFrame({
        "trader": ["t1", "t1", "t1", "t2", "t2", "t3"],
        "condition_id": ["A", "B", "C", "A", "D", "A"],
        "market_pnl": [10.0, -5.0, 20.0, -3.0, 8.0, 100.0],
        "market_volume": [100.0, 50.0, 200.0, 80.0, 120.0, 500.0],
        "net_yes_tokens": [100.0, -50.0, 200.0, -30.0, 80.0, 1000.0],
        "wavg_yes_entry_price": [0.30, 0.70, 0.40, 0.60, 0.35, 0.10],
        "first_trade": [_ts(8), _ts(9), _ts(10), _ts(8, 30), _ts(9, 30), _ts(8)],
        "last_trade": [_ts(8, 5), _ts(9, 5), _ts(10, 5), _ts(8, 35), _ts(9, 35), _ts(8, 5)],
        "resolved_at": [_ts(23)] * 6,
        "resolution_value": [1, 0, 1, 1, 0, 1],
        "yes_won": [True, False, True, True, False, True],
    })


def test_evaluate_traders_actual_shape_and_values():
    from strategies.consistency_copy.backtester.portfolio_runner import (
        evaluate_traders_actual,
    )

    holdout = _make_holdout_pnl()
    pool = {"t1", "t2"}
    result = evaluate_traders_actual(holdout, pool)

    assert set(result.columns) == {
        "trader", "actual_pnl", "actual_n_markets",
        "actual_wins", "actual_win_rate", "actual_volume",
    }
    assert result.height == 2  # only t1, t2 (not t3)

    t1 = result.filter(pl.col("trader") == "t1").to_dicts()[0]
    assert t1["actual_pnl"] == pytest.approx(25.0)
    assert t1["actual_n_markets"] == 3
    assert t1["actual_wins"] == 2
    assert t1["actual_win_rate"] == pytest.approx(2.0 / 3.0)
    assert t1["actual_volume"] == pytest.approx(350.0)

    t2 = result.filter(pl.col("trader") == "t2").to_dicts()[0]
    assert t2["actual_pnl"] == pytest.approx(5.0)
    assert t2["actual_n_markets"] == 2
    assert t2["actual_wins"] == 1
    assert t2["actual_win_rate"] == pytest.approx(0.5)


def test_evaluate_traders_copy_basic():
    """Copy PnL using trader's own entry price (no forward pricing)."""
    from strategies.consistency_copy.backtester.portfolio_runner import (
        evaluate_traders_copy,
    )

    holdout = _make_holdout_pnl()
    pool = {"t1", "t2"}
    base_bet = 100.0

    result = evaluate_traders_copy(holdout, pool, entry_prices=None, base_bet=base_bet)

    assert set(result.columns) == {
        "trader", "copy_pnl", "copy_n_markets",
        "copy_wins", "copy_win_rate",
    }
    assert result.height == 2

    # t1 market A: YES (net_yes>0), entry=0.30, yes_won=True -> won
    #   pnl = 100 * (1-0.30)/0.30 = 233.33
    # t1 market B: NO (net_yes<0), entry=1-0.70=0.30, yes_won=False -> won (NO wins)
    #   pnl = 100 * (1-0.30)/0.30 = 233.33
    # t1 market C: YES (net_yes>0), entry=0.40, yes_won=True -> won
    #   pnl = 100 * (1-0.40)/0.40 = 150.00
    # t1 total: 233.33 + 233.33 + 150.00 = 616.67, 3/3 wins
    t1 = result.filter(pl.col("trader") == "t1").to_dicts()[0]
    assert t1["copy_wins"] == 3
    assert t1["copy_n_markets"] == 3
    assert t1["copy_pnl"] == pytest.approx(616.67, abs=0.01)

    # t2 market A: NO (net_yes<0), entry=1-0.60=0.40, yes_won=True -> lost (YES won)
    #   pnl = -100
    # t2 market D: YES (net_yes>0), entry=0.35, yes_won=False -> lost (NO won)
    #   pnl = -100
    # t2 total: -200, 0/2 wins
    t2 = result.filter(pl.col("trader") == "t2").to_dicts()[0]
    assert t2["copy_wins"] == 0
    assert t2["copy_n_markets"] == 2
    assert t2["copy_pnl"] == pytest.approx(-200.0)


def test_evaluate_traders_copy_with_forward_prices():
    """Copy PnL uses forward prices when available, falls back otherwise."""
    from strategies.consistency_copy.backtester.portfolio_runner import (
        evaluate_traders_copy,
    )

    holdout = _make_holdout_pnl()
    pool = {"t1"}

    # Forward prices: only for market A (t1), not B or C
    entry_prices = pl.DataFrame({
        "condition_id": ["A"],
        "trader": ["t1"],
        "first_trade": [_ts(8)],
        "market_yes_price": [0.25],  # different from trader's 0.30
    })

    result = evaluate_traders_copy(holdout, pool, entry_prices=entry_prices, base_bet=100.0)
    t1 = result.to_dicts()[0]

    # Market A: YES, forward yes_price=0.25, won -> 100*(1-0.25)/0.25 = 300.00
    # Market B: NO, no forward -> fallback entry=1-0.70=0.30, won -> 233.33
    # Market C: YES, no forward -> fallback entry=0.40, won -> 150.00
    # Total: 300 + 233.33 + 150 = 683.33
    assert t1["copy_pnl"] == pytest.approx(683.33, abs=0.01)
