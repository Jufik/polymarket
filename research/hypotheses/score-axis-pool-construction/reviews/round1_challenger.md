# Challenger Review: score-axis-pool-construction (Round 1)

**Date**: 2026-03-11
**Reviewer**: Challenger (capital efficiency lens)
**Source artifacts**: discovery/results.json, discovery/analysis.md

---

## Compounding Score Assessment

The compounding score formula is `excess_hr x avg_edge_usd / median_hold_days`.

Working from the best-case configuration (K=50 N=1x1 directional):

- Excess HR (price-level adjusted): **+8.2pp** (0.082)
- Avg edge per trade: **+$0.034** (vectorized upper bound)
- Median hold: **0.22 days** (5.2 hours)
- Reported compounding score: **0.059**

Cross-checking against benchmarks:

| Strategy | CS | Notes |
|----------|----|-------|
| Sports v3 | 148 | Production benchmark |
| Esports | 55 | Category comparison |
| score-axis best (UB) | 0.059 | This hypothesis, upper bound |
| score-axis best (realistic) | **negative** | After 20-40pp tick degradation |

The reported CS of 0.059 is itself an upper bound. After the known vectorized-to-tick gap (20-40pp), the price-level-adjusted excess of +8.2pp becomes approximately -12pp to -32pp, making the realistic CS firmly negative. The hypothesis is not capital-efficient at any parameter setting explored.

---

## Hold Time Analysis

- Median: **5.2 hours (0.22 days)** for the best combo
- P25: 4.4 hours — right at the mandatory hold filter floor
- P75: 8.8 hours
- Distribution shape: **tight, concentrated near the 4h minimum**, suggesting most signals are forced into the hold window rather than naturally holding

Capital turns per month:
- At 5.2h median hold, theoretical turnover is ~140x/month per dollar deployed
- This is theoretically excellent for compounding — Sports resolves in ~8 days, but individual position exposure is under half a day

The hold time profile is the single favorable dimension of this hypothesis. Short holds mean capital is not locked. However, extremely fast turnover combined with near-zero or negative edge-per-trade means losses compound just as efficiently as gains would. Fast recycling amplifies negative edge, not just positive edge.

The tight clustering around 4.4h (P25 = 4.4h, median = 5.2h) is a structural concern: this is not organically short-hold behavior — it is the minimum the hold filter permits. True hold time would be the market resolution time (days), but the strategy measures pool agreement windows. Actual capital remains deployed until market resolution.

---

## Capital Efficiency Suggestions

**1. Abandon this construction as a standalone strategy.**

The dual-pool AND-gate is consuming signal throughput to produce phantom confidence. K=50 directional gives 65 signals across 8 months (8/month). K=100 directional gives 498 signals but at +$0.001/trade — statistical noise. There is no viable K where the AND-gate produces both adequate volume and adequate edge.

**2. Strip to single-pool, apply a price ceiling.**

The analysis explicitly flags this: "sports-yes-single-pool-price-gated [HIGH]". A top-100 excess_hr pool with a max_price=0.55 gate would force signals into price territory where the population HR is 40-55%, giving genuine room for a +5-8pp excess to matter. The dual-pool construction is a red herring — the edge problem is price-level, not pool orthogonality.

**3. Pivot to Sports NO direction immediately.**

The analysis notes Sports NO base rate = 58.8% vs YES = 41.2%. NO signals fire at lower prices (0.20-0.40) where population HR is 20-40%. A +8pp excess over a 30% base rate is worth 11 cents per $1 bet, versus a +8pp excess over a 65% base rate being worth 5 cents. Same absolute excess, more than double the capital return per dollar. The spawned idea "score-axis-no-direction-sports" should be prioritized ahead of any further dual-pool YES work.

**4. If dual-pool construction is retained for investigation, require N >= 50 signals per monthly slice for any statistical claim.**

August 2025 produced 18 signals at 44.4% HR — below break-even for a 0.67 avg entry price. This one month contaminates the entire dataset. The strategy has insufficient throughput to absorb monthly variance. Any configuration producing fewer than 50 signals per month cannot be validated at discovery stage.

---

## Category Recommendation

- Current category: Sports (typical resolution: ~8 days from entry to resolution)
- Hold time measured: 5-9 hours (pool agreement window, not actual capital lock-up)
- Actual capital lock-up is the residual from entry to resolution: approximately 8 days minus 5 hours, which is still approximately 8 days

From a capital efficiency standpoint, the 5-hour "hold" is misleading. The trader holds the position until the market resolves, not until the second pool agrees. The pool agreement gap (median 0 to -2.4 hours) is almost simultaneous — the pools are agreeing at roughly the same time, not in sequence. The hold measured here is pool-entry to pool-entry, not entry to resolution.

If the actual capital lock-up is ~8 days (sports resolution), the revised CS calculation is:

- CS(realistic) = 0.082 x $0.034 / 8 = **0.000035** (vectorized upper bound, per-resolution-day)

This is three orders of magnitude below Sports v3 and Esports benchmarks. No implementation effort is justified at this capital efficiency level.

---

## Risk Caveat

The one parameter worth watching is the spawned idea "score-axis-no-direction-sports" (Sports NO direction). If Pool A agrees on NO (implied by SELL YES or BUY NO routes), that fires at 0.20-0.40 price, where the signal-to-noise ratio is structurally better. Abandoning the dual-pool construction prematurely forecloses this variant. However, this is a new hypothesis, not a refinement of the current one.

Additionally, the last 5 months of data (Oct 2025 - Feb 2026) show HR of 82-89% for K=50 N=1x1 directional. If this represents a regime shift or seasonal effect, the trailing performance would look attractive to any forward-looking paper trade. The challenger position is that 65 total signals over 8 months is insufficient to distinguish regime from noise — any 5-month window with 37 signals can produce this variance.

---

## Summary

This hypothesis is not worth implementation effort in its current form. The reported compounding score of 0.059 is itself a vectorized upper bound — after accounting for the known 20-40pp tick degradation against a price-level-adjusted excess of only +8.2pp, the realistic CS is negative. The hold time data is structurally misleading: the 5-hour figure measures pool agreement windows, not actual capital lock-up, which follows sports market resolution (~8 days). Adjusting for true lock-up duration puts this hypothesis at CS ~ 0.000035, roughly 1/1,000,000th of the Sports v3 benchmark. The dual-pool AND-gate construction does not produce useful orthogonality — Pool B (consistency_sharpe) traders are entering at confirmation prices (0.67+) where population HR is already 65-67%, leaving no room for their agreement to add information. The capital is better deployed pursuing the spawned ideas: single-pool with price ceiling (HIGH priority), or Sports NO direction (MEDIUM priority), both of which address the root cause (entry price too high) rather than adding complexity.
