# S2 Hit-Rate Copy Strategy Design

**Date**: 2026-03-02
**Status**: Approved
**Hypothesis**: Traders with high hit rate (excess over direction-specific base rate) at resolution time are skilled. Copying their entries builds an edge.

## Core Concept

Hit rate is not PnL — it measures how often a trader is correct at resolution. A trader who bets NO and wins 62% is at base rate (NO wins 62% naturally). True skill = excess hit rate above the direction-specific base rate.

## Approach: Tiered Conviction

Two-tier entry system that captures early prices while confirming with consensus:

- **Seed** (1-2 unique qualified traders on same side): enter small position (seed_pct of max)
- **Scale** (3-4+ unique qualified traders): scale to full position size
- **Timeout**: if seed doesn't reach scale within configurable window, evaluate exit (parameter to sweep: exit vs hold)

### Qualified Trader Pool

Pre-computed from `trader_positions_resolved`. Refreshed periodically.

Qualification criteria (all sweepable):
- `min_positions`: minimum resolved trades (sweep: 20, 30, 50, 100)
- `min_excess_hr`: minimum excess hit rate above direction-specific base rate (sweep: 5pp, 10pp, 15pp, 20pp)
- `recency_months`: lookback window (sweep: 3, 6, 12)

Base rates: YES = 38.1%, NO = 61.9%.

### On-Trade Logic

```
on_trade(trade):
  1. if side != "BUY": return None          # SELL is exit, not signal
  2. if maker not in qualified_pool: return  # not a skilled trader
  3. consensus[cid][dir].add(maker)          # track unique traders per side
  4. count = len(consensus[cid][dir])
  5. if count >= scale_threshold AND no full position yet:
       return TradeIntent(size = max_position_usd)
  6. elif count >= seed_threshold AND no seed position yet:
       return TradeIntent(size = max_position_usd * seed_pct)
  7. check_seed_timeouts() → exit intents for stale seeds
```

### Full Parameter Space

| Parameter | Description | Sweep Range |
|-----------|-------------|-------------|
| `min_positions` | Min resolved trades for qualification | 20, 30, 50, 100 |
| `min_excess_hr` | Min excess HR above base | 5pp, 10pp, 15pp, 20pp |
| `seed_threshold` | Unique traders for seed entry | 1, 2 |
| `scale_threshold` | Unique traders for full entry | 3, 4, 5 |
| `seed_pct` | Seed size as % of max | 0.25, 0.50 |
| `seed_timeout_hours` | Hours before stale seed action | 24, 72, 168, None |
| `recency_months` | Qualification lookback | 3, 6, 12 |
| `direction` | Which side to copy | YES, NO, BOTH |
| `max_position_usd` | Per-position cap | 50, 100, 200 |
| `max_open_positions` | Concurrent position limit | 10, 20, 50 |
| `capital_usd` | Total capital | 1000, 2500, 5000 |

## Architecture

### Components

```
configs/s2_hitrate_copy.toml           # Strategy + provider config
research/strategies/s2_hitrate_copy.py # Research strategy (single file)
research/notebooks/s2_workbench.py     # Marimo interactive workbench
src/.../strategies_impl/s2_hitrate_copy/
    ├── __init__.py                     # Registry entry (production)
    ├── provider.py                     # FeatureProvider
    └── strategy.py                     # Strategy
```

### Data Flow

```
                     ┌─────────────────────────────────────────┐
                     │            FeatureProvider               │
                     │                                         │
CH: trader_positions │  compute() ──> qualified_pool: set[str] │
    _resolved        │  refresh() ──> re-query pool            │
                     │  on_trade()──> consensus[cid][dir].add()│
                     │               + seed_timestamps[cid]    │
                     └──────────────────┬──────────────────────┘
                                        │ context.features
                                        ▼
                     ┌──────────────────────────────────────────┐
                     │              Strategy                     │
trades_raw ─────────>│  on_trade():                              │
 (tick stream)       │    skip if side != BUY                    │
                     │    skip if maker not in qualified_pool    │
                     │    consensus_count = len(consensus[cid])  │
                     │    if count >= scale: full position       │
                     │    elif count >= seed: seed position      │
                     │    check seed timeouts → exit intents     │
                     └──────────────────┬───────────────────────┘
                                        │ list[TradeIntent]
                                        ▼
                     ┌──────────────────────────────────────────┐
                     │         ExecutionGateway                  │
                     │  quality gate → budget gate → executor    │
                     │  (RealisticFillSimulator for replay)      │
                     └──────────────────────────────────────────┘
```

## Interactive Research Workbench

A marimo notebook with two modes:

### Mode 1: Manual Explore

Parameter sliders/dropdowns + "Run" button. Runs tick-by-tick replay across selected periods. Shows:
- Per-period breakdown (excess HR, Sharpe, trades, edge, compounding score)
- Aggregate with std dev across periods
- Equity curve (per-period overlay)
- Category breakdown (per-category excess HR, hold time)
- Consistency check (how many periods profitable, excess HR > threshold, Sharpe > floor)

### Mode 2: Auto-Sweep

Define parameter grid (check which params to sweep), hit "Sweep". Runs tick-by-tick for every combination across all periods.

Output:
- Sortable results table ranked by compounding score
- Sensitivity heatmaps (e.g., excess_hr vs scale_threshold, colored by compounding)
- Click any row to load config into manual mode for drill-down

### Speed Optimizations

1. Pre-filter trades in CH — only fetch trades from qualified makers (11x speedup)
2. Cache qualified pool — recompute only when qualification params change
3. Parallel period replay — multiprocessing pool, one per config-period combo
4. Pre-load compact trades at notebook startup, slice by period

## Robustness Metrics

| Metric | Why |
|--------|-----|
| `excess_hr` | True signal above direction-specific base rate |
| `avg_edge_usd` | Expected profit per trade after slippage |
| `sharpe` | Risk-adjusted return |
| `max_drawdown` | Worst peak-to-trough |
| `median_hold_days` | Capital efficiency |
| `compounding_score` | `excess_hr * avg_edge / median_hold` |
| `fill_rate` | % of intents that fill |
| `consistency` | # of periods profitable / total periods |
| `stability` | Std dev of excess HR across periods |
| `worst_period` | Floor performance |

## Success Criteria

- Excess HR > 10pp above direction-specific base rate (tick-by-tick)
- Sharpe > 1.0 after slippage
- Positive expected value per trade (avg edge > 0)
- Consistent across 4+ of 5 test periods
- Reasonable capital efficiency (median hold < 7 days, or category-filtered)

## Critical Pitfalls (Must Implement)

1. **Consensus dedup**: count unique traders, not trade events (72.6% inflation otherwise)
2. **SELL filtering**: `side != "BUY"` → skip (SELL is exit, not signal)
3. **Resolution**: asset_id-based `token_won`, never string matching
4. **Settlement**: use ReplayRunner (settles mid-run), never BacktestRunner for capital-constrained
5. **Base rate adjustment**: always report excess HR, not raw HR
6. **Vectorized gap**: expect 20-40pp degradation from vectorized to tick-by-tick

## Extension Path

The tiered conviction architecture naturally extends to:
- **Composite scoring**: weight traders by excess HR, volume, recency (Approach C upgrade)
- **Category specialization**: separate models per category (sports vs politics)
- **Exit signals**: qualified traders selling → early exit
- **MVF integration**: maker volume fraction as additional trader quality signal
