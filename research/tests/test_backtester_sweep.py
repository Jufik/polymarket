"""Tests for sweep engine — vectorized parameter grid over signal table."""

from __future__ import annotations

from datetime import datetime

import polars as pl

from strategies.consistency_copy.backtester.sweep import SweepConfig, run_sweep


def _make_signal_table() -> pl.DataFrame:
    """3 markets, varying consensus states.
    m1: 3 arrivals, all YES, resolution=1
    m2: 2 arrivals, first NO then YES, resolution=0
    m3: 4 arrivals, all YES, resolution=1
    """
    base = datetime(2025, 12, 15)
    return pl.DataFrame({
        "condition_id": ["m1"] * 3 + ["m2"] * 2 + ["m3"] * 4,
        "arrival_idx": [1, 2, 3, 1, 2, 1, 2, 3, 4],
        "trigger_time": [base.replace(hour=h) for h in [8, 9, 10, 8, 9, 8, 9, 10, 11]],
        "resolved_at": [datetime(2025, 12, 20)] * 3
        + [datetime(2025, 12, 22)] * 2
        + [datetime(2025, 12, 25)] * 4,
        "resolution_value": [1] * 3 + [0] * 2 + [1] * 4,
        "n_traders": [1, 2, 3, 1, 2, 1, 2, 3, 4],
        "n_yes": [1, 2, 3, 0, 1, 1, 1, 2, 3],
        "n_no": [0, 0, 0, 1, 1, 0, 1, 1, 1],
        "agreement_frac": [1.0, 1.0, 1.0, 1.0, 0.5, 1.0, 0.5, 2 / 3, 0.75],
        "signal_direction": ["YES"] * 3 + ["NO", "YES"] + ["YES", "YES", "YES", "YES"],
        "trigger_entry_price": [0.30, 0.35, 0.40, 0.60, 0.55, 0.20, 0.25, 0.30, 0.35],
        "avg_pool_entry": [0.30, 0.325, 0.35, None, 0.55, 0.20, 0.225, 0.25, 0.283],
        "trader": [f"t{i}" for i in [1, 2, 3, 4, 5, 6, 7, 8, 9]],
        "mvf": [0.05, 0.05, 0.20, 0.05, 0.30, 0.60, 0.05, 0.05, 0.10],
    })


# --------------------------------------------------------------------------
# Test 1: run_sweep returns a pl.DataFrame with expected metric columns
# --------------------------------------------------------------------------
def test_sweep_returns_results() -> None:
    st = _make_signal_table()
    config = SweepConfig(
        min_traders_values=[1, 2],
        agreement_pct_values=[0.50],
        direction_values=["both"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
        min_bets=1,
    )
    result = run_sweep(st, config, base_bet=10.0, fee_pct=0.02)
    assert isinstance(result, pl.DataFrame)
    assert result.height > 0
    # Must contain metric columns
    for col in ("hit_rate", "total_pnl", "sharpe"):
        assert col in result.columns, f"Missing column: {col}"
    # Must contain param columns
    for col in ("min_traders", "agreement_pct", "direction", "price_band_lo", "price_band_hi",
                "sizing"):
        assert col in result.columns, f"Missing param column: {col}"


# --------------------------------------------------------------------------
# Test 2: min_traders=3, agree=0.50, direction=both
#   Qualifying rows: n_traders >= 3 AND agreement_frac >= 0.50
#     m1: idx=3 (n_traders=3, agree=1.0) -> qualifies
#     m2: neither row has n_traders >= 3 -> excluded
#     m3: idx=3 (n_traders=3, agree=2/3), idx=4 (n_traders=4, agree=0.75) -> qualify
#   First per market: m1@3, m3@3 -> 2 bets
# --------------------------------------------------------------------------
def test_sweep_filters_min_traders() -> None:
    st = _make_signal_table()
    config = SweepConfig(
        min_traders_values=[3],
        agreement_pct_values=[0.50],
        direction_values=["both"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
        min_bets=1,
    )
    result = run_sweep(st, config, base_bet=10.0, fee_pct=0.02)
    assert result.height == 1  # one combo row
    assert result["total_bets"].item() == 2


# --------------------------------------------------------------------------
# Test 3: direction="YES-only" excludes NO signals
#   With min_traders=1, agree=0.50, all rows qualify.
#   First per market: m1@1 (YES), m2@1 (NO), m3@1 (YES).
#   YES-only filter removes m2 -> 2 bets.
# --------------------------------------------------------------------------
def test_sweep_yes_only_filter() -> None:
    st = _make_signal_table()
    config = SweepConfig(
        min_traders_values=[1],
        agreement_pct_values=[0.50],
        direction_values=["YES-only"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
        min_bets=1,
    )
    result = run_sweep(st, config, base_bet=10.0, fee_pct=0.02)
    assert result.height == 1
    assert result["total_bets"].item() == 2


# --------------------------------------------------------------------------
# Test 4: entry price band [0.25, 0.50] filters entries outside range
#   With min_traders=1, agree=0.50, direction=both, first per market:
#     m1@1: price=0.30 -> in [0.25, 0.50] -> keep
#     m2@1: price=0.60 -> out of [0.25, 0.50] -> excluded
#     m3@1: price=0.20 -> out of [0.25, 0.50] -> excluded
#   -> 1 bet
# --------------------------------------------------------------------------
def test_sweep_entry_price_band() -> None:
    st = _make_signal_table()
    config = SweepConfig(
        min_traders_values=[1],
        agreement_pct_values=[0.50],
        direction_values=["both"],
        entry_price_bands=[(0.25, 0.50)],
        sizing_strategies=["fixed"],
        min_bets=1,
    )
    result = run_sweep(st, config, base_bet=10.0, fee_pct=0.02)
    assert result.height == 1
    assert result["total_bets"].item() == 1
