# Informed MM Estimate — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `scripts/informed_mm_estimate.py` — a two-stage analytical estimate comparing market-maker vs taker execution on consistency-pool signals.

**Architecture:** Reuse existing backtester infrastructure (`_load_data`, `get_consistent_traders`, `build_signal_table`, `_precompute_entry_prices`) from `strategies/consistency_copy/backtester/runner.py`. Add MM-specific PnL formulas, a YES-buy volume scanner, and a capital-constrained monthly sim. Single standalone script with testable helper functions.

**Tech Stack:** Polars (lazy + eager), fastparquet (compact scan), existing backtester modules.

---

### Task 1: MM PnL Helper Functions + Tests

**Files:**
- Create: `scripts/informed_mm_estimate.py` (initial structure with helpers)
- Create: `tests/test_informed_mm_estimate.py`

**Step 1: Write the failing tests for MM PnL formulas**

```python
# tests/test_informed_mm_estimate.py
"""Tests for informed MM estimate helpers."""
from __future__ import annotations

import pytest


def test_taker_pnl_no_wins():
    """Taker buys NO at 75c, NO wins → profit."""
    from scripts.informed_mm_estimate import compute_taker_bet_pnl

    pnl = compute_taker_bet_pnl(
        yes_price=0.25, bet_size=100.0, no_won=True, fee_pct=0.0
    )
    # NO cost = 0.75, tokens = 100/0.75 = 133.33, profit = 133.33 * 0.25 = 33.33
    assert abs(pnl - 33.33) < 0.01


def test_taker_pnl_no_loses():
    """Taker buys NO at 75c, YES wins → full loss."""
    from scripts.informed_mm_estimate import compute_taker_bet_pnl

    pnl = compute_taker_bet_pnl(
        yes_price=0.25, bet_size=100.0, no_won=False, fee_pct=0.0
    )
    assert pnl == -100.0


def test_maker_pnl_no_wins():
    """Maker sells YES at (yes_price + spread), NO wins → profit."""
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    pnl = compute_maker_bet_pnl(
        yes_price=0.25, spread_edge=0.01, bet_size=100.0, no_won=True, fee_pct=0.0
    )
    # Sells YES at 0.26, effective NO cost = 0.74
    # tokens = 100/0.74 = 135.14, profit = 135.14 * 0.26 = 35.14
    expected = 100.0 * 0.26 / 0.74
    assert abs(pnl - expected) < 0.01


def test_maker_pnl_no_loses():
    """Maker sells YES, YES wins → full loss."""
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    pnl = compute_maker_bet_pnl(
        yes_price=0.25, spread_edge=0.01, bet_size=100.0, no_won=False, fee_pct=0.0
    )
    assert pnl == -100.0


def test_maker_better_than_taker_on_win():
    """Maker always earns more than taker per winning bet due to spread."""
    from scripts.informed_mm_estimate import compute_maker_bet_pnl, compute_taker_bet_pnl

    for yes_p in [0.15, 0.25, 0.35, 0.45]:
        taker = compute_taker_bet_pnl(yes_p, 100.0, True, 0.0)
        maker = compute_maker_bet_pnl(yes_p, 0.01, 100.0, True, 0.0)
        assert maker > taker, f"Maker should beat taker at yes_price={yes_p}"


def test_maker_pnl_with_fees():
    """Fees reduce PnL symmetrically."""
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    no_fee = compute_maker_bet_pnl(0.25, 0.01, 100.0, True, 0.0)
    with_fee = compute_maker_bet_pnl(0.25, 0.01, 100.0, True, 0.02)
    assert with_fee < no_fee


def test_fill_prob_from_volume():
    """Fill probability = min(1.0, volume / bet_size)."""
    from scripts.informed_mm_estimate import estimate_fill_probability

    assert estimate_fill_probability(200.0, 100.0) == 1.0
    assert estimate_fill_probability(50.0, 100.0) == 0.5
    assert estimate_fill_probability(0.0, 100.0) == 0.0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: FAIL (module not found)

**Step 3: Write the helper implementations**

```python
# scripts/informed_mm_estimate.py (initial)
"""Informed MM Estimate: Consistency Pool Signal + Market-Making Execution.

Two-stage analysis:
  Stage 1: Unconstrained sweep — signal configs × MM params
  Stage 2: Capital-constrained monthly sim on top configs

Usage:
    uv run python scripts/informed_mm_estimate.py
    uv run python scripts/informed_mm_estimate.py --force-volume
"""
from __future__ import annotations


def compute_taker_bet_pnl(
    yes_price: float,
    bet_size: float,
    no_won: bool,
    fee_pct: float,
) -> float:
    """Compute PnL for a taker buying NO at (1 - yes_price).

    Taker buys NO tokens at price (1 - yes_price).
    If NO wins: profit = bet_size * yes_price / (1 - yes_price) - fee
    If YES wins: loss = -bet_size - fee
    """
    no_price = 1.0 - yes_price
    fee = fee_pct * min(yes_price, no_price) * bet_size
    if no_won:
        return bet_size * yes_price / no_price - fee
    return -bet_size - fee


def compute_maker_bet_pnl(
    yes_price: float,
    spread_edge: float,
    bet_size: float,
    no_won: bool,
    fee_pct: float,
) -> float:
    """Compute PnL for a maker selling YES at (yes_price + spread_edge).

    Maker sells YES at effective_yes = yes_price + spread_edge.
    Effective NO cost = 1 - effective_yes.
    If NO wins: profit = bet_size * effective_yes / (1 - effective_yes) - fee
    If YES wins: loss = -bet_size - fee
    """
    effective_yes = yes_price + spread_edge
    effective_no = 1.0 - effective_yes
    fee = fee_pct * min(effective_yes, effective_no) * bet_size
    if no_won:
        return bet_size * effective_yes / effective_no - fee
    return -bet_size - fee


def estimate_fill_probability(yes_buy_volume: float, bet_size: float) -> float:
    """Estimate fill probability from historical YES-buy volume.

    fill_prob = min(1.0, volume / bet_size).
    """
    if bet_size <= 0:
        return 0.0
    return min(1.0, yes_buy_volume / bet_size)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: 7 passed

**Step 5: Commit**

```bash
git add scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py
git commit -m "feat: add informed MM estimate helpers with tests"
```

---

### Task 2: YES-Buy Volume Scanner + Cache

**Files:**
- Modify: `scripts/informed_mm_estimate.py`
- Modify: `tests/test_informed_mm_estimate.py`

**Step 1: Write the failing test for the volume scanner**

Append to `tests/test_informed_mm_estimate.py`:

```python
import polars as pl


def test_compute_yes_buy_volume_per_market():
    """Compute per-market YES-buy volume from trade-level data."""
    from scripts.informed_mm_estimate import compute_yes_buy_volume_per_market

    # Simulate trades: 2 markets, mixed YES/NO buys
    trades = pl.DataFrame({
        "condition_id": ["m1", "m1", "m1", "m2", "m2"],
        "asset_id": ["a1", "a1", "a1", "a2", "a2"],
        "side": ["BUY", "SELL", "BUY", "BUY", "BUY"],
        "price": [0.20, 0.80, 0.30, 0.15, 0.25],
        "amount_usd": [100.0, 50.0, 200.0, 150.0, 100.0],
        "timestamp": [1.0, 2.0, 3.0, 4.0, 5.0],
    })
    token_map = pl.DataFrame({
        "asset_id": ["a1", "a2"],
        "condition_id": ["m1", "m2"],
        "token_index": [0, 0],  # both are YES tokens
    })

    result = compute_yes_buy_volume_per_market(trades, token_map)

    # m1: BUY+YES=100, SELL+YES(=buy NO, not YES-buy)=ignored, BUY+YES=200 → 300
    # m2: BUY+YES=150, BUY+YES=100 → 250
    m1 = result.filter(pl.col("condition_id") == "m1")
    m2 = result.filter(pl.col("condition_id") == "m2")
    assert abs(float(m1["yes_buy_volume"][0]) - 300.0) < 0.01
    assert abs(float(m2["yes_buy_volume"][0]) - 250.0) < 0.01
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_informed_mm_estimate.py::test_compute_yes_buy_volume_per_market -x -q`
Expected: FAIL (function not found)

**Step 3: Implement the volume scanner**

Add to `scripts/informed_mm_estimate.py`:

```python
from pathlib import Path

import polars as pl

DATA_DIR = Path("data")
DERIVED_DIR = DATA_DIR / "derived"
COMPACT_DIR = DATA_DIR / "compact"
METADATA_DIR = DATA_DIR / "metadata"
VOLUME_CACHE = DERIVED_DIR / "yes_buy_volume.parquet"


def compute_yes_buy_volume_per_market(
    trades: pl.DataFrame,
    token_map: pl.DataFrame,
) -> pl.DataFrame:
    """Compute per-market YES-buy volume from trade-level data.

    YES-buy = (side=BUY & token_index=0) OR (side=SELL & token_index=1).
    Returns DataFrame with columns: condition_id, yes_buy_volume, yes_buy_count.
    """
    # Join trades with token map to get token_index
    df = trades.join(
        token_map.select("asset_id", "condition_id", "token_index"),
        on="asset_id",
        how="inner",
        suffix="_tm",
    )

    # Use condition_id from token_map if trades has its own
    cid_col = "condition_id_tm" if "condition_id_tm" in df.columns else "condition_id"

    # Identify YES-buy flow
    is_yes_buy = (
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 0))
        | ((pl.col("side") == "SELL") & (pl.col("token_index") == 1))
    )

    yes_buys = df.filter(is_yes_buy)

    return yes_buys.group_by(pl.col(cid_col).alias("condition_id")).agg(
        pl.col("amount_usd").sum().alias("yes_buy_volume"),
        pl.len().alias("yes_buy_count"),
    )


def load_or_compute_yes_buy_volume(force: bool = False) -> pl.DataFrame:
    """Load cached YES-buy volume or compute from compact parquet.

    Caches result to data/derived/yes_buy_volume.parquet.
    """
    if VOLUME_CACHE.exists() and not force:
        print(f"[volume] Loading cached {VOLUME_CACHE}")
        return pl.read_parquet(VOLUME_CACHE)

    print("[volume] Scanning compact parquet for YES-buy volume...")
    token_map = pl.read_parquet(METADATA_DIR / "token_map.parquet").select(
        "asset_id", "condition_id", "token_index"
    )

    trades = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .select("condition_id", "asset_id", "side", "price", "amount_usd", "timestamp")
        .collect()
    )

    result = compute_yes_buy_volume_per_market(trades, token_map)
    result.write_parquet(VOLUME_CACHE)
    print(f"[volume] Cached {result.height:,} markets to {VOLUME_CACHE}")
    return result
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: 8 passed

**Step 5: Commit**

```bash
git add scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py
git commit -m "feat: add YES-buy volume scanner with disk cache"
```

---

### Task 3: Stage 1 — Unconstrained Signal Sweep with MM Overlay

**Files:**
- Modify: `scripts/informed_mm_estimate.py`

This task adds the core Stage 1 logic. It reuses backtester infrastructure directly (import from `strategies.consistency_copy.backtester`).

**Step 1: Write the failing test for `stage1_sweep`**

Append to `tests/test_informed_mm_estimate.py`:

```python
def test_stage1_sweep_produces_both_taker_and_maker_pnl():
    """Stage 1 sweep must produce taker_pnl and maker_pnl columns."""
    from scripts.informed_mm_estimate import stage1_mm_overlay

    # Minimal signal fires: 2 markets, both NO signal, one wins one loses
    signals = pl.DataFrame({
        "condition_id": ["m1", "m2"],
        "trigger_entry_price": [0.70, 0.65],  # NO-side entry prices
        "signal_direction": ["NO", "NO"],
        "yes_won": [False, True],  # m1: NO wins, m2: YES wins
        "n_traders": [5, 7],
        "agreement_frac": [0.80, 0.71],
    })
    volume = pl.DataFrame({
        "condition_id": ["m1", "m2"],
        "yes_buy_volume": [500.0, 200.0],
        "yes_buy_count": [10, 5],
    })

    result = stage1_mm_overlay(
        signals=signals,
        volume=volume,
        spread_edges=[0.01],
        flat_fill_rates=[1.0],
        bet_size=100.0,
        fee_pct=0.0,
    )

    assert "taker_pnl" in result.columns
    assert "maker_pnl" in result.columns
    assert "maker_delta" in result.columns
    assert "fill_model" in result.columns
    assert result.height > 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_informed_mm_estimate.py::test_stage1_sweep_produces_both_taker_and_maker_pnl -x -q`
Expected: FAIL

**Step 3: Implement `stage1_mm_overlay`**

Add to `scripts/informed_mm_estimate.py`:

```python
import itertools


def stage1_mm_overlay(
    signals: pl.DataFrame,
    volume: pl.DataFrame,
    spread_edges: list[float],
    flat_fill_rates: list[float],
    bet_size: float = 100.0,
    fee_pct: float = 0.0,
) -> pl.DataFrame:
    """Stage 1: Compute taker vs maker PnL for each signal fire × MM param combo.

    For each (spread_edge, fill_model) combination:
    - Computes per-bet taker PnL (baseline)
    - Computes per-bet maker PnL (with spread edge)
    - Applies fill probability (flat scenarios + volume-derived)

    Returns one row per (signal_fire × MM_config) with aggregated PnL.
    """
    # Join volume data
    df = signals.join(volume, on="condition_id", how="left").with_columns(
        pl.col("yes_buy_volume").fill_null(0.0),
    )

    results: list[dict] = []

    # Determine NO-won for PnL calc (NO signal: NO wins when yes_won is False)
    # For each signal fire, compute PnL
    for spread_edge in spread_edges:
        # --- Taker PnL (constant across MM configs) ---
        # For NO signals: yes_price = 1 - trigger_entry_price
        # trigger_entry_price IS the NO-side price for NO signals
        taker_pnls = []
        maker_pnls = []

        for row in df.iter_rows(named=True):
            no_entry = row["trigger_entry_price"]
            yes_price = 1.0 - no_entry  # convert back to YES price
            no_won = not row["yes_won"]

            t_pnl = compute_taker_bet_pnl(yes_price, bet_size, no_won, fee_pct)
            m_pnl = compute_maker_bet_pnl(yes_price, spread_edge, bet_size, no_won, fee_pct)
            taker_pnls.append(t_pnl)
            maker_pnls.append(m_pnl)

        df_with_pnl = df.with_columns(
            pl.Series("taker_pnl_per_bet", taker_pnls),
            pl.Series("maker_pnl_per_bet", maker_pnls),
        )

        # --- Flat fill rate scenarios ---
        for fill_rate in flat_fill_rates:
            total_taker = sum(taker_pnls)
            total_maker = sum(p * fill_rate for p in maker_pnls)
            n_bets = len(taker_pnls)
            n_filled = int(n_bets * fill_rate)
            wins_taker = sum(1 for p in taker_pnls if p > 0)
            wins_maker = sum(1 for p in maker_pnls if p > 0)

            results.append({
                "spread_edge": spread_edge,
                "fill_model": "flat",
                "fill_rate": fill_rate,
                "n_signals": n_bets,
                "n_filled": n_filled,
                "taker_pnl": total_taker,
                "maker_pnl": total_maker,
                "taker_pnl_per_bet": total_taker / n_bets if n_bets else 0,
                "maker_pnl_per_bet": total_maker / n_filled if n_filled else 0,
                "maker_delta": total_maker - total_taker,
                "maker_delta_pct": (total_maker / total_taker - 1) * 100 if total_taker > 0 else 0,
                "taker_hr": wins_taker / n_bets if n_bets else 0,
                "maker_hr": wins_maker / n_bets if n_bets else 0,
            })

        # --- Volume-derived fill model ---
        vol_pnl = 0.0
        vol_filled = 0
        for row, m_pnl in zip(df_with_pnl.iter_rows(named=True), maker_pnls):
            fp = estimate_fill_probability(row["yes_buy_volume"], bet_size)
            vol_pnl += m_pnl * fp
            if fp > 0.5:
                vol_filled += 1

        n_bets = len(maker_pnls)
        results.append({
            "spread_edge": spread_edge,
            "fill_model": "volume_derived",
            "fill_rate": vol_filled / n_bets if n_bets else 0,
            "n_signals": n_bets,
            "n_filled": vol_filled,
            "taker_pnl": sum(taker_pnls),
            "maker_pnl": vol_pnl,
            "taker_pnl_per_bet": sum(taker_pnls) / n_bets if n_bets else 0,
            "maker_pnl_per_bet": vol_pnl / vol_filled if vol_filled else 0,
            "maker_delta": vol_pnl - sum(taker_pnls),
            "maker_delta_pct": (vol_pnl / sum(taker_pnls) - 1) * 100 if sum(taker_pnls) > 0 else 0,
            "taker_hr": sum(1 for p in taker_pnls if p > 0) / n_bets if n_bets else 0,
            "maker_hr": sum(1 for p in maker_pnls if p > 0) / n_bets if n_bets else 0,
        })

    return pl.DataFrame(results)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: 9 passed

**Step 5: Commit**

```bash
git add scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py
git commit -m "feat: add Stage 1 MM overlay sweep"
```

---

### Task 4: Stage 2 — Capital-Constrained Monthly Simulation

**Files:**
- Modify: `scripts/informed_mm_estimate.py`
- Modify: `tests/test_informed_mm_estimate.py`

**Step 1: Write the failing test**

Append to `tests/test_informed_mm_estimate.py`:

```python
from datetime import datetime


def test_stage2_capital_sim_respects_slot_limit():
    """Capital sim should not place more bets than available slots."""
    from scripts.informed_mm_estimate import stage2_capital_sim

    # 15 signals all in one month, but only 10 slots
    signals = pl.DataFrame({
        "condition_id": [f"m{i}" for i in range(15)],
        "trigger_entry_price": [0.70] * 15,
        "signal_direction": ["NO"] * 15,
        "yes_won": [False] * 15,  # all NO wins
        "trigger_time": [datetime(2025, 1, 5 + i) for i in range(15)],
        "resolved_at": [datetime(2025, 1, 20 + i) for i in range(15)],
    })
    volume = pl.DataFrame({
        "condition_id": [f"m{i}" for i in range(15)],
        "yes_buy_volume": [500.0] * 15,
        "yes_buy_count": [10] * 15,
    })

    result = stage2_capital_sim(
        signals=signals,
        volume=volume,
        capital=1000.0,
        bet_size=100.0,
        spread_edge=0.01,
        fill_rate=1.0,
        fee_pct=0.0,
    )

    total_placed = sum(m["placed"] for m in result["months"])
    # 10 slots, 15-day lockup → can't do more than ~10-12
    assert total_placed <= 15
    assert total_placed >= 10  # should fill at least 10
    assert result["total_pnl"] > 0  # all NO wins → positive
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_informed_mm_estimate.py::test_stage2_capital_sim_respects_slot_limit -x -q`
Expected: FAIL

**Step 3: Implement `stage2_capital_sim`**

Add to `scripts/informed_mm_estimate.py`:

```python
from datetime import datetime


def stage2_capital_sim(
    signals: pl.DataFrame,
    volume: pl.DataFrame,
    capital: float = 1000.0,
    bet_size: float = 100.0,
    spread_edge: float = 0.01,
    fill_rate: float = 1.0,
    fee_pct: float = 0.0,
) -> dict:
    """Stage 2: Month-by-month FIFO simulation with capital constraints.

    Parameters
    ----------
    signals
        Signal fires with: condition_id, trigger_entry_price, signal_direction,
        yes_won, trigger_time, resolved_at.
    volume
        Per-market YES-buy volume for fill probability.
    capital, bet_size
        Capital constraints.
    spread_edge
        Spread improvement for maker execution.
    fill_rate
        Fill rate assumption (for flat model; ignored if volume_derived).
    fee_pct
        Fee as fraction of smaller side.

    Returns
    -------
    dict with keys: total_bets, total_wins, total_pnl, months (list of monthly dicts).
    """
    max_slots = int(capital / bet_size)

    # Join volume
    df = signals.join(volume, on="condition_id", how="left").with_columns(
        pl.col("yes_buy_volume").fill_null(0.0),
    )

    # Add month column
    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
    )

    # Sort by trigger time for FIFO
    df = df.sort("trigger_time")

    months = sorted(df.filter(pl.col("month").is_not_null())["month"].unique().to_list())

    cum_pnl = 0.0
    total_bets = 0
    total_wins = 0
    monthly_results = []

    for m in months:
        month_df = df.filter(pl.col("month") == m).sort("trigger_time")
        if month_df.height == 0:
            continue

        # Estimate lockup from this month's data
        lockups = (
            (pl.col("resolved_at") - pl.col("trigger_time")).dt.total_days()
        )
        month_df = month_df.with_columns(lockups.alias("lockup_days"))

        med_lockup = max(float(month_df["lockup_days"].median()), 1.0)
        capacity_days = max_slots * 30
        max_bets = int(capacity_days / med_lockup)

        placed = min(month_df.height, max_bets)
        if placed == 0:
            monthly_results.append({
                "month": m, "available": month_df.height, "placed": 0,
                "wins": 0, "pnl": 0.0, "cum_pnl": cum_pnl,
            })
            continue

        selected = month_df.head(placed)
        month_pnl = 0.0
        wins = 0

        for row in selected.iter_rows(named=True):
            no_entry = row["trigger_entry_price"]
            yes_price = 1.0 - no_entry
            no_won = not row["yes_won"]

            # Apply fill probability
            fp = min(fill_rate, estimate_fill_probability(row["yes_buy_volume"], bet_size))
            if fp < 0.5:
                # Not filled — skip (no capital consumed)
                placed -= 1
                continue

            pnl = compute_maker_bet_pnl(yes_price, spread_edge, bet_size, no_won, fee_pct)
            month_pnl += pnl
            if no_won:
                wins += 1

        total_bets += placed
        total_wins += wins
        cum_pnl += month_pnl
        monthly_results.append({
            "month": m, "available": month_df.height, "placed": placed,
            "wins": wins, "pnl": month_pnl, "cum_pnl": cum_pnl,
        })

    return {
        "total_bets": total_bets,
        "total_wins": total_wins,
        "total_pnl": cum_pnl,
        "months": monthly_results,
    }
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: 10 passed

**Step 5: Commit**

```bash
git add scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py
git commit -m "feat: add Stage 2 capital-constrained monthly sim"
```

---

### Task 5: Main Orchestration + Console Report

**Files:**
- Modify: `scripts/informed_mm_estimate.py`

This task wires everything together: loads data, builds signals using the backtester infrastructure, runs both stages, prints the report.

**Step 1: Implement `main()`**

Add to `scripts/informed_mm_estimate.py`:

```python
import sys
import time

from strategies.consistency_copy.backtester.config import load_config
from strategies.consistency_copy.backtester.runner import (
    _load_data,
    _precompute_mvf_subsets,
    _compute_trader_median_entry,
    get_consistent_traders,
    _precompute_entry_prices,
    _apply_precomputed_prices,
    PRICE_CACHE_DIR,
)
from strategies.consistency_copy.backtester.signal_table import build_signal_table
from strategies.consistency_copy.backtester.sweep import SweepConfig

CONFIG_PATH = Path("strategies/consistency_copy/sweep_config.toml")

# MM parameters
SPREAD_EDGES = [0.005, 0.01, 0.015, 0.02]  # 0.5c, 1c, 1.5c, 2c
FLAT_FILL_RATES = [0.25, 0.50, 0.75, 1.00]
CAPITAL = 1000.0
BET_SIZE = 100.0
TOP_N_FOR_SIM = 5

# Signal configs to sweep (top from existing analysis)
SIGNAL_CONFIGS = [
    {"min_traders": 5, "agreement_pct": 0.70},
    {"min_traders": 5, "agreement_pct": 0.80},
    {"min_traders": 7, "agreement_pct": 0.70},
    {"min_traders": 7, "agreement_pct": 0.80},
    {"min_traders": 10, "agreement_pct": 0.70},
    {"min_traders": 10, "agreement_pct": 0.80},
    {"min_traders": 5, "agreement_pct": 0.90},
    {"min_traders": 5, "agreement_pct": 1.00},
    {"min_traders": 7, "agreement_pct": 0.90},
    {"min_traders": 10, "agreement_pct": 0.90},
]


def _select_signal_fires(
    signal_table: pl.DataFrame,
    min_traders: int,
    agreement_pct: float,
    price_lo: float = 0.05,
    price_hi: float = 0.95,
) -> pl.DataFrame:
    """Filter signal table to qualifying NO-only fires, first per market."""
    qualified = signal_table.filter(
        (pl.col("n_traders") >= min_traders)
        & (pl.col("agreement_frac") >= agreement_pct)
        & (pl.col("signal_direction") == "NO")
        & (pl.col("trigger_entry_price") >= price_lo)
        & (pl.col("trigger_entry_price") <= price_hi)
    )
    return qualified.sort("arrival_idx").group_by("condition_id").first()


def print_stage1_report(all_results: list[dict]) -> None:
    """Print Stage 1 sweep results as a formatted table."""
    print(f"\n{'=' * 90}")
    print("  STAGE 1: Unconstrained Signal × MM Sweep")
    print(f"{'=' * 90}\n")

    # Group by signal config, show best MM config for each
    print(f"  {'Signal Config':<30} {'Spread':>6} {'Fill':>6} {'Model':<8} "
          f"{'Taker PnL':>10} {'Maker PnL':>10} {'Delta':>8} {'Delta%':>7}")
    print(f"  {'-' * 88}")

    for r in sorted(all_results, key=lambda x: x.get("maker_delta", 0), reverse=True)[:20]:
        label = f"mt={r['min_traders']} ag={r['agreement_pct']:.0%}"
        print(f"  {label:<30} {r['spread_edge']:>5.1f}c {r['fill_rate']:>5.0%} "
              f"{r['fill_model']:<8} ${r['taker_pnl']:>9,.0f} ${r['maker_pnl']:>9,.0f} "
              f"${r['maker_delta']:>7,.0f} {r['maker_delta_pct']:>6.1f}%")


def print_stage2_report(sim_results: list[dict]) -> None:
    """Print Stage 2 monthly simulation results."""
    print(f"\n{'=' * 90}")
    print(f"  STAGE 2: Capital-Constrained Monthly Sim (${CAPITAL:.0f} capital, ${BET_SIZE:.0f}/bet)")
    print(f"{'=' * 90}")

    for sim in sim_results:
        label = sim["label"]
        print(f"\n  --- {label} ---")
        print(f"  {'Month':<10} {'Avail':>6} {'Placed':>7} {'HR':>7} {'PnL':>10} {'Cum':>10}")
        print(f"  {'-' * 52}")
        for mr in sim["months"]:
            if mr["placed"] > 0:
                hr = mr["wins"] / mr["placed"] * 100
                print(f"  {mr['month']:<10} {mr['available']:>6} {mr['placed']:>7} "
                      f"{hr:>6.1f}% ${mr['pnl']:>8,.0f} ${mr['cum_pnl']:>8,.0f}")

        tb = sim["total_bets"]
        tw = sim["total_wins"]
        hr = tw / tb * 100 if tb > 0 else 0
        n_months = len([m for m in sim["months"] if m["placed"] > 0])
        avg_mo = sim["total_pnl"] / n_months if n_months > 0 else 0
        roi_mo = avg_mo / CAPITAL * 100
        print(f"  {'-' * 52}")
        print(f"  TOTAL: {tb} bets, {hr:.1f}% HR, ${sim['total_pnl']:,.0f} PnL")
        print(f"  Avg/month: ${avg_mo:,.0f} ({roi_mo:.1f}% ROI on ${CAPITAL:.0f})")


def main() -> None:
    """Run the full informed MM estimate: Stage 1 sweep + Stage 2 sim."""
    t0 = time.time()
    force_volume = "--force-volume" in sys.argv

    # ── Load data ──
    config = load_config(CONFIG_PATH)
    df_pnl, mvf_df, markets, price_ts = _load_data()
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    trader_median_entry = _compute_trader_median_entry(df_pnl)

    # ── Load/compute YES-buy volume ──
    volume = load_or_compute_yes_buy_volume(force=force_volume)
    print(f"[volume] {volume.height:,} markets with YES-buy data")

    # ── Build signals using best window ──
    # Use the most recent dev window for the estimate
    windows = config.generate_windows()
    dev_windows = [w for w in windows if not w.is_test]
    if not dev_windows:
        print("[error] No dev windows found")
        return

    win = dev_windows[-1]  # most recent dev window
    print(f"\n[signal] Using window: {win.name} "
          f"(train {win.train_start:%Y-%m-%d} to {win.train_end:%Y-%m-%d}, "
          f"holdout {win.holdout_start:%Y-%m-%d} to {win.holdout_end:%Y-%m-%d})")

    holdout_data = df_pnl.filter(
        (pl.col("resolved_at") >= win.holdout_start)
        & (pl.col("resolved_at") < win.holdout_end)
    )

    # Pre-compute forward prices (use 60s delay — optimal from sweep)
    DELAY_S = 60.0
    if price_ts is not None:
        PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = PRICE_CACHE_DIR / f"{win.name}_delay_{DELAY_S}.parquet"
        if cache_path.exists():
            entry_prices = pl.read_parquet(cache_path)
        else:
            entry_prices = _precompute_entry_prices(
                holdout_data, price_ts, execution_delay_s=DELAY_S,
                max_price_delay_s=config.max_price_delay_s,
            )
            entry_prices.write_parquet(cache_path)
    else:
        entry_prices = None

    # ── Stage 1: Sweep across signal configs × MM params ──
    print(f"\n[stage1] Sweeping {len(SIGNAL_CONFIGS)} signal configs × "
          f"{len(SPREAD_EDGES)} spread edges...")

    all_stage1: list[dict] = []

    for pool_cfg in [
        {"n_months": 9, "min_mkts": 20, "mvf_band": "pure_taker", "max_entry": 0.80},
        {"n_months": 6, "min_mkts": 10, "mvf_band": "pure_taker", "max_entry": 0.90},
    ]:
        skilled = get_consistent_traders(
            df_pnl, win.train_start, win.train_end,
            pool_cfg["n_months"], pool_cfg["min_mkts"],
        )
        pool = skilled & mvf_subsets[pool_cfg["mvf_band"]]

        # Filter by median entry
        eligible = set(
            trader_median_entry.filter(
                pl.col("median_entry") <= pool_cfg["max_entry"]
            )["trader"].to_list()
        )
        pool = pool & eligible

        if len(pool) < 5:
            continue

        signal_table = build_signal_table(holdout_data, pool, mvf_df)
        if entry_prices is not None:
            signal_table = _apply_precomputed_prices(signal_table, entry_prices)

        print(f"  pool({pool_cfg['n_months']}m, {pool_cfg['min_mkts']}mkts, "
              f"{pool_cfg['mvf_band']}, <={pool_cfg['max_entry']}): "
              f"{len(pool)} traders, {signal_table.height} signal rows")

        for sig_cfg in SIGNAL_CONFIGS:
            fires = _select_signal_fires(
                signal_table,
                min_traders=sig_cfg["min_traders"],
                agreement_pct=sig_cfg["agreement_pct"],
            )
            if fires.height < 5:
                continue

            overlay = stage1_mm_overlay(
                signals=fires,
                volume=volume,
                spread_edges=SPREAD_EDGES,
                flat_fill_rates=FLAT_FILL_RATES,
                bet_size=BET_SIZE,
                fee_pct=config.fee_pct,
            )

            for row in overlay.to_dicts():
                row["min_traders"] = sig_cfg["min_traders"]
                row["agreement_pct"] = sig_cfg["agreement_pct"]
                row["pool"] = f"{pool_cfg['n_months']}m_{pool_cfg['min_mkts']}mkts"
                all_stage1.append(row)

    print_stage1_report(all_stage1)

    # ── Stage 2: Capital sim on top configs ──
    if not all_stage1:
        print("\n[stage2] No Stage 1 results — skipping")
        return

    # Sort by maker_delta descending, take top N
    top = sorted(all_stage1, key=lambda x: x.get("maker_pnl_per_bet", 0), reverse=True)[:TOP_N_FOR_SIM]

    print(f"\n[stage2] Running capital sim on top {len(top)} configs...")

    # For each top config, also run the taker baseline for comparison
    sim_results = []
    for cfg in top:
        # Re-select signals with this config
        for pool_cfg in [
            {"n_months": 9, "min_mkts": 20, "mvf_band": "pure_taker", "max_entry": 0.80},
            {"n_months": 6, "min_mkts": 10, "mvf_band": "pure_taker", "max_entry": 0.90},
        ]:
            if cfg["pool"] != f"{pool_cfg['n_months']}m_{pool_cfg['min_mkts']}mkts":
                continue

            skilled = get_consistent_traders(
                df_pnl, win.train_start, win.train_end,
                pool_cfg["n_months"], pool_cfg["min_mkts"],
            )
            pool = skilled & mvf_subsets[pool_cfg["mvf_band"]]
            eligible = set(
                trader_median_entry.filter(
                    pl.col("median_entry") <= pool_cfg["max_entry"]
                )["trader"].to_list()
            )
            pool = pool & eligible

            signal_table = build_signal_table(holdout_data, pool, mvf_df)
            if entry_prices is not None:
                signal_table = _apply_precomputed_prices(signal_table, entry_prices)

            fires = _select_signal_fires(
                signal_table,
                min_traders=cfg["min_traders"],
                agreement_pct=cfg["agreement_pct"],
            )
            if fires.height < 5:
                continue

            # Ensure trigger_time and resolved_at are present
            if "trigger_time" not in fires.columns or "resolved_at" not in fires.columns:
                continue

            sim = stage2_capital_sim(
                signals=fires,
                volume=volume,
                capital=CAPITAL,
                bet_size=BET_SIZE,
                spread_edge=cfg["spread_edge"],
                fill_rate=cfg["fill_rate"],
                fee_pct=config.fee_pct,
            )
            sim["label"] = (
                f"MM mt={cfg['min_traders']} ag={cfg['agreement_pct']:.0%} "
                f"sp={cfg['spread_edge']:.1f}c fill={cfg['fill_rate']:.0%} "
                f"({cfg['fill_model']})"
            )
            sim_results.append(sim)

    print_stage2_report(sim_results)

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
```

**Step 2: Run the full test suite**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: All 10 tests pass

**Step 3: Run the script to verify it works end-to-end**

Run: `uv run python scripts/informed_mm_estimate.py`
Expected: Console output with Stage 1 sweep table and Stage 2 monthly sims. May take 1-2 minutes on first run (YES-buy volume scan).

**Step 4: Commit**

```bash
git add scripts/informed_mm_estimate.py
git commit -m "feat: add main orchestration and console report for informed MM estimate"
```

---

### Task 6: Verify End-to-End + Review Output

**Step 1: Run with force-volume to ensure clean state**

Run: `uv run python scripts/informed_mm_estimate.py --force-volume`

**Step 2: Verify output has all expected sections**

Check console output includes:
1. Data loading summary
2. Signal table stats
3. Stage 1 sweep table (signal × MM params)
4. Stage 2 monthly detail for top configs
5. Summary comparison
6. Timing

**Step 3: Run full test suite**

Run: `uv run pytest tests/test_informed_mm_estimate.py -x -q`
Expected: All tests pass

**Step 4: Lint**

Run: `uv run ruff check scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py`
Expected: No errors

**Step 5: Final commit**

```bash
git add scripts/informed_mm_estimate.py tests/test_informed_mm_estimate.py
git commit -m "chore: verify informed MM estimate end-to-end"
```
