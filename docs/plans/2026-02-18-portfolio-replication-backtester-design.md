# Portfolio Replication Backtester — Design

**Date**: 2026-02-18
**Status**: Approved
**Context**: The consensus-copy backtester aggregates skilled traders into market-level directional signals, losing individual trader edge. This backtester measures each trader's actual holdout performance individually — directly validating insight #02's 87% win rate claim using the same rolling-window framework.

---

## Goal

Produce a per-trader, per-window evaluation of the consistency + MVF + entry-price pool. Two PnL measures per trader:

1. **Actual PnL**: The trader's real market_pnl on positions resolving in the holdout window
2. **Copy PnL**: Simulated $100 fixed bets at forward-priced entry (t+delay), matching live execution

## Architecture

New file `portfolio_runner.py` alongside existing `runner.py`. Shares all data loading, pool construction, and forward pricing helpers via imports. Dispatched from `__main__.py` with `--mode portfolio`.

## Data Flow

```
Same inputs as consensus backtester:
  trader_market_pnl.parquet
  maker_volume_fractions.parquet
  markets_resolved.parquet
  market_prices.parquet

For each window × pool_config:
  1. Build pool: get_consistent_traders() ∩ mvf_subset ∩ entry_filter
  2. For each trader in pool:
     a. Filter df_pnl to holdout window
     b. ACTUAL: sum market_pnl, count wins (market_pnl > 0)
     c. COPY: for each position, look up forward price at t+delay,
        compute $100 binary bet PnL, determine win from yes_won
  3. Emit one row per (trader, window, pool_config, delay)
```

## Output Schema

`strategies/consistency_copy/portfolio_results.parquet`

| Column | Type | Description |
|--------|------|-------------|
| trader | str | Trader address |
| window | str | Window name |
| is_test | bool | Test window flag |
| consistency_months | int | Pool param |
| min_markets | int | Pool param |
| mvf_band | str | Pool param |
| max_median_entry | float | Pool param |
| execution_delay | float | Delay for copy pricing |
| pool_size | int | Total traders in pool |
| actual_pnl | float | Trader's real PnL on holdout markets |
| actual_n_markets | int | Markets resolved in holdout |
| actual_wins | int | Markets with positive PnL |
| actual_win_rate | float | actual_wins / actual_n_markets |
| actual_volume | float | Sum of market_volume in holdout |
| copy_pnl | float | Simulated $100 fixed bets at forward price |
| copy_n_markets | int | Markets where forward price was available |
| copy_wins | int | Markets where copy bet won |
| copy_win_rate | float | copy_wins / copy_n_markets |
| median_entry | float | Trader's median directional entry price |
| mvf | float | Trader's maker volume fraction |

## Copy PnL Model

Per position (trader, market):
1. Direction: `net_yes_tokens > 0` → YES, else NO
2. Forward price: asof join at `first_trade + delay_s` on market_prices
3. Entry: YES → `yes_price`, NO → `1 - yes_price`
4. Outcome: from `yes_won` column (non-tautological)
5. PnL: won → `base_bet * (1 - entry) / entry`, lost → `-base_bet`
6. Fallback: trader's `wavg_yes_entry_price` when no forward price available

Fee = 0 (matching current sweep_config.toml).

## Shared Code (imported from runner.py)

- `_load_data()`
- `get_consistent_traders()`
- `_precompute_mvf_subsets()`
- `_compute_trader_median_entry()`
- `_precompute_entry_prices()`

## Sweep Dimensions

Uses same `sweep_config.toml`. Iterates:
- `consistency_months × min_markets × mvf_bands × max_median_entry_price` (pool)
- `execution_delays` (for copy PnL only; actual PnL is delay-independent)

Does NOT iterate over consensus-specific params (min_traders, agreement_pct, direction, price_bands, sizing).

## Files Modified/Created

| File | Change |
|------|--------|
| `backtester/portfolio_runner.py` | **New** — main loop, per-trader eval, copy PnL |
| `backtester/__main__.py` | Add `--mode` flag to dispatch consensus vs portfolio |
| `tests/test_portfolio_runner.py` | **New** — test actual PnL, copy PnL, pool filtering |

## Console Output

Per-window summary: pool size, % profitable (actual), median actual PnL, aggregate PnL.
Final: total rows written, path to parquet.
