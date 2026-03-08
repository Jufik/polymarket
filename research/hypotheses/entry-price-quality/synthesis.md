# Entry Price Quality — Synthesis

**Date**: 2026-03-07
**Tracks**: 3 parallel researchers, ~15 approaches tested
**Dataset**: 8.9M positions, 55,623 traders (≥20 positions), gambling excluded

---

## The Core Discovery: The Hypothesis Was Inverted

The original framing — "promote cheap buyers, penalize sure-thing buyers" — is **backwards** on Polymarket.

**Why**: Polymarket is well-calibrated. Entry price ≈ market-implied probability. A trader who buys YES at 0.90 wins ~93% of the time — not because they're skilled, but because the market correctly priced that outcome at 90%. A trader who buys YES at 0.10 wins ~7% — again, the market is right.

This means:
- **Raw "buy cheap" metrics are anti-signals** (IC = -0.49 to -0.80 with HR)
- **Sure-thing ratio cannot be penalized** — high sure-thing traders have HIGHER excess HR (+13.6pp)
- **Cheap-entry consistency is the strongest anti-predictor** (IC = -0.801 with HR)

The market already does the job of penalizing bad entries. What we need is: **does the trader BEAT the market's implied probability at whatever price level they enter?**

---

## What Works (Ranked)

### Tier 1: Recommended for Scorecard

| Metric | IC vs HR | Description | Verdict |
|--------|---------|-------------|---------|
| **bucket_excess_hr** | +0.918 | HR within 10pp price bucket minus population HR in that bucket | **PRIMARY** — controls for price level |
| **calibration_gap** (= avg_edge) | +0.273 raw, +0.082 OOS | `avg(correct) - avg(entry_price)` per trader | **FALLBACK** — simpler, works when bucket data sparse |

**bucket_excess_hr** is the clear winner. It directly answers: "Does this trader beat the population of traders who enter at similar prices?" A trader who enters at 0.15 and achieves 30% HR has +16pp excess (vs 14% pop HR at that level). A trader who enters at 0.90 and achieves 93% HR has +0pp excess.

**avg_edge** (`hit_rate - avg_entry_price`) is mathematically equivalent to calibration_gap. It's simpler to compute but doesn't control for the non-linear price→HR relationship.

### Tier 2: Useful as Composite Multiplier

| Metric | IC vs HR | Description | Verdict |
|--------|---------|-------------|---------|
| **bargain_score** | +0.824 | `excess_hr × avg_profit_when_correct` | **TIE-BREAKER** — among traders with similar excess HR, prefer the one who achieves it at cheaper prices |

Top-decile bargain hunters: 83.6% HR, +26.2pp excess, avg entry 0.65. These are not extreme contrarians — they're mid-range value finders.

### Tier 3: Discard

| Metric | IC | Problem |
|--------|-----|---------|
| avg_payoff_on_wins | -0.585 | Measures price level, not skill |
| cheap_entry_ratio | -0.801 | Measures underdog tendency |
| expensive_entry_ratio | +0.786 | Mirror of cheap ratio — price proxy |
| sure_thing_ratio | +0.445 | Price proxy, penalty would hurt |
| entry_lag (timing) | +0.024 | Near zero — irrelevant |
| rr_ratio (risk/reward) | -0.349 | Measures bet structure, not skill |

---

## The Three Trader Archetypes

| Archetype | N | Avg HR | Avg Entry Price | Calibration Gap | Verdict |
|-----------|---|--------|----------------|----------------|---------|
| **Skilled cheap buyer** | 3,270 | 43.6% | 0.298 | **+13.8pp** | Genuine alpha — beats 29.8% implied by +13.8pp |
| **Value-oriented** | 31,035 | 50.0% | 0.472 | +2.8pp | Modest edge |
| **Sure-thing piler** | 1,882 | 68.4% | 0.751 | **-6.7pp** | NEGATIVE alpha — underperforms 75.1% implied by -6.7pp |

The counterintuitive finding: **sure-thing pilers actually have negative calibration gap** (-6.7pp). They UNDERPERFORM the implied probability of their chosen markets. Their 68.4% HR looks great but it should be 75.1% given their price selections. They're actually destroying value.

Meanwhile, skilled cheap buyers with only 43.6% HR have genuine +13.8pp alpha — they're winning 44% on positions the market prices at 30%.

---

## Concrete Scorecard Integration

### Recommended Formula

```
entry_quality_score = bucket_excess_hr
```

Where bucket_excess_hr = weighted average across 10pp entry price buckets of:
```
(trader_HR_in_bucket - population_HR_in_bucket) × (n_positions_in_bucket / total_positions)
```

### Hard Gates

| Gate | Condition | Action |
|------|-----------|--------|
| **Sure-thing with no alpha** | avg_entry > 0.85 AND bucket_excess_hr < +2pp | EXCLUDE from copy pool |
| **Chronic overpayer** | calibration_gap < -5pp AND n_positions ≥ 50 | EXCLUDE — systematically buying overpriced |
| **Pure gambler** | cheap_entry_ratio > 0.70 AND HR < 30% | EXCLUDE — long-shot gambling, not alpha |

### Composite with Existing Scorecard

The entry quality score should be **secondary** to the existing scorecard components:

```
scorecard = 0.45 × excess_hr
          + 0.25 × consistency_sharpe
          + 0.15 × avg_edge_usd
          + 0.15 × entry_quality_score_normalized   # NEW: bucket_excess_hr percentile
```

Rationale for 0.15 weight:
- OOS IC of bucket_excess_hr vs future HR was not tested (Track A only had 354 traders meeting both train/test thresholds)
- bucket_excess_hr has r=+0.918 with overall HR — high redundancy with excess_hr
- Primary value is as an EXCLUSION gate (remove sure-thing pilers, chronic overpayers) rather than a ranking signal

### Alternative: Pure Exclusion Gate (Simpler)

If we want to avoid composite bloat:
```
1. Compute calibration_gap = hit_rate - avg_entry_price per trader
2. EXCLUDE if calibration_gap < -5pp AND n_positions >= 50
3. Keep all other scorecard weights unchanged
```

This removes the worst offenders (sure-thing pilers who underperform their implied probability) without adding a new composite dimension.

---

## Key Evidence

### Population calibration is excellent (Track A)

| Entry Price Bucket | N Positions | Population HR |
|-------------------|-------------|---------------|
| 0.00-0.05 | 1,662,703 | 6.1% |
| 0.45-0.50 | 422,253 | 48.0% |
| 0.95-0.99 | 995,069 | 97.6% |

Market prices = resolution probabilities. Any metric that doesn't subtract this baseline is measuring calibration, not skill.

### 2D price × hold time grid (Track C)

| Price Bucket | 1-24h | 1-3d | 7-30d | 30d+ |
|-------------|-------|------|-------|------|
| <0.10 | 7.0% | 11.8% | 14.2% | 11.2% |
| 0.40-0.55 | 48.4% | 50.0% | 51.8% | 52.0% |
| 0.85+ | 94.4% | 95.1% | 96.1% | 97.3% |

Hold time adds +3-7pp within each price band. Price dominates. The grid can be used as a context-adjusted baseline for more precise excess HR computation.

### Sure-thing penalty is backwards (Track B)

| Sure-Thing Ratio | Avg Excess HR |
|-----------------|---------------|
| 0-10% (low) | -8.8pp |
| 70-100% (high) | +13.6pp |

Penalizing high sure-thing ratio would remove your best traders. The correct penalty is on **calibration gap** (do they underperform their price level?), not on the price level itself.

### Rare genuine contrarians exist (Track B)

Top 5 bargain hunters:
- `0xa05c42...`: 60 positions, 100% HR at avg entry 0.027 → +86pp excess over 2.7% implied
- `0xf8ccc5...`: 939 positions, 90.7% HR at avg entry 0.132 → +77pp excess

These traders are extraordinarily rare (top 0.01% by bargain_score) but genuinely skilled — they buy at 3-13 cents and win 90%+. The market priced those outcomes at 3-13% and they resolve at 90%+.

---

## Summary: What the User Asked vs What We Found

**User asked**: "Promote those who enter correctly cheap and sanction sure-thing buyers"

**What we found**:
1. "Enter correctly cheap" → **Yes, bucket_excess_hr captures this.** Traders who beat the population HR at their chosen price level are genuinely skilled, regardless of whether they enter cheap or expensive.
2. "Sanction sure-thing buyers" → **Partially correct, but the mechanism is different.** Don't penalize high entry prices directly (those traders often have positive excess HR). Instead, penalize **negative calibration gap** — traders whose HR falls below what their entry prices imply. These are the true "no-alpha" traders: they pick near-certainties but still manage to underperform them.

The right framing: **reward traders who beat the market's implied probability at any price level; exclude traders who consistently fail to meet it.**

---

## Open Questions

1. **OOS validation needed**: bucket_excess_hr and calibration_gap OOS IC were either untested or weak (+0.082). Need walk-forward validation.
2. **Tag interaction**: Does entry quality signal vary by tag? Politics cheap buyers vs Crypto cheap buyers may be fundamentally different populations.
3. **Integration with consensus strategies**: Does filtering by calibration_gap > 0 improve consensus pool quality (the only surviving strategy is Politics NO K=50)?
4. **Minimum positions per bucket**: Track A lost 31% of traders due to sparse bucket data. Need fallback for low-volume traders.

---

## Artifacts

```
research/hypotheses/entry-price-quality/
├── discovery/
│   ├── price_percentile_scoring.md      # Track A: bucket excess HR, calibration gap
│   ├── contrarian_value_signal.md       # Track B: sure-thing ratio, bargain score
│   ├── contrarian_value_results.json    # Track B: raw data
│   └── dynamic_entry_quality.md         # Track C: timing, 2D grid, consistency
├── scripts/
│   ├── price_scoring.py                 # Track A analysis
│   ├── contrarian_value.py              # Track B analysis
│   └── dynamic_entry.py                 # Track C analysis
└── synthesis.md                         # This document
```
