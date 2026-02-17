"""Tests for P&L curve analytics — backtester metrics module."""

from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from strategies.consistency_copy.backtester.metrics import compute_metrics


def _make_daily_pnl() -> pl.DataFrame:
    """10 days: daily_pnl = [5, 3, -8, 4, 6, -2, 7, 5, -4, 10],
    n_bets = [3,2,1,4,3,2,5,3,1,4], n_wins = [2,2,0,3,2,1,4,3,0,3]"""
    return pl.DataFrame(
        {
            "resolved_date": [
                date(2025, 1, 1),
                date(2025, 1, 2),
                date(2025, 1, 3),
                date(2025, 1, 4),
                date(2025, 1, 5),
                date(2025, 1, 6),
                date(2025, 1, 7),
                date(2025, 1, 8),
                date(2025, 1, 9),
                date(2025, 1, 10),
            ],
            "daily_pnl": [5.0, 3.0, -8.0, 4.0, 6.0, -2.0, 7.0, 5.0, -4.0, 10.0],
            "n_bets": [3, 2, 1, 4, 3, 2, 5, 3, 1, 4],
            "n_wins": [2, 2, 0, 3, 2, 1, 4, 3, 0, 3],
        },
        schema={
            "resolved_date": pl.Date,
            "daily_pnl": pl.Float64,
            "n_bets": pl.Int64,
            "n_wins": pl.Int64,
        },
    )


REQUIRED_KEYS = {
    "total_pnl",
    "total_bets",
    "total_wins",
    "hit_rate",
    "sharpe",
    "max_drawdown",
    "max_drawdown_pct",
    "profit_factor",
    "avg_daily_pnl",
    "pnl_per_bet",
    "n_days",
    "win_days",
    "loss_days",
    "best_day",
    "worst_day",
    "max_win_streak",
    "max_loss_streak",
}


# --------------------------------------------------------------------------
# Test 1: all required keys present
# --------------------------------------------------------------------------
def test_compute_metrics_keys() -> None:
    metrics = compute_metrics(_make_daily_pnl())
    assert set(metrics.keys()) >= REQUIRED_KEYS


# --------------------------------------------------------------------------
# Test 2: basic aggregate values
# --------------------------------------------------------------------------
def test_compute_metrics_basic_values() -> None:
    metrics = compute_metrics(_make_daily_pnl())
    assert metrics["total_pnl"] == pytest.approx(26.0)
    assert metrics["total_bets"] == 28
    assert metrics["total_wins"] == 20
    assert metrics["hit_rate"] == pytest.approx(20 / 28)
    assert metrics["n_days"] == 10
    assert metrics["win_days"] == 7
    assert metrics["loss_days"] == 3
    assert metrics["best_day"] == pytest.approx(10.0)
    assert metrics["worst_day"] == pytest.approx(-8.0)


# --------------------------------------------------------------------------
# Test 3: profit factor = gross_wins / gross_losses
# --------------------------------------------------------------------------
def test_compute_metrics_profit_factor() -> None:
    """gross_wins = 5+3+4+6+7+5+10 = 40, gross_losses = 8+2+4 = 14."""
    metrics = compute_metrics(_make_daily_pnl())
    assert metrics["profit_factor"] == pytest.approx(40.0 / 14.0)


# --------------------------------------------------------------------------
# Test 4: max drawdown
# --------------------------------------------------------------------------
def test_compute_metrics_drawdown() -> None:
    """cumulative: [5,8,0,4,10,8,15,20,16,26]
    peak:       [5,8,8,8,10,10,15,20,20,26]
    drawdown:   [0,0,-8,-4,0,-2,0,0,-4,0]
    max_drawdown = 8 (absolute value)
    max_drawdown_pct = 8/8 = 1.0  (drawdown of 8 from peak of 8)
    """
    metrics = compute_metrics(_make_daily_pnl())
    assert metrics["max_drawdown"] == pytest.approx(8.0)
    assert metrics["max_drawdown_pct"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Test 5: streaks
# --------------------------------------------------------------------------
def test_compute_metrics_streaks() -> None:
    """PnL signs: +,+,-,+,+,-,+,+,-,+
    Win streaks: 2, 2, 2, 1 -> max = 2
    Loss streaks: 1, 1, 1 -> max = 1
    """
    metrics = compute_metrics(_make_daily_pnl())
    assert metrics["max_win_streak"] == 2
    assert metrics["max_loss_streak"] == 1


# --------------------------------------------------------------------------
# Test 6: Sharpe ratio (annualized)
# --------------------------------------------------------------------------
def test_compute_metrics_sharpe() -> None:
    """Sharpe = mean(daily_pnl) / std(daily_pnl, ddof=1) * sqrt(365)."""
    pnl_values = [5.0, 3.0, -8.0, 4.0, 6.0, -2.0, 7.0, 5.0, -4.0, 10.0]
    mean_pnl = sum(pnl_values) / len(pnl_values)
    var_pnl = sum((x - mean_pnl) ** 2 for x in pnl_values) / (len(pnl_values) - 1)
    std_pnl = math.sqrt(var_pnl)
    expected_sharpe = (mean_pnl / std_pnl) * math.sqrt(365)

    metrics = compute_metrics(_make_daily_pnl())
    assert metrics["sharpe"] == pytest.approx(expected_sharpe)


# --------------------------------------------------------------------------
# Test 7: empty DataFrame returns all zeros
# --------------------------------------------------------------------------
def test_compute_metrics_empty() -> None:
    empty_df = pl.DataFrame(
        {
            "resolved_date": [],
            "daily_pnl": [],
            "n_bets": [],
            "n_wins": [],
        },
        schema={
            "resolved_date": pl.Date,
            "daily_pnl": pl.Float64,
            "n_bets": pl.Int64,
            "n_wins": pl.Int64,
        },
    )
    metrics = compute_metrics(empty_df)
    for key in REQUIRED_KEYS:
        assert metrics[key] == 0, f"Expected {key} to be 0 for empty input, got {metrics[key]}"


# --------------------------------------------------------------------------
# Base rate adjustment tests
# --------------------------------------------------------------------------

def _make_daily_pnl_br(
    pnl_values: list[float], bets: int = 1, wins: int = 1,
) -> pl.DataFrame:
    base = date(2025, 12, 1)
    return pl.DataFrame({
        "resolved_date": [base + timedelta(days=i) for i in range(len(pnl_values))],
        "daily_pnl": pnl_values,
        "n_bets": [bets] * len(pnl_values),
        "n_wins": [wins] * len(pnl_values),
    })


def test_metrics_without_base_rate_backward_compatible() -> None:
    daily = _make_daily_pnl_br([10.0, -5.0, 8.0, -2.0, 6.0])
    result = compute_metrics(daily)
    assert "excess_hr" not in result
    assert "base_adjusted_sharpe" not in result
    assert "sharpe" in result


def test_metrics_with_base_rate_adds_excess_hr() -> None:
    daily = _make_daily_pnl_br([10.0, -5.0, 8.0], bets=2, wins=1)
    result = compute_metrics(daily, base_rate=0.60)
    assert "excess_hr" in result
    # hit_rate = 3 wins / 6 bets = 0.5, excess = 0.5 - 0.6 = -0.1
    assert abs(result["excess_hr"] - (-0.1)) < 1e-9


def test_metrics_with_base_rate_adds_adjusted_sharpe() -> None:
    daily = _make_daily_pnl_br([10.0, -5.0, 8.0, -2.0, 6.0])
    result = compute_metrics(daily, base_rate=0.50)
    assert "base_adjusted_sharpe" in result
    assert isinstance(result["base_adjusted_sharpe"], float)


def test_metrics_base_rate_zero_gives_full_excess() -> None:
    daily = _make_daily_pnl_br([10.0, 10.0, 10.0], bets=1, wins=1)
    result = compute_metrics(daily, base_rate=0.0)
    assert abs(result["excess_hr"] - 1.0) < 1e-9


def test_metrics_base_rate_one_gives_negative_excess() -> None:
    daily = _make_daily_pnl_br([10.0, -5.0], bets=2, wins=1)
    result = compute_metrics(daily, base_rate=1.0)
    assert result["excess_hr"] < 0


def test_metrics_empty_with_base_rate() -> None:
    empty = pl.DataFrame(schema={
        "resolved_date": pl.Date,
        "daily_pnl": pl.Float64,
        "n_bets": pl.Int64,
        "n_wins": pl.Int64,
    })
    result = compute_metrics(empty, base_rate=0.5)
    assert result["excess_hr"] == 0
    assert result["base_adjusted_sharpe"] == 0


def test_metrics_with_baseline_daily_pnl() -> None:
    daily = _make_daily_pnl_br([10.0, -5.0, 8.0, -2.0, 6.0])
    baseline = [5.0, -3.0, 4.0, -1.0, 3.0]
    result = compute_metrics(daily, base_rate=0.50, baseline_daily_pnl=baseline)
    assert "base_adjusted_sharpe" in result
    # Excess series: [5, -2, 4, -1, 3] -> mean=1.8, should give positive adjusted sharpe
    assert result["base_adjusted_sharpe"] > 0
