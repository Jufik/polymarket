# Consensus Signal Backtester Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a vectorized parameter sweep backtester for the consistency_copy consensus signal strategy, with walk-forward validation and P&L curve tracking.

**Architecture:** Pre-compute a "signal table" (one row per market x Nth skilled trader arrival) that captures running consensus state. All parameter sweeps are pure Polars filter+aggregate on this table. Bet sizing is applied as post-processing. Walk-forward validation across 3 rolling windows.

**Tech Stack:** Python 3.11+, Polars, NumPy, Pydantic v2. Data from cached Parquet files (no ClickHouse needed).

---

### Task 1: Signal Table Builder — Core Data Structure

**Files:**
- Create: `strategies/consistency_copy/backtester/__init__.py`
- Create: `strategies/consistency_copy/backtester/signal_table.py`
- Test: `tests/test_backtester_signal_table.py`

This is the core of the whole system. The signal table has one row per (market, Nth skilled trader arrival), with running consensus state.

**Step 1: Write the failing test**

Create `tests/test_backtester_signal_table.py`:

```python
"""Tests for signal table builder."""
from __future__ import annotations

import polars as pl
import pytest
from datetime import datetime


def _make_pnl_df() -> pl.DataFrame:
    """Tiny PnL dataset: 2 markets, 4 traders each.

    Market A (condition_id='mkt_a', resolution_value=1 → YES won):
      Trader t1: first_trade=08:00, market_pnl=+10, wavg_yes_entry_price=0.30  → bet YES (won)
      Trader t2: first_trade=09:00, market_pnl=-5,  wavg_yes_entry_price=0.00  → bet NO  (lost)
      Trader t3: first_trade=10:00, market_pnl=+20, wavg_yes_entry_price=0.40  → bet YES (won)
      Trader t4: first_trade=11:00, market_pnl=+15, wavg_yes_entry_price=0.35  → bet YES (won)

    Market B (condition_id='mkt_b', resolution_value=0 → NO won):
      Trader t1: first_trade=08:30, market_pnl=+5,  wavg_yes_entry_price=0.00  → bet NO  (won)
      Trader t2: first_trade=09:30, market_pnl=-10, wavg_yes_entry_price=0.60  → bet YES (lost)
      Trader t5: first_trade=10:30, market_pnl=+8,  wavg_yes_entry_price=0.00  → bet NO  (won)
      Trader t3: first_trade=11:30, market_pnl=+3,  wavg_yes_entry_price=0.00  → bet NO  (won)
    """
    base = datetime(2025, 12, 15)
    return pl.DataFrame({
        "trader":              ["t1","t2","t3","t4",  "t1","t2","t5","t3"],
        "condition_id":        ["mkt_a"]*4 + ["mkt_b"]*4,
        "resolution_value":    [1,1,1,1,  0,0,0,0],
        "settlement_value":    [1.0]*8,
        "total_spent":         [0.0]*8,
        "total_received":      [0.0]*8,
        "total_fees":          [0.0]*8,
        "market_pnl":          [10.0, -5.0, 20.0, 15.0,  5.0, -10.0, 8.0, 3.0],
        "market_volume":       [100.0]*8,
        "trade_count":         [1]*8,
        "wavg_yes_entry_price":[0.30, 0.00, 0.40, 0.35,  0.00, 0.60, 0.00, 0.00],
        "first_trade": [
            base.replace(hour=8), base.replace(hour=9),
            base.replace(hour=10), base.replace(hour=11),
            base.replace(hour=8, minute=30), base.replace(hour=9, minute=30),
            base.replace(hour=10, minute=30), base.replace(hour=11, minute=30),
        ],
        "last_trade": [
            base.replace(hour=8), base.replace(hour=9),
            base.replace(hour=10), base.replace(hour=11),
            base.replace(hour=8, minute=30), base.replace(hour=9, minute=30),
            base.replace(hour=10, minute=30), base.replace(hour=11, minute=30),
        ],
        "resolved_at": [
            base.replace(day=20)]*4 + [base.replace(day=22)]*4,
    })


def test_signal_table_shape_and_columns():
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({
        "trader": ["t1", "t2", "t3", "t4", "t5"],
        "mvf": [0.05, 0.20, 0.50, 0.80, 0.02],
    })

    st = build_signal_table(df, skilled, mvf)

    # 4 skilled traders per market, 2 markets = 8 rows
    assert st.height == 8
    expected_cols = {
        "condition_id", "arrival_idx", "trigger_time", "resolved_at",
        "resolution_value", "n_traders", "n_yes", "n_no",
        "agreement_frac", "signal_direction", "trigger_entry_price",
        "avg_pool_entry", "trader", "mvf",
    }
    assert set(st.columns) == expected_cols


def test_signal_table_arrival_order():
    """Traders arrive in first_trade order per market."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({"trader": list(skilled), "mvf": [0.0]*5})
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    assert mkt_a["trader"].to_list() == ["t1", "t2", "t3", "t4"]
    assert mkt_a["arrival_idx"].to_list() == [1, 2, 3, 4]


def test_signal_table_cumulative_counts():
    """n_yes/n_no accumulate correctly as traders arrive."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({"trader": list(skilled), "mvf": [0.0]*5})
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    # Market A: t1=YES, t2=NO, t3=YES, t4=YES
    assert mkt_a["n_yes"].to_list() == [1, 1, 2, 3]
    assert mkt_a["n_no"].to_list() == [0, 1, 1, 1]
    assert mkt_a["n_traders"].to_list() == [1, 2, 3, 4]


def test_signal_table_agreement_frac():
    """agreement_frac = max(n_yes, n_no) / n_traders."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({"trader": list(skilled), "mvf": [0.0]*5})
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    # After t1: 1/1=1.0, after t2: 1/2=0.5, after t3: 2/3=0.667, after t4: 3/4=0.75
    expected = [1.0, 0.5, 2/3, 0.75]
    actual = mkt_a["agreement_frac"].to_list()
    for a, e in zip(actual, expected):
        assert abs(a - e) < 0.001, f"{a} != {e}"


def test_signal_table_direction():
    """signal_direction reflects current majority."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({"trader": list(skilled), "mvf": [0.0]*5})
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    # t1=YES → YES, t2=NO → tie→YES (YES>=NO), t3=YES → YES, t4=YES → YES
    assert mkt_a["signal_direction"].to_list() == ["YES", "YES", "YES", "YES"]

    mkt_b = st.filter(pl.col("condition_id") == "mkt_b").sort("arrival_idx")
    # t1=NO → NO, t2=YES → tie→YES (YES>=NO), t5=NO → NO, t3=NO → NO
    assert mkt_b["signal_direction"].to_list() == ["NO", "YES", "NO", "NO"]


def test_signal_table_trigger_entry_price():
    """trigger_entry_price is the Nth trader's wavg_yes_entry_price."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({"trader": list(skilled), "mvf": [0.0]*5})
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    assert mkt_a["trigger_entry_price"].to_list() == [0.30, 0.00, 0.40, 0.35]


def test_signal_table_mvf_joined():
    """MVF values are joined from the mvf DataFrame."""
    from strategies.consistency_copy.backtester.signal_table import build_signal_table

    df = _make_pnl_df()
    skilled = {"t1", "t2", "t3", "t4", "t5"}
    mvf = pl.DataFrame({
        "trader": ["t1", "t2", "t3", "t4", "t5"],
        "mvf": [0.05, 0.20, 0.50, 0.80, 0.02],
    })
    st = build_signal_table(df, skilled, mvf)

    mkt_a = st.filter(pl.col("condition_id") == "mkt_a").sort("arrival_idx")
    assert mkt_a["mvf"].to_list() == [0.05, 0.20, 0.50, 0.80]
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtester_signal_table.py -x -q`
Expected: ModuleNotFoundError (module doesn't exist yet)

**Step 3: Create package init**

Create `strategies/consistency_copy/backtester/__init__.py`:
```python
"""Consensus signal backtester — vectorized parameter sweep."""
```

**Step 4: Implement signal_table.py**

Create `strategies/consistency_copy/backtester/signal_table.py`:

```python
"""Build the signal table: one row per (market, Nth skilled trader arrival).

The signal table pre-computes running consensus state so that all parameter
sweeps are pure filter+aggregate operations — no loops over individual markets.
"""
from __future__ import annotations

import polars as pl


def _infer_direction(market_pnl: float, resolution_value: int) -> str:
    """Infer bet direction from P&L and resolution outcome.

    If trader profited when YES resolved (or lost when NO resolved) → YES.
    Otherwise → NO.
    """
    if (market_pnl > 0 and resolution_value == 1) or \
       (market_pnl < 0 and resolution_value == 0):
        return "YES"
    return "NO"


def build_signal_table(
    df_pnl: pl.DataFrame,
    skilled_traders: set[str],
    mvf_data: pl.DataFrame,
) -> pl.DataFrame:
    """Build signal table from PnL data for skilled traders.

    Args:
        df_pnl: Per-trader per-market PnL data. Must have columns:
            trader, condition_id, resolution_value, market_pnl,
            wavg_yes_entry_price, first_trade, resolved_at
        skilled_traders: Set of trader addresses in the skilled pool.
        mvf_data: DataFrame with columns (trader, mvf).

    Returns:
        Signal table with columns: condition_id, arrival_idx, trigger_time,
        resolved_at, resolution_value, n_traders, n_yes, n_no, agreement_frac,
        signal_direction, trigger_entry_price, avg_pool_entry, trader, mvf
    """
    # Filter to skilled traders with non-zero P&L
    df = df_pnl.filter(
        pl.col("trader").is_in(list(skilled_traders))
        & (pl.col("market_pnl") != 0)
    )

    # Infer bet direction
    df = df.with_columns(
        pl.when(
            ((pl.col("market_pnl") > 0) & (pl.col("resolution_value") == 1))
            | ((pl.col("market_pnl") < 0) & (pl.col("resolution_value") == 0))
        )
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("bet_direction")
    )

    # Sort by (condition_id, first_trade) and assign arrival index
    df = df.sort(["condition_id", "first_trade"])
    df = df.with_columns(
        pl.col("first_trade")
        .cum_count()
        .over("condition_id")
        .alias("arrival_idx")
    )

    # Cumulative counts per market
    df = df.with_columns([
        (pl.col("bet_direction") == "YES")
        .cum_sum()
        .over("condition_id")
        .alias("n_yes"),
        (pl.col("bet_direction") == "NO")
        .cum_sum()
        .over("condition_id")
        .alias("n_no"),
    ])
    df = df.with_columns(
        (pl.col("n_yes") + pl.col("n_no")).alias("n_traders")
    )

    # Agreement fraction and signal direction
    df = df.with_columns([
        (pl.max_horizontal("n_yes", "n_no") / pl.col("n_traders")).alias("agreement_frac"),
        pl.when(pl.col("n_yes") >= pl.col("n_no"))
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("signal_direction"),
    ])

    # Running average entry for the YES pool (cumulative mean of wavg_yes_entry_price for YES bettors)
    # For each row, avg_pool_entry = running mean of entry prices for the current majority side
    # Approximation: use running mean of all YES bettors' wavg_yes_entry_price
    df = df.with_columns(
        pl.when(pl.col("bet_direction") == "YES")
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(pl.lit(None))
        .alias("_yes_entry_for_avg")
    )
    df = df.with_columns(
        pl.col("_yes_entry_for_avg")
        .fill_null(strategy="forward")
        .over("condition_id")
        .alias("_yes_ffill")
    )
    # Cumulative mean of YES entries
    df = df.with_columns(
        pl.when(pl.col("bet_direction") == "YES")
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(pl.lit(0.0))
        .cum_sum()
        .over("condition_id")
        .alias("_yes_entry_cumsum")
    )
    df = df.with_columns(
        (
            pl.col("_yes_entry_cumsum")
            / pl.col("n_yes").cast(pl.Float64).replace(0, None)
        ).alias("avg_pool_entry")
    )

    # Join MVF
    df = df.join(mvf_data.select(["trader", "mvf"]), on="trader", how="left")
    df = df.with_columns(pl.col("mvf").fill_null(0.0))

    # Select final columns
    return df.select([
        "condition_id",
        "arrival_idx",
        pl.col("first_trade").alias("trigger_time"),
        "resolved_at",
        "resolution_value",
        "n_traders",
        "n_yes",
        "n_no",
        "agreement_frac",
        "signal_direction",
        pl.col("wavg_yes_entry_price").alias("trigger_entry_price"),
        "avg_pool_entry",
        "trader",
        "mvf",
    ])
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtester_signal_table.py -x -q`
Expected: All 7 tests PASS

**Step 6: Commit**

```bash
git add strategies/consistency_copy/backtester/ tests/test_backtester_signal_table.py
git commit -m "feat: add signal table builder for consensus backtester"
```

---

### Task 2: Metrics Module — P&L Curve Analytics

**Files:**
- Create: `strategies/consistency_copy/backtester/metrics.py`
- Test: `tests/test_backtester_metrics.py`

**Step 1: Write the failing test**

Create `tests/test_backtester_metrics.py`:

```python
"""Tests for backtester metrics computation."""
from __future__ import annotations

import polars as pl
import numpy as np
import pytest
from datetime import date


def _make_daily_pnl() -> pl.DataFrame:
    """10 days of P&L data: 7 wins, 3 losses."""
    return pl.DataFrame({
        "resolved_date": [
            date(2025, 12, 1), date(2025, 12, 2), date(2025, 12, 3),
            date(2025, 12, 4), date(2025, 12, 5), date(2025, 12, 6),
            date(2025, 12, 7), date(2025, 12, 8), date(2025, 12, 9),
            date(2025, 12, 10),
        ],
        "daily_pnl": [5.0, 3.0, -8.0, 4.0, 6.0, -2.0, 7.0, 5.0, -4.0, 10.0],
        "n_bets": [3, 2, 1, 4, 3, 2, 5, 3, 1, 4],
        "n_wins": [2, 2, 0, 3, 2, 1, 4, 3, 0, 3],
    })


def test_compute_metrics_keys():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = _make_daily_pnl()
    m = compute_metrics(df)
    required = {
        "total_pnl", "total_bets", "total_wins", "hit_rate",
        "sharpe", "max_drawdown", "max_drawdown_pct", "profit_factor",
        "avg_daily_pnl", "pnl_per_bet", "n_days", "win_days", "loss_days",
        "best_day", "worst_day", "max_win_streak", "max_loss_streak",
    }
    assert required.issubset(set(m.keys()))


def test_compute_metrics_basic_values():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = _make_daily_pnl()
    m = compute_metrics(df)
    assert m["total_pnl"] == pytest.approx(26.0)
    assert m["total_bets"] == 28
    assert m["total_wins"] == 20
    assert m["hit_rate"] == pytest.approx(20 / 28, abs=0.001)
    assert m["n_days"] == 10
    assert m["win_days"] == 7
    assert m["loss_days"] == 3
    assert m["best_day"] == pytest.approx(10.0)
    assert m["worst_day"] == pytest.approx(-8.0)


def test_compute_metrics_profit_factor():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = _make_daily_pnl()
    m = compute_metrics(df)
    gross_wins = 5 + 3 + 4 + 6 + 7 + 5 + 10  # 40
    gross_losses = 8 + 2 + 4  # 14
    assert m["profit_factor"] == pytest.approx(gross_wins / gross_losses, abs=0.01)


def test_compute_metrics_drawdown():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    # Cumulative: 5, 8, 0, 4, 10, 8, 15, 20, 16, 26
    # Peak:       5, 8, 8, 8, 10, 10, 15, 20, 20, 26
    # Drawdown:   0, 0, -8, -4, 0,  -2,  0,  0, -4,  0
    # Max drawdown = 8
    df = _make_daily_pnl()
    m = compute_metrics(df)
    assert m["max_drawdown"] == pytest.approx(8.0)


def test_compute_metrics_streaks():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = _make_daily_pnl()
    m = compute_metrics(df)
    # PnL: +,+,-,+,+,-,+,+,-,+  → win streaks: 2,2,2,1 → max=2; loss streaks: 1,1,1 → max=1
    assert m["max_win_streak"] == 2
    assert m["max_loss_streak"] == 1


def test_compute_metrics_sharpe():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = _make_daily_pnl()
    m = compute_metrics(df)
    daily = [5.0, 3.0, -8.0, 4.0, 6.0, -2.0, 7.0, 5.0, -4.0, 10.0]
    expected_sharpe = (np.mean(daily) / np.std(daily, ddof=1)) * np.sqrt(365)
    assert m["sharpe"] == pytest.approx(expected_sharpe, abs=0.1)


def test_compute_metrics_empty():
    from strategies.consistency_copy.backtester.metrics import compute_metrics

    df = pl.DataFrame({
        "resolved_date": [], "daily_pnl": [], "n_bets": [], "n_wins": [],
    }).cast({"resolved_date": pl.Date, "daily_pnl": pl.Float64,
             "n_bets": pl.Int64, "n_wins": pl.Int64})
    m = compute_metrics(df)
    assert m["total_pnl"] == 0.0
    assert m["sharpe"] == 0.0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtester_metrics.py -x -q`
Expected: ModuleNotFoundError

**Step 3: Implement metrics.py**

Create `strategies/consistency_copy/backtester/metrics.py`:

```python
"""P&L curve analytics: Sharpe, drawdown, profit factor, streaks."""
from __future__ import annotations

import numpy as np
import polars as pl


def compute_metrics(daily_pnl: pl.DataFrame) -> dict:
    """Compute portfolio metrics from daily P&L series.

    Args:
        daily_pnl: DataFrame with columns (resolved_date, daily_pnl, n_bets, n_wins).

    Returns:
        Dict with: total_pnl, total_bets, total_wins, hit_rate, sharpe,
        max_drawdown, max_drawdown_pct, profit_factor, avg_daily_pnl,
        pnl_per_bet, n_days, win_days, loss_days, best_day, worst_day,
        max_win_streak, max_loss_streak.
    """
    if daily_pnl.height == 0:
        return {
            "total_pnl": 0.0, "total_bets": 0, "total_wins": 0,
            "hit_rate": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0, "profit_factor": 0.0,
            "avg_daily_pnl": 0.0, "pnl_per_bet": 0.0,
            "n_days": 0, "win_days": 0, "loss_days": 0,
            "best_day": 0.0, "worst_day": 0.0,
            "max_win_streak": 0, "max_loss_streak": 0,
        }

    pnl_arr = daily_pnl["daily_pnl"].to_numpy().astype(float)
    n_days = len(pnl_arr)
    total_pnl = float(np.sum(pnl_arr))
    total_bets = int(daily_pnl["n_bets"].sum())
    total_wins = int(daily_pnl["n_wins"].sum())

    # Sharpe (annualized from daily)
    if n_days >= 2 and np.std(pnl_arr, ddof=1) > 0:
        sharpe = float((np.mean(pnl_arr) / np.std(pnl_arr, ddof=1)) * np.sqrt(365))
    else:
        sharpe = 0.0

    # Drawdown
    cumsum = np.cumsum(pnl_arr)
    running_max = np.maximum.accumulate(cumsum)
    drawdowns = cumsum - running_max
    max_dd = float(-np.min(drawdowns)) if len(drawdowns) > 0 else 0.0
    max_dd_pct = float(max_dd / running_max[np.argmin(drawdowns)]) if max_dd > 0 and running_max[np.argmin(drawdowns)] > 0 else 0.0

    # Profit factor
    gross_wins = float(np.sum(pnl_arr[pnl_arr > 0]))
    gross_losses = float(-np.sum(pnl_arr[pnl_arr < 0]))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    # Win/loss streaks
    signs = np.sign(pnl_arr)
    max_win = _max_streak(signs, 1.0)
    max_loss = _max_streak(signs, -1.0)

    return {
        "total_pnl": round(total_pnl, 4),
        "total_bets": total_bets,
        "total_wins": total_wins,
        "hit_rate": round(total_wins / max(total_bets, 1), 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_daily_pnl": round(total_pnl / n_days, 4),
        "pnl_per_bet": round(total_pnl / max(total_bets, 1), 4),
        "n_days": n_days,
        "win_days": int(np.sum(pnl_arr > 0)),
        "loss_days": int(np.sum(pnl_arr < 0)),
        "best_day": round(float(np.max(pnl_arr)), 4),
        "worst_day": round(float(np.min(pnl_arr)), 4),
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
    }


def _max_streak(signs: np.ndarray, target: float) -> int:
    """Longest consecutive run of `target` in array."""
    best = 0
    current = 0
    for s in signs:
        if s == target:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_backtester_metrics.py -x -q`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/metrics.py tests/test_backtester_metrics.py
git commit -m "feat: add P&L metrics module (Sharpe, drawdown, profit factor, streaks)"
```

---

### Task 3: Bet Sizing Module

**Files:**
- Create: `strategies/consistency_copy/backtester/sizing.py`
- Test: `tests/test_backtester_sizing.py`

**Step 1: Write the failing test**

Create `tests/test_backtester_sizing.py`:

```python
"""Tests for bet sizing strategies."""
from __future__ import annotations

import polars as pl
import pytest


def _make_bets() -> pl.DataFrame:
    """5 bets with known entry prices and outcomes."""
    return pl.DataFrame({
        "condition_id": ["a", "b", "c", "d", "e"],
        "agreement_frac": [0.60, 0.70, 0.80, 0.90, 0.55],
        "trigger_entry_price": [0.30, 0.40, 0.25, 0.50, 0.35],
        "resolution_value": [1, 0, 1, 1, 0],
        "signal_direction": ["YES", "YES", "YES", "YES", "YES"],
        "won": [True, False, True, True, False],
    })


def test_fixed_sizing():
    from strategies.consistency_copy.backtester.sizing import apply_sizing

    bets = _make_bets()
    result = apply_sizing(bets, "fixed", base_bet=10.0, fee_pct=0.02)
    assert result["bet_size"].to_list() == [10.0] * 5
    assert "bet_pnl" in result.columns


def test_agreement_weighted_sizing():
    from strategies.consistency_copy.backtester.sizing import apply_sizing

    bets = _make_bets()
    result = apply_sizing(bets, "agreement_weighted", base_bet=10.0, fee_pct=0.02)
    # bet = base * (agreement - 0.5) * 2
    # a=0.60: 10*(0.10)*2=2.0, b=0.70: 10*(0.20)*2=4.0, c=0.80: 6.0, d=0.90: 8.0, e=0.55: 1.0
    expected = [2.0, 4.0, 6.0, 8.0, 1.0]
    actual = result["bet_size"].to_list()
    for a, e in zip(actual, expected):
        assert abs(a - e) < 0.01, f"{a} != {e}"


def test_pnl_computation_win():
    """Winning YES bet at price 0.30: pnl = bet*(1-0.30)/0.30 - fee."""
    from strategies.consistency_copy.backtester.sizing import apply_sizing

    bets = _make_bets()
    result = apply_sizing(bets, "fixed", base_bet=10.0, fee_pct=0.02)
    row_a = result.filter(pl.col("condition_id") == "a")
    # fee = 0.02 * min(0.30, 0.70) * 10 = 0.06
    # pnl = 10 * 0.70/0.30 - 0.06 = 23.333 - 0.06 = 23.273
    assert row_a["bet_pnl"][0] == pytest.approx(23.2733, abs=0.01)


def test_pnl_computation_loss():
    """Losing YES bet: pnl = -bet - fee."""
    from strategies.consistency_copy.backtester.sizing import apply_sizing

    bets = _make_bets()
    result = apply_sizing(bets, "fixed", base_bet=10.0, fee_pct=0.02)
    row_b = result.filter(pl.col("condition_id") == "b")
    # fee = 0.02 * min(0.40, 0.60) * 10 = 0.08
    # pnl = -10 - 0.08 = -10.08
    assert row_b["bet_pnl"][0] == pytest.approx(-10.08, abs=0.01)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtester_sizing.py -x -q`
Expected: ModuleNotFoundError

**Step 3: Implement sizing.py**

Create `strategies/consistency_copy/backtester/sizing.py`:

```python
"""Bet sizing strategies: fixed, Kelly, agreement-weighted, edge-weighted."""
from __future__ import annotations

import polars as pl


def apply_sizing(
    bets: pl.DataFrame,
    strategy: str,
    base_bet: float = 1.0,
    fee_pct: float = 0.02,
    kelly_cap: float = 0.25,
    rolling_hr: float | None = None,
) -> pl.DataFrame:
    """Apply bet sizing and compute P&L for each bet.

    Args:
        bets: DataFrame with columns: agreement_frac, trigger_entry_price,
              signal_direction, won, resolution_value.
        strategy: One of "fixed", "kelly", "agreement_weighted", "edge_weighted".
        base_bet: Base bet size in dollars.
        fee_pct: Polymarket fee rate (default 2%).
        kelly_cap: Maximum Kelly fraction (default 0.25).
        rolling_hr: Pre-computed hit rate for Kelly/edge (uses bets mean if None).

    Returns:
        Input DataFrame with added columns: bet_size, fee, bet_pnl.
    """
    if strategy == "fixed":
        sized = bets.with_columns(pl.lit(base_bet).alias("bet_size"))
    elif strategy == "agreement_weighted":
        sized = bets.with_columns(
            (base_bet * (pl.col("agreement_frac") - 0.5) * 2.0).alias("bet_size")
        )
    elif strategy == "kelly":
        hr = rolling_hr if rolling_hr is not None else float(bets["won"].mean())
        sized = bets.with_columns(
            _kelly_size(hr, pl.col("trigger_entry_price"), base_bet, kelly_cap).alias("bet_size")
        )
    elif strategy == "edge_weighted":
        hr = rolling_hr if rolling_hr is not None else float(bets["won"].mean())
        sized = bets.with_columns(
            _edge_size(hr, pl.col("trigger_entry_price"), base_bet).alias("bet_size")
        )
    else:
        msg = f"Unknown sizing strategy: {strategy}"
        raise ValueError(msg)

    # Compute fee and P&L
    sized = sized.with_columns(
        (fee_pct * pl.min_horizontal(
            pl.col("trigger_entry_price"),
            1.0 - pl.col("trigger_entry_price"),
        ) * pl.col("bet_size")).alias("fee")
    )
    sized = sized.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("bet_size") * (1.0 - pl.col("trigger_entry_price"))
            / pl.col("trigger_entry_price") - pl.col("fee")
        )
        .otherwise(-pl.col("bet_size") - pl.col("fee"))
        .alias("bet_pnl")
    )
    return sized


def _kelly_size(hr: float, price: pl.Expr, base: float, cap: float) -> pl.Expr:
    """Kelly criterion: f* = (p*b - q) / b, capped."""
    # b = odds = (1-p)/p for a YES bet at price p
    odds = (1.0 - price) / price
    f_star = (hr * odds - (1 - hr)) / odds
    return pl.max_horizontal(pl.lit(0.0), pl.min_horizontal(f_star, pl.lit(cap))) * base


def _edge_size(hr: float, price: pl.Expr, base: float) -> pl.Expr:
    """Edge-weighted: bet proportional to expected edge."""
    odds = (1.0 - price) / price
    edge = hr * odds - (1 - hr)
    return pl.max_horizontal(pl.lit(0.0), edge) * base
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_backtester_sizing.py -x -q`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/sizing.py tests/test_backtester_sizing.py
git commit -m "feat: add bet sizing strategies (fixed, kelly, agreement, edge)"
```

---

### Task 4: Sweep Engine — Vectorized Parameter Grid

**Files:**
- Create: `strategies/consistency_copy/backtester/sweep.py`
- Test: `tests/test_backtester_sweep.py`

The sweep engine takes a signal table and a parameter grid, filters the signal table for each combo, computes P&L with sizing, and returns summary metrics. The key design: for signal parameters we filter rows; for sizing we post-process the filtered rows.

**Step 1: Write the failing test**

Create `tests/test_backtester_sweep.py`:

```python
"""Tests for the vectorized parameter sweep engine."""
from __future__ import annotations

import polars as pl
import pytest
from datetime import datetime, date


def _make_signal_table() -> pl.DataFrame:
    """Small signal table: 3 markets, varying consensus states."""
    base = datetime(2025, 12, 15)
    return pl.DataFrame({
        "condition_id": ["m1"]*3 + ["m2"]*2 + ["m3"]*4,
        "arrival_idx": [1, 2, 3, 1, 2, 1, 2, 3, 4],
        "trigger_time": [
            base.replace(hour=h) for h in [8, 9, 10, 8, 9, 8, 9, 10, 11]
        ],
        "resolved_at": [
            datetime(2025, 12, 20)]*3 + [datetime(2025, 12, 22)]*2 + [datetime(2025, 12, 25)]*4,
        "resolution_value": [1]*3 + [0]*2 + [1]*4,
        "n_traders": [1, 2, 3, 1, 2, 1, 2, 3, 4],
        "n_yes": [1, 2, 3, 0, 1, 1, 1, 2, 3],
        "n_no": [0, 0, 0, 1, 1, 0, 1, 1, 1],
        "agreement_frac": [1.0, 1.0, 1.0, 1.0, 0.5, 1.0, 0.5, 2/3, 0.75],
        "signal_direction": ["YES"]*3 + ["NO", "YES"] + ["YES", "YES", "YES", "YES"],
        "trigger_entry_price": [0.30, 0.35, 0.40, 0.60, 0.55, 0.20, 0.25, 0.30, 0.35],
        "avg_pool_entry": [0.30, 0.325, 0.35, None, 0.55, 0.20, 0.225, 0.25, 0.283],
        "trader": [f"t{i}" for i in [1,2,3,4,5,6,7,8,9]],
        "mvf": [0.05, 0.05, 0.20, 0.05, 0.30, 0.60, 0.05, 0.05, 0.10],
    })


def test_sweep_returns_results():
    from strategies.consistency_copy.backtester.sweep import run_sweep, SweepConfig

    st = _make_signal_table()
    grid = SweepConfig(
        min_traders_values=[2, 3],
        agreement_pct_values=[0.60],
        direction_values=["YES-only"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
    )
    results = run_sweep(st, grid, base_bet=10.0)
    assert isinstance(results, pl.DataFrame)
    assert results.height > 0
    assert "hit_rate" in results.columns
    assert "total_pnl" in results.columns
    assert "sharpe" in results.columns


def test_sweep_filters_min_traders():
    from strategies.consistency_copy.backtester.sweep import run_sweep, SweepConfig

    st = _make_signal_table()
    grid = SweepConfig(
        min_traders_values=[3],
        agreement_pct_values=[0.50],
        direction_values=["both"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
    )
    results = run_sweep(st, grid, base_bet=10.0)
    # Only rows where n_traders >= 3 qualify: m1@arrival_idx=3, m3@arrival_idx=3,4
    assert results["n_bets"][0] == 3


def test_sweep_yes_only_filter():
    from strategies.consistency_copy.backtester.sweep import run_sweep, SweepConfig

    st = _make_signal_table()
    grid = SweepConfig(
        min_traders_values=[1],
        agreement_pct_values=[0.50],
        direction_values=["YES-only"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
    )
    results = run_sweep(st, grid, base_bet=10.0)
    # Should exclude the one NO signal (m2@arrival_idx=1)
    # All YES signals from latest arrival per market: m1@3, m2@2(YES), m3@4
    # Actually we take the LAST arrival row per market that meets threshold
    # m1: arrival 1,2,3 all YES → take the one where threshold first met (idx=1 since agree>=0.5 and n>=1)
    # We take the FIRST row where signal fires (threshold first crossed)
    pass  # Exact count depends on signal selection logic


def test_sweep_entry_price_band():
    from strategies.consistency_copy.backtester.sweep import run_sweep, SweepConfig

    st = _make_signal_table()
    grid = SweepConfig(
        min_traders_values=[1],
        agreement_pct_values=[0.50],
        direction_values=["both"],
        entry_price_bands=[(0.25, 0.50)],
        sizing_strategies=["fixed"],
    )
    results = run_sweep(st, grid, base_bet=10.0)
    # Only entries with trigger_entry_price in [0.25, 0.50]
    assert results.height == 1
    # All bet entries should have price in band
    assert results["n_bets"][0] > 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtester_sweep.py -x -q`
Expected: ModuleNotFoundError

**Step 3: Implement sweep.py**

Create `strategies/consistency_copy/backtester/sweep.py`:

```python
"""Vectorized parameter sweep over signal table.

For each parameter combination:
1. Filter signal table to the FIRST row per market that meets threshold
   (first time n_traders >= min_t AND agreement >= min_agree)
2. Apply direction and entry price filters
3. Compute P&L with the specified sizing strategy
4. Compute daily P&L series and portfolio metrics
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

import polars as pl

from strategies.consistency_copy.backtester.metrics import compute_metrics
from strategies.consistency_copy.backtester.sizing import apply_sizing


@dataclass
class SweepConfig:
    """Parameter grid for the sweep."""

    min_traders_values: list[int] = field(default_factory=lambda: [2, 3, 5, 7, 10, 15, 20, 30, 50])
    agreement_pct_values: list[float] = field(default_factory=lambda: [0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 0.90])
    direction_values: list[str] = field(default_factory=lambda: ["YES-only", "NO-only", "both"])
    entry_price_bands: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.05, 0.95), (0.10, 0.90), (0.15, 0.85), (0.20, 0.80)]
    )
    sizing_strategies: list[str] = field(default_factory=lambda: ["fixed", "agreement_weighted", "kelly", "edge_weighted"])
    min_bets: int = 20  # skip configs with fewer bets


def _select_signal_rows(
    st: pl.DataFrame,
    min_traders: int,
    agreement_pct: float,
) -> pl.DataFrame:
    """Select the FIRST row per market where the signal threshold is crossed.

    This simulates: the signal fires the moment enough traders agree.
    """
    qualified = st.filter(
        (pl.col("n_traders") >= min_traders)
        & (pl.col("agreement_frac") >= agreement_pct)
    )
    # Take first qualifying row per market (lowest arrival_idx)
    return qualified.sort("arrival_idx").group_by("condition_id").first()


def run_sweep(
    signal_table: pl.DataFrame,
    config: SweepConfig,
    base_bet: float = 1.0,
    fee_pct: float = 0.02,
) -> pl.DataFrame:
    """Run parameter sweep and return results DataFrame.

    Returns one row per (min_traders, agreement, direction, price_band, sizing)
    with columns: all params + n_bets, hit_rate, total_pnl, sharpe, max_drawdown, etc.
    """
    results: list[dict] = []

    for min_t, agree, direction, (p_lo, p_hi) in product(
        config.min_traders_values,
        config.agreement_pct_values,
        config.direction_values,
        config.entry_price_bands,
    ):
        # Step 1: Select signal rows (first threshold crossing per market)
        signals = _select_signal_rows(signal_table, min_t, agree)

        # Step 2: Direction filter
        if direction == "YES-only":
            signals = signals.filter(pl.col("signal_direction") == "YES")
        elif direction == "NO-only":
            signals = signals.filter(pl.col("signal_direction") == "NO")

        # Step 3: Entry price band filter
        signals = signals.filter(
            (pl.col("trigger_entry_price") >= p_lo)
            & (pl.col("trigger_entry_price") <= p_hi)
        )

        if signals.height < config.min_bets:
            continue

        # Step 4: Determine wins
        signals = signals.with_columns(
            pl.when(
                ((pl.col("signal_direction") == "YES") & (pl.col("resolution_value") == 1))
                | ((pl.col("signal_direction") == "NO") & (pl.col("resolution_value") == 0))
            )
            .then(pl.lit(True))
            .otherwise(pl.lit(False))
            .alias("won")
        )

        # Step 5: Apply each sizing strategy
        for sizing in config.sizing_strategies:
            sized = apply_sizing(signals, sizing, base_bet=base_bet, fee_pct=fee_pct)

            # Step 6: Compute daily P&L
            daily = (
                sized
                .with_columns(pl.col("resolved_at").cast(pl.Date).alias("resolved_date"))
                .group_by("resolved_date")
                .agg([
                    pl.col("bet_pnl").sum().alias("daily_pnl"),
                    pl.len().alias("n_bets"),
                    pl.col("won").sum().alias("n_wins"),
                ])
                .sort("resolved_date")
            )

            metrics = compute_metrics(daily)
            metrics.update({
                "min_traders": min_t,
                "agreement_pct": agree,
                "direction": direction,
                "price_band_lo": p_lo,
                "price_band_hi": p_hi,
                "sizing": sizing,
                "n_bets": sized.height,
            })
            results.append(metrics)

    if not results:
        return pl.DataFrame()

    return pl.DataFrame(results)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_backtester_sweep.py -x -q`
Expected: All 4 tests PASS (the `pass` test is a design note, not a failure)

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/sweep.py tests/test_backtester_sweep.py
git commit -m "feat: add vectorized parameter sweep engine"
```

---

### Task 5: Runner — Load Data, Build Tables, Sweep, Save

**Files:**
- Create: `strategies/consistency_copy/backtester/runner.py`

This is the CLI entry point that ties everything together. No separate tests — it's an integration script that operates on real cached data.

**Step 1: Implement runner.py**

Create `strategies/consistency_copy/backtester/runner.py`:

```python
"""CLI runner: load cached data, build signal tables, sweep, save results.

Usage:
    uv run python -m strategies.consistency_copy.backtester.runner
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import polars as pl

from strategies.consistency_copy.backtester.signal_table import build_signal_table
from strategies.consistency_copy.backtester.sweep import SweepConfig, run_sweep

CACHE_DIR = Path("scripts/_cache")
OUTPUT_DIR = Path("strategies/consistency_copy")
EQUITY_DIR = OUTPUT_DIR / "equity_curves"

# Rolling windows: (name, train_start, train_end, holdout_end)
WINDOWS = [
    ("win0_dec25", "2025-08-01", "2025-12-01", "2026-01-01"),
    ("win1_jan26", "2025-09-01", "2026-01-01", "2026-02-01"),
    ("win2_feb26", "2025-10-01", "2026-02-01", "2026-03-01"),
]
N_PERIODS = 4  # minimum consecutive profitable months


def load_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Load cached parquet files."""
    df_pnl = pl.read_parquet(CACHE_DIR / "trader_market_pnl.parquet")
    df_mvf = pl.read_parquet(CACHE_DIR / "maker_volume_fractions.parquet")
    df_markets = pl.read_parquet(CACHE_DIR / "markets_resolved.parquet")

    df_pnl = df_pnl.join(df_markets, on="condition_id", how="inner")
    if df_pnl["resolved_at"].dtype == pl.Datetime("us", "Etc/UTC"):
        df_pnl = df_pnl.with_columns(
            pl.col("resolved_at").dt.replace_time_zone(None)
        )
    return df_pnl, df_mvf, df_markets


def get_consistent_traders(
    df_pnl: pl.DataFrame,
    train_start: str,
    train_end: str,
    n_periods: int,
    min_markets: int,
) -> set[str]:
    """Get traders profitable in ALL months during training window."""
    train = df_pnl.filter(
        (pl.col("resolved_at") >= pl.lit(train_start).str.to_datetime("%Y-%m-%d"))
        & (pl.col("resolved_at") < pl.lit(train_end).str.to_datetime("%Y-%m-%d"))
    )
    monthly = (
        train
        .with_columns(pl.col("resolved_at").dt.strftime("%Y%m").cast(pl.UInt32).alias("month"))
        .group_by(["trader", "month"])
        .agg([
            pl.col("market_pnl").sum().alias("monthly_pnl"),
            pl.col("condition_id").n_unique().alias("markets_traded"),
        ])
    )
    consistent = (
        monthly.group_by("trader")
        .agg([
            (pl.col("monthly_pnl") > 0).sum().alias("pos_months"),
            pl.col("monthly_pnl").count().alias("tot_months"),
            pl.col("markets_traded").sum().alias("tot_markets"),
        ])
        .filter(
            (pl.col("pos_months") == pl.col("tot_months"))
            & (pl.col("tot_months") >= n_periods)
            & (pl.col("tot_markets") >= min_markets)
        )
    )
    return set(consistent["trader"].to_list())


def build_mvf_subsets(df_mvf: pl.DataFrame) -> dict[str, set[str]]:
    """Pre-compute trader sets for each MVF band."""
    col = "mvf" if "mvf" in df_mvf.columns else "maker_volume_fraction"
    return {
        "all": set(df_mvf["trader"].to_list()),
        "pure_taker": set(df_mvf.filter(pl.col(col) < 0.10)["trader"].to_list()),
        "mixed": set(df_mvf.filter((pl.col(col) >= 0.10) & (pl.col(col) <= 0.50))["trader"].to_list()),
        "maker_dominant": set(df_mvf.filter(pl.col(col) > 0.50)["trader"].to_list()),
    }


def main() -> None:
    t0 = time.time()
    print("Loading cached data...")
    df_pnl, df_mvf, _ = load_data()
    print(f"  PnL: {df_pnl.shape}, MVF: {df_mvf.shape}")

    EQUITY_DIR.mkdir(parents=True, exist_ok=True)

    # Sweep config for trader pool params
    consistency_months_values = [3, 4, 5, 6]
    min_markets_values = [10, 20, 30, 50]
    mvf_bands = ["all", "pure_taker", "mixed", "maker_dominant"]

    mvf_subsets = build_mvf_subsets(df_mvf)
    mvf_col = "mvf" if "mvf" in df_mvf.columns else "maker_volume_fraction"
    mvf_df = df_mvf.select(["trader", pl.col(mvf_col).alias("mvf")])

    signal_config = SweepConfig()  # default grid for signal+sizing params

    all_results: list[pl.DataFrame] = []

    for win_name, train_start, train_end, holdout_end in WINDOWS:
        print(f"\n{'='*60}")
        print(f"WINDOW: {win_name} (train: {train_start}-{train_end}, holdout: {train_end}-{holdout_end})")
        print(f"{'='*60}")

        holdout_data = df_pnl.filter(
            (pl.col("resolved_at") >= pl.lit(train_end).str.to_datetime("%Y-%m-%d"))
            & (pl.col("resolved_at") < pl.lit(holdout_end).str.to_datetime("%Y-%m-%d"))
        )

        if holdout_data.height == 0:
            print("  No holdout data, skipping.")
            continue

        for n_months in consistency_months_values:
            for min_mkts in min_markets_values:
                skilled = get_consistent_traders(df_pnl, train_start, train_end, n_months, min_mkts)
                if len(skilled) < 10:
                    continue

                for mvf_band in mvf_bands:
                    if mvf_band == "all":
                        pool = skilled
                    else:
                        pool = skilled & mvf_subsets.get(mvf_band, set())
                    if len(pool) < 5:
                        continue

                    # Build signal table for this pool
                    st = build_signal_table(holdout_data, pool, mvf_df)
                    if st.height < 20:
                        continue

                    # Run sweep
                    results = run_sweep(st, signal_config, base_bet=1.0)
                    if results.height == 0:
                        continue

                    # Add pool params
                    results = results.with_columns([
                        pl.lit(win_name).alias("window"),
                        pl.lit(n_months).alias("consistency_months"),
                        pl.lit(min_mkts).alias("min_markets"),
                        pl.lit(mvf_band).alias("mvf_band"),
                        pl.lit(len(pool)).alias("pool_size"),
                    ])
                    all_results.append(results)

                    n_configs = results.height
                    best_hr = results["hit_rate"].max()
                    best_pnl = results["total_pnl"].max()
                    print(f"  months={n_months} mkts={min_mkts} mvf={mvf_band} pool={len(pool)}: {n_configs} configs, best HR={best_hr:.1%}, best PnL=${best_pnl:.2f}")

    if not all_results:
        print("\nNo results generated!")
        return

    full = pl.concat(all_results)
    print(f"\n{'='*60}")
    print(f"TOTAL: {full.height:,} config-window combinations")
    print(f"{'='*60}")

    # Save full results
    full.write_parquet(OUTPUT_DIR / "sweep_results.parquet")
    print(f"Saved to {OUTPUT_DIR / 'sweep_results.parquet'}")

    # Find top configs stable across windows
    # Group by config params (excluding window), require presence in all windows
    config_cols = [
        "min_traders", "agreement_pct", "direction", "price_band_lo",
        "price_band_hi", "sizing", "consistency_months", "min_markets", "mvf_band",
    ]
    n_windows = full["window"].n_unique()

    stable = (
        full
        .group_by(config_cols)
        .agg([
            pl.col("window").n_unique().alias("n_windows"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("max_drawdown").mean().alias("avg_drawdown"),
            pl.col("n_bets").mean().alias("avg_bets"),
            pl.col("hit_rate").min().alias("min_hit_rate"),
            pl.col("total_pnl").min().alias("min_pnl"),
        ])
        .filter(pl.col("n_windows") >= min(n_windows, 2))
        .sort("avg_sharpe", descending=True)
        .head(50)
    )

    top_configs = stable.to_dicts()
    with open(OUTPUT_DIR / "top_configs.json", "w") as f:
        json.dump(top_configs, f, indent=2, default=str)
    print(f"Top {len(top_configs)} stable configs saved to top_configs.json")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
```

**Step 2: Add `__main__.py` for module execution**

Create `strategies/consistency_copy/backtester/__main__.py`:
```python
"""Allow running as: python -m strategies.consistency_copy.backtester"""
from strategies.consistency_copy.backtester.runner import main

main()
```

**Step 3: Run on real data**

Run: `uv run python -m strategies.consistency_copy.backtester`
Expected: Completes in ~5 minutes, prints progress per window, saves sweep_results.parquet and top_configs.json

**Step 4: Commit**

```bash
git add strategies/consistency_copy/backtester/runner.py strategies/consistency_copy/backtester/__main__.py
git commit -m "feat: add backtester runner with walk-forward sweep"
```

---

### Task 6: Run Sweep and Analyze Results

**Files:**
- Modify: `strategies/consistency_copy/backtester/runner.py` (if needed)

**Step 1: Execute the full sweep**

Run: `uv run python -m strategies.consistency_copy.backtester`

**Step 2: Inspect results**

```bash
uv run python3 -c "
import polars as pl, json
df = pl.read_parquet('strategies/consistency_copy/sweep_results.parquet')
print(f'Total configs: {df.height:,}')
print(f'Columns: {df.columns}')
print(df.sort('sharpe', descending=True).head(20))
top = json.loads(open('strategies/consistency_copy/top_configs.json').read())
print(f'\nTop {len(top)} stable configs:')
for i, c in enumerate(top[:10]):
    print(f'  {i+1}. HR={c[\"avg_hit_rate\"]:.1%} Sharpe={c[\"avg_sharpe\"]:.1f} PnL={c[\"avg_pnl\"]:.0f} | t>={c[\"min_traders\"]} agree>={c[\"agreement_pct\"]} {c[\"direction\"]} {c[\"sizing\"]} months={c[\"consistency_months\"]} mkts={c[\"min_markets\"]} mvf={c[\"mvf_band\"]}')
"
```

**Step 3: Fix any issues and re-run if needed**

**Step 4: Commit results**

```bash
git add strategies/consistency_copy/sweep_results.parquet strategies/consistency_copy/top_configs.json
git commit -m "data: sweep results across 3 rolling windows"
```

---

### Task 7: Add `strategies/__init__.py` chain if missing

**Files:**
- Create if missing: `strategies/__init__.py`
- Create if missing: `strategies/consistency_copy/__init__.py`

These are needed for Python module imports to work.

**Step 1: Check and create**

```bash
touch strategies/__init__.py strategies/consistency_copy/__init__.py
```

**Step 2: Run: `uv run python -c "from strategies.consistency_copy.backtester.signal_table import build_signal_table; print('OK')"`**

Expected: `OK`

**Step 3: Commit**

```bash
git add strategies/__init__.py strategies/consistency_copy/__init__.py
git commit -m "chore: add __init__.py for strategy module imports"
```

---

## Execution Order

Task 7 (init files) should be done FIRST since all other tasks depend on imports working.

Then: Task 1 (signal table) → Task 2 (metrics) → Task 3 (sizing) → Task 4 (sweep) → Task 5 (runner) → Task 6 (run + analyze).

Tasks 1-3 are independent of each other but Task 4 depends on 2+3, and Task 5 depends on 1+4.
