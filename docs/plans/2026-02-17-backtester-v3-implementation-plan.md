# Backtester V3: Robust Validation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add config-driven window generation, base rate adjustment, full sizing grid, and OOS validation to the consensus copy backtester.

**Architecture:** TOML config parsed by new `config.py` module drives window generation and sweep parameters. `metrics.py` gains base-rate-adjusted metrics. `runner.py` reads config, computes per-window base rates, and splits dev/test outputs. All 4 sizing strategies enter the sweep grid.

**Tech Stack:** Python 3.11+ (tomllib built-in), Polars, pytest

---

### Task 1: Create sweep_config.toml

**Files:**
- Create: `strategies/consistency_copy/sweep_config.toml`

**Step 1: Write the config file**

```toml
# Backtester V3 sweep configuration.
# All parameters for window generation, pool selection, and sweep grid.

[windows]
strategy = "anchored_expanding"
train_anchor = 2023-01-01
holdout_months = 3
step_months = 3
first_holdout = 2024-01-01
last_holdout = 2026-01-01
test_after = 2026-01-01

[pool]
consistency_months = [6, 9, 12]
min_markets = [10, 20, 30]
mvf_bands = ["all", "pure_taker", "informed_taker"]

[pricing]
execution_delays = [0, 30, 60, 300]
max_price_delay_s = 3600.0

[sweep]
min_traders = [2, 3, 5, 7, 10]
agreement_pct = [0.60, 0.70, 0.80, 0.90, 1.00]
directions = ["YES-only", "NO-only", "both"]
price_bands = [[0.05, 0.95], [0.10, 0.90], [0.20, 0.80]]
sizing_strategies = ["fixed", "kelly", "agreement_weighted", "edge_weighted"]
min_bets = 20
base_bet = 100.0
fee_pct = 0.02

[metrics]
base_rate_adjustment = true

[ranking]
top_n = 50
min_windows = 2
```

**Step 2: Commit**

```bash
git add strategies/consistency_copy/sweep_config.toml
git commit -m "feat(backtester): add sweep config TOML for v3"
```

---

### Task 2: Create config.py with TOML parser and window generator

**Files:**
- Create: `strategies/consistency_copy/backtester/config.py`
- Create: `tests/test_backtester_config.py`

**Step 1: Write the failing tests**

```python
"""Tests for backtester config — TOML parsing and window generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from strategies.consistency_copy.backtester.config import (
    BacktestConfig,
    WindowDef,
    load_config,
)


@pytest.fixture()
def sample_toml(tmp_path: Path) -> Path:
    """Write a minimal TOML config and return its path."""
    cfg = tmp_path / "test_config.toml"
    cfg.write_text(dedent("""\
        [windows]
        strategy = "anchored_expanding"
        train_anchor = 2023-01-01
        holdout_months = 3
        step_months = 3
        first_holdout = 2024-01-01
        last_holdout = 2024-07-01
        test_after = 2024-07-01

        [pool]
        consistency_months = [6]
        min_markets = [10]
        mvf_bands = ["all"]

        [pricing]
        execution_delays = [0, 60]
        max_price_delay_s = 3600.0

        [sweep]
        min_traders = [5]
        agreement_pct = [0.70]
        directions = ["NO-only"]
        price_bands = [[0.05, 0.95]]
        sizing_strategies = ["fixed"]
        min_bets = 20
        base_bet = 100.0
        fee_pct = 0.02

        [metrics]
        base_rate_adjustment = true

        [ranking]
        top_n = 50
        min_windows = 2
    """))
    return cfg


def test_load_config_returns_backtest_config(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    assert isinstance(cfg, BacktestConfig)


def test_window_generation_count(sample_toml: Path) -> None:
    """3 dev windows + 1 test window from 2024-01 to 2024-07 (step=3mo)."""
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    dev = [w for w in windows if not w.is_test]
    test = [w for w in windows if w.is_test]
    assert len(dev) == 2  # 2024-01→04, 2024-04→07
    assert len(test) == 1  # 2024-07→10


def test_window_generation_anchored_expanding(sample_toml: Path) -> None:
    """All windows share the same train_anchor as train_start."""
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    for w in windows:
        assert w.train_start == datetime(2023, 1, 1)


def test_window_train_end_equals_holdout_start(sample_toml: Path) -> None:
    """No gap between training and holdout."""
    cfg = load_config(sample_toml)
    for w in cfg.generate_windows():
        assert w.train_end == w.holdout_start


def test_window_holdout_length(sample_toml: Path) -> None:
    """Each holdout spans holdout_months."""
    cfg = load_config(sample_toml)
    for w in cfg.generate_windows():
        delta_months = (w.holdout_end.year - w.holdout_start.year) * 12 + (
            w.holdout_end.month - w.holdout_start.month
        )
        assert delta_months == 3


def test_window_names_sequential(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    dev = [w for w in windows if not w.is_test]
    for i, w in enumerate(dev):
        assert w.name.startswith(f"dev_{i:02d}_")


def test_test_window_name(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    test = [w for w in windows if w.is_test]
    assert test[0].name.startswith("test_")


def test_sweep_config_from_toml(sample_toml: Path) -> None:
    """SweepConfig is built from [sweep] section."""
    cfg = load_config(sample_toml)
    sc = cfg.to_sweep_config()
    assert sc.min_traders_values == [5]
    assert sc.agreement_pct_values == [0.70]
    assert sc.direction_values == ["NO-only"]
    assert sc.entry_price_bands == [(0.05, 0.95)]
    assert sc.sizing_strategies == ["fixed"]
    assert sc.min_bets == 20
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_backtester_config.py -x -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.consistency_copy.backtester.config'`

**Step 3: Write the implementation**

```python
"""Config loader — parse TOML config and generate rolling windows.

Supports anchored-expanding window strategy: fixed training start,
training window grows as holdout slides forward.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from strategies.consistency_copy.backtester.sweep import SweepConfig


@dataclass(frozen=True)
class WindowDef:
    """A single train/holdout window definition."""

    name: str
    train_start: datetime
    train_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    is_test: bool


@dataclass
class BacktestConfig:
    """Full backtest configuration parsed from TOML."""

    # [windows]
    window_strategy: str
    train_anchor: datetime
    holdout_months: int
    step_months: int
    first_holdout: datetime
    last_holdout: datetime
    test_after: datetime

    # [pool]
    consistency_months: list[int]
    min_markets: list[int]
    mvf_bands: list[str]

    # [pricing]
    execution_delays: list[float]
    max_price_delay_s: float

    # [sweep]
    min_traders: list[int]
    agreement_pct: list[float]
    directions: list[str]
    price_bands: list[tuple[float, float]]
    sizing_strategies: list[str]
    min_bets: int
    base_bet: float
    fee_pct: float

    # [metrics]
    base_rate_adjustment: bool

    # [ranking]
    top_n: int
    min_windows: int

    def generate_windows(self) -> list[WindowDef]:
        """Generate window definitions from config parameters."""
        if self.window_strategy != "anchored_expanding":
            msg = f"Unknown window strategy: {self.window_strategy!r}"
            raise ValueError(msg)

        windows: list[WindowDef] = []
        dev_idx = 0
        current_holdout_start = self.first_holdout

        while current_holdout_start <= self.last_holdout:
            holdout_end = _add_months(current_holdout_start, self.holdout_months)
            is_test = current_holdout_start >= self.test_after

            # Quarter label from holdout_start
            q = (current_holdout_start.month - 1) // 3 + 1
            year = current_holdout_start.year

            if is_test:
                name = f"test_{year}Q{q}"
            else:
                name = f"dev_{dev_idx:02d}_{year}Q{q}"
                dev_idx += 1

            windows.append(
                WindowDef(
                    name=name,
                    train_start=self.train_anchor,
                    train_end=current_holdout_start,
                    holdout_start=current_holdout_start,
                    holdout_end=holdout_end,
                    is_test=is_test,
                )
            )

            current_holdout_start = _add_months(current_holdout_start, self.step_months)

        return windows

    def to_sweep_config(self) -> SweepConfig:
        """Build a SweepConfig from the [sweep] section."""
        return SweepConfig(
            min_traders_values=self.min_traders,
            agreement_pct_values=self.agreement_pct,
            direction_values=self.directions,
            entry_price_bands=self.price_bands,
            sizing_strategies=self.sizing_strategies,
            min_bets=self.min_bets,
        )


def _add_months(dt: datetime, months: int) -> datetime:
    """Add calendar months to a datetime (day stays at 1)."""
    month = dt.month + months
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return datetime(year, month, 1)


def load_config(path: Path) -> BacktestConfig:
    """Load a BacktestConfig from a TOML file."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    w = raw["windows"]
    p = raw["pool"]
    pr = raw["pricing"]
    s = raw["sweep"]
    m = raw["metrics"]
    r = raw["ranking"]

    return BacktestConfig(
        window_strategy=w["strategy"],
        train_anchor=datetime.fromisoformat(str(w["train_anchor"])),
        holdout_months=w["holdout_months"],
        step_months=w["step_months"],
        first_holdout=datetime.fromisoformat(str(w["first_holdout"])),
        last_holdout=datetime.fromisoformat(str(w["last_holdout"])),
        test_after=datetime.fromisoformat(str(w["test_after"])),
        consistency_months=p["consistency_months"],
        min_markets=p["min_markets"],
        mvf_bands=p["mvf_bands"],
        execution_delays=[float(d) for d in pr["execution_delays"]],
        max_price_delay_s=pr["max_price_delay_s"],
        min_traders=s["min_traders"],
        agreement_pct=s["agreement_pct"],
        directions=s["directions"],
        price_bands=[tuple(b) for b in s["price_bands"]],
        sizing_strategies=s["sizing_strategies"],
        min_bets=s["min_bets"],
        base_bet=s["base_bet"],
        fee_pct=s["fee_pct"],
        base_rate_adjustment=m["base_rate_adjustment"],
        top_n=r["top_n"],
        min_windows=r["min_windows"],
    )
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_backtester_config.py -x -q
```

Expected: All 8 tests PASS.

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/config.py tests/test_backtester_config.py
git commit -m "feat(backtester): add config.py TOML parser and window generator"
```

---

### Task 3: Add base rate metrics to metrics.py

**Files:**
- Modify: `strategies/consistency_copy/backtester/metrics.py`
- Create: `tests/test_backtester_metrics.py`

**Step 1: Write the failing tests**

```python
"""Tests for backtester metrics — base rate adjustment."""

from __future__ import annotations

import math

import polars as pl
import pytest

from strategies.consistency_copy.backtester.metrics import compute_metrics


def _make_daily_pnl(pnl_values: list[float], bets: int = 1, wins: int = 1) -> pl.DataFrame:
    """Build a minimal daily_pnl DataFrame."""
    from datetime import date, timedelta

    base = date(2025, 12, 1)
    return pl.DataFrame({
        "resolved_date": [base + timedelta(days=i) for i in range(len(pnl_values))],
        "daily_pnl": pnl_values,
        "n_bets": [bets] * len(pnl_values),
        "n_wins": [wins] * len(pnl_values),
    })


def test_metrics_without_base_rate_backward_compatible() -> None:
    """Without base_rate, no excess metrics are returned."""
    daily = _make_daily_pnl([10.0, -5.0, 8.0, -2.0, 6.0])
    result = compute_metrics(daily)
    assert "excess_hr" not in result
    assert "base_adjusted_sharpe" not in result
    assert "sharpe" in result


def test_metrics_with_base_rate_adds_excess_hr() -> None:
    """With base_rate, excess_hr = hit_rate - base_rate."""
    daily = _make_daily_pnl([10.0, -5.0, 8.0], bets=2, wins=1)
    result = compute_metrics(daily, base_rate=0.60)
    assert "excess_hr" in result
    # hit_rate = 3 wins / 6 bets = 0.5, excess = 0.5 - 0.6 = -0.1
    assert abs(result["excess_hr"] - (-0.1)) < 1e-9


def test_metrics_with_base_rate_adds_adjusted_sharpe() -> None:
    """base_adjusted_sharpe is computed from excess daily PnL."""
    daily = _make_daily_pnl([10.0, -5.0, 8.0, -2.0, 6.0])
    result = compute_metrics(daily, base_rate=0.50)
    assert "base_adjusted_sharpe" in result
    assert isinstance(result["base_adjusted_sharpe"], float)


def test_metrics_base_rate_zero_gives_full_excess() -> None:
    """With base_rate=0, excess_hr = hit_rate (all wins are above base)."""
    daily = _make_daily_pnl([10.0, 10.0, 10.0], bets=1, wins=1)
    result = compute_metrics(daily, base_rate=0.0)
    assert abs(result["excess_hr"] - 1.0) < 1e-9


def test_metrics_base_rate_one_gives_negative_excess() -> None:
    """With base_rate=1.0, excess_hr = hit_rate - 1.0 (always negative unless 100% win)."""
    daily = _make_daily_pnl([10.0, -5.0], bets=2, wins=1)
    result = compute_metrics(daily, base_rate=1.0)
    # hit_rate = 2/4 = 0.5, excess = 0.5 - 1.0 = -0.5
    assert result["excess_hr"] < 0


def test_metrics_empty_with_base_rate() -> None:
    """Empty DataFrame with base_rate returns zero excess metrics."""
    empty = pl.DataFrame(schema={
        "resolved_date": pl.Date,
        "daily_pnl": pl.Float64,
        "n_bets": pl.Int64,
        "n_wins": pl.Int64,
    })
    result = compute_metrics(empty, base_rate=0.5)
    assert result["excess_hr"] == 0
    assert result["base_adjusted_sharpe"] == 0
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_backtester_metrics.py -x -q
```

Expected: FAIL — `compute_metrics() got an unexpected keyword argument 'base_rate'`

**Step 3: Implement base rate metrics in metrics.py**

Add `base_rate: float | None = None` parameter to `compute_metrics`. At the end of the function, after existing metrics, add:

```python
    # Base rate adjustment (optional)
    if base_rate is not None:
        excess_hr = hit_rate - base_rate
        # Baseline PnL: what a random bettor at base_rate would earn per day
        # baseline_daily = n_bets_day * (base_rate * avg_win - (1-base_rate) * avg_loss)
        # Simplified: baseline per bet = base_rate * avg_payoff_win + (1-base_rate) * avg_payoff_lose
        # We approximate: excess_daily = daily_pnl - baseline_daily_pnl
        # Where baseline = daily PnL scaled by (base_rate / hit_rate) if hit_rate > 0
        # More precisely: excess = actual - (what random at base_rate would get at same daily structure)
        if n_days >= 2 and total_bets > 0:
            # Per-day baseline: each day's bets would win at base_rate instead of actual rate
            # baseline_daily_pnl = daily_wins_at_base * avg_win + daily_losses_at_base * avg_loss
            # Using the fact that total_pnl = sum(daily_pnl), and a random bettor:
            #   expected_wins = total_bets * base_rate
            #   expected_pnl = expected_wins * avg_win_pnl + expected_losses * avg_loss_pnl
            # We need per-day excess. Simplest correct approach:
            # excess_daily_pnl[i] = daily_pnl[i] - (n_bets[i] * expected_pnl_per_bet)
            # where expected_pnl_per_bet = base_rate * avg_win_pnl + (1-base_rate) * avg_loss_pnl
            # and avg_win_pnl, avg_loss_pnl estimated from overall actuals.
            if total_wins > 0 and total_wins < total_bets:
                avg_win_pnl = sum(max(0, x) for x in pnl_series) / win_days if win_days > 0 else 0
                avg_loss_pnl = sum(min(0, x) for x in pnl_series) / loss_days if loss_days > 0 else 0
                # But this is day-level, not bet-level. Use bet-level:
                # total_win_pnl + total_loss_pnl = total_pnl
                # Estimate: avg win payoff per bet and avg loss payoff per bet
                # We don't have per-bet data here, so use day-level approximation.
                # excess_daily = daily_pnl - n_bets * baseline_per_bet
                # baseline_per_bet = total_pnl / total_bets (= pnl_per_bet at base_rate)
                # Actually, more precisely:
                # If hit_rate = actual, base_rate = random:
                # baseline_pnl_per_bet = (base_rate / hit_rate) * pnl_per_bet_for_wins + ...
                # This gets complicated. Use the simple approach:
                # excess_daily[i] = daily_pnl[i] - n_bets[i] * baseline_pnl_per_bet
                # baseline_pnl_per_bet = (base_rate - hit_rate) * (avg_win_payoff - avg_loss_payoff) + pnl_per_bet
                # SIMPLEST: subtract expected PnL of random bettor with same bet sizes
                pass

            # Simple approach: scale daily PnL by excess factor
            # excess_daily_pnl = daily_pnl - n_bets * baseline_per_bet
            bets_per_day = df["n_bets"].to_list()
            baseline_per_bet = pnl_per_bet * (base_rate / hit_rate) if hit_rate > 0 else 0.0
            excess_pnl_series = [
                dp - nb * baseline_per_bet
                for dp, nb in zip(pnl_series, bets_per_day)
            ]
            mean_excess = sum(excess_pnl_series) / n_days
            var_excess = sum((x - mean_excess) ** 2 for x in excess_pnl_series) / (n_days - 1)
            std_excess = math.sqrt(var_excess) if var_excess > 0 else 0.0
            base_adjusted_sharpe = (
                (mean_excess / std_excess) * math.sqrt(365) if std_excess > 0 else 0.0
            )
        else:
            base_adjusted_sharpe = 0.0

        result["excess_hr"] = excess_hr
        result["base_adjusted_sharpe"] = base_adjusted_sharpe
```

Wait — the above approach conflates win-level and day-level PnL. Let me think more carefully about the right formula.

The cleanest per-bet baseline: a random bettor placing the same bets (same entry prices, same directions) would win at `base_rate` instead of `hit_rate`. Since we don't have per-bet data in `compute_metrics` (it receives daily aggregated data), we need to pass the baseline PnL as a pre-computed daily series.

**Revised approach**: Compute baseline at the sweep level (where we have per-bet data), pass `daily_baseline_pnl` into `compute_metrics`.

Actually, let me simplify this. The `compute_metrics` function receives daily_pnl with n_bets and n_wins. We can compute:

```
actual_pnl_per_bet = total_pnl / total_bets
expected_pnl_per_bet at base_rate:
  If actual hit_rate > 0 and actual hit_rate < 1:
    avg_win_payoff_per_bet = (total_pnl + total_losses_cost) / total_wins
    avg_loss_cost_per_bet = total_losses_cost / (total_bets - total_wins)
    baseline_per_bet = base_rate * avg_win_payoff + (1-base_rate) * avg_loss_cost
```

But we don't have per-bet win/loss splits at the metrics level. The simplest correct approach: pass `baseline_daily_pnl: pl.DataFrame | None` as a separate parameter computed at the sweep level.

**FINAL APPROACH**: Keep `compute_metrics` simple. Add two new optional parameters:
- `base_rate: float | None` — for `excess_hr` only
- `baseline_daily_pnl: list[float] | None` — pre-computed daily baseline PnL for `base_adjusted_sharpe`

The sweep computes `baseline_daily_pnl` from per-bet data where it's straightforward.

Let me rewrite the implementation more carefully:

Modify `compute_metrics` signature at `metrics.py:27`:

```python
def compute_metrics(
    daily_pnl: pl.DataFrame,
    base_rate: float | None = None,
    baseline_daily_pnl: list[float] | None = None,
) -> dict:
```

At the end (after line 142, before the return), add base rate metrics:

```python
    if base_rate is not None:
        result["excess_hr"] = hit_rate - base_rate

        if baseline_daily_pnl is not None and n_days >= 2:
            excess_series = [a - b for a, b in zip(pnl_series, baseline_daily_pnl)]
            mean_ex = sum(excess_series) / n_days
            var_ex = sum((x - mean_ex) ** 2 for x in excess_series) / (n_days - 1)
            std_ex = math.sqrt(var_ex) if var_ex > 0 else 0.0
            result["base_adjusted_sharpe"] = (
                (mean_ex / std_ex) * math.sqrt(365) if std_ex > 0 else 0.0
            )
        else:
            result["base_adjusted_sharpe"] = 0.0
```

And update the empty-data early return (line 45-63) to also include these when `base_rate` is not None.

Now rewrite the tests more precisely and the implementation. Let me finalize this in the plan properly.

**Step 3 (revised): Modify metrics.py**

Change the function signature at line 27 and add base rate metrics before the final return. Also update the empty early-return block.

At `metrics.py:27`, change:
```python
def compute_metrics(daily_pnl: pl.DataFrame) -> dict:
```
to:
```python
def compute_metrics(
    daily_pnl: pl.DataFrame,
    base_rate: float | None = None,
    baseline_daily_pnl: list[float] | None = None,
) -> dict:
```

At `metrics.py:44-63` (empty early return), after the existing zeros dict but before `return`, conditionally add:
```python
    if daily_pnl.height == 0:
        result = { ... existing zeros ... }
        if base_rate is not None:
            result["excess_hr"] = 0.0
            result["base_adjusted_sharpe"] = 0.0
        return result
```

At `metrics.py:124-142` (before final return), change the `return` to use a variable and add:
```python
    result = {
        "total_pnl": total_pnl,
        ... existing keys ...
    }

    if base_rate is not None:
        result["excess_hr"] = hit_rate - base_rate
        if baseline_daily_pnl is not None and n_days >= 2:
            excess_series = [a - b for a, b in zip(pnl_series, baseline_daily_pnl)]
            mean_ex = sum(excess_series) / n_days
            var_ex = sum((x - mean_ex) ** 2 for x in excess_series) / (n_days - 1)
            std_ex = math.sqrt(var_ex) if var_ex > 0 else 0.0
            result["base_adjusted_sharpe"] = (
                (mean_ex / std_ex) * math.sqrt(365) if std_ex > 0 else 0.0
            )
        else:
            result["base_adjusted_sharpe"] = 0.0

    return result
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_backtester_metrics.py tests/test_backtester_signal_table.py -x -q
```

Expected: All new metric tests PASS + existing signal table tests still PASS (backward compatible).

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/metrics.py tests/test_backtester_metrics.py
git commit -m "feat(backtester): add excess_hr and base_adjusted_sharpe metrics"
```

---

### Task 4: Add base rate plumbing to sweep.py

**Files:**
- Modify: `strategies/consistency_copy/backtester/sweep.py:50-175`

**Step 1: Write the failing test**

Add to `tests/test_backtester_metrics.py`:

```python
from strategies.consistency_copy.backtester.sweep import SweepConfig, run_sweep


def _make_signal_table() -> pl.DataFrame:
    """Minimal signal table with 25 rows (5 markets × 5 traders) to pass min_bets=20."""
    from datetime import datetime, timedelta

    rows = []
    base = datetime(2025, 12, 1)
    for m in range(25):
        cid = f"mkt_{m:02d}"
        rows.append({
            "condition_id": cid,
            "arrival_idx": 1,
            "trigger_time": base + timedelta(hours=m),
            "resolved_at": base + timedelta(days=m % 10 + 1),
            "resolution_value": 1,
            "n_traders": 5,
            "n_yes": 1 if m % 3 == 0 else 4,
            "n_no": 4 if m % 3 == 0 else 1,
            "agreement_frac": 0.80,
            "signal_direction": "YES" if m % 3 == 0 else "NO",
            "trigger_entry_price": 0.40 + (m % 5) * 0.05,
            "avg_pool_entry": 0.40,
            "trader": f"t_{m % 5}",
            "mvf": 0.05,
            "yes_won": m % 2 == 0,
        })
    return pl.DataFrame(rows)


def test_sweep_with_base_rates_adds_excess_metrics() -> None:
    """When base_rates is provided, sweep results include excess_hr."""
    st = _make_signal_table()
    cfg = SweepConfig(
        min_traders_values=[5],
        agreement_pct_values=[0.80],
        direction_values=["NO-only"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
        min_bets=10,
    )
    result = run_sweep(st, cfg, base_bet=1.0, base_rates={"NO": 0.60, "YES": 0.40})
    assert "excess_hr" in result.columns
    assert "base_adjusted_sharpe" in result.columns


def test_sweep_without_base_rates_no_excess_metrics() -> None:
    """Without base_rates, no excess metrics in output."""
    st = _make_signal_table()
    cfg = SweepConfig(
        min_traders_values=[5],
        agreement_pct_values=[0.80],
        direction_values=["NO-only"],
        entry_price_bands=[(0.05, 0.95)],
        sizing_strategies=["fixed"],
        min_bets=10,
    )
    result = run_sweep(st, cfg, base_bet=1.0)
    assert "excess_hr" not in result.columns
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_backtester_metrics.py::test_sweep_with_base_rates_adds_excess_metrics -x -q
```

Expected: FAIL — `run_sweep() got an unexpected keyword argument 'base_rates'`

**Step 3: Modify sweep.py**

At `sweep.py:50`, add `base_rates` parameter:

```python
def run_sweep(
    signal_table: pl.DataFrame,
    config: SweepConfig,
    base_bet: float = 1.0,
    fee_pct: float = 0.02,
    base_rates: dict[str, float] | None = None,
) -> pl.DataFrame:
```

Inside the sizing loop (after line 146, `daily_pnl = ...`), compute baseline and pass to metrics:

```python
            # Compute base rate and baseline daily PnL if requested
            br: float | None = None
            baseline_daily: list[float] | None = None
            if base_rates is not None:
                if direction == "YES-only":
                    br = base_rates.get("YES")
                elif direction == "NO-only":
                    br = base_rates.get("NO")
                else:
                    # "both": weighted by actual direction mix
                    n_yes_bets = sized.filter(pl.col("signal_direction") == "YES").height
                    n_no_bets = sized.filter(pl.col("signal_direction") == "NO").height
                    total = n_yes_bets + n_no_bets
                    if total > 0:
                        br = (
                            n_yes_bets / total * base_rates.get("YES", 0.5)
                            + n_no_bets / total * base_rates.get("NO", 0.5)
                        )

                if br is not None:
                    # Baseline per-bet PnL: what a random bettor at base_rate would earn
                    # For each bet: baseline = br * win_payoff + (1-br) * lose_payoff
                    baseline_bets = sized.with_columns(
                        (
                            pl.lit(br) * (pl.col("bet_size") * (1.0 - pl.col("trigger_entry_price")) / pl.col("trigger_entry_price") - pl.col("fee"))
                            + pl.lit(1.0 - br) * (-pl.col("bet_size") - pl.col("fee"))
                        ).alias("baseline_pnl")
                    )
                    baseline_daily_df = (
                        baseline_bets.with_columns(pl.col("resolved_at").cast(pl.Date).alias("resolved_date"))
                        .group_by("resolved_date")
                        .agg(pl.col("baseline_pnl").sum().alias("baseline_daily_pnl"))
                        .sort("resolved_date")
                    )
                    # Align with actual daily_pnl (left join to ensure same dates)
                    daily_pnl_joined = daily_pnl.join(
                        baseline_daily_df, on="resolved_date", how="left"
                    ).with_columns(pl.col("baseline_daily_pnl").fill_null(0.0))
                    baseline_daily = daily_pnl_joined["baseline_daily_pnl"].to_list()

            metrics = compute_metrics(daily_pnl, base_rate=br, baseline_daily_pnl=baseline_daily)
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_backtester_metrics.py -x -q
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add strategies/consistency_copy/backtester/sweep.py tests/test_backtester_metrics.py
git commit -m "feat(backtester): pass base rates through sweep to metrics"
```

---

### Task 5: Refactor runner.py to use config

**Files:**
- Modify: `strategies/consistency_copy/backtester/runner.py`

This is the largest change. The runner replaces hardcoded `WINDOWS`, `DEFAULT_SWEEP`, `CONSISTENCY_MONTHS`, etc. with config-driven values.

**Step 1: No new tests for runner (it's the integration point; tested by running the sweep)**

The runner is tested by running the full sweep. Unit tests for its components (config, metrics, sweep) are already covered.

**Step 2: Modify runner.py**

Key changes:
1. Remove hardcoded `WINDOWS`, `DEFAULT_SWEEP`, `CONSISTENCY_MONTHS`, `MIN_MARKETS`, `MVF_BAND_NAMES`, `BASE_BET`, `EXECUTION_DELAYS`, `MAX_PRICE_DELAY_S` constants
2. Add `main(config_path: Path | None = None)` parameter
3. Load config via `load_config()`
4. Generate windows via `config.generate_windows()`
5. Compute per-window base rates from holdout markets
6. Pass `base_rates` to `run_sweep()`
7. Add `is_test` column to results
8. Split dev/test in `_compute_stability_ranking`
9. New `top_configs.json` format with `{config, dev, test}`

Replace `runner.py` imports (add at top):
```python
from strategies.consistency_copy.backtester.config import BacktestConfig, load_config
```

Replace the constants block (lines 21-93) with:
```python
# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("data/derived")
OUTPUT_DIR = Path("strategies/consistency_copy")
DEFAULT_CONFIG = OUTPUT_DIR / "sweep_config.toml"
```

Replace `main()` (line 440+) with a new version that:
1. Loads config: `config = load_config(config_path or DEFAULT_CONFIG)`
2. Generates windows: `windows = config.generate_windows()`
3. Builds sweep config: `sweep_cfg = config.to_sweep_config()`
4. In the window loop, computes base rates:
```python
        # Compute per-window base rates from holdout markets
        base_rates: dict[str, float] | None = None
        if config.base_rate_adjustment:
            holdout_mkts = markets.filter(
                (pl.col("resolved_at") >= win.holdout_start)
                & (pl.col("resolved_at") < win.holdout_end)
            )
            if holdout_mkts.height > 0 and "yes_won" in holdout_mkts.columns:
                yes_rate = holdout_mkts["yes_won"].mean()
                base_rates = {"YES": yes_rate, "NO": 1.0 - yes_rate}
                print(f"[base_rate] {win.name}: YES={yes_rate:.3f}, NO={1-yes_rate:.3f}")
```
5. Passes `base_rates` into `run_sweep()`
6. Adds `is_test` column to sweep results
7. Updates `_compute_stability_ranking` to filter `is_test == False`
8. Builds new `top_configs.json` format

Modify `_compute_stability_ranking` (line 240+) to accept `test_results` and produce the nested format:

```python
def _compute_stability_ranking(
    all_results: pl.DataFrame,
    top_n: int = 50,
    min_windows: int = 2,
) -> list[dict]:
    # Filter to dev windows only
    dev = all_results.filter(pl.col("is_test") == False)  # noqa: E712
    test = all_results.filter(pl.col("is_test") == True)  # noqa: E712

    ... existing grouping on dev ...

    # For each top config, look up test performance
    top_list = []
    for cfg_dict in top.to_dicts():
        entry = {"config": {}, "dev": {}, "test": {}}
        # Split config vs metric keys
        for k in config_cols:
            entry["config"][k] = cfg_dict[k]
        for k in ["avg_sharpe", "std_sharpe", "avg_pnl", "avg_hit_rate", "n_windows", "avg_pool_size"]:
            entry["dev"][k] = cfg_dict.get(k)
        # Add excess metrics if available
        for k in ["avg_excess_hr", "avg_base_adjusted_sharpe"]:
            if k in cfg_dict:
                entry["dev"][k] = cfg_dict[k]

        # Look up test results
        if test.height > 0:
            mask = pl.lit(True)
            for col in config_cols:
                if col in test.columns:
                    mask = mask & (pl.col(col) == cfg_dict[col])
            test_rows = test.filter(mask)
            if test_rows.height > 0:
                entry["test"] = {
                    "sharpe": test_rows["sharpe"].mean(),
                    "hit_rate": test_rows["hit_rate"].mean(),
                    "total_pnl": test_rows["total_pnl"].sum(),
                    "total_bets": int(test_rows["total_bets"].sum()),
                }
                if "excess_hr" in test_rows.columns:
                    entry["test"]["excess_hr"] = test_rows["excess_hr"].mean()
                if "base_adjusted_sharpe" in test_rows.columns:
                    entry["test"]["base_adjusted_sharpe"] = test_rows["base_adjusted_sharpe"].mean()

        top_list.append(entry)

    return top_list
```

**Step 3: Run existing tests to check nothing broke**

```bash
uv run pytest tests/test_backtester_signal_table.py tests/test_backtester_config.py tests/test_backtester_metrics.py -x -q
```

Expected: All PASS.

**Step 4: Commit**

```bash
git add strategies/consistency_copy/backtester/runner.py
git commit -m "feat(backtester): refactor runner to config-driven with base rates and dev/test split"
```

---

### Task 6: Update __main__.py with --config CLI arg

**Files:**
- Modify: `strategies/consistency_copy/backtester/__main__.py`

**Step 1: Update __main__.py**

```python
"""Run the consensus copy backtester sweep.

Usage:
    uv run python -m strategies.consistency_copy.backtester
    uv run python -m strategies.consistency_copy.backtester --config path/to/config.toml
"""

import argparse
from pathlib import Path

from strategies.consistency_copy.backtester.runner import main

parser = argparse.ArgumentParser(description="Consensus copy backtester sweep")
parser.add_argument(
    "--config",
    type=Path,
    default=None,
    help="Path to sweep config TOML (default: strategies/consistency_copy/sweep_config.toml)",
)

args = parser.parse_args()
main(config_path=args.config)
```

**Step 2: Commit**

```bash
git add strategies/consistency_copy/backtester/__main__.py
git commit -m "feat(backtester): add --config CLI arg to __main__"
```

---

### Task 7: Update analyze_sweep.py with OOS validation section

**Files:**
- Modify: `scripts/analyze_sweep.py`

**Step 1: Add OOS validation section**

Add a new function between `analyze_robustness` and `analyze_red_flags`:

```python
# ============================================================================
# G. OUT-OF-SAMPLE VALIDATION
# ============================================================================
def analyze_oos(df: pl.DataFrame) -> None:
    header("G. OUT-OF-SAMPLE VALIDATION (Dev vs Test)")

    if "is_test" not in df.columns:
        print("\n  No is_test column found — skipping OOS analysis.")
        print("  Re-run the sweep with v3 config to get dev/test split.")
        return

    dev = df.filter(pl.col("is_test") == False)  # noqa: E712
    test = df.filter(pl.col("is_test") == True)  # noqa: E712

    print(f"\n  Dev configs:  {dev.height:,} rows across {dev['window'].n_unique()} windows")
    print(f"  Test configs: {test.height:,} rows across {test['window'].n_unique()} windows")

    if test.height == 0:
        print("  No test data available.")
        return

    # Top 10 dev configs
    dev_grouped = (
        dev.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("dev_sharpe"),
            pl.col("hit_rate").mean().alias("dev_hr"),
            pl.col("total_pnl").mean().alias("dev_pnl"),
            pl.col("window").n_unique().alias("dev_windows"),
        )
        .filter(pl.col("dev_windows") >= 2)
        .sort("dev_sharpe", descending=True)
    )

    top10 = dev_grouped.head(10).to_dicts()

    sub_header("Top 10 Dev Configs: Dev vs Test Performance")
    print(f"  {'#':>3} {'Dev Sharpe':>12} {'Test Sharpe':>13} {'Dev HR':>8} {'Test HR':>9} "
          f"{'Dev PnL':>10} {'Test PnL':>10} {'Test Bets':>10} {'Verdict':>10}")

    for rank, cfg in enumerate(top10, 1):
        # Look up in test data
        mask = pl.lit(True)
        for col in CONFIG_COLS:
            if col in test.columns and col in cfg:
                mask = mask & (pl.col(col) == cfg[col])
        test_rows = test.filter(mask)

        if test_rows.height > 0:
            t_sharpe = test_rows["sharpe"].mean()
            t_hr = test_rows["hit_rate"].mean()
            t_pnl = test_rows["total_pnl"].sum()
            t_bets = int(test_rows["total_bets"].sum())
            # Verdict: test sharpe >= 50% of dev sharpe
            verdict = "PASS" if t_sharpe >= cfg["dev_sharpe"] * 0.5 else "FAIL"
        else:
            t_sharpe = float("nan")
            t_hr = float("nan")
            t_pnl = float("nan")
            t_bets = 0
            verdict = "NO DATA"

        print(
            f"  {rank:>3} {cfg['dev_sharpe']:>12.2f} {t_sharpe:>13.2f} "
            f"{pct(cfg['dev_hr']):>8} {pct(t_hr):>9} "
            f"{usd(cfg['dev_pnl']):>10} {usd(t_pnl):>10} "
            f"{t_bets:>10} {verdict:>10}"
        )

    # Excess metrics if available
    if "excess_hr" in dev.columns:
        sub_header("Base-Rate-Adjusted OOS Comparison")
        dev_excess = (
            dev.group_by(CONFIG_COLS)
            .agg(
                pl.col("excess_hr").mean().alias("dev_excess_hr"),
                pl.col("base_adjusted_sharpe").mean().alias("dev_adj_sharpe"),
                pl.col("window").n_unique().alias("dev_windows"),
            )
            .filter(pl.col("dev_windows") >= 2)
            .sort("dev_adj_sharpe", descending=True)
        )

        top5_excess = dev_excess.head(5).to_dicts()
        print(f"  {'#':>3} {'Dev ExHR':>10} {'Test ExHR':>11} {'Dev AdjSh':>11} {'Test AdjSh':>12}")

        for rank, cfg in enumerate(top5_excess, 1):
            mask = pl.lit(True)
            for col in CONFIG_COLS:
                if col in test.columns and col in cfg:
                    mask = mask & (pl.col(col) == cfg[col])
            test_rows = test.filter(mask)

            t_excess = test_rows["excess_hr"].mean() if test_rows.height > 0 and "excess_hr" in test_rows.columns else float("nan")
            t_adj = test_rows["base_adjusted_sharpe"].mean() if test_rows.height > 0 and "base_adjusted_sharpe" in test_rows.columns else float("nan")

            print(
                f"  {rank:>3} {cfg['dev_excess_hr']:>10.4f} {t_excess:>11.4f} "
                f"{cfg['dev_adj_sharpe']:>11.2f} {t_adj:>12.2f}"
            )
```

Add to `CONFIG_COLS` (if not already present): no changes needed, existing list is fine.

Add the call in `main()` between `analyze_robustness(df)` and `analyze_red_flags(df)`:

```python
    analyze_oos(df)
```

**Step 2: Commit**

```bash
git add scripts/analyze_sweep.py
git commit -m "feat(analyze): add OOS validation section to sweep analysis"
```

---

### Task 8: Run full test suite and verify

**Step 1: Run all unit tests**

```bash
uv run pytest tests/test_backtester_config.py tests/test_backtester_metrics.py tests/test_backtester_signal_table.py -x -q
```

Expected: All PASS.

**Step 2: Run lint**

```bash
uv run ruff check strategies/consistency_copy/backtester/ tests/test_backtester_config.py tests/test_backtester_metrics.py scripts/analyze_sweep.py
```

Fix any issues.

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: lint and type fixes for backtester v3"
```
