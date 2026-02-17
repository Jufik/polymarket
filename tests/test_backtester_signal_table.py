"""Tests for signal table builder — consensus-copy backtester core data structure."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from strategies.consistency_copy.backtester.signal_table import build_signal_table


def _ts(hour: int, minute: int = 0) -> datetime:
    """Helper: build a UTC datetime on a fixed date with given hour:minute."""
    return datetime(2025, 6, 1, hour, minute, tzinfo=timezone.utc)


def _make_pnl_df() -> pl.DataFrame:
    """Market A (resolution_value=1, YES won):
      t1: 08:00, pnl=+10, yes_price=0.30 -> YES (positive pnl + resolution=1)
      t2: 09:00, pnl=-5,  yes_price=0.00 -> NO  (negative pnl + resolution=1)
      t3: 10:00, pnl=+20, yes_price=0.40 -> YES
      t4: 11:00, pnl=+15, yes_price=0.35 -> YES

    Market B (resolution_value=0, NO won):
      t1: 08:30, pnl=+5,  yes_price=0.00 -> NO  (positive pnl + resolution=0)
      t2: 09:30, pnl=-10, yes_price=0.60 -> YES  (negative pnl + resolution=0)
      t5: 10:30, pnl=+8,  yes_price=0.00 -> NO
      t3: 11:30, pnl=+3,  yes_price=0.00 -> NO
    """
    return pl.DataFrame(
        {
            "trader": ["t1", "t2", "t3", "t4", "t1", "t2", "t5", "t3"],
            "condition_id": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "resolution_value": [1, 1, 1, 1, 0, 0, 0, 0],
            "market_pnl": [10.0, -5.0, 20.0, 15.0, 5.0, -10.0, 8.0, 3.0],
            "wavg_yes_entry_price": [0.30, 0.00, 0.40, 0.35, 0.00, 0.60, 0.00, 0.00],
            "first_trade": [
                _ts(8, 0),
                _ts(9, 0),
                _ts(10, 0),
                _ts(11, 0),
                _ts(8, 30),
                _ts(9, 30),
                _ts(10, 30),
                _ts(11, 30),
            ],
            "last_trade": [
                _ts(8, 5),
                _ts(9, 5),
                _ts(10, 5),
                _ts(11, 5),
                _ts(8, 35),
                _ts(9, 35),
                _ts(10, 35),
                _ts(11, 35),
            ],
            "resolved_at": [
                _ts(23, 0),
                _ts(23, 0),
                _ts(23, 0),
                _ts(23, 0),
                _ts(23, 30),
                _ts(23, 30),
                _ts(23, 30),
                _ts(23, 30),
            ],
        }
    )


def _make_skilled_traders() -> set[str]:
    """All traders in the fixture are skilled."""
    return {"t1", "t2", "t3", "t4", "t5"}


def _make_mvf_data() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trader": ["t1", "t2", "t3", "t4", "t5"],
            "mvf": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )


@pytest.fixture()
def signal_df() -> pl.DataFrame:
    return build_signal_table(_make_pnl_df(), _make_skilled_traders(), _make_mvf_data())


# --------------------------------------------------------------------------
# Test 1: shape and columns
# --------------------------------------------------------------------------
def test_signal_table_shape_and_columns(signal_df: pl.DataFrame) -> None:
    """8 rows (4 per market), correct column set."""
    assert signal_df.shape[0] == 8

    expected_cols = {
        "condition_id",
        "arrival_idx",
        "trigger_time",
        "resolved_at",
        "resolution_value",
        "n_traders",
        "n_yes",
        "n_no",
        "agreement_frac",
        "signal_direction",
        "trigger_entry_price",
        "avg_pool_entry",
        "trader",
        "mvf",
    }
    assert set(signal_df.columns) == expected_cols


# --------------------------------------------------------------------------
# Test 2: arrival order
# --------------------------------------------------------------------------
def test_signal_table_arrival_order(signal_df: pl.DataFrame) -> None:
    """Traders ordered by first_trade per market."""
    mkt_a = signal_df.filter(pl.col("condition_id") == "A").sort("arrival_idx")
    assert mkt_a["trader"].to_list() == ["t1", "t2", "t3", "t4"]

    mkt_b = signal_df.filter(pl.col("condition_id") == "B").sort("arrival_idx")
    assert mkt_b["trader"].to_list() == ["t1", "t2", "t5", "t3"]


# --------------------------------------------------------------------------
# Test 3: cumulative counts
# --------------------------------------------------------------------------
def test_signal_table_cumulative_counts(signal_df: pl.DataFrame) -> None:
    """n_yes/n_no accumulate correctly.

    Market A (resolution=1): YES if pnl>0, NO if pnl<0
      t1: pnl=+10 -> YES  => n_yes=[1], n_no=[0]
      t2: pnl=-5  -> NO   => n_yes=[1], n_no=[1]
      t3: pnl=+20 -> YES  => n_yes=[2], n_no=[1]
      t4: pnl=+15 -> YES  => n_yes=[3], n_no=[1]

    Market B (resolution=0): NO if pnl>0, YES if pnl<0
      t1: pnl=+5  -> NO   => n_yes=[0], n_no=[1]
      t2: pnl=-10 -> YES  => n_yes=[1], n_no=[1]
      t5: pnl=+8  -> NO   => n_yes=[1], n_no=[2]
      t3: pnl=+3  -> NO   => n_yes=[1], n_no=[3]
    """
    mkt_a = signal_df.filter(pl.col("condition_id") == "A").sort("arrival_idx")
    assert mkt_a["n_yes"].to_list() == [1, 1, 2, 3]
    assert mkt_a["n_no"].to_list() == [0, 1, 1, 1]
    assert mkt_a["n_traders"].to_list() == [1, 2, 3, 4]

    mkt_b = signal_df.filter(pl.col("condition_id") == "B").sort("arrival_idx")
    assert mkt_b["n_yes"].to_list() == [0, 1, 1, 1]
    assert mkt_b["n_no"].to_list() == [1, 1, 2, 3]
    assert mkt_b["n_traders"].to_list() == [1, 2, 3, 4]


# --------------------------------------------------------------------------
# Test 4: agreement fraction
# --------------------------------------------------------------------------
def test_signal_table_agreement_frac(signal_df: pl.DataFrame) -> None:
    """agreement_frac = max(n_yes, n_no) / n_traders."""
    mkt_a = signal_df.filter(pl.col("condition_id") == "A").sort("arrival_idx")
    # [1/1, 1/2, 2/3, 3/4]
    expected_a = [1.0, 0.5, 2.0 / 3.0, 0.75]
    actual_a = mkt_a["agreement_frac"].to_list()
    for exp, act in zip(expected_a, actual_a):
        assert abs(exp - act) < 1e-9, f"Expected {exp}, got {act}"

    mkt_b = signal_df.filter(pl.col("condition_id") == "B").sort("arrival_idx")
    # [1/1, 1/2, 2/3, 3/4]
    expected_b = [1.0, 0.5, 2.0 / 3.0, 0.75]
    actual_b = mkt_b["agreement_frac"].to_list()
    for exp, act in zip(expected_b, actual_b):
        assert abs(exp - act) < 1e-9, f"Expected {exp}, got {act}"


# --------------------------------------------------------------------------
# Test 5: signal direction (ties go to YES)
# --------------------------------------------------------------------------
def test_signal_table_direction(signal_df: pl.DataFrame) -> None:
    """YES/NO majority at each step. Ties go to YES."""
    mkt_a = signal_df.filter(pl.col("condition_id") == "A").sort("arrival_idx")
    # yes=[1,1,2,3], no=[0,1,1,1] => YES, YES(tie), YES, YES
    assert mkt_a["signal_direction"].to_list() == ["YES", "YES", "YES", "YES"]

    mkt_b = signal_df.filter(pl.col("condition_id") == "B").sort("arrival_idx")
    # yes=[0,1,1,1], no=[1,1,2,3] => NO, YES(tie), NO, NO
    assert mkt_b["signal_direction"].to_list() == ["NO", "YES", "NO", "NO"]


# --------------------------------------------------------------------------
# Test 6: trigger entry price
# --------------------------------------------------------------------------
def test_signal_table_trigger_entry_price(signal_df: pl.DataFrame) -> None:
    """trigger_entry_price = wavg_yes_entry_price of the triggering trader."""
    mkt_a = signal_df.filter(pl.col("condition_id") == "A").sort("arrival_idx")
    assert mkt_a["trigger_entry_price"].to_list() == [0.30, 0.00, 0.40, 0.35]

    mkt_b = signal_df.filter(pl.col("condition_id") == "B").sort("arrival_idx")
    assert mkt_b["trigger_entry_price"].to_list() == [0.00, 0.60, 0.00, 0.00]


# --------------------------------------------------------------------------
# Test 7: MVF joined
# --------------------------------------------------------------------------
def test_signal_table_mvf_joined(signal_df: pl.DataFrame) -> None:
    """MVF values correctly joined from mvf_data."""
    mvf_map = {"t1": 0.1, "t2": 0.2, "t3": 0.3, "t4": 0.4, "t5": 0.5}

    for row in signal_df.iter_rows(named=True):
        expected_mvf = mvf_map[row["trader"]]
        assert abs(row["mvf"] - expected_mvf) < 1e-9, (
            f"Trader {row['trader']}: expected mvf={expected_mvf}, got {row['mvf']}"
        )
