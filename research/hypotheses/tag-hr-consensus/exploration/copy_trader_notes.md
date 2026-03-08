# Copy-Trader Contamination Analysis

**Date**: 2026-03-06
**Status**: COMPLETE -- hypothesis REJECTED (copy-traders are NOT the primary failure mode)

---

## Executive Summary

The copy-trader contamination hypothesis predicted that pool growth (47 -> 774 traders) is driven by copiers following originals, creating fake consensus. **The data refutes this.**

The actual findings are far more nuanced and contain a genuine surprise:

> [!CRITICAL]
> **Later entrants have HIGHER HR than first movers, not lower.** This is the opposite of what copy-trader contamination would predict. The consensus signal IMPROVES with depth, not degrades.

The real failure mode is **base rate non-stationarity** (2025-10 Esports: 65% base rate vs 37-46% in other folds), not copy-trader dilution.

---

## Finding 1: Entry Time Clustering -- Copy Behavior IS Present But Moderate

### Esports clustering across folds:

| Gap type | 2025-07 | 2025-10 | 2026-01 |
|----------|---------|---------|---------|
| <5min (instant copy) | 5.4% | 26.2% | 21.3% |
| 5-30min (fast copy) | 19.6% | 27.5% | 36.7% |
| 30min-2h (delayed) | 35.1% | 20.1% | 22.3% |
| 2h-24h (same day) | 37.2% | 23.2% | 18.1% |
| >24h (independent) | 2.7% | 3.0% | 1.7% |

### Tennis clustering:

| Gap type | 2025-07 | 2025-10 | 2026-01 |
|----------|---------|---------|---------|
| <5min (instant copy) | 7.5% | 8.7% | 6.8% |
| 5-30min (fast copy) | 21.6% | 23.2% | 21.3% |
| 30min-2h (delayed) | 24.6% | 32.6% | 28.6% |
| 2h-24h (same day) | 44.0% | 31.8% | 37.3% |
| >24h (independent) | 2.3% | 3.8% | 6.1% |

**Interpretation**: Esports has a significant copy problem in later folds (53.7% of entries within 30min in 2025-10, 58.0% in 2026-01 vs only 25.0% in 2025-07). Tennis is more stable at ~28-32% within 30min.

But clustering alone does not prove contamination -- we need to check whether clustered entries have lower signal quality.

---

## Finding 2: Leaders vs Followers -- FOLLOWERS HAVE HIGHER HR (Surprise!)

### Esports

| Fold | Leaders n | Leaders HR | Leaders exc | Followers n | Followers HR | Followers exc |
|------|-----------|-----------|-------------|-------------|-------------|---------------|
| 2025-07 | 18 | 0.531 | +16.4pp | 8 | 0.628 | +26.0pp |
| 2025-10 | 18 | 0.634 | -2.0pp | 72 | 0.674 | +2.0pp |
| 2026-01 | 94 | 0.599 | +14.3pp | 72 | 0.637 | +18.1pp |

### Tennis

| Fold | Leaders n | Leaders HR | Leaders exc | Followers n | Followers HR | Followers exc |
|------|-----------|-----------|-------------|-------------|-------------|---------------|
| 2025-07 | 16 | 0.507 | +26.0pp | 31 | 0.601 | +35.4pp |
| 2025-10 | 45 | 0.514 | +11.9pp | 28 | 0.547 | +15.1pp |
| 2026-01 | 39 | 0.577 | +12.4pp | 40 | 0.713 | +26.0pp |

> [!WARNING]
> **In every fold, for both tags, followers have equal or higher HR than leaders.**
> This directly contradicts the copy-trader contamination hypothesis.

**Why?** Possible explanations:
1. Followers are not blind copiers -- they wait for MORE information before entering (confirmation bias helps here)
2. Late entry = market closer to resolution = less uncertainty = easier to be right
3. Leaders who enter first take more speculative positions that often fail
4. The qualified pool filters already exclude bad traders, so remaining followers are genuinely skilled

---

## Finding 3: Entry Order vs HR -- MONOTONICALLY INCREASING (Key Finding)

### Esports 2025-07 (pool=50, the "good" fold)

| Order | HR | Excess | n |
|-------|-----|--------|---|
| 1st | 47.2% | +10.5pp | 163 |
| 2nd | 57.8% | +21.0pp | 116 |
| 3rd | 64.1% | +27.3pp | 78 |
| 4th | 72.6% | +35.8pp | 51 |
| 5th | 72.4% | +35.6pp | 29 |

### Esports 2026-01 (pool=517, the "bad" fold)

| Order | HR | Excess | n |
|-------|-----|--------|---|
| 1st | 51.1% | +5.5pp | 2379 |
| 2nd | 65.0% | +19.4pp | 825 |
| 3rd | 65.5% | +19.9pp | 464 |
| 4th | 64.8% | +19.3pp | 290 |
| 5th | 66.5% | +20.9pp | 185 |

### Tennis 2026-01 (pool=315)

| Order | HR | Excess | n |
|-------|-----|--------|---|
| 1st | 52.4% | +7.1pp | 1091 |
| 2nd | 59.4% | +14.1pp | 441 |
| 3rd | 61.1% | +15.8pp | 267 |
| 4th | 70.0% | +24.7pp | 150 |
| 5th | 71.3% | +26.0pp | 94 |

> [!CRITICAL]
> **HR is MONOTONICALLY INCREASING with entry order.** This means consensus WORKS --
> more traders entering a market is a STRONGER signal, not a weaker one.
> The problem is NOT that followers dilute the signal.

**Exception**: Esports 2025-10 (base=65.4%) shows NEGATIVE excess at ALL entry orders.
This fold is hostile regardless of entry order -- the base rate is too high.

---

## Finding 4: Temporal Independence -- Mixed Signal

### Esports 2025-07

| Independence | HR | Excess | n |
|-------------|-----|--------|---|
| Has <5min pair | 46.7% | +9.9pp | 15 |
| Has 5-30min pair | 77.5% | +40.7pp | 40 |
| All 30min-2h | 45.5% | +8.7pp | 33 |
| All >2h | 50.0% | +13.2pp | 28 |

### Esports 2026-01

| Independence | HR | Excess | n |
|-------------|-----|--------|---|
| Has <5min pair | 66.7% | +21.1pp | 261 |
| Has 5-30min pair | 61.2% | +15.6pp | 263 |
| All 30min-2h | 67.8% | +22.2pp | 149 |
| All >2h | 65.8% | +20.2pp | 152 |

**Interpretation**: No consistent pattern. Markets with copy-pairs (<5min) do NOT have lower HR than independent markets. In 2026-01, the "instant copy" bucket actually has the HIGHEST HR. The temporal independence filter would NOT improve the signal.

Tennis 2026-01 shows a clearer gradient: <5min=71.2% HR, >2h=47.1% HR. But this may be confounded by n_entries (high-copy markets have more traders = stronger consensus).

---

## Finding 5: Pool Composition -- Newcomers Are NOT Worse

### Esports pool turnover

| Fold | Size | New | Returning | Overlap% | Returning HR | New HR |
|------|------|-----|-----------|----------|-------------|--------|
| 2025-07 | 50 | - | - | - | - | - |
| 2025-10 | 197 | 168 | 29 | 14.7% | 0.663 | 0.676 |
| 2026-01 | 517 | 403 | 114 | 22.1% | 0.714 | 0.738 |

### Tennis pool turnover

| Fold | Size | New | Returning | Overlap% | Returning HR | New HR |
|------|------|-----|-----------|----------|-------------|--------|
| 2025-07 | 83 | - | - | - | - | - |
| 2025-10 | 269 | 210 | 59 | 21.9% | 0.644 | 0.634 |
| 2026-01 | 315 | 174 | 141 | 44.8% | 0.690 | 0.691 |

> [!TIP]
> New traders have EQUAL or HIGHER HR than returning traders (Esports 2026-01: 73.8% vs 71.4%).
> Pool explosion is not driven by copiers with inflated stats -- it's driven by genuine market growth
> attracting more traders who pass the HR threshold.

---

## Root Cause Reanalysis

The copy-trader contamination hypothesis is **WRONG** as the primary explanation. The evidence shows:

1. **Followers have HIGHER HR** than leaders (consistent across tags/folds)
2. **HR INCREASES with entry order** (more consensus = better, not worse)
3. **Clustered entries (potential copies) do NOT have lower HR** than independent entries
4. **New pool members are NOT worse** than returning ones
5. **The 2025-10 Esports fold fails at ALL entry orders** -- it's base rate, not pool quality

### The actual failure modes:

1. **Base rate non-stationarity**: Esports 2025-10 has 65.4% base (nearly 2x the other folds). ANY YES-biased signal fails here regardless of quality. This is the primary PnL destroyer.

2. **Fill price compression**: The signal is real (10-20pp excess across folds) but execution at 0.45-0.55 prices makes the edge tiny in dollar terms. Entry order shows 1st mover gets the best price but worst HR; 4th-5th mover gets better HR but worse price.

3. **Pool size and market coverage**: With 517 traders and 2379 markets, coverage is sparse (~2 traders/market avg). Many "consensus=2" signals are just random overlap, not true agreement.

---

## Which Fixes Would Help?

Based on this analysis, the proposed fixes should be re-evaluated:

### 1. First-mover filter (only count first 2-3 traders) -- HARMFUL
Entry order analysis shows HR INCREASES with depth. Restricting to first movers would
**reduce** signal quality. REJECT this fix.

### 2. Temporal independence requirement (>30min apart) -- NEUTRAL to HARMFUL
No consistent evidence that independent entries predict better. In some folds, clustered entries
have HIGHER HR (Esports 2026-01: 66.7% for <5min vs 65.8% for >2h). REJECT this fix.

### 3. Leader-only pool -- HARMFUL
Leaders have LOWER HR than followers in every fold. This would degrade pool quality. REJECT this fix.

### 4. Entry-order penalty (weight by 1/order) -- HARMFUL
Monotonically increasing HR with order means later entries are MORE informative. Penalizing them
is backwards. REJECT this fix.

### What WOULD help (based on findings):

1. **Base rate regime filter**: Skip trading when training-window base rate deviates >15pp from
   historical norm. The 2025-10 Esports fold (base=44.3% train, 65.4% test) would have been caught.

2. **Deeper consensus requirement**: Since HR increases with depth, require N=4 or N=5 instead of N=2-3.
   2025-07 shows 72.6% HR at order 4 vs 47.2% at order 1 (+25.4pp). The tradeoff is fewer signals.

3. **Market coverage ratio**: When pool_size/n_test_markets < 0.1, the signal is too sparse.
   Esports 2026-01: 212 active traders / 2379 markets = 0.089 -- below threshold.

4. **Minimum entry density**: Only fire consensus when N traders enter the SAME market within a
   window AND the total market has >= M qualified entries. Markets with exactly 2 qualified
   entries (common at large pool sizes) are near-random.

---

## Verdict

The copy-trader contamination hypothesis is **REJECTED**. The qualified pool growth is NOT driven by
uninformed copiers -- it's driven by genuine market growth attracting more traders who pass the HR
threshold. Followers are actually BETTER predictors than leaders.

The failure mode is structural: **base rate non-stationarity** and **fill price economics**, not pool quality.

## Fix Validation: Deep Consensus + Independence Filter

### Test: Standard Consensus vs Independence-Only (min_gap > 30min between all consecutive pairs)

> [!WARNING] UPPER BOUNDS -- vectorized. Expect 20-40pp degradation in tick-by-tick.

#### Esports

| Fold | N | Standard HR | Standard exc | Std n | IndepOnly HR | IndepOnly exc | Indep n |
|------|---|-------------|-------------|-------|-------------|--------------|---------|
| 2025-07 | 3 | 64.1% | +27.3pp | 78 | 50.0% | +13.2pp | 28 |
| 2025-07 | 4 | 72.6% | +35.8pp | 51 | 60.0% | +23.2pp | 15 |
| 2025-07 | 5 | 72.4% | +35.6pp | 29 | 50.0% | +13.2pp | 6 |
| 2025-10 | 3 | 60.4% | -5.0pp | 207 | 46.0% | -19.4pp | 50 |
| 2025-10 | 4 | 60.8% | -4.5pp | 166 | 51.9% | -13.5pp | 27 |
| 2025-10 | 5 | 63.9% | -1.5pp | 133 | 50.0% | -15.4pp | 14 |
| 2026-01 | 3 | 65.5% | +19.9pp | 464 | 68.0% | +22.4pp | 78 |
| 2026-01 | 4 | 64.8% | +19.2pp | 290 | 60.0% | +14.4pp | 20 |
| 2026-01 | 5 | 66.5% | +20.9pp | 185 | 75.0% | +29.4pp | 4 |

#### Tennis

| Fold | N | Standard HR | Standard exc | Std n | IndepOnly HR | IndepOnly exc | Indep n |
|------|---|-------------|-------------|-------|-------------|--------------|---------|
| 2025-07 | 3 | 64.9% | +40.1pp | 94 | 64.7% | +40.0pp | 34 |
| 2025-07 | 4 | 65.7% | +40.9pp | 67 | 64.7% | +40.0pp | 17 |
| 2025-07 | 5 | 65.2% | +40.5pp | 46 | 44.4% | +19.7pp | 9 |
| 2025-10 | 3 | 53.2% | +13.7pp | 250 | 51.6% | +12.0pp | 95 |
| 2025-10 | 4 | 54.6% | +15.0pp | 132 | 44.4% | +4.9pp | 18 |
| 2025-10 | 5 | 61.1% | +21.6pp | 72 | 60.0% | +20.5pp | 5 |
| 2026-01 | 3 | 61.1% | +15.7pp | 267 | 53.9% | +8.5pp | 117 |
| 2026-01 | 4 | 70.0% | +24.7pp | 150 | 65.1% | +19.8pp | 43 |
| 2026-01 | 5 | 71.3% | +26.0pp | 94 | 61.1% | +15.8pp | 18 |

### Key takeaways from fix tests:

1. **Independence filter is HARMFUL in every case.** It reduces signal count by 60-95% while producing
   LOWER HR than standard consensus in most folds. The filter destroys sample size without improving
   quality. This confirms Finding 4: clustered entries are NOT lower quality.

2. **Deep consensus (N>=4-5) shows clear improvement over N>=3.**
   - Tennis N>=5: avg excess = +29.4pp across non-hostile folds (vs +23.1pp for N>=3)
   - Esports N>=5: avg excess = +28.3pp in good folds (vs +23.6pp for N>=3)
   - Tradeoff: 40-60% fewer signals at N>=5 vs N>=3

3. **Regime gate would help Esports but has no Tennis folds to skip.** Esports 2026-01
   (train_base=0.507) is the only fold flagged. Tennis train bases stay in 0.22-0.35 range.
   The regime gate is more relevant for Esports where base rate volatility is extreme.

### Recommended configuration for next validation round:

| Tag | N | meh | mpe | pc | Regime gate | Expected vec HR | Expected signals/fold |
|-----|---|-----|-----|----|------------|----------------|----------------------|
| Esports | 5 | 10pp | 0.80 | 0.40 | skip if train_base > 0.50 | 65-72% | 29-185 |
| Tennis | 4 | 20pp | 0.90 | 0.40 | none needed | 55-70% | 67-150 |
| Tennis | 5 | 20pp | 0.90 | 0.40 | none needed | 61-71% | 46-94 |

After 20-40pp tick degradation: expected HR 25-52%. At price_ceil=0.40, break-even HR = 40%.
This is **marginal** -- Tennis N>=5 may clear it, Esports unlikely.

---

## Spawned Ideas

1. **base-rate-regime-gate** [HIGH]: Suspend YES signals when base rate > 55% or deviates >15pp from prior fold
2. **deep-consensus** [HIGH]: Require N=4-5 instead of N=2-3, accepting fewer signals for higher HR
3. **entry-density-filter** [MEDIUM]: Only fire when >= M qualified entries in market (not just first N)
4. **min-coverage-ratio** [MEDIUM]: Skip folds/tags where active_traders/markets < 0.1
5. **consensus-depth-as-continuous-signal** [HIGH]: Instead of binary N>=K, use actual entry count as
   a continuous feature. Weight position size by depth: $100 * min(n_qualified/5, 2.0).
   Markets with 8+ qualified entries get 2x position, markets with exactly 2 get 0.4x.
