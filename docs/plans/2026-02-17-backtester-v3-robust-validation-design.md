# Backtester V3: Robust Validation Design

**Date**: 2026-02-17
**Status**: Approved
**Approach**: Config-Driven (Approach B)

---

## Problem

The current backtester (v2) has four limitations identified in insight 06:

1. **Only 2 holdout windows** contributed to top configs (Dec 2025 + Jan 2026) — earlier 6-month windows had too few consistent traders
2. **No OOS isolation** — Jan 2026 was optimized on, not held out
3. **Raw hit rates** — NO-only configs benefit from 61.9% NO base rate without normalization
4. **Fixed sizing only** — kelly/agreement/edge strategies exist in `sizing.py` but aren't swept

## Design

### Config-Driven Architecture

All parameters move to `strategies/consistency_copy/sweep_config.toml`. The runner reads the config, generates windows programmatically, and sweeps all combos.

```toml
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

### Window Generation (Anchored Expanding)

Fixed train start at `train_anchor`, expanding training window. Holdout slides forward by `step_months`:

```
dev_00_2024Q1: train 2023-01→2024-01, holdout 2024-01→2024-04
dev_01_2024Q2: train 2023-01→2024-04, holdout 2024-04→2024-07
dev_02_2024Q3: train 2023-01→2024-07, holdout 2024-07→2024-10
dev_03_2024Q4: train 2023-01→2024-10, holdout 2024-10→2025-01
dev_04_2025Q1: train 2023-01→2025-01, holdout 2025-01→2025-04
dev_05_2025Q2: train 2023-01→2025-04, holdout 2025-04→2025-07
dev_06_2025Q3: train 2023-01→2025-07, holdout 2025-07→2025-10
dev_07_2025Q4: train 2023-01→2025-10, holdout 2025-10→2026-01
test_2026Q1:   train 2023-01→2026-01, holdout 2026-01→2026-04 (is_test=True)
```

8 dev windows + 1 test window. `is_test` determined by `holdout_start >= test_after`.

### Base Rate Adjustment

Per-window YES-won% computed from holdout markets only.

**excess_hr**: `hit_rate - direction_base_rate`
- NO-only: base = window NO-won%
- YES-only: base = window YES-won%
- Both: weighted by actual bet direction mix in that config

**base_adjusted_sharpe**: For each bet, compute baseline PnL (expected PnL of a random bet at base rate at same entry price):
```
baseline_bet_pnl = base_rate * (bet_size * (1-p)/p - fee) + (1-base_rate) * (-bet_size - fee)
excess_bet_pnl = actual_bet_pnl - baseline_bet_pnl
```
Aggregate excess to daily, compute Sharpe from excess daily PnL series.

Both metrics added to `compute_metrics` via optional `base_rate: float | None` parameter. Backward compatible.

### Position Sizing (Full Grid)

All 4 strategies in the sweep: `["fixed", "kelly", "agreement_weighted", "edge_weighted"]`.

Kelly and edge_weighted use oracle hit rate (`bets["won"].mean()`) — full-window empirical rate. This is look-ahead but gives an upper bound. If oracle Kelly can't beat fixed, real Kelly won't either.

The sizing loop is inside `run_sweep` after expensive signal filtering, so marginal cost per strategy is just PnL computation (~1.5-2x total, not 4x).

### OOS Validation

1. Runner sweeps all windows (dev + test) in one pass
2. Before saving, adds `is_test` column to results
3. `_compute_stability_ranking` runs ONLY on dev results
4. Top N dev configs looked up in test results for OOS performance

**Output files**:

| File | Contents |
|------|----------|
| `sweep_results.parquet` | All configs, all windows, with `is_test` column |
| `top_configs.json` | Top 50 by dev ranking, nested: `{config, dev: {...}, test: {...}}` |
| `sweep_config.toml` | Config used (for reproducibility) |

**analyze_sweep.py** gets OOS validation section: dev vs test side-by-side, flag configs where test Sharpe < 50% of dev avg.

## File Changes

| Module | Change | Lines (est) |
|--------|--------|-------------|
| `config.py` | NEW: TOML parse, window gen, dataclass | ~120 |
| `runner.py` | Replace hardcoded params with config, base rate plumbing, dev/test split | ~60 |
| `sweep.py` | Add `base_rates` param, pass to metrics | ~15 |
| `metrics.py` | Add `base_rate` param, `excess_hr` + `base_adjusted_sharpe` | ~30 |
| `__main__.py` | Add `--config` CLI arg | ~5 |
| `sweep_config.toml` | NEW config file | ~30 |
| `analyze_sweep.py` | Add OOS report section | ~40 |
| `test_backtester_config.py` | NEW tests | ~60 |
| `test_backtester_metrics.py` | NEW tests | ~40 |

**Unchanged**: `sizing.py`, `signal_table.py`, `price_scanner.py`.
