# Informed Market-Making Estimate: Consistency Pool + Spread Capture

**Date**: 2026-02-19
**Type**: Analytical estimate script
**File**: `scripts/informed_mm_estimate.py`

---

## Motivation

The consistency-copy backtester (S1) identifies markets where 5+ pure_taker traders agree directionally (NO-only at 70%+ agreement). Currently this signal is executed as a taker (market order). Strategy #5 proposes executing as a market maker (limit order), capturing both the directional edge AND the bid-ask spread.

Unlike the S2 MM analysis (insights #18-19), which showed only 11% PnL uplift from MM execution on structurally-biased "Will" markets, the S1 signal is higher-conviction and applies across all market types. The hypothesis: spread capture matters more when directional accuracy is higher.

---

## Approach: Hybrid (Two Stages)

### Stage 1: Unconstrained Signal Sweep with MM Overlay

Load the existing signal table from `build_signal_table()`, apply forward pricing, then compute both taker and maker PnL for each qualifying signal fire. Sweep across signal configs × MM params.

**Signal parameters:**

| Parameter | Values |
|-----------|--------|
| `min_traders` | [5, 7, 10] |
| `agreement_pct` | [0.70, 0.80, 0.90, 1.00] |
| `direction` | ["NO-only"] |
| `mvf_band` | ["pure_taker"] |
| `execution_delay` | [30, 60] |
| `price_band` | [[0.05, 0.95], [0.10, 0.90]] |

**MM parameters:**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `spread_edge_c` | [0.5, 1.0, 1.5, 2.0] | Spread improvement in cents |
| `fill_model` | ["flat", "volume_derived"] | Fill probability model |
| `flat_fill_rate` | [0.25, 0.50, 0.75, 1.00] | Scenario brackets (flat model only) |

**PnL formulas:**

```
# Taker buys NO at no_price = 1 - yes_price
taker_pnl = bet * (1 - no_price) / no_price   if NO wins
           = -bet                                if YES wins

# Maker sells YES at (yes_price + spread_edge)
effective_no_cost = 1 - (yes_price + spread_edge)
maker_pnl = bet * (yes_price + spread_edge) / effective_no_cost   if NO wins
          = -bet                                                    if YES wins

# Fill-adjusted
maker_expected_pnl = fill_prob * maker_pnl
```

**Volume-derived fill model:** For each signal-fire market, measure historical YES-buy volume in the 60-300s window after the signal trigger. `fill_prob = min(1.0, yes_buy_volume_in_window / bet_size)`.

### Stage 2: Capital-Constrained Monthly Simulation

Take top 5 configs from Stage 1 (ranked by maker PnL/bet). Run month-by-month FIFO simulation.

**Capital assumptions:**
- Capital: $1,000
- Bet size: $100
- Max concurrent slots: 10
- Window: Jan 2025 – Jan 2026

**Monthly loop:**
1. Identify signal fires resolving in this month
2. Sort by trigger_time (FIFO)
3. For each fire: check slot availability (accounting for lockup)
4. MM variant: apply fill probability — unfilled orders don't consume capital
5. On resolution: release capital, collect PnL
6. Track: placed, filled, wins, PnL, cumulative equity, idle capital

---

## Data Inputs

All from existing derived tables (no new computation except YES-buy volume):

- `data/derived/trader_market_pnl.parquet`
- `data/derived/maker_volume_fractions.parquet`
- `data/derived/markets_resolved.parquet`
- `data/derived/market_prices.parquet`
- `data/metadata/markets.parquet`
- `data/compact/compact_*.parquet` (for YES-buy volume)
- `strategies/consistency_copy/sweep_config.toml`

**Cached intermediate:** `data/derived/yes_buy_volume.parquet` (per-market YES-buy volume stats). Rebuilt with `--force-volume` flag.

---

## Script Structure

```python
# scripts/informed_mm_estimate.py

load_signal_data()          # Load PnL, MVF, markets, price timeseries
build_signals()             # Call build_signal_table() + forward pricing
compute_yes_buy_volume()    # Scan compact trades, cache to derived/
stage1_sweep()              # Unconstrained: signal configs × MM params
stage2_sim()                # Capital-constrained on top-N configs
print_report()              # Console output
main()                      # Orchestrate
```

---

## Output

Console tables (no parquet output):

1. **Data summary** — signal fires, markets, date range
2. **Stage 1 sweep** — top configs ranked by maker PnL/bet, showing delta over taker
3. **Stage 2 monthly detail** — top 3 configs with monthly breakdown
4. **Three-way comparison** — informed MM vs informed taker (same signal) vs S2 uninformed MM
5. **Risk profile** — drawdown, losing months, variance

---

## Key Question Being Answered

> Does market-making execution meaningfully improve the economics of the consistency-copy strategy, or is the spread capture (~1-2c) as marginal for S1 as it was for S2 (only 11% uplift)?

If the answer is "marginal": stick with taker execution for simplicity.
If the answer is "meaningful": design the CLOB API limit-order execution layer.
