# Strategy Research Idea Backlog

## Queued

- [ ] **Maker volume fraction as signal** — traders with high MVF (limit orders) may be more informed
  - Source: data/derived/maker_volume_fractions.parquet exists, unexplored
  - Priority: HIGH
  - Compounding angle: MVF is computable per-trade, no hold-time dependency
  - Data: pure takers (MVF<0.1) = 25.8% HR; makers-who-take (MVF 0.5-0.9) ~45% HR

- [ ] **Consensus velocity** — speed at which qualified traders converge on a side
  - Source: consensus threshold is static, timing might carry signal
  - Priority: MEDIUM
  - Compounding angle: fast consensus → short hold time → faster recycling
  - Related: pitfalls/consensus_dedup.md

- [ ] **Category-specialized ensembles** — separate models per category, combine
  - Source: category breakdown shows very different dynamics (hold time, HR, volume)
  - Priority: MEDIUM
  - Compounding angle: sports/esports sub-models recycle in <1 day
  - Related: execution/hold_time_capital.md

- [ ] **Exit signal from trader reversals** — qualified traders selling = informative exit signal
  - Source: pitfalls/sell_is_exit.md — SELL is exit, but IS it predictive?
  - Priority: HIGH
  - Compounding angle: early exits free capital faster (shorter hold)
  - Related: pitfalls/sell_is_exit.md

- [ ] **Price momentum at consensus** — entry price trajectory when consensus forms
  - Source: entry price filter was dominant in prior research, momentum might refine it
  - Priority: LOW
  - Compounding angle: unclear, needs exploration

- [ ] **Sports-specific insider filter** — sports has 76% HR but NEGATIVE PnL at strict tier
  - Source: S2 discovery shows sports edge consumed by spread/slippage
  - Priority: MEDIUM
  - Compounding angle: 1-day median hold, high throughput IF edge can be recovered
  - Spawned from: S2 insider discovery category analysis
  - Related: signals/insider_copy.md

- [ ] **Bot-as-signal** — 534+ late-betting bots (99%+ HR) detect resolution before market
  - Source: S2 discovery -- bots buy at $0.99+ on near-certain outcomes
  - Priority: LOW (likely too late for profitable copy, but worth quantifying timing)
  - Compounding angle: if bots act 5-60s before resolution, there may be a tiny window
  - Spawned from: S2 insider discovery HR distribution analysis

- [ ] **Insider x MVF interaction** — combine insider HR filter with maker volume fraction
  - Source: MVF 0.5-0.9 already ~45% HR. Intersect with train_hr >= 0.65 for double filter
  - Priority: HIGH
  - Compounding angle: MVF is per-trade feature, insider is per-trader feature -- orthogonal
  - Related: data/derived/maker_volume_fractions.parquet

- [ ] **Data-driven insider scoring** — replace equal-weight 6-feature composite with F1+F6 only
  - Source: S2 enhancement 2 found F1 (HR excess) = 0.213 corr, F6 (susceptibility) = 0.072 corr. F3/F5 = zero.
  - Priority: MEDIUM
  - Compounding angle: better scoring -> smaller pool -> less capital needed
  - Spawned from: S2 feature weight optimization (2026-03-02)
  - Related: signals/insider_copy.md

- [ ] **Early-market insider sub-strategy** — focus on insiders entering markets with < $100 cumulative volume
  - Source: S2 enhancement 3 found median vol-at-entry = $33. Insiders ARE the early market-makers.
  - Priority: LOW (high execution risk: illiquid markets, wide spreads)
  - Compounding angle: early entry = longer hold time but larger potential payout
  - Spawned from: S2 volume-at-entry analysis (2026-03-02)
  - Related: signals/insider_entry_characteristics.md

- [ ] **High-liquidity insider sub-strategy** — only copy insiders in markets with >= $5K volume at entry
  - Source: S2 vol >= $5K: +$183/pos but only 13K positions (vs $45/pos for all 388K)
  - Priority: LOW (too few positions for reliable signal, but per-trade edge is very high)
  - Compounding angle: liquid markets = better execution, less slippage
  - Spawned from: S2 volume-at-entry analysis (2026-03-02)
  - Related: signals/insider_entry_characteristics.md

## In Progress

### S2: Insider Copy (HIGH priority) -- TICK-BY-TICK VALIDATED

**Hypothesis**: Some traders exhibit "insider knowledge" -- infrequent, high-conviction,
high-accuracy bets on susceptible markets. Copy their BUY trades.

**Status**: TICK-BY-TICK VALIDATED (2026-03-02). All 8 tested configurations profitable. Ready for production implementation.
**Design doc**: `docs/plans/2026-03-02-insider-copy-strategy-design.md`
**Knowledge entry**: `research/knowledge/signals/insider_copy.md`
**Validation script**: `research/scripts/s2_tick_validation.py`

**Tick-by-tick results (3 OOS months: Jul-25, Oct-25, Jan-26):**

| Config | HR | PnL Net | Compounding | Assessment |
|--------|-----|---------|-------------|------------|
| C>=3, p<0.65 | 57.3% | $783,828 | 12.37 | Best PnL, EXCELLENT |
| C>=3, no filter | 64.4% | $131,348 | 3.11 | Best HR, MODERATE |
| C>=2, no filter | 66.8% | $253,759 | 7.01 | EXCELLENT |
| C>=5, no filter | 65.0% | $193,301 | 4.76 | MODERATE |
| C>=1, no filter | 65.9% | $135,474 | 3.90 | MODERATE |

**Vectorized vs tick-by-tick gap**: 18-29pp, WITHIN expected 20-40pp range.

**Key tick-by-tick findings**:
- Entry price filter INVERTS: helps vectorized (+2.7pp) but hurts tick-by-tick (-7pp HR, +6x PnL)
- Capital constraint is primary bottleneck: ~130 fills/month from 28K-60K signals
- NO direction dominates (95%+ of fills); YES gets only 2-7 positions/month
- Avg hold 25 days; long-dated markets block capital recycling
- New knowledge entry: `pitfalls/entry_price_filter_inversion.md`

**Recommended deployment configs**:
- **Max compounding**: C>=3, p<0.65, compounding=12.37 (high PnL per position)
- **Max stability**: C>=2, no filter, HR=66.8%, compounding=7.01
- Capital: $5K+, 50 max positions, $50/position

**Next**: Implement in `strategies_impl/`, paper trading validation

### S2: Hit-Rate Copy -- Tiered Conviction (HIGH priority)

**Hypothesis**: Traders with excess hit rate above direction-specific base rate are skilled.
Copying their entries with tiered conviction (seed + scale) builds an edge.

**Status**: Two critical bugs FIXED (2026-03-02). Re-estimation COMPLETE. Awaiting tick-by-tick.
**Design doc**: `docs/plans/2026-03-02-s2-hitrate-copy-design.md`
**Workbench**: `research/notebooks/s2_workbench.py`
**Estimation notebook**: `research/notebooks/s2_estimation.py`
**Improvement comparison**: `research/notebooks/s2_improvement_comparison.py`
**Fixed estimation**: `research/notebooks/s2_fixed_estimation.py`

**Key parameters**: min_excess_hr, scale_threshold, direction, seed_timeout_hours

**Bugs fixed (2026-03-02)**:
1. `max_entry_price` 0.85 -> 0.95: old value removed 83% of the pool (354 vs 1,161 traders)
2. `exclude_categories` now uses tag-based joins: old `m.category` caught 0% of positions;
   new tag chain catches 38-53% of sports/weather/games positions
3. Bayesian HR was already implemented (pre-existing fix)

**Entry price sweep (with fixes)**:
| max_ep | Pool | NO Contam | Mean Excess |
|--------|------|-----------|-------------|
| 0.85   |  354 | 0%        | 18.1pp      |
| 0.93   |  857 | 0%        | 19.6pp      |
| **0.95** | **1,161** | **0%** | **20.4pp** |
| 0.97   | 1,573 | 0.5%     | 21.3pp      |
| 0.99   | 2,498 | 1.0%     | 23.3pp      |
Best: 0.95 (largest clean pool, zero contamination)

**Category exclusion effectiveness (tag-based)**:
| Period | Positions excluded (tag) | Positions excluded (old m.category) |
|--------|--------------------------|-------------------------------------|
| Apr 25 | 469K (38.4%)            | 0 (0.0%)                            |
| Jul 25 | 477K (44.6%)            | 0 (0.0%)                            |
| Oct 25 | 1.78M (52.7%)           | 0 (0.0%)                            |

**Vectorized results -- FIXED (Bayesian + tag excl + entry 0.95, UPPER BOUNDS)**:

Walk-forward OOS at consensus >= 3:
| Period | Pool | N     | HR    | YES HR | NO HR | Total PnL | Avg PnL | Comp Score |
|--------|------|-------|-------|--------|-------|-----------|---------|------------|
| Apr 25 | 410  | 8,512 | 82.9% | 73.6% | 89.1% | $2.05M   | $241    | 9.96       |
| Jul 25 | 375  | 14,532| 84.2% | 73.5% | 87.9% | $2.11M   | $145    | 6.75       |
| Oct 25 | 462  | 15,227| 85.5% | 77.4% | 89.5% | $2.37M   | $155    | 11.46      |

Walk-forward OOS at consensus >= 4:
| Period | Pool | N     | HR    | YES HR | NO HR | Total PnL | Avg PnL |
|--------|------|-------|-------|--------|-------|-----------|---------|
| Apr 25 | 410  | 6,511 | 84.7% | 76.8% | 89.9% | $2.02M   | $311    |
| Jul 25 | 375  | 11,490| 85.8% | 78.1% | 88.2% | $1.99M   | $173    |
| Oct 25 | 462  | 11,483| 86.6% | 79.9% | 89.8% | $2.22M   | $193    |

**Comparison: BEFORE (unfixed) vs FIXED at consensus >= 4**:
| Period | BEFORE HR | BEFORE Avg PnL | FIXED HR | FIXED Avg PnL | HR Delta | PnL/pos Delta |
|--------|-----------|----------------|----------|---------------|----------|---------------|
| Apr 25 | 82.9%     | $88            | 84.7%    | $311          | +1.8pp   | +$223 (+254%) |
| Jul 25 | 81.4%     | $181           | 85.8%    | $173          | +4.4pp   | -$8 (-5%)     |
| Oct 25 | 81.7%     | $115           | 86.6%    | $193          | +4.9pp   | +$78 (+68%)   |

The FIXED variant has higher HR across all periods and 2-3x higher PnL per position
(except Jul which is comparable). The smaller, cleaner universe concentrates edge.

**Realistic range after 20-40pp tick-by-tick degradation**:
| Period | Vectorized HR | Realistic HR Range |
|--------|--------------|-------------------|
| Apr 25 | 82.9%        | 42.9% - 62.9%     |
| Jul 25 | 84.2%        | 44.2% - 64.2%     |
| Oct 25 | 85.5%        | 45.5% - 65.5%     |

**Compounding scores (consensus >= 3)**: 9.96 (Apr), 6.75 (Jul), 11.46 (Oct) -- all excellent (>5.0)

**Recommended tick-by-tick config (FINAL)**:
- `use_bayesian_hr=True` -- eliminates NO contamination
- `max_entry_price=0.95` -- FIXED (was 0.85, removed 83% of pool)
- `exclude_categories=("Sports", "Weather")` -- FIXED (now tag-based, catches 38-53%)
- `direction=BOTH` -- Bayesian handles contamination
- `min_positions=30`, `min_excess_hr=0.10`
- `scale_threshold=3` (consensus >= 3 for best compounding)
- `seed_threshold=1`
- `max_hold_hours=168` (7 days) -- test in tick-by-tick

**Risks**:
- NO direction may still collapse in tick-by-tick (S1 finding: 82% -> 34%)
- Consensus dedup critical (72.6% inflation if trades not unique traders)
- Monthly base rate variance can flip PnL negative even at high HR

**Next**: Tick-by-tick validation with FIXED config

## Tested

(none — clean slate)

## Parked

(none)
