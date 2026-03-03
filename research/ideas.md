# Strategy Research Idea Backlog

## Queued

- [x] **Tag-aware hit-rate copy** — REJECTED (2026-03-03, tick-by-tick validated)
  - Result: Tag-aware mode WORSE than global pool (46.7% HR vs 50.6%, -$6,486 vs -$5,835)
  - Tag-aware pool 2.1x larger (3,433 vs 1,634 traders) but 85% YES-direction
  - Crypto YES is only tag with meaningful signal (51.3% HR, +22.4pp excess)
  - Extreme-NO-bias tags are terrible for YES: Culture 12.9%, Politics 3.0%
  - Vectorized-to-tick gap smaller (11pp vs 34pp) because vectorized was already lower (57.8% vs 84.2%)
  - Root cause: beating tag-specific base (9-29% YES) is too easy; aggregate HR != trade-level HR
  - Notebook: research/notebooks/s2_tag_aware_estimation.py
  - Script: research/scripts/s2_tag_aware_tick_validation.py
  - Related: signals/tag_edge_analysis.md, data/tag_base_rates.md

- [ ] **NO mispricing in extreme-bias tags** — NO traders in Culture/Music/Movies/Elections
  have NEGATIVE excess HR but POSITIVE PnL (market overprices NO outcome)
  - Source: tag_edge_analysis.md — NO paradox section
  - Priority: HIGH
  - Culture NO: -9.9pp excess but +$12.44/pos ($1.1M aggregate from 112 traders)
  - Elections NO: -18.4pp excess but +$23.81/pos
  - Approach: buy NO in extreme-bias tags where market price > true probability
  - Warning: need to distinguish market mispricing from spread capture

- [ ] **Earnings YES niche strategy** — 73% YES base rate, sweet spot at 0.50-0.70 entry
  - Source: earnings_naive_edge.sql — 55% HR, +$3-5/pos at 0.50-0.70 entry prices
  - Priority: MEDIUM
  - Very small universe (612 markets) but fast resolution (1-3 days)
  - Compounding: high throughput if enough volume

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

- [x] **Category-specialized ensembles** — COMPLETED as per-tag parameter tuning (2026-03-02)
  - Result: Per-tag tuning shows 6% direct compounding improvement + crypto/esports exclusion
  - Sports is 10x more capital-efficient than politics (comp=1867 vs 181)
  - Knowledge: signals/insider_tag_tuning.md
  - Notebook: research/notebooks/S2_tag_tuning.py
  - SPAWNED: tick-by-tick validation of per-tag configs (below)

- [ ] **Exit signal from trader reversals** — qualified traders selling = informative exit signal
  - Source: pitfalls/sell_is_exit.md — SELL is exit, but IS it predictive?
  - Priority: HIGH
  - Compounding angle: early exits free capital faster (shorter hold)
  - Related: pitfalls/sell_is_exit.md

- [ ] **Price momentum at consensus** — entry price trajectory when consensus forms
  - Source: entry price filter was dominant in prior research, momentum might refine it
  - Priority: LOW
  - Compounding angle: unclear, needs exploration

- [x] **Sports-specific insider filter** — RESOLVED: sports is POSITIVE PnL at cons>=3-5 (2026-03-02)
  - Result: Sports cons>=4 = 78.4% HR, +$78.69/pos, comp=1,867 (10x politics)
  - Previous negative PnL finding was from a single training window; walk-forward shows positive
  - 1-day median hold enables fastest capital recycling of any category
  - Knowledge: signals/insider_tag_tuning.md

- [x] **Per-tag tick-by-tick validation** — COMPLETED (2026-03-02)
  - Result: Sports is the standout (74.3% HR, +13.5pp NO excess, -4.1pp vec gap)
  - Politics/culture/other/weather profitable but negative excess HR (base-rate artifact)
  - Crypto: confirmed NO-GO (55.7% HR, -20.5pp NO excess)
  - Esports: marginal (54.3% HR, near-zero PnL)
  - Entry price filter confirmed suboptimal for ALL categories in tick-by-tick
  - Hold times much longer than vectorized estimated (8-33d vs 1-7d)
  - Knowledge: signals/insider_tag_tuning.md
  - Script: research/scripts/s2_tick_tag_validation.py
  - Output: research/output/s2_tick_tag/per_tag_all.parquet

- [ ] **Sports-only insider deployment** — implement sports C>=4 insider copy in strategies_impl/
  - Source: per-tag tick validation shows sports is ONLY category with genuine positive excess HR (+13.5pp)
  - Priority: HIGHEST
  - Compounding angle: 74.3% HR, $391/pos, 8d median hold -> deploy as first per-tag strategy
  - Related: signals/insider_tag_tuning.md
  - Spawned from: per-tag tick-by-tick validation (2026-03-02)

- [ ] **Asymmetric payoff thesis** — investigate whether positive PnL with negative excess HR is sustainable
  - Source: per-tag tick validation found most categories have NEGATIVE excess HR but POSITIVE PnL
  - The PnL comes from very-low-price entries creating asymmetric payoffs, not from predicting outcomes
  - Priority: MEDIUM
  - Question: is this a reliable edge or a survivorship artifact from 3 test months?
  - Compounding angle: if sustainable, entry price is the real signal, not insider accuracy
  - Spawned from: per-tag tick-by-tick validation (2026-03-02)

- [ ] **Hold time discrepancy investigation** — vectorized estimates 1-7d median holds, tick shows 8-33d
  - Source: per-tag tick validation found hold times 5-10x longer than vectorized predicted
  - Priority: LOW
  - Root cause likely: vectorized counts only resolved positions (selection bias toward faster resolution)
  - Impact: compounding scores from vectorized are 5-10x too optimistic
  - Spawned from: per-tag tick-by-tick validation (2026-03-02)

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

- [x] **Direction-aware hit-rate consensus** — TESTED as part of gap fix validation (2026-03-03)
  - Result: Direction-aware filtering IMPLEMENTED and validated in gap fix bundle
  - See "S2: Hit-Rate Copy Gap Fixes" in Tested section below
  - Source: S2 HRC tick validation found +7pp HR from same-direction consensus vs naive
  - Spawned from: S2 HRC tick-by-tick validation (2026-03-02)

- [ ] **YES-only hit-rate copy** — ignore NO direction entirely in S2 HRC
  - Source: S2 HRC tick validation found YES has +7 to +18pp excess HR; NO is BELOW base
  - Priority: LOW (43-46% absolute HR is unprofitable despite excess)
  - Compounding angle: unclear, YES-only reduces fill count by ~50-60%
  - Spawned from: S2 HRC tick-by-tick validation (2026-03-02)

- [ ] **Hit-rate x Insider composite** — use hit-rate filter as secondary on insider pool
  - Source: Insider copy shows 57-67% HR tick-by-tick. Hit-rate filter could narrow pool
  - Priority: MEDIUM
  - Compounding angle: tighter pool -> higher consensus signal quality
  - Related: signals/insider_copy.md

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

**Status**: GAP FIXES VALIDATED (2026-03-03). All 3 fixes tested. Marginal improvement. Strategy remains UNPROFITABLE. SEE TESTED BELOW.
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

**Next**: Strategy REJECTED. See Tested section for full gap fix results.

## Tested

### S2: Hit-Rate Copy Gap Fixes -- REJECTED (2026-03-03)

**Hypothesis**: Three targeted fixes address the 34pp vectorized-to-tick gap:
  1. Position-level dedup (1 signal per trader per market) -- addresses 23pp dilution
  2. Consensus cap (max_consensus=5) -- addresses -3pp anti-predictive consensus
  3. Direction-aware filtering -- addresses -5pp cross-direction contamination

**Result**: Marginal HR improvement (+2-7pp), but strategy remains UNPROFITABLE.
Gap reduced from 34pp to 30-37pp. PnL improved for Apr only; Jul/Oct negative.
C>=4 is the ONLY config with aggregate positive PnL (+$2,275 total).

**Tick-by-tick results (walk-forward, all 3 fixes ON):**

| Period | Vec HR | BEFORE HR | AFTER HR | Delta | BEFORE PnL | AFTER PnL | Fills |
|--------|--------|-----------|----------|-------|------------|-----------|-------|
| Apr 25 | 82.9% | 45.9% | 53.3% | +7.4pp | -$6,417 | +$185 | 482 |
| Jul 25 | 84.2% | 50.6% | 52.9% | +2.3pp | -$5,835 | -$1,451 | 1,104 |
| Oct 25 | 85.5% | 46.1% | 48.5% | +2.4pp | +$2,385 | -$5,919 | 1,098 |

**C>=4 with all fixes (best config found):**

| Period | HR | PnL | Fills | Sharpe | Hold |
|--------|-----|-----|-------|--------|------|
| Apr 25 | 56.7% | +$5,905 | 239 | +0.43 | 18.5d |
| Jul 25 | 55.5% | -$5,021 | 647 | -0.63 | 14.4d |
| Oct 25 | 52.9% | +$1,391 | 589 | +0.14 | 14.2d |
| **Total** | **54.7%** | **+$2,275** | **1,475** | **-0.02** | |

**Fix isolation analysis:**
- **Dedup (dedup_per_position)**: NEGATIVE impact. Dedup REDUCES HR by 2-4pp and hurts PnL.
  No-dedup variant: 53.8% avg HR vs all_fixes 51.2%. Dedup removes fills that would
  have been correct, because multiple trades from the same trader ARE informative
  (subsequent trades indicate ongoing conviction).
- **Consensus cap (max_consensus=5)**: ZERO impact. Cap=5 and no-cap produce identical
  results across all 3 periods. Consensus rarely reaches 5+ in practice with
  direction-aware filtering (pool is too small).
- **Direction-aware filtering**: This is the SOLE driver of the improvement. Restricting
  traders to their qualified direction produces the +2-7pp HR lift vs BEFORE.

**Why Oct 25 is worse with all fixes (-$5,919 vs +$2,385 BEFORE):**
Oct 25 has 401 traders (largest pool) entering 242 markets, but 48.5% HR is BELOW
50% base. The direction filter reduces NO fills (fewer cross-direction trades) but
the remaining NO fills still collapse below base. More markets entered = more
exposure to NO collapse.

**Cross-period averages:**

| Config | Avg HR | Total PnL | Total Fills | Avg Sharpe |
|--------|--------|-----------|-------------|------------|
| all_fixes (C>=3) | 51.2% | -$7,185 | 2,684 | -0.11 |
| no_dedup (C>=3) | 53.8% | -$6,231 | 2,939 | -0.09 |
| c4_fixes (C>=4) | 54.7% | +$2,275 | 1,475 | -0.02 |
| no_cap (C>=3) | 51.2% | -$7,185 | 2,684 | -0.11 |

**Key learnings:**
1. Position-level dedup is COUNTERPRODUCTIVE: multiple trades from same trader
   in same market are informative (ongoing conviction), not dilution
2. Consensus cap has zero effect with direction-aware pools (too small to reach 5+)
3. Direction-aware filtering is the only fix that helps (+2-7pp)
4. Even with all fixes, NO direction still collapses in tick-by-tick (structural)
5. C>=4 is marginally profitable but inconsistent across periods (1 of 3 negative)
6. on_timer() now fires correctly (max_hold_hours=168h) but causes oversell
   warnings when trying to sell already-settled positions (harmless)
7. Hold times are 13-18 days (consistent with prior finding of vectorized underestimate)
8. All rejections are position_limit (281-461 per period), not capital

**Verdict**: Strategy remains UNPROFITABLE at C>=3. C>=4 is marginal (+$2,275 total,
negative Sharpe). The vectorized-to-tick gap is 30-37pp (still WITHIN 20-40pp range).
The fundamental issue remains: trade-level copying cannot capture position-level edge.

**Compounding scores**: all near zero or negative. Not viable for capital recycling.

**Recommendation**: ABANDON hit-rate copy strategy. The position-to-trade dilution
(Step 2 in vectorized_tick_gap_anatomy.md) is structural and unfixable. Focus on
insider copy strategy which uses position-level signals (infrequent, high-conviction).

**Script**: `research/scripts/s2_hitrate_gapfix_validation.py`
**Output**: `research/output/s2_tick_hitrate_gapfix/gapfix_validation_results.json`

### S2: Tag-Aware Hit-Rate Copy -- REJECTED (2026-03-03)

**Hypothesis**: Using tag-specific base rates (9%-73% YES) instead of global
(38%/62%) to qualify traders, with direction-specific consensus, should identify
more genuinely skilled traders and avoid NO direction collapse.

**Result**: WORSE than global pool. Tag-aware pool is too permissive and produces
mostly YES signals that underperform.

**Tick-by-tick results (Jul 2025 OOS, consensus >= 3):**

| Variant | Vec HR | Tick HR | Gap | Tick PnL | Fills | Sharpe |
|---------|--------|--------|------|----------|-------|--------|
| Global (BEFORE) | 84.2% | 50.6% | -33.6pp | -$5,835 | 1,317 | -0.14 |
| Tag-aware | 57.8% | 46.7% | -11.1pp | -$6,486 | 4,870 | -0.14 |

**Root causes (3 identified):**
1. **Too-permissive qualification**: Beating 9-29% YES base is easy; 2,793 tag-only traders
   have near-zero global excess HR (median = -0.2pp)
2. **Aggregate-to-trade gap persists**: A trader with 55% aggregate YES HR on Culture (11.7% base)
   still enters bad individual YES trades because most Culture markets resolve NO
3. **Crypto concentration**: 81% of resolved signals are Crypto YES (51.3% HR).
   Other tags add noise: Culture 12.9%, Politics 3.0%, Movies 13.7%

**Per-tag tick-by-tick (C>=3, Jul 25):**
| Tag | YES HR | Base | Excess | Signals |
|-----|--------|------|--------|---------|
| Crypto | 51.3% | 28.9% | +22.4pp | 2,364 |
| Trump | 38.9% | 34.1% | +4.8pp | 131 |
| Culture | 12.9% | 11.7% | +1.2pp | 147 |
| Movies | 13.7% | 9.7% | +4.0pp | 51 |
| Politics | 3.0% | 23.2% | -20.2pp | 33 |

**Key learning**: Tag-specific base rates produce MORE HONEST vectorized estimates
(57.8% vs 84.2%) with smaller vec-to-tick gap (11pp vs 34pp), but the absolute
tick performance is still unprofitable. The fundamental issue is NET POSITION HR
(aggregate) vs INDIVIDUAL TRADE HR (per-tick) -- tag-awareness doesn't fix this.

**Spawned ideas:**
- Crypto-YES-only copy (51.3% HR, needs multi-month validation)

**Notebook**: `research/notebooks/s2_tag_aware_estimation.py`
**Script**: `research/scripts/s2_tag_aware_tick_validation.py`

### S2: Hit-Rate Copy -- REJECTED (2026-03-02)

**Hypothesis**: Traders with excess hit rate above direction-specific base rate are skilled.
Copying their entries with tiered conviction builds an edge.

**Result**: DOES NOT SURVIVE tick-by-tick validation. HR at or below base rate, negative PnL.

**Tick-by-tick results (3 OOS months: Apr-25, Jul-25, Oct-25):**

| Period | Vec HR (UB) | Tick HR | Gap (pp) | Tick PnL | Fills | Sharpe |
|--------|-------------|---------|----------|----------|-------|--------|
| Apr 25 | 82.9% | 45.9% | -37.0pp | -$6,417 | 740 | -0.21 |
| Jul 25 | 84.2% | 50.6% | -33.6pp | -$5,835 | 1,317 | -0.14 |
| Oct 25 | 85.5% | 46.1% | -39.4pp | +$2,385 | 1,579 | +0.04 |

**Root causes (5 identified):**
1. **Direction-agnostic consensus** (-7pp HR): Strategy ignores trader's qualified direction
2. **NO HR collapse in tick-by-tick** (-8 to -15pp below base): Structural, not a bug
3. **UNKNOWN outcomes** (50-180 fills/period): Missing token_map defaults to YES incorrectly
4. **Entry price shift** (~-2pp PnL): max_price = price + 0.02 worsens entry
5. **on_timer() never called**: ReplayRunner doesn't call on_timer(), max_hold_hours is non-functional

**Direction diagnosis (manual simulation):**
| Variant | Apr HR | Jul HR | Oct HR | YES excess | NO excess |
|---------|--------|--------|--------|-----------|----------|
| Naive (current) | 46.1% | 50.6% | 46.0% | +8.0pp | -14.7pp |
| Same-dir consensus | 52.9% | 53.6% | 50.7% | +14.0pp | -7.0pp |
| YES-only directed | 46.4% | 43.8% | 43.9% | +14.0pp | N/A |

**Key learning**: The vectorized-to-tick gap for HR-based strategies is
STRUCTURAL, not fixable by code improvements. The vectorized analysis uses
per-trader per-direction aggregate HR (net position over many trades). Tick-by-tick
enters at a specific trade price. The NO direction is particularly vulnerable because:
- NO is the default outcome (62-74% base rate)
- Entering at a specific NO trade price is worse than the blended average
- Many "NO wins" happen after price already moved to 0.90+

**Validation script**: `research/scripts/s2_hitrate_tick_validation.py`
**Diagnostic scripts**: `research/scripts/s2_hitrate_diagnose*_tick.py`
**Notebook**: `research/notebooks/s2_tick_validation.py`

**Spawned ideas**:
- Direction-aware consensus (queued, MEDIUM priority)
- YES-only hit-rate copy (queued, LOW priority)
- Hit-rate x Insider composite (queued, MEDIUM priority)

## Parked

(none)
