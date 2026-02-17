# Consensus Signal Backtester Design

**Date**: 2026-02-16
**Strategy**: consistency_copy — consensus signal from skilled trader pool

## Problem

We have a working signal: consistency-filtered traders' YES-side majority direction predicts market outcomes at ~70% HR across two holdout windows (Dec 2025, Jan 2026). We need to find optimal parameters and validate stability through a systematic parameter sweep with walk-forward validation.

## Architecture

### Core Insight: Signal Table

Pre-compute a "signal table" — one row per (market, Nth skilled trader arrival). This table captures the running consensus state at each trader entry. All parameter sweeps become pure filter+aggregate operations on this table.

```
signal_table schema:
  condition_id        — market ID
  arrival_idx         — Nth skilled trader to enter (1, 2, 3, ...)
  trigger_time        — first_trade timestamp of the Nth trader
  resolved_at         — market resolution timestamp
  resolution_value    — 0 (NO) or 1 (YES)
  n_traders           — cumulative skilled traders at this point
  n_yes / n_no        — cumulative direction counts
  agreement_frac      — max(n_yes, n_no) / n_traders
  signal_direction    — YES or NO (current majority)
  trigger_entry_price — wavg_yes_entry_price of the triggering trader
  avg_pool_entry      — running avg entry of majority-side traders
  trader              — the triggering trader address
  mvf                 — maker volume fraction of the triggering trader
```

### Entry Price Model

Use the entry price of the Nth skilled trader (the one who triggers the threshold) as our entry price proxy. This is more realistic than averaging all trader entries, since it approximates the market price at signal time.

### Parameter Grid

| Dimension | Values | Count |
|-----------|--------|-------|
| min_traders | 2, 3, 5, 7, 10, 15, 20, 30, 50 | 9 |
| agreement_pct | 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 0.90 | 7 |
| direction | YES-only, NO-only, both | 3 |
| entry_price_band | (0.05, 0.95), (0.10, 0.90), (0.15, 0.85), (0.20, 0.80) | 4 |
| consistency_months | 3, 4, 5, 6 | 4 |
| min_markets | 10, 20, 30, 50 | 4 |
| mvf_band | all, pure_taker(<0.10), mixed(0.10-0.50), maker_dominant(>0.50) | 4 |

Signal combos: 9 x 7 x 3 x 4 x 4 x 4 x 4 = 48,384

### Bet Sizing (Post-processing)

Applied after signal filtering on each config's bet series:

| Strategy | Formula |
|----------|---------|
| Fixed | $1 per bet (baseline) |
| Kelly | f* = (p*b - q) / b, rolling HR as p, capped at 0.25 |
| Agreement-weighted | base x (agreement_frac - 0.5) x 2 |
| Edge-weighted | base x max(0, HR x payoff_ratio - 1) |

4 sizing strategies per signal config = ~193K total configurations.

### Walk-Forward Validation

Three rolling windows:
- **Win0**: train Aug-Nov 2025, holdout Dec 2025 (31 days)
- **Win1**: train Sep-Dec 2025, holdout Jan 2026 (31 days)
- **Win2**: train Oct 2025-Jan 2026, holdout Feb 2026 (13 days, partial)

A config is "stable" if it ranks in the top-50 for ALL windows where it has sufficient bets (>= 20).

### P&L Curve Metrics

For each config per window:
- Daily P&L series (grouped by resolved_at date)
- Cumulative equity curve
- Sharpe ratio (annualized from daily)
- Max drawdown (% and absolute)
- Profit factor (gross wins / gross losses)
- Win/loss streaks
- Weekly hit rate consistency

### Output Files

- `strategies/consistency_copy/sweep_results.parquet` — all configs x windows, summary stats
- `strategies/consistency_copy/top_configs.json` — stable top configs with full metrics
- `strategies/consistency_copy/equity_curves/` — daily P&L parquet per window for top configs

## Module Structure

```
strategies/consistency_copy/backtester/
  __init__.py
  signal_table.py    — build_signal_table(df_pnl, skilled_traders, mvf_data)
  sweep.py           — run_sweep(signal_table, param_grid) -> results DataFrame
  metrics.py         — compute_metrics(bets_df) -> dict of Sharpe, drawdown, etc.
  sizing.py          — apply_sizing(bets_df, strategy) -> sized P&L series
  runner.py          — CLI: load cached data, build tables per window, sweep, save
```

## Data Dependencies

All from existing `scripts/_cache/`:
- `trader_market_pnl.parquet` (17.6M rows) — per-trader per-market PnL with timestamps
- `maker_volume_fractions.parquet` (1.07M rows) — MVF per trader
- `markets_resolved.parquet` — resolution dates

No ClickHouse queries needed during sweep.

## Performance Estimate

- Signal table build: ~30s per window (sort + cumulative agg over 4M rows)
- Sweep per window: ~48K configs x ~1ms each = ~50s
- Bet sizing: ~4x overhead on top configs = trivial
- Total: ~5 minutes for full sweep across 3 windows
