# Portfolio Replication Backtester — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a per-trader holdout evaluator that measures actual PnL and simulated copy PnL for the consistency + MVF + entry-price trader pool, using the same rolling-window framework as the consensus backtester.

**Architecture:** New `portfolio_runner.py` file alongside `runner.py`, sharing data-loading and pool-construction helpers. Dispatched via `--mode portfolio` CLI flag. Output is one parquet row per (trader, window, pool_config, delay).

**Tech Stack:** Python 3.11+, Polars, existing `BacktestConfig` / TOML parser, pytest

---

### Task 1: Core per-trader evaluation function with tests

**Files:**
- Create: `strategies/consistency_copy/backtester/portfolio_runner.py`
- Create: `tests/test_portfolio_runner.py`

**Step 1: Write the failing test for `evaluate_traders_actual()`**

This function takes a holdout slice of df_pnl and a set of pool traders, returns a DataFrame with one row per trader containing actual PnL stats.

```python
# tests/test_portfolio_runner.py
"""Tests for portfolio replication backtester."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 6, 1, hour, minute, tzinfo=timezone.utc)


def _make_holdout_pnl() -> pl.DataFrame:
    """Two traders, multiple markets resolving in holdout window.

    t1: markets A (+10), B (-5), C (+20)  → pnl=+25, 2/3 wins, wr=66.7%
    t2: markets A (-3), D (+8)            → pnl=+5,  1/2 wins, wr=50.0%
    t3 (not in pool): market A (+100)     → should be excluded
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_portfolio_runner.py::test_evaluate_traders_actual_shape_and_values -x -q`
Expected: FAIL — `ImportError: cannot import name 'evaluate_traders_actual'`

**Step 3: Implement `evaluate_traders_actual()`**

```python
# strategies/consistency_copy/backtester/portfolio_runner.py
"""Portfolio replication backtester — per-trader holdout evaluation.

Measures each trader's actual and simulated copy PnL individually,
using the same rolling-window framework as the consensus backtester.
"""

from __future__ import annotations

import polars as pl


def evaluate_traders_actual(
    holdout_pnl: pl.DataFrame,
    pool: set[str],
) -> pl.DataFrame:
    """Compute actual PnL stats for each trader in the pool.

    Parameters
    ----------
    holdout_pnl
        Per-trader per-market PnL filtered to the holdout window.
        Must have columns: trader, condition_id, market_pnl, market_volume.
    pool
        Set of trader addresses in the current pool.

    Returns
    -------
    pl.DataFrame
        One row per trader: trader, actual_pnl, actual_n_markets,
        actual_wins, actual_win_rate, actual_volume.
    """
    df = holdout_pnl.filter(pl.col("trader").is_in(list(pool)))

    return df.group_by("trader").agg(
        pl.col("market_pnl").sum().alias("actual_pnl"),
        pl.len().alias("actual_n_markets"),
        (pl.col("market_pnl") > 0).sum().alias("actual_wins"),
        pl.col("market_volume").sum().alias("actual_volume"),
    ).with_columns(
        (pl.col("actual_wins").cast(pl.Float64) / pl.col("actual_n_markets"))
        .alias("actual_win_rate"),
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_portfolio_runner.py::test_evaluate_traders_actual_shape_and_values -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/portfolio_runner.py tests/test_portfolio_runner.py
git commit --no-gpg-sign -m "feat(portfolio): add evaluate_traders_actual with tests"
```

---

### Task 2: Copy PnL evaluation function with tests

**Files:**
- Modify: `strategies/consistency_copy/backtester/portfolio_runner.py`
- Modify: `tests/test_portfolio_runner.py`

**Step 1: Write the failing test for `evaluate_traders_copy()`**

```python
# Append to tests/test_portfolio_runner.py

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

    # t1 market A: YES (net_yes>0), entry=0.30, yes_won=True → won
    #   pnl = 100 * (1-0.30)/0.30 = 233.33
    # t1 market B: NO (net_yes<0), entry=1-0.70=0.30, yes_won=False → won (NO wins)
    #   pnl = 100 * (1-0.30)/0.30 = 233.33
    # t1 market C: YES (net_yes>0), entry=0.40, yes_won=True → won
    #   pnl = 100 * (1-0.40)/0.40 = 150.00
    # t1 total: 233.33 + 233.33 + 150.00 = 616.67, 3/3 wins
    t1 = result.filter(pl.col("trader") == "t1").to_dicts()[0]
    assert t1["copy_wins"] == 3
    assert t1["copy_n_markets"] == 3
    assert t1["copy_pnl"] == pytest.approx(616.67, abs=0.01)

    # t2 market A: NO (net_yes<0), entry=1-0.60=0.40, yes_won=True → lost (YES won)
    #   pnl = -100
    # t2 market D: YES (net_yes>0), entry=0.35, yes_won=False → lost (NO won)
    #   pnl = -100
    # t2 total: -200, 0/2 wins
    t2 = result.filter(pl.col("trader") == "t2").to_dicts()[0]
    assert t2["copy_wins"] == 0
    assert t2["copy_n_markets"] == 2
    assert t2["copy_pnl"] == pytest.approx(-200.0)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_portfolio_runner.py::test_evaluate_traders_copy_basic -x -q`
Expected: FAIL — `ImportError: cannot import name 'evaluate_traders_copy'`

**Step 3: Implement `evaluate_traders_copy()`**

```python
# Add to portfolio_runner.py

def evaluate_traders_copy(
    holdout_pnl: pl.DataFrame,
    pool: set[str],
    entry_prices: pl.DataFrame | None,
    base_bet: float = 100.0,
) -> pl.DataFrame:
    """Compute simulated copy PnL for each trader in the pool.

    For each (trader, market), determines direction from net_yes_tokens,
    looks up the forward-priced entry (or falls back to trader's own entry),
    and computes binary bet PnL.

    Parameters
    ----------
    holdout_pnl
        Per-trader per-market PnL with columns: trader, condition_id,
        net_yes_tokens, wavg_yes_entry_price, first_trade, yes_won.
    pool
        Set of trader addresses in the current pool.
    entry_prices
        Pre-computed forward prices (from _precompute_entry_prices).
        Columns: condition_id, trader, first_trade, market_yes_price.
        If None, uses trader's own wavg_yes_entry_price.
    base_bet
        Fixed bet size per position.

    Returns
    -------
    pl.DataFrame
        One row per trader: trader, copy_pnl, copy_n_markets, copy_wins,
        copy_win_rate.
    """
    df = holdout_pnl.filter(pl.col("trader").is_in(list(pool)))

    # Direction from actual trade data
    df = df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes")
    )

    # Entry price: use forward price if available, else trader's own
    if entry_prices is not None:
        df = df.join(
            entry_prices.select(["condition_id", "trader", "first_trade", "market_yes_price"]),
            on=["condition_id", "trader", "first_trade"],
            how="left",
        )
        # Directional entry: YES → yes_price, NO → 1 - yes_price
        df = df.with_columns(
            pl.when(pl.col("market_yes_price").is_not_null())
            .then(
                pl.when(pl.col("bet_yes"))
                .then(pl.col("market_yes_price"))
                .otherwise(1.0 - pl.col("market_yes_price"))
            )
            .otherwise(
                pl.when(pl.col("bet_yes"))
                .then(pl.col("wavg_yes_entry_price"))
                .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
            )
            .alias("copy_entry")
        )
    else:
        df = df.with_columns(
            pl.when(pl.col("bet_yes"))
            .then(pl.col("wavg_yes_entry_price"))
            .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
            .alias("copy_entry")
        )

    # Outcome: did the copy bet win?
    df = df.with_columns(
        (
            (pl.col("bet_yes") & pl.col("yes_won"))
            | (~pl.col("bet_yes") & ~pl.col("yes_won"))
        ).alias("copy_won")
    )

    # PnL: won → base_bet * (1-entry)/entry, lost → -base_bet
    df = df.with_columns(
        pl.when(pl.col("copy_won"))
        .then(pl.lit(base_bet) * (1.0 - pl.col("copy_entry")) / pl.col("copy_entry"))
        .otherwise(pl.lit(-base_bet))
        .alias("position_pnl")
    )

    return df.group_by("trader").agg(
        pl.col("position_pnl").sum().alias("copy_pnl"),
        pl.len().alias("copy_n_markets"),
        pl.col("copy_won").sum().alias("copy_wins"),
    ).with_columns(
        (pl.col("copy_wins").cast(pl.Float64) / pl.col("copy_n_markets"))
        .alias("copy_win_rate"),
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_portfolio_runner.py -x -q`
Expected: 2 passed

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/portfolio_runner.py tests/test_portfolio_runner.py
git commit --no-gpg-sign -m "feat(portfolio): add evaluate_traders_copy with tests"
```

---

### Task 3: Test copy PnL with forward pricing

**Files:**
- Modify: `tests/test_portfolio_runner.py`

**Step 1: Write the test for forward-priced copy PnL**

```python
# Append to tests/test_portfolio_runner.py

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

    # Market A: YES, forward yes_price=0.25, won → 100*(1-0.25)/0.25 = 300.00
    # Market B: NO, no forward → fallback entry=1-0.70=0.30, won → 233.33
    # Market C: YES, no forward → fallback entry=0.40, won → 150.00
    # Total: 300 + 233.33 + 150 = 683.33
    assert t1["copy_pnl"] == pytest.approx(683.33, abs=0.01)
```

**Step 2: Run test**

Run: `uv run pytest tests/test_portfolio_runner.py -x -q`
Expected: 3 passed

**Step 3: Commit**

```bash
git add tests/test_portfolio_runner.py
git commit --no-gpg-sign -m "test(portfolio): add forward-priced copy PnL test"
```

---

### Task 4: Main loop and CLI integration

**Files:**
- Modify: `strategies/consistency_copy/backtester/portfolio_runner.py`
- Modify: `strategies/consistency_copy/backtester/__main__.py`

**Step 1: Implement `main()` in portfolio_runner.py**

Add the following to the end of `portfolio_runner.py`. This is the main loop that iterates windows × pool configs × delays, calling the two evaluate functions.

```python
# Add these imports at the top of portfolio_runner.py
import time
from datetime import datetime
from pathlib import Path

from strategies.consistency_copy.backtester.config import load_config
from strategies.consistency_copy.backtester.runner import (
    _compute_trader_median_entry,
    _load_data,
    _precompute_entry_prices,
    _precompute_mvf_subsets,
    get_consistent_traders,
)

OUTPUT_DIR = Path("strategies/consistency_copy")
DEFAULT_CONFIG = OUTPUT_DIR / "sweep_config.toml"
PRICE_CACHE_DIR = Path("data/derived/forward_prices")


def main(config_path: Path | None = None) -> None:
    """Run per-trader portfolio evaluation across windows and pool configs.

    For each window × pool_config, evaluates every trader in the pool
    individually, producing both actual PnL and simulated copy PnL.
    """
    t0 = time.time()

    config = load_config(config_path or DEFAULT_CONFIG)
    windows = config.generate_windows()

    print(f"[config] Loaded from {config_path or DEFAULT_CONFIG}")
    print(f"[config] {len(windows)} windows, mode=portfolio")

    # Load data (same as consensus runner)
    df_pnl, mvf_df, markets, price_ts = _load_data()

    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    trader_median_entry = _compute_trader_median_entry(df_pnl)

    # Build MVF lookup for per-trader metadata
    mvf_lookup = mvf_df.select(["trader", "mvf"])

    all_rows: list[pl.DataFrame] = []

    for win in windows:
        print(f"\n{'=' * 70}")
        print(f"[window] {win.name}: holdout {win.holdout_start:%Y-%m-%d} to "
              f"{win.holdout_end:%Y-%m-%d}")

        holdout_data = df_pnl.filter(
            (pl.col("resolved_at") >= win.holdout_start)
            & (pl.col("resolved_at") < win.holdout_end)
        )

        if holdout_data.height == 0:
            print(f"  no holdout data, skipping")
            continue

        # Pre-compute forward prices for all delays (cached on disk)
        delay_prices: dict[float, pl.DataFrame] = {}
        if price_ts is not None:
            PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for delay_s in config.execution_delays:
                cache_path = PRICE_CACHE_DIR / f"{win.name}_delay_{delay_s}.parquet"
                if cache_path.exists():
                    delay_prices[delay_s] = pl.read_parquet(cache_path)
                else:
                    print(f"  computing forward prices delay={delay_s}s...")
                    ep = _precompute_entry_prices(
                        holdout_data, price_ts,
                        execution_delay_s=delay_s,
                        max_price_delay_s=config.max_price_delay_s,
                    )
                    ep.write_parquet(cache_path)
                    delay_prices[delay_s] = ep

        for n_months in config.consistency_months:
            for min_mkts in config.min_markets:
                skilled = get_consistent_traders(
                    df_pnl, win.train_start, win.train_end, n_months, min_mkts
                )
                if len(skilled) < 10:
                    continue

                for band_name in config.mvf_bands:
                    pool_base = skilled & mvf_subsets[band_name]
                    if len(pool_base) < 5:
                        continue

                    for max_entry in config.max_median_entry_price:
                        eligible = set(
                            trader_median_entry.filter(
                                pl.col("median_entry") <= max_entry
                            )["trader"].to_list()
                        )
                        pool = pool_base & eligible
                        if len(pool) < 5:
                            continue

                        # Actual PnL (delay-independent, compute once)
                        actual = evaluate_traders_actual(holdout_data, pool)

                        if actual.height == 0:
                            continue

                        # Add trader metadata
                        actual = actual.join(
                            trader_median_entry, on="trader", how="left"
                        ).join(
                            mvf_lookup, on="trader", how="left"
                        )

                        # Copy PnL for each delay
                        for delay_s in config.execution_delays:
                            ep = delay_prices.get(delay_s)
                            copy = evaluate_traders_copy(
                                holdout_data, pool,
                                entry_prices=ep,
                                base_bet=config.base_bet,
                            )

                            # Join actual + copy
                            combined = actual.join(copy, on="trader", how="left")

                            # Add pool/window metadata
                            combined = combined.with_columns(
                                pl.lit(win.name).alias("window"),
                                pl.lit(win.is_test).alias("is_test"),
                                pl.lit(n_months).alias("consistency_months"),
                                pl.lit(min_mkts).alias("min_markets"),
                                pl.lit(band_name).alias("mvf_band"),
                                pl.lit(max_entry).alias("max_median_entry"),
                                pl.lit(delay_s).alias("execution_delay"),
                                pl.lit(len(pool)).alias("pool_size"),
                            )

                            all_rows.append(combined)

                        # Progress
                        n_profitable = int(actual.filter(
                            pl.col("actual_pnl") > 0
                        ).height)
                        pct = n_profitable / actual.height * 100
                        med_pnl = actual["actual_pnl"].median()
                        print(
                            f"  m={n_months} mkts={min_mkts} mvf={band_name} "
                            f"entry<={max_entry} pool={len(pool)}: "
                            f"{pct:.0f}% profitable, "
                            f"median=${med_pnl:.2f}"
                        )

    if not all_rows:
        print("\n[done] No results.")
        return

    result = pl.concat(all_rows, how="diagonal_relaxed")
    out_path = OUTPUT_DIR / "portfolio_results.parquet"
    result.write_parquet(out_path)
    print(f"\n[save] {result.height:,} rows -> {out_path}")

    elapsed = time.time() - t0
    print(f"[done] Finished in {elapsed:.1f}s")
```

**Step 2: Update `__main__.py` to dispatch on `--mode`**

Replace the entire contents of `__main__.py`:

```python
"""Run the consistency_copy backtester.

Usage:
    uv run python -m strategies.consistency_copy.backtester                    # consensus (default)
    uv run python -m strategies.consistency_copy.backtester --mode portfolio   # portfolio replication
    uv run python -m strategies.consistency_copy.backtester --config path.toml
"""

import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description="Consistency copy backtester")
parser.add_argument(
    "--config",
    type=Path,
    default=None,
    help="Path to sweep config TOML (default: strategies/consistency_copy/sweep_config.toml)",
)
parser.add_argument(
    "--mode",
    choices=["consensus", "portfolio"],
    default="consensus",
    help="Backtester mode: consensus (default) or portfolio replication",
)

args = parser.parse_args()

if args.mode == "portfolio":
    from strategies.consistency_copy.backtester.portfolio_runner import main
else:
    from strategies.consistency_copy.backtester.runner import main

main(config_path=args.config)
```

**Step 3: Run all tests to verify nothing is broken**

Run: `uv run pytest tests/test_portfolio_runner.py tests/test_backtester_config.py tests/test_backtester_signal_table.py -x -q`
Expected: all pass

**Step 4: Commit**

```bash
git add strategies/consistency_copy/backtester/portfolio_runner.py strategies/consistency_copy/backtester/__main__.py
git commit --no-gpg-sign -m "feat(portfolio): add main loop and --mode CLI dispatch"
```

---

### Task 5: Run portfolio backtester and validate output

**Step 1: Run the portfolio backtester**

```bash
uv run python -m strategies.consistency_copy.backtester --mode portfolio
```

Expected: runs through all windows, prints per-pool progress with % profitable and median PnL, writes `portfolio_results.parquet`.

**Step 2: Validate output schema and sanity**

```bash
uv run python3 -c "
import polars as pl
df = pl.read_parquet('strategies/consistency_copy/portfolio_results.parquet')
print('Shape:', df.shape)
print('Columns:', df.columns)
print()
# Per-window summary
for win in sorted(df['window'].unique().to_list()):
    sub = df.filter(pl.col('window') == win)
    n = sub.height
    pct_profit = (sub['actual_pnl'] > 0).mean() * 100
    med_pnl = sub['actual_pnl'].median()
    med_wr = sub['actual_win_rate'].median()
    med_copy_wr = sub['copy_win_rate'].median()
    print(f'{win:25s}: {n:5d} traders, {pct_profit:.0f}% profitable, '
          f'med_pnl=\${med_pnl:.0f}, med_actual_wr={med_wr:.1%}, med_copy_wr={med_copy_wr:.1%}')
"
```

Expected: actual_win_rate should be substantially higher than the ~46% from the consensus sweep, closer to insight #02's 83-87% range. copy_win_rate may be lower due to forward pricing slippage.

**Step 3: Commit output (the parquet will be in .gitignore, just commit the code)**

```bash
git add -A && git status
git commit --no-gpg-sign -m "feat(portfolio): complete portfolio replication backtester v1"
```
