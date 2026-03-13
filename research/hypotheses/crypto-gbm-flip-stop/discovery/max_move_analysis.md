# Max Move Entry Filter — Deep Dive Analysis

**Date**: 2026-03-11
**Analyst**: Researcher Agent
**Status**: UPPER BOUNDS — vectorized sim, PM prices from ASOF-joined trade data (20-40pp optimistic vs tick)

---

## Executive Summary

The capital recycling argument for the max_move filter is **decisively refuted**: BTC Up/Down markets are strictly sequential (900s gap between windows, 0% concurrent overlap). Capital is never a binding constraint. Max concurrent positions observed: **1**. This removes the primary theoretical motivation for the filter.

The filter itself does improve per-trade quality (HR and PnL per trade), but this comes at the cost of reduced total PnL — a pure quality-vs-volume tradeoff with no capital efficiency benefit.

**Verdict: Do NOT implement the max_move filter.**

---

## Key Findings

### 1. Capital Saturation Rate: 0%

| Metric | Value |
|--------|-------|
| Max concurrent positions ever | **1** |
| Saturation rate (slots full when signal arrives) | **0.0%** |
| Market window gap (median) | 900s (exactly 15 min) |
| Markets with concurrent overlap | **0 out of 16,176** |
| Entries missed due to capital constraint | **0** |

**Root cause**: BTC Up/Down markets run sequentially. Each new 15-minute window starts exactly when the previous one ends. The strategy never has 2 concurrent positions because there are never 2 concurrent markets. With 20 slots and max 1 position needed, the capital constraint never binds.

This completely invalidates the capital recycling argument. The filter cannot "free capital for better entries" because no capital is tied up.

### 2. Capital-Constrained PnL Comparison

With 0% saturation, the "capital-constrained" and "unconstrained" simulations are identical. The filter only reduces total PnL:

| Filter | Taken | Sat% | HR | PnL/trade | Total PnL | Δ vs baseline |
|--------|-------|------|----|-----------|-----------|---------------|
| No filter (delay=1) | 15,929 | 0.0% | 79.9% | +0.1076 | **+1,713.8** | baseline |
| max_move≤0.30% | 15,692 | 0.0% | 80.2% | +0.1082 | +1,698.2 | -15.6 |
| max_move≤0.20% | 15,199 | 0.0% | 80.6% | +0.1093 | +1,661.8 | -52.0 |
| max_move≤0.15% | 14,525 | 0.0% | 81.0% | +0.1110 | +1,612.7 | -101.1 |
| max_move≤0.12% | 13,675 | 0.0% | 81.4% | +0.1129 | +1,544.2 | -169.6 |
| **max_move≤0.10%** | **12,740** | **0.0%** | **81.6%** | **+0.1147** | **+1,460.9** | **-252.9** |
| max_move≤0.08% | 11,407 | 0.0% | 81.9% | +0.1175 | +1,339.8 | -374.0 |
| max_move≤0.05% | 8,123 | 0.0% | 82.3% | +0.1244 | +1,010.8 | -703.0 |

**All filters reduce total PnL.** No threshold produces positive delta under capital constraint because there IS no capital constraint.

### 3. Compounding Score Comparison

The compounding score (excess_HR × avg_edge_USD / median_hold_days) favors tighter filters only because they improve HR while hold time doesn't decrease much:

| Filter (v1, delay=1) | Excess HR | Avg Edge USD | Hold (s) | CompScore |
|----------------------|-----------|--------------|----------|-----------|
| No filter | +29.9pp | $5.38 | 41s | 2,326 |
| max_move≤0.30% | +30.2pp | $5.41 | 41s | 2,352 |
| max_move≤0.10% | +31.6pp | $5.74 | 45s | **2,608** |
| max_move≤0.05% | +32.3pp | $6.22 | 54s | **2,895** |

The compounding score increases with tighter filters — but this is misleading. The denominator (hold time) barely decreases, so the improvement is almost entirely from the numerator. Since capital isn't constrained, the compounding score's "recycling rate" argument doesn't apply here. The correct comparison is total PnL, which monotonically decreases.

### 4. Hold Time Analysis by Log-Move Bucket

Large-move entries are NOT held significantly longer than small-move entries:

| Move Bucket | N | HR (v1) | HR (v5) | Hold (v1) | Hold (v5) | PnL/tr (v1) | Flip% (v1) |
|------------|---|---------|---------|-----------|-----------|-------------|------------|
| <0.05% | 8,123 | **82.3%** | 83.4% | 54s | 55s | +0.1244 | 11.3% |
| 0.05-0.10% | 4,617 | 80.3% | **86.9%** | 34s | 34s | +0.0975 | 13.1% |
| 0.10-0.15% | 1,785 | 77.1% | 89.2% | 27s | 28s | +0.0850 | 18.2% |
| 0.15-0.20% | 674 | 70.0% | 89.9% | 24s | 24s | +0.0729 | 24.5% |
| 0.20-0.30% | 493 | 69.0% | 90.1% | 28s | 29s | +0.0738 | 26.2% |
| 0.30-0.50% | 198 | 64.1% | 89.4% | 29.5s | 29.5s | +0.0693 | 29.3% |
| >0.50% | 39 | **53.8%** | 82.1% | 35s | 36s | +0.0484 | 35.9% |

**Key insight**: Large-move entries are actually held SHORTER (27-35s vs 54s for small moves), not longer. The capital recycling argument assumed bad entries tie up capital longer — the opposite is true. Bad (large-move) entries exit faster via flip-stop, freeing capital sooner. This further undermines the capital recycling rationale.

**Critical anomaly (v5 confirm=5)**: Large-move entries have HIGHER HR with v5 (89-90%) than small-move entries (83-86%). This is a surprise. The flip confirm=5 filter is doing more work on large-move entries — it's preventing false flip stops, which are more common in high-volatility large-move situations. This suggests flip_confirm=5 partially addresses the same quality issue as the max_move filter.

### 5. Interaction with Flip Stop Confirm=5

| Config | N | HR | PnL/trade | Total PnL | Flip% |
|--------|---|----|-----------|-----------|-------|
| No filter, confirm=1 (deployed) | 15,929 | 79.9% | +0.1076 | +1,713.8 | 13.9% |
| No filter, confirm=5 (new) | 15,929 | **85.6%** | +0.1098 | +**1,748.4** | 12.0% |
| max_move≤0.10%, confirm=1 | 12,740 | 81.6% | +0.1147 | +1,460.9 | 11.9% |
| max_move≤0.10%, confirm=5 | 12,740 | 84.7% | +0.1154 | +1,469.9 | 10.0% |
| max_move≤0.05%, confirm=1 | 8,123 | 82.3% | +0.1244 | +1,010.8 | 11.3% |
| max_move≤0.05%, confirm=5 | 8,123 | 83.4% | +0.1235 | +1,003.6 | 9.5% |

**Critical finding**: The new flip_confirm=5 is strictly better than max_move filtering:
- confirm=5 (no filter): +35.7pp HR improvement, +$34.6 total PnL vs confirm=1 no-filter
- max_move≤0.10% + confirm=1: +17pp HR improvement, **-$252.9 total PnL** vs confirm=1 no-filter
- Combined (max_move≤0.10% + confirm=5): +47pp HR improvement but **-$243.9 total PnL** vs confirm=1 no-filter

The flip_confirm=5 improves PnL; the max_move filter reduces PnL. Adding them together nets out to a large PnL loss despite the HR gain.

**Interesting interaction**: flip_confirm=5 on large-move entries actually INCREASES their HR to ~89% (higher than small-move entries at 83%), suggesting the filter=5 is solving the same underlying problem (false flip-stops in high-vol situations) that the max_move filter was trying to address via entry suppression.

### 6. Time-of-Window Analysis

Do large-move entries cluster at specific window times? Is `skip_seconds` the simpler fix?

| Elapsed | Small Move (HR) | Large Move (HR) | Small Move N | Large Move N |
|---------|----------------|-----------------|-------------|-------------|
| 0-30s | 79.2% | **87.5%** | 2,078 | 360 |
| 30-60s | 85.0% | **89.5%** | 1,934 | 286 |
| 60-120s | 84.9% | 87.7% | 2,985 | 604 |
| 120-180s | 83.5% | 83.2% | 1,848 | 489 |
| 180-300s | 85.0% | 71.5% | 2,050 | 701 |
| 300-600s | 71.0% | **44.6%** | 1,774 | 704 |
| 600-900s | 38.0% | **28.9%** | 71 | 45 |

**Unexpected finding**: Large-move entries in the EARLY window (0-60s) have the BEST HR (87.5-89.5%), better than same-time small-move entries. The problem is NOT early large-move entries — it's **late large-move entries (>180s elapsed)**.

Only **20.3% of large-move entries occur in the first 60s**. The majority (79.7%) are from 60-900s elapsed.

The filter should target: "large move AND late in window" — NOT "large move at any time."

**However**: the 300-600s bucket is already poorly performing for BOTH types (small: 71.0%, large: 44.6%). A simpler fix might be a stricter time cutoff (reduce `no_entry_within_s` from 90s to 300s or similar).

---

## Granular Threshold Sweep Summary

| Threshold | Suppressed | HR δ | PnL/trade δ | Total PnL δ | Hold δ | CompScore |
|-----------|-----------|------|-------------|-------------|--------|-----------|
| 0.05% | 49.0% | +2.4pp | +0.0168 | -703.0 | +13s | 2,895 |
| 0.08% | 28.4% | +2.0pp | +0.0099 | -374.0 | +6s | 2,701 |
| **0.10%** | **20.0%** | **+1.7pp** | **+0.0071** | **-252.9** | **+4s** | **2,608** |
| 0.12% | 14.2% | +1.5pp | +0.0053 | -169.6 | +2s | 2,552 |
| 0.15% | 8.8% | +1.1pp | +0.0034 | -101.1 | +1s | 2,481 |
| 0.20% | 4.6% | +0.6pp | +0.0017 | -52.0 | 0s | 2,405 |
| 0.30% | 1.5% | +0.3pp | +0.0006 | -15.6 | 0s | 2,352 |
| 0.50% | 0.2% | +0.1pp | +0.0001 | -1.9 | 0s | 2,326 |

The only threshold with negligible total PnL cost (< $20) is **0.30%** — but it only suppresses 1.5% of entries and adds just 0.3pp HR.

---

## Sensitivity Analysis

### Is the signal robust?

The effect is robust by construction: larger moves are monotonically worse for HR (53.8-82.3% across buckets). The relationship is structural (GBM mean-reversion: large moves from S₀ create over-extended GBM signals). The threshold parameter is less important than the effect direction.

**Sensitivity**: Changing threshold by ±10% from 0.10% (0.09% to 0.11%):
- Removes 11%-29% of entries (vs 20% at 0.10%)
- HR delta changes from +1.4pp to +2.0pp
- Total PnL delta changes from -200 to -310

The strategy is NOT fragile to parameter choice — the tradeoff curve is smooth. But the tradeoff is always negative for total PnL.

---

## Conclusion and Recommendation

### The capital recycling argument is invalid

The core premise was: "bad entries tie up capital, preventing good entries." Data shows:
1. Only **1** BTC Up/Down market is active at any time (markets are sequential, 900s apart)
2. Max concurrent positions: 1 (of 20 available slots)
3. Capital is never constrained — saturation rate is 0%
4. Large-move entries hold SHORTER than small-move entries (flip-stopped faster)
5. No entries are ever "missed due to capital" in either filtered or unfiltered scenario

### The quality-vs-volume tradeoff is real but unfavorable

The max_move filter does remove genuinely worse entries:
- HR of suppressed entries: 73.3% vs 81.6% for kept entries (at 0.10% threshold)
- PnL per trade improves by +$0.36/trade (+6.6%)
- But total PnL **decreases by $252.9** at the 0.10% threshold

Since capital recycling doesn't apply, this is a pure quality-vs-volume decision. Given that the strategy's objective is to maximize total PnL (not per-trade quality or Sharpe), the filter is not beneficial.

### flip_confirm=5 is already solving the problem

The newly deployed `gbm_flip_confirm_ticks=5` addresses the same root cause (false flip-stops in high-volatility large-move situations) more effectively:
- Improves total PnL by +$34.6 (vs -$252.9 for max_move filter)
- Improves HR from 79.9% to 85.6% (vs +1.7pp for max_move filter)
- Applies to ALL entries, not just suppressed ones
- The interaction shows v5 actually raises large-move entry HR to 89+%, resolving the quality gap

### Final Recommendation: **DO NOT IMPLEMENT max_move filter**

| Criterion | max_move≤0.10% filter | flip_confirm=5 (deployed) |
|-----------|----------------------|--------------------------|
| Total PnL impact | -$252.9 | +$34.6 |
| HR improvement | +1.7pp | +5.7pp |
| Capital freed | None (0% saturation) | N/A |
| Implementation cost | Medium | Already deployed |
| Verdict | **REJECT** | **KEEP** |

If the strategy transitions to a higher-frequency mode with genuinely concurrent markets (e.g., multi-asset or different market structure), revisit the capital recycling argument. For the current BTC Up/Down sequential structure, the filter has no capital efficiency benefit and reduces total PnL.

---

## Secondary Finding: Late Window is the Real Problem

The time-of-window analysis reveals a more actionable filter:

- Large-move entries at **300-600s elapsed**: 44.6% HR — catastrophic
- Small-move entries at **300-600s elapsed**: 71.0% HR — bad but less so
- Large-move entries at **0-60s elapsed**: 87.5-89.5% HR — better than small-move!

The "late window problem" affects all entries (both small and large moves), but large moves amplify it. A simpler, more effective filter might be:

**Alternative: Reduce `no_entry_within_s` from 90s → 300s**

This would suppress ALL entries in the final 300s (instead of 90s), removing:
- The 300-600s bucket where large-move HR collapses to 44.6%
- At a cost of ~15% of entries (2,478 signals) vs 20% for max_move

This should be tested in a future hypothesis. Effect is likely similar but the mechanism is cleaner (time cutoff vs price filter).

---

*All results are UPPER BOUNDS — vectorized simulation, 20-40pp optimistic vs tick-by-tick.*
