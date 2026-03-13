# Score-Axis Pool Construction — Discovery Analysis

**Date**: 2026-03-11
**Status**: UPPER BOUNDS ONLY — vectorized, 20-40pp optimistic vs tick
**Sweep runtime**: 93s (DuckDB)

---

## Executive Summary

The score-axis dual-pool construction (Pool A = top-K excess_hr, Pool B = top-K consistency_sharpe,
disjoint by construction) **does produce signal above tag base rate** in directional mode,
but the **price-level-adjusted excess is thin (+1-8pp)** and all top combos are **FRAGILE**
(HR drops 7-10pp with K±25).

The prior art +16pp finding (cross-pool-consensus) was based on **3 signals** and
**did not apply the hold>=4h filter**. After corrections, the same K=50 N=1x1 BUY-only
configuration yields only 2 signals and negative PnL.

**Verdict: MARGINAL** for directional mode (not "promising" as auto-assigned by script —
see price-level analysis below). **NO-GO for BUY-only** (signals too thin).

---

## Prior Art Verification

The pre-mortem raised three concerns. All three verified:

| Filter | Applied in prior? | Impact |
|--------|------------------|--------|
| hold >= 4h | NO | 1/3 signals dropped (33%), HR drops +16.7pp → −5.96pp price-adj |
| first_trade >= test_start | YES (code inspection confirms) | No additional impact |
| Price-level base rate | NO | Tag base 33.3% vs price-level 55.96% at K=50 avg price 0.62 → real excess = -5.96pp |

**Conclusion**: The prior +33.4pp figure was computed on N=3 signals without hold filter
and against tag base rate (not price-level base rate). After corrections:
- K=50, N=1x1, BUY-only, hold>=4h: N=2 signals, HR=50%, **excess_price = -5.96pp** (NEGATIVE)
- The "+16pp over random split" is spurious — driven by N=3 in prior vs N=9 in random split

---

## Pool Construction Quality

| K | Pool A avg excess_hr | Pool A avg consistency_sharpe | Pool B avg excess_hr | Pool B avg consistency_sharpe |
|---|---------------------|------------------------------|---------------------|------------------------------|
| 25 | 0.747 | 13.0 | 0.530 | 10.7 |
| 50 | 0.716 | 10.5 | 0.357 | 7.9 |
| 100 | 0.637 | 7.6 | 0.295 | 6.1 |

Axis correlation (Spearman approx): **0.46** — genuinely orthogonal (NOT 0.95 as hypothesized
from Politics recon). Sports axes are truly separate dimensions. Pool B (consistency) traders
have substantially lower raw HR than Pool A (excess_hr) traders, confirming the axes select
different trader phenotypes.

Note: K=25 produces 0 signals in ALL combos after hold>=4h. K=50 directional N=1x1 is
the minimum viable configuration.

---

## Signal Volume Summary

| K | N_a x N_b | BUY-only signals | Directional signals |
|---|-----------|-----------------|---------------------|
| 25 | 1x1 | 0 | 0 |
| 50 | 1x1 | 2 | 65 |
| 50 | 1x2 | 0 | 16 |
| 50 | 2x1 | 0 | 0 |
| 50 | 2x2 | 0 | 0 |
| 100 | 1x1 | 24 | 498 |
| 100 | 1x2 | 4 | 203 |
| 100 | 2x1 | 2 | 93 |
| 100 | 2x2 | 0 | 65 |

BUY-only: catastrophically thin (max 24 signals/8mo). Universe collapse from the AND-gate
requiring two disjoint pools to both have direct BUY YES trades in the same market.

Directional: K=100 N=1x1 achieves 498 signals (62/month) — the only combo with adequate
statistical power for conclusions. K=50 N=1x1 has 65 signals (8.1/month) — borderline.

---

## Hit Rate Results (ALL UPPER BOUNDS)

### BUY-Only (all fragile, N < 30)

| K | N_a x N_b | N | HR | Excess(tag) | Excess(price) | Avg price | PnL/trade | CS |
|---|-----------|---|----|------------|--------------|-----------|-----------|-----|
| 100 | 1x2 | 4 | 50.0% | +16.7pp | **+25.0pp** | 0.349 | +$0.151 | 0.075 |
| 100 | 1x1 | 24 | 58.3% | +25.0pp | **+6.9pp** | 0.578 | +$0.006 | 0.007 |

> [!WARNING] FRAGILE: BUY-only K=100 N=1x2 has only 4 signals (2 months coverage).
> The +25pp price-level excess is sampling noise. A single trade flip changes HR by 25pp.

### Directional (SELL NO included as bullish entry)

| K | N_a x N_b | N | HR | Excess(tag) | Excess(price) | Avg price | PnL/trade | Med hold | CS | Fragile |
|---|-----------|---|----|------------|--------------|-----------|-----------|----------|----|---------|
| 50 | 1x1 | 65 | **70.8%** | **+37.5pp** | **+8.2pp** | 0.674 | +$0.034 | 5.2h | 0.059 | YES (K-sensitive) |
| 50 | 1x2 | 16 | 68.8% | +35.5pp | +6.2pp | 0.667 | +$0.021 | 9.2h | 0.019 | YES |
| 100 | 1x1 | 498 | 62.6% | +29.4pp | **+5.5pp** | 0.626 | +$0.001 | 5.6h | 0.001 | NO |
| 100 | 2x1 | 93 | 67.7% | +34.5pp | **+0.8pp** | 0.690 | -$0.012 | 7.2h | -0.014 | NO |
| 100 | 2x2 | 65 | 66.1% | +32.9pp | +1.4pp | 0.681 | -$0.019 | 8.7h | -0.017 | NO |

---

## Price-Level Base Rate Analysis (CRITICAL)

> [!CRITICAL]
> The strategy fires at avg entry prices of 0.63-0.69. At these price levels,
> the population Sports YES HR is 57-67%, not the tag base rate of 33.3%.
> The **price-level-adjusted excess is only +0.8pp to +8.2pp** — far smaller
> than the +29-37pp tag-level excess would suggest.

Price-level base rates by config:
- K=50 N=1x1 dir: avg price 0.674 → population HR at [0.62-0.72] = **62.6%** → real excess = +8.2pp
- K=100 N=1x1 dir: avg price 0.626 → population HR at [0.57-0.67] = **57.1%** → real excess = +5.5pp
- K=100 N=2x1 dir: avg price 0.690 → population HR at [0.64-0.74] = **67.0%** → real excess = +0.8pp

Best price-level-adjusted combo: K=50 N=1x1 directional at **+8.2pp** above
what ANY trader achieves at that entry price level.

Even at +8.2pp, after 20-40pp vectorized-to-tick degradation, expected tick excess ≈ -12 to -32pp.
The strategy likely does NOT survive tick validation.

---

## Hold Time Distribution

| K | N_a x N_b | sell_mode | Med hold | P25 | P75 |
|---|-----------|-----------|----------|-----|-----|
| 50 | 1x1 | directional | 5.2h | 4.4h | 8.8h |
| 50 | 1x2 | directional | 9.2h | 5.1h | 13.3h |
| 100 | 1x1 | directional | 5.6h | 4.6h | 11.2h |
| 100 | 2x1 | directional | 7.2h | 4.8h | 12.9h |

All combos: med hold = 5-9 hours. P25 = 4.4h (right at the mandatory minimum).
Hold time is short — capital recycling fast, but also means signals cluster in
active sports periods (July-August, November spike seen in monthly data).

---

## Sensitivity Analysis (FRAGILE)

All top-3 combos are FRAGILE:

| Combo | K_base | K-25 result | K+25 HR delta | Fragile? |
|-------|--------|-------------|---------------|----------|
| K=100 N=1x2 BUY | 100 | 0 signals | -10.0pp | YES |
| K=50 N=1x1 DIR | 50 | 0 signals | -7.0pp | YES |
| K=50 N=1x2 DIR | 50 | 0 signals | -6.6pp | YES |

All three collapse to 0 signals at K-25, and drop 7-10pp HR at K+25. This indicates the
signal is **highly sensitive to the exact pool membership** — small changes in which traders
are included/excluded cause significant outcome variance. Not robust.

The only "robust" combo (not fragile by the 5pp threshold): **K=100 N=1x1 directional**
(498 signals, excess_price=+5.5pp) — but this large pool dilutes the quality signal.

---

## SELL Mode Comparison

| K=50 N=1x1 | N | HR | Excess(price) | Avg price |
|------------|---|----|---------------|-----------|
| BUY-only | 2 | 50.0% | -5.96pp | 0.618 |
| Directional | 65 | 70.8% | +8.21pp | 0.674 |

SELL sensitivity = **critical** (>5pp HR gap). Directional and BUY-only are not comparable:
- BUY-only: requires direct BUY YES from both pools in same market — very rare
- Directional: includes SELL NO routes (Market Maker split entries) — adds 63 signals

The directional signals are firing on markets where both Pool A and Pool B traders have
net YES exposure regardless of how they acquired it. This likely captures more genuine
agreement signals but also includes noisy SELL NO (split-entry) positions.

---

## Monthly Consistency (K=50 N=1x1 Directional)

| Month | N | HR | Avg PnL |
|-------|---|----|---------|
| 2025-07 | 3 | 66.7% | -$0.054 |
| 2025-08 | 18 | 44.4% | -$0.085 |
| 2025-09 | 3 | 66.7% | -$0.131 |
| 2025-10 | 7 | 85.7% | +$0.047 |
| 2025-11 | 12 | 83.3% | +$0.205 |
| 2025-12 | 6 | 66.7% | +$0.073 |
| 2026-01 | 9 | 88.9% | +$0.021 |
| 2026-02 | 7 | 85.7% | +$0.127 |

First 3 months (Jul-Sep 2025): poor (avg HR ~60%, negative PnL). Last 5 months: strong
(avg HR ~82%, positive PnL). Possible seasonal effect (summer sports different from
fall/winter). Could also be in-sample overfitting given short history.

**Critical concern**: August 2025 has N=18 signals at only 44.4% HR — BELOW break-even
at avg entry price ~0.67 (BE=67%). This month drags down the overall performance.

---

## Verdict

| Dimension | Assessment |
|-----------|-----------|
| Prior +16pp validated? | NO — based on N=3, missing hold filter, wrong base rate |
| BUY-only viable? | NO — 0-24 signals, fragile, negative price-adj excess |
| Directional viable? | MARGINAL — +8.2pp price-adj excess (best case), but FRAGILE |
| Enough for tick validation? | BORDERLINE — K=100 N=1x1 dir has 498 signals |
| Compounding score vs threshold | BELOW — CS=0.059 best (threshold typically > 5.0) |
| Expected tick excess HR | -12 to -32pp (after 20-40pp degradation) → likely negative |

**RECOMMENDATION: DO NOT proceed to tick validation in this configuration.**

The dual-axis AND-gate produces a universe that is too thin (BUY-only) or fires at
entry prices where the population HR is already 57-67% (directional), leaving only
+5-8pp true alpha — insufficient to survive the vectorized-to-tick gap.

---

## Spawned Ideas

1. **score-axis-no-direction-sports** [MEDIUM]: Apply score-axis AND-gate to Sports NO direction.
   Sports NO base rate = 58.8% (much higher than YES 41.2%). Dual-axis agreement on NO may
   produce better price-level-adjusted alpha since NO signals fire at lower prices (0.20-0.40),
   where the population HR is 20-40% and genuine edge is more visible.

2. **sports-yes-single-pool-price-gated** [HIGH]: Instead of dual-pool construction, use
   SINGLE Pool A (top-100 excess_hr) with a MAX PRICE GATE of 0.55. Forces entry at lower
   prices where price-level base rate is 40-55%, and even modest skill improvement gives
   real edge. The K=100 N=1 single pool baseline likely works better at low price.

3. **sports-yes-dual-pool-price-ceiling** [MEDIUM]: Same construction but add max_price=0.55
   gate to ensure signals fire in genuine uncertainty zone, not near-certainty confirmation.
   If most directional signals are at 0.67-0.75, price gate would eliminate them and change
   the fundamental nature of the signal.

4. **consistency-sharpe-standalone** [LOW]: Pool B traders (consistency_sharpe) alone may have
   different HR profile than Pool A. Test consistency-only pool as standalone signal — if Pool B
   has lower but more stable HR, it may complement Portfolio Track A (elite whales).

---

## Surprising Findings

1. **Spearman correlation = 0.46, NOT 0.95**: The hypothesis framing said "Spearman 0.95 for
   Politics but 0.66 for Sports". The actual Sports Spearman in this sweep is 0.46 — even
   more orthogonal than expected. The axes are genuinely independent. But independence alone
   doesn't produce alpha if the AND-gate collapses the universe.

2. **Pool B traders have dramatically lower excess_hr (0.30-0.53) than Pool A (0.64-0.75)**:
   The consistency_sharpe rank selects traders who are "consistently mediocre" rather than
   "sporadically excellent". These traders have monthly HR~50-55% but very low variance.
   Their agreement with Pool A adds little — they are entering at confirmation prices
   (0.67+) where any decent trader wins 67%+ of the time.

3. **K=25 produces zero signals**: Even K=25×25=625 trader-pair combinations cannot find
   a market where both pools agree with hold>=4h. Sports YES markets with genuine hold time
   (not in-play) are rare in the test period — most are in-play results.

4. **K=100 directional N=1x1 has 498 signals but near-zero PnL (+$0.001/trade)**:
   With K=100 each pool, most qualified traders in the dataset are in one of the two pools.
   The AND-gate becomes very loose (equivalent to "any qualified trader" entered both pools,
   which is nearly every market). Signal quality degrades to baseline.

---

## Classification Proposals

Two classifications should be created if this hypothesis is revisited with modified parameters:

- **sports_yes_excess_hr_pool**: Top-K Sports YES traders by excess_hr training
- **sports_yes_consistency_sharpe_pool**: Top-K Sports YES by consistency_sharpe, excl Pool A
