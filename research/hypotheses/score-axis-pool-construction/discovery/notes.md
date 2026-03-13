# Discovery Notes: Score-Axis Pool Construction

**Date**: 2026-03-11
**Duration**: ~100s sweep (DuckDB in-memory)
**Universe**: Sports YES, 2025-07-01 to 2026-03-01

## Critical Finding: Prior Art Was Spurious

The cross-pool-consensus +16pp finding (score_axis vs random split) was based on:
- N=3 signals total in K=50 BUY-only configuration
- No hold>=4h filter (in-play contamination present)
- Tag base rate (33.3%) used instead of price-level base rate (~56% at avg price 0.62)

After all three corrections: excess_price = **-5.96pp** for the same K=50 N=1x1 BUY-only.
The prior "finding" was pure sampling noise from N=3 observations.

## Axis Orthogonality Confirmed (but Insufficient)

Spearman correlation = 0.46 for excess_hr vs consistency_sharpe in Sports. The axes ARE
genuinely orthogonal (not 0.95 as Politics). BUT orthogonality alone doesn't produce alpha
if Pool B (consistency_sharpe) traders consistently enter near-certainty markets (0.67+).

Pool B traders: avg excess_hr = 0.30-0.53, consistency_sharpe = 6-11. They are "stably
mediocre" traders — consistent monthly HR ~50-55% with low variance. Their agreement with
Pool A's sharp traders is not informative because they're all entering post-movement markets.

## The Core Problem: Dual-Pool AND-Gate Creates Two Failure Modes

**Failure mode A (BUY-only)**: The AND-gate requiring direct BUY YES from both disjoint pools
collapses to near-zero signals. Most Sports YES markets with hold>=4h don't have traders from
BOTH specialized groups entering via direct BUY. Result: 0-24 signals/8mo.

**Failure mode B (directional)**: Including SELL NO routes generates enough signals but the
consensus forms late in market resolution — at avg price 0.63-0.69. At that price, the
population HR is already 57-67%. Real alpha = +1-8pp above what any trader achieves.

## SELL Mode Sensitivity: Critical (>5pp)

BUY-only: 2 signals / 8 months at K=50 N=1x1
Directional: 65 signals / 8 months at K=50 N=1x1

The 30x signal difference reveals that the "score-axis pool construction" signal
as stated is fundamentally a SELL-mode-dependent signal. This is a structural issue:
the directional mode includes SELL NO (split-entry) positions, which are net-YES but not
direct evidence of conviction. The strategy degrades to "any trader with net YES exposure
from either pool = signal", which is much looser than the stated hypothesis.

## Parameter Sensitivity

All viable combos are FRAGILE:
- K-25 from any working K: 0 signals (strategy turns off entirely)
- K+25: HR drops 7-10pp (fragile threshold = 5pp)

This fragility suggests the signal is driven by a small set of traders near the
K-boundary. When K expands, different (weaker) traders enter, diluting quality.
When K contracts, cross-pool overlap collapses to zero signals.

## Recommendations for Revised Hypothesis

1. Add max_price=0.55 gate: forces signals to fire before markets are near-certainty.
   This would eliminate the K=50/K=100 directional signals (most fire at 0.67+) but
   the survivors would have genuine price-level alpha.

2. Test Sports NO direction: NO signals fire at lower prices (0.20-0.40) where
   price-level base rate = 20-40% and excess from skilled traders is more visible.

3. Single Pool A (top-100 excess_hr) with price gate is simpler and likely better:
   The AND-gate between Pool A and Pool B adds almost nothing when Pool B is "consistently
   mediocre". Just use Pool A with a price ceiling.

## Surprising Patterns

- November 2025 spike: 12 signals at 83.3% HR, avg PnL +$0.205 (K=50 N=1x1 dir)
  → suspect sports season effect (NBA/NFL season start). Not in Aug/Sep/Oct at same rate.
- Pool A traders often fire BEFORE Pool B (pct_a_first = 0.51-0.75 in some combos)
  → excess_hr traders enter the market earlier than consistency traders (as expected)
  → This supports the "excess_hr traders are faster" hypothesis

## Proposed Classifications

If this hypothesis is revisited:
- `sports_yes_excess_hr_pool`: Top-K by excess_hr (Pool A)
- `sports_yes_consistency_sharpe_pool`: Top-K by consistency_sharpe excluding Pool A (Pool B)

These would be useful for the revised hypothesis variants listed above.
