# Backtester V2 Enhancements Design

**Date**: 2026-02-17
**Status**: Approved
**Scope**: Items 2-5 from `insights/06_forward_pricing_backtest.md` Next Steps

---

## Context

The current backtester (`strategies/consistency_copy/backtester/`) uses 5 hardcoded rolling windows, only 2 of which (Dec 2025, Jan 2026) produce meaningful results. Top configs all cluster in NO-only + pure_taker with Sharpe 3.5-5.0, but this is based on just 1-2 months of holdout data. The backtest also lacks base rate normalization (61.9% NO base rate inflates apparent NO signal quality) and only uses fixed $100 bet sizing despite Kelly/edge-weighted implementations existing in `sizing.py`.

---

## Enhancement 1: Sliding Monthly Windows

**Replace** the 5 hardcoded `WINDOWS` in `runner.py` with a generated sequence.

### Specification

- **Window generator**: `generate_sliding_windows(start_month, end_month, oos_cutoff)`
- **Training**: rolling lookback matching `consistency_months` (6, 9, 12 months)
- **Holdout**: 1 calendar month forward from training end
- **Range**: holdout months from 2024-07 through 2025-12 (up to 18 windows)
- **OOS cutoff**: `OOS_CUTOFF = "2026-01-01"` — no holdout crosses this date
- **Labels**: `holdout_2024_07`, `holdout_2024_08`, etc.

### Impact on grid size

- Old: 5 windows × 27 pools × 4 delays × 225 sweep combos = 121,500 max
- New: up to 18 windows × 27 pools × 4 delays × 225 = ~437,400 max
- Mitigated by: early windows producing few bets → filtered by `min_bets`
- Expected survival: similar ~20% → ~87K configs (vs 22.6K current)

### Data structure

```python
@dataclass
class Window:
    name: str              # "holdout_2024_07"
    train_start: datetime
    train_end: datetime
    holdout_start: datetime
    holdout_end: datetime
```

The `consistency_months` parameter controls training lookback per pool config, but the holdout month is fixed per window. Training window = `holdout_start - timedelta(months=consistency_months)` to `holdout_start`.

---

## Enhancement 2: Hard OOS Lockout

### Specification

- **Constant**: `OOS_CUTOFF = datetime(2026, 1, 1)` in `runner.py`
- **Window generator** never produces holdout months at or after `OOS_CUTOFF`
- **New function**: `run_oos_evaluation(top_configs: list[dict], oos_window: Window) -> pl.DataFrame`
  - Takes top-N configs selected from the main sweep
  - Rebuilds signal tables and runs sweep for ONLY those configs on the OOS window
  - Returns metrics DataFrame tagged with `oos=True`
  - Outputs to `oos_results.parquet` (separate from `sweep_results.parquet`)
- **CLI flag**: `--oos` to trigger OOS evaluation after main sweep
- **Guard**: prints warning + requires `--oos` flag. Never runs automatically.

### OOS Window

```python
OOS_WINDOW = Window(
    name="oos_jan26",
    train_start=datetime(2025, 3, 1),   # flexible, matches best training lookback
    train_end=datetime(2026, 1, 1),
    holdout_start=datetime(2026, 1, 1),
    holdout_end=datetime(2026, 2, 1),
)
```

Training start for OOS adapts to each config's `consistency_months`.

---

## Enhancement 3: Excess Hit Rate

### Specification

**Per-window, direction-specific base rate:**

```python
def compute_base_rate(resolved_markets: pl.DataFrame, holdout_start, holdout_end, direction: str) -> float:
    holdout_markets = resolved_markets.filter(
        (pl.col("resolved_at") >= holdout_start) &
        (pl.col("resolved_at") < holdout_end)
    )
    yes_won_frac = holdout_markets["yes_won"].mean()

    if direction == "NO-only":
        return 1.0 - yes_won_frac    # fraction that resolved NO
    elif direction == "YES-only":
        return yes_won_frac           # fraction that resolved YES
    else:  # "both"
        return 0.5                    # random baseline
```

**New metrics columns** in sweep output:
- `base_rate`: direction-specific base rate for the holdout window
- `excess_hr`: `hit_rate - base_rate`

**Stability ranking change:**
- Primary sort: `avg_excess_hr` (descending)
- Secondary sort: `avg_sharpe` (descending)
- Keeps `avg_sharpe`, `avg_hr`, etc. for reference

---

## Enhancement 4: Separate Sizing Analysis

### Specification

**New function**: `run_sizing_analysis(top_configs, n_configs, sizing_strategies, windows)`

- **Input**: top-N configs from the fixed-size main sweep (selected by `avg_excess_hr`)
- **Sizing strategies**: `["fixed", "kelly", "edge_weighted", "agreement_weighted"]`
- **Process**:
  1. For each top config, rebuild signal table + forward prices (cached from main sweep)
  2. Re-run sweep with each sizing strategy
  3. Kelly `p` estimate: use **training window hit rate** (not holdout) to avoid lookahead
  4. Collect results into `sizing_results.parquet`

**Output columns** (in addition to standard metrics):
- `sizing_strategy`: which strategy was used
- `base_sizing_sharpe`: the Sharpe from the fixed-size run (for comparison)
- `sizing_improvement`: `sharpe - base_sizing_sharpe`

**CLI**: `--sizing-top N` flag to run sizing analysis on top-N configs after main sweep.

### Kelly p estimation

The existing `sizing.py` Kelly implementation uses `bets["won"].mean()` which is the **holdout hit rate** (lookahead). Fix:

```python
# In run_sizing_analysis:
training_hr = compute_training_hit_rate(config, training_window)
# Pass to apply_sizing as rolling_hit_rate parameter
sized = apply_sizing(bets, strategy="kelly", base_bet=100, fee_pct=0.02, hit_rate_prior=training_hr)
```

---

## Files Modified

| File | Changes |
|------|---------|
| `runner.py` | Window generator, OOS lockout, base rate computation, sizing analysis orchestration |
| `sweep.py` | Accept `base_rate` param, compute `excess_hr`, pass `hit_rate_prior` for sizing |
| `metrics.py` | Add `excess_hr`, `base_rate` to metrics dict |
| `sizing.py` | Accept external `hit_rate_prior` for Kelly (instead of computing from holdout) |
| `__main__.py` | New CLI flags: `--oos`, `--sizing-top N` |

No new files needed. All changes are additive to existing modules.

---

## Non-Goals

- Changing the signal table construction logic
- Adding new MVF bands or consistency parameters
- Modifying forward pricing or execution delay logic
- Insider detection (separate strategy)
