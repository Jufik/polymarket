# Cross-Pool Consensus — Discovery Notes

## Overview

Sweep tested 4 pool construction methods (style_split, recency_split, score_axis, random_split) for Sports YES and Politics YES.

All pools achieved Jaccard = 0.000 (fully disjoint) because the construction methods explicitly excluded traders already selected into Pool A.

---

## What Worked

### Score-axis construction outperforms random split by +16pp

When Pool A is top-K by excess_hr and Pool B is top-K by consistency_sharpe (non-overlapping), the directional mode shows 87.9% HR vs 71.4% for random split. This is a real structural effect:
- Excess_hr traders (sharp, high HR over many markets) and consistency traders (stable month-over-month) represent different skill dimensions.
- When both groups agree on a market direction, the signal is stronger than when any two random traders agree.
- This finding is independent of the cross-pool hypothesis itself — it's about HOW you select the pool that matters.

### All pool constructions achieve true independence

By design (excluding Pool A traders from Pool B selection), we achieved Jaccard=0 across all variants. This demonstrates that independent pool construction is feasible without relying on arbitrary demographic splits.

---

## What Didn't Work

### BUY-only signal volume collapses geometrically

The fundamental problem: a single pool of K=100 generates 741 BUY-only signals in 8 months (93/month). When split into 2x K=50 pools, each pool generates ~half the signals. Cross-pool overlap requires BOTH pools to enter the same market, which is approximately (50/100)^2 = 25% of the original volume — and in practice we observed ~1.6% (12/741). The geometric collapse is much worse than expected, likely because the rarest high-quality markets (which generate good BUY-only signals) require very high pool density to achieve cross-pool overlap.

### Directional mode fires at break-even

The directional mode generates more signals (213-377 directional vs 3-12 BUY-only) but fires at avg price 0.85-0.90. At these prices:
- Break-even HR = 85-90%
- Vectorized HR = 86.9-87.9%
- Expected tick HR = 47-68% (20-40pp degradation)
- Conclusion: likely negative EV in tick validation

This is the same in-play contamination pattern observed in earlier research (in-play Sports markets with near-certain outcomes, SELL NO = bullish signal at high price).

### No temporal structure

Cross-pool consensus was hypothesized to provide a sequential signal (Pool A fires, then Pool B confirms later). In practice, the median gap between pools is 0 hours — they enter the same market within minutes of each other. Sports markets resolve in 3h median, so there is no time for a genuine "wait for confirmation" dynamic.

The only exception is score_axis BUY-only where Pool A (excess_hr) fires 2.8h before Pool B (consistency). But this variant has only 3 signals over 8 months — unusable.

---

## Surprising Findings

1. **Score-axis +16pp HR improvement**: This is more than expected. The hypothesis was that cross-pool consensus adds signal over a single pool. What we found is that the CONSTRUCTION METHOD matters more than the CONSENSUS REQUIREMENT. score_axis vs random split (+16pp) is bigger than the cross-pool vs single-pool improvement (+20pp excess vs +13pp single-pool N=2).

2. **All pools fire simultaneously**: The timing gap analysis showed med_gap ≈ 0h for all variants. This disproves the hypothesis that independent pools provide temporal confirmation. The cross-pool "confirmation" is actually just coincidence — both pools happen to be in the same market at the same time.

3. **BUY-only non-deployable**: The thesis was that cross-pool consensus solves single-pool N=2 thinness. It doesn't — cross-pool makes it worse by requiring both pools to have seen the same market, which happens ~16% as often as single-pool does.

---

## SELL Variant Comparison

| Dimension | BUY-only | Directional | Verdict |
|-----------|---------|-------------|---------|
| Signal volume | 3-12 signals | 13-377 signals | Huge difference |
| HR | 50-89% | 67-93% | Similar range |
| Avg fill price | 0.43-0.67 | 0.70-0.90 | Very different |
| Break-even at fill price | No concern | At edge | Directional risky |
| PnL per trade | -0.055 to +0.215 | -0.026 to +0.026 | BUY-only has better PnL per trade |

The difference is NOT minor (>2pp threshold for "insensitive" — it's 30x volume difference). SELL handling fundamentally changes which markets the strategy fires on:
- BUY-only fires on genuine discretionary long positions
- Directional fires predominantly on near-certainty in-play markets (SELL NO = exit after outcome nearly certain)

---

## Parameter Sensitivity

### BUY-only: too few signals to test sensitivity reliably

Style_split BUY-only: N=9 signals over 8 months. Single market flip = 11pp HR change. Impossible to distinguish signal from noise.

### Directional: score_axis is stable

Score_axis directional N_a=1 N_b=1: 8/8 months positive, lowest month 59.4% HR (Aug 2025), highest 96.9% (Jan 2026). Consistent signal across time.

### K sensitivity: not tested in this sweep

Varying K_each from 30 to 70 was not tested. Hypothesis: HR is insensitive to exact K because the signal comes from WHICH skill axes are selected, not the exact count. Would need a follow-up run.

---

## Classification Proposals

### score_axis_pool_sports_yes

Label: `score_axis_pool_a_sports_yes` — top-50 Sports YES traders by excess_hr, train cutoff 2025-07-01
Label: `score_axis_pool_b_sports_yes` — top-50 Sports YES traders by consistency_sharpe, NOT in Pool A

**Why worth creating**: The +16pp HR improvement from score_axis vs random is large enough to test as a classification-based filter on existing v3 strategies. Could be applied as a classification on any Sports YES signal.

---

## Spawned Ideas

See `research/ideas.md`:
1. `score-axis-pool-construction` [MEDIUM] — use score-axis split as meta-filter on existing v3 strategies
2. `sequential-cross-pool` [LOW] — enforce Pool A fires >2h before Pool B
