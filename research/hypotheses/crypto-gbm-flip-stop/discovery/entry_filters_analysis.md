# Entry Filter Analysis: Vol Spike and Flat Window Suppression

**Date**: 2026-03-11
**Analyst**: Researcher Agent
**Status**: UPPER BOUNDS — vectorized sim, PM prices from ASOF-joined trade data (20-40pp optimistic vs tick)

## Baseline

| Metric | Value |
|--------|-------|
| N signals | 15,933 |
| Hit rate | 80.0% |
| Avg PnL (raw units) | +0.1079 |
| Total PnL | +1,718.6 |

---

## Idea A: Vol Spike Entry Suppression

**Hypothesis**: During vol spikes (σ_60s / σ_1800s > threshold), GBM entries are noisier.

### Vol Ratio Distribution
| Percentile | Ratio |
|-----------|-------|
| p10 | 0.40 |
| p25 | 0.65 |
| p50 | 0.88 |
| p75 | 1.12 |
| p90 | 1.44 |
| p95 | 1.71 |
| p99 | 2.59 |

The median vol ratio is **0.88** — most entries occur during periods where short-term vol is slightly *below* long-term vol. This is the stable-regime sweet spot for the GBM model.

### Sweep Results

| Threshold | % Suppressed | HR (suppressed) | HR (allowed) | FQR | PnL (allowed) | PnL Δ/trade | Total PnL Δ | Flip% (supp) | Flip% (allow) |
|-----------|-------------|-----------------|--------------|-----|---------------|-------------|-------------|--------------|----------------|
| > 1.5 | 8.6% | 0.786 | 0.801 | **0.981** | +0.1075 | -0.0004 | -152.9 | 9.1% | 14.3% |
| > 2.0 | 2.7% | 0.751 | 0.802 | **0.937** | +0.1074 | -0.0005 | -54.8 | 9.1% | 14.0% |
| > 2.5 | 1.1% | 0.746 | 0.801 | **0.931** | +0.1075 | -0.0003 | -25.0 | 12.7% | 13.9% |
| > 3.0 | 0.5% | 0.802 | 0.800 | 1.003 | +0.1075 | -0.0004 | -15.2 | 12.8% | 13.9% |
| > 4.0 | 0.2% | 0.828 | 0.800 | 1.034 | +0.1076 | -0.0002 | -6.6 | 17.2% | 13.9% |

**FQR (filter quality ratio) = HR(suppressed) / HR(allowed). FQR < 1.0 means the filter removes worse-than-average entries.**

### Vol Ratio Bucket Analysis

This shows the monotonic relationship between vol ratio and HR:

| Vol Ratio Bucket | N | HR | Avg PnL | Flip Rate |
|-----------------|---|----|---------|---------  |
| < 0.50 | 2,289 | **0.750** | +0.128 | 20.0% |
| 0.50–0.75 | 3,407 | 0.773 | +0.104 | 17.6% |
| 0.75–1.00 | 4,489 | 0.814 | +0.103 | 13.4% |
| 1.00–1.25 | 3,050 | **0.840** | +0.104 | 10.3% |
| 1.25–1.50 | 1,329 | 0.830 | +0.107 | 8.4% |
| 1.50–2.00 | 931 | 0.802 | +0.105 | 9.1% |
| 2.00–2.50 | 257 | 0.755 | +0.116 | 6.6% |
| 2.50–3.00 | 95 | **0.695** | +0.104 | 12.6% |
| > 3.00 | 86 | 0.802 | +0.176 | 12.8% |

**Key insight**: The relationship is NOT monotonic for suppression purposes. The worst HR is at **vol_ratio < 0.50** (0.750 HR) and the **2.0–3.0** range (0.755/0.695 HR). Entries at **1.0–1.5 ratio are best** (0.83–0.84 HR).

### Verdict on Idea A

The filter **does work** but only weakly and in a narrow range:
- At threshold > 2.0: FQR = 0.937, removes 2.7% of entries with 74.9pp lower HR
- The total PnL impact is **negative** at all thresholds (−6 to −153 total units)
- **Root cause**: We lose profitable entries from the >3.0 bucket (86 signals, 80.2% HR, +0.176 avg PnL) and the 1.5–2.0 bucket (931 signals, 80.2% HR)
- **The low vol_ratio entries (< 0.50, n=2,289) are the bigger problem** — these have 75.0% HR but the suppression approach doesn't catch them (they're below the threshold, not above)

**Unexpected finding**: The hypothesis is directionally correct for the 2.0–3.0 range but the gain is trivial. The more actionable finding is that **LOW vol ratio (< 0.5) is actually where quality degrades most**.

---

## Idea B: Flat Window Entry Suppression

**Hypothesis**: When BTC hasn't moved meaningfully from S₀ (|log(spot/s0)| < threshold), entries have lower conviction.

### Log Move Distribution
| Percentile | Move |
|-----------|------|
| p5 | 0.013% |
| p10 | 0.017% |
| p25 | 0.028% |
| p50 | 0.049% |
| p75 | 0.087% |
| p90 | 0.141% |
| p95 | 0.190% |

The median entry occurs when BTC has moved **0.049% from S₀** within the 15-minute window.

### Sweep Results

| Threshold | % Suppressed | HR (suppressed) | HR (allowed) | FQR | PnL (allowed) | PnL Δ/trade | Total PnL Δ |
|-----------|-------------|-----------------|--------------|-----|---------------|-------------|-------------|
| < 0.05% | 50.9% | **0.822** | 0.777 | **1.058** | +0.090 | -0.018 | -1,014 |
| < 0.10% | 80.0% | 0.816 | 0.738 | 1.105 | +0.080 | -0.028 | -1,462 |
| < 0.15% | 91.4% | 0.811 | 0.687 | 1.180 | +0.072 | -0.036 | -1,620 |
| < 0.20% | 95.6% | 0.806 | 0.672 | 1.200 | +0.070 | -0.038 | -1,669 |
| < 0.30% | 98.5% | 0.803 | 0.635 | 1.263 | +0.064 | -0.044 | -1,704 |

**FQR > 1.0 for every threshold — the hypothesis is INVERTED.**

### Log Move Bucket Analysis

| Move Bucket | N | HR | Avg PnL | Flip% | GBM Dev |
|------------|---|----|---------|---------|---------  |
| < 0.02% | 2,269 | **0.807** | +0.147 | 16.9% | 0.104 |
| 0.02–0.05% | 5,848 | **0.828** | +0.117 | 9.4% | 0.139 |
| 0.05–0.10% | 4,623 | 0.804 | +0.097 | 12.9% | 0.187 |
| 0.10–0.15% | 1,821 | 0.776 | +0.086 | 18.1% | 0.232 |
| 0.15–0.20% | 665 | 0.704 | +0.075 | 25.4% | 0.262 |
| 0.20–0.30% | 474 | 0.690 | +0.072 | 23.8% | 0.302 |
| 0.30–0.50% | 190 | 0.642 | +0.067 | 30.0% | 0.320 |
| > 0.50% | 43 | 0.605 | +0.052 | 32.6% | 0.357 |

**The hypothesis is completely backwards.** Flat windows (small moves from S₀) have **higher HR**, not lower. The reason is clear from the GBM deviation column:

- **Small moves** → GBM barely deviates from 0.50 (avg_dev = 0.104–0.139) → the strategy is making narrow-edged calls that tend to be correct (the PM is pricing a fair coin, GBM agrees)
- **Large moves** → large GBM deviation (0.30–0.36) → the price has already moved far from S₀ → the GBM model is predicting extreme outcomes → **mean reversion works against you**
- The flip stop rate confirms this: it rises from 9.4% at small moves to 32.6% at large moves

**Root cause of the inversion**: The GBM P(Up) formula measures how far the current price is from S₀ relative to remaining time. When the price has moved a lot from S₀ early in the window, GBM gives a very high signal — but the PM has already priced that in. Entries at large log_move are chasing moves that are over, not leading them.

**Actionable finding (inverse of the hypothesis)**: A **maximum move filter** (suppress when |log(spot/s0)| > 0.1%) would help:
- Above 0.1% move: 1,372 entries, HR drops to ~70-64%, avg_pnl -20pp vs baseline
- Below 0.1%: 12,740 entries (80%), HR = 0.816, avg_pnl ≈ baseline

---

## Combined Filter

The combined filter adds vol_ratio > threshold OR log_move < threshold. Because Idea B is inverted (suppressing low-move entries hurts), the combined results are all worse than baseline:

**Best combined** (vol > 4.0 + flat < 0.05%): suppresses 51.1%, HR_allow = 0.777 (Δ −2.3pp), total PnL −181

There is no viable combined filter using the original hypothesized direction.

---

## Corrected Hypotheses (What Actually Matters)

Based on the diagnostic buckets, the *real* actionable insights are:

### Finding 1: Low Vol Ratio Is Problematic (not high)
- vol_ratio < 0.5: 2,289 entries, HR = 0.750 (vs 80.0% baseline), flip rate = 20.0%
- Suppressing vol_ratio < 0.5 would remove the **worst HR bucket**
- Test: does this improve? vol_ratio < 0.5 has 0.750 HR vs 0.814 for the 0.75–1.0 bucket

### Finding 2: Large Price Moves From S₀ Degrade Quality
- Above 0.1% move from S₀: HR degrades monotonically from 78% to 60%
- Suppressing entries when |log(spot/s0)| > 0.001 would remove ~20% of entries but improve HR
- These are the "chasing" entries where BTC has already moved meaningfully

### Corrected filter results (post-hoc from bucket analysis):

| Filter | Suppressed | HR (suppressed) | HR (allowed) | FQR | PnL/trade (allowed) | PnL/trade Δ | Total PnL Δ |
|--------|-----------|-----------------|--------------|-----|---------------------|-------------|-------------|
| vol_ratio < 0.5 | 14.4% (2,289) | 0.750 | **0.808** | 0.928 | +0.1046 | −0.0033 | −291.9 |
| log_move > 0.1% | 20.0% (3,193) | 0.738 | **0.816** | 0.905 | +0.1148 | **+0.0069** | −256.3 |

The inverted flat-window filter (suppress HIGH moves instead of low moves) is clearly the stronger signal per trade (+6.4% PnL improvement). However, both reduce total absolute PnL because fewer trades fire.

---

## Summary and Recommendations

### Idea A: Vol Spike Suppression — WEAK POSITIVE, DO NOT IMPLEMENT
- The original hypothesis works in the 2.0–3.0 range (FQR 0.93–0.94)
- But the total PnL impact is tiny (−55 to −25 absolute units across 16k trades)
- The bigger problem is **low vol ratio** (below 0.5), which is outside the scope of "suppression" (it would need to be an "allow only if vol_ratio > 0.5" type filter)
- Net recommendation: **do not implement** in the currently proposed direction

### Idea B: Flat Window Suppression — REJECT (hypothesis inverted)
- The hypothesis is backwards: flat windows have HIGHER HR (0.828 at 0.02–0.05% range)
- Large moves from S₀ are the problem, not flat markets
- Implementing as proposed would **reduce PnL by 1,000–1,700 units**

### What to Actually Implement

**Corrected Idea B: Max Move Filter** — suppress when |log(spot/s0)| > 0.001 (0.1%)
- Removes 20% of entries (3,193 signals) where BTC has already moved far from S₀
- FQR = 0.905 — removes clearly worse entries
- HR improvement: 80.0% → 81.6% (+1.6pp)
- PnL/trade improvement: +0.1079 → +0.1148 (+0.0069, +6.4%)
- Total PnL impact: **−256 absolute** (fewer trades, though each trade is better)
- Interpretation: this is a **quality-vs-volume tradeoff**. Worthwhile if the strategy is capital-constrained or Sharpe-sensitive, not worthwhile if maximizing total PnL

**Corrected Idea A: Min Vol Ratio Filter** — suppress when vol_ratio < 0.5
- Removes 14.4% of entries (2,289 signals) where short-term vol is very subdued
- FQR = 0.928
- HR improvement: 80.0% → 80.8% (+0.8pp)
- PnL/trade change: −0.003/trade (worse per trade despite better HR — those entries had high avg_pnl due to hold_resolution exits)
- VERDICT: Do not implement — PnL per trade worsens despite HR improvement

**Combined corrected filter** (vol_ratio > 0.5 AND log_move < 0.001):
- Would remove ~28–32% of entries (some overlap)
- HR improvement dominated by the max_move component
- Total PnL likely negative

### Action Items
1. The max_move > 0.1% filter (Corrected Idea B) is the only filter worth pursuing
2. It improves per-trade PnL by 6.4% at cost of 20% fewer signals
3. Tick-validate: does the 20-40pp tick gap close more for high-move or low-move entries?
4. If strategy is capital-constrained and Sharpe matters, implement max_move ≤ 0.1% gate
5. Do NOT implement the other three filters (vol>2.5, log<0.05%, vol<0.5)

---

*UPPER BOUNDS: All results are 20–40pp optimistic vs tick-by-tick. Tick validation required before deployment.*
