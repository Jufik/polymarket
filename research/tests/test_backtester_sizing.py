"""Tests for bet sizing module — apply_sizing function."""

from __future__ import annotations

import polars as pl
import pytest

from strategies.consistency_copy.backtester.sizing import apply_sizing


def _make_bets() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "condition_id": ["a", "b", "c", "d", "e"],
            "agreement_frac": [0.60, 0.70, 0.80, 0.90, 0.55],
            "trigger_entry_price": [0.30, 0.40, 0.25, 0.50, 0.35],
            "resolution_value": [1, 0, 1, 1, 0],
            "signal_direction": ["YES", "YES", "YES", "YES", "YES"],
            "won": [True, False, True, True, False],
        }
    )


# --------------------------------------------------------------------------
# Test 1: fixed sizing — all bet_size = base_bet, bet_pnl column exists
# --------------------------------------------------------------------------
def test_fixed_sizing() -> None:
    result = apply_sizing(_make_bets(), strategy="fixed", base_bet=10.0)
    assert "bet_size" in result.columns
    assert "bet_pnl" in result.columns
    assert result["bet_size"].to_list() == pytest.approx([10.0, 10.0, 10.0, 10.0, 10.0])


# --------------------------------------------------------------------------
# Test 2: agreement_weighted — bet = base * (agreement_frac - 0.5) * 2
# --------------------------------------------------------------------------
def test_agreement_weighted_sizing() -> None:
    result = apply_sizing(_make_bets(), strategy="agreement_weighted", base_bet=10.0)
    expected = [2.0, 4.0, 6.0, 8.0, 1.0]
    assert result["bet_size"].to_list() == pytest.approx(expected)


# --------------------------------------------------------------------------
# Test 3: P&L for a win — row "a" with fixed sizing base_bet=10
#   fee = 0.02 * min(0.30, 0.70) * 10 = 0.06
#   pnl = 10 * (1 - 0.30) / 0.30 - 0.06 = 10 * 0.70 / 0.30 - 0.06
#       = 23.3333... - 0.06 = 23.2733...
# --------------------------------------------------------------------------
def test_pnl_computation_win() -> None:
    result = apply_sizing(_make_bets(), strategy="fixed", base_bet=10.0, fee_pct=0.02)
    row_a = result.filter(pl.col("condition_id") == "a")
    fee = row_a["fee"].item()
    pnl = row_a["bet_pnl"].item()
    assert fee == pytest.approx(0.06, abs=1e-6)
    assert pnl == pytest.approx(10.0 * 0.70 / 0.30 - 0.06, abs=1e-4)


# --------------------------------------------------------------------------
# Test 4: P&L for a loss — row "b" with fixed sizing base_bet=10
#   fee = 0.02 * min(0.40, 0.60) * 10 = 0.08
#   pnl = -10 - 0.08 = -10.08
# --------------------------------------------------------------------------
def test_pnl_computation_loss() -> None:
    result = apply_sizing(_make_bets(), strategy="fixed", base_bet=10.0, fee_pct=0.02)
    row_b = result.filter(pl.col("condition_id") == "b")
    fee = row_b["fee"].item()
    pnl = row_b["bet_pnl"].item()
    assert fee == pytest.approx(0.08, abs=1e-6)
    assert pnl == pytest.approx(-10.08, abs=1e-4)
