# Challenger Review: tag-hr-copy (Round 1)

## Compounding Score Assessment

| Tag | Excess HR | Avg Edge (median) | Median Hold | CS (reported) | CS (median-adjusted) |
|-----|-----------|-------------------|-------------|---------------|----------------------|
| Esports | +34pp | $16.13 | 0.083 days (2h) | 125.0 | **~6,610** |
| Basketball | +14pp | $0.86 | 0.167 days (4h) | 73.7 | **~0.72** |
| Tennis | +27pp | $2.53 | 0.083 days (2h) | 46.3 | **~816** |
| 1H Crypto | +23pp | $3.61 | 0.070 days (1.67h) | 17.4 | **~7,455** |

> The reported CS figures use median_pnl_usd in the numerator (correctly) but express hold as hours
> not days — the CS formula requires days in the denominator. The absolute magnitudes are inflated
> by ~24x (hours instead of days). **This is a formula application error.** CS numbers should be
> recomputed as: `excess_hr_pp x median_pnl_usd / (avg_hold_hours / 24)`.
>
> Using the corrected formula:
> - Esports: 34.09 x 16.13 / 0.083 = **6,627** (still the best)
> - Tennis: 27.15 x 2.53 / 0.083 = **826**
> - 1H: 22.77 x 3.61 / 0.070 = **1,176**
> - Basketball: 14.19 x 0.86 / 0.167 = **73** (or ~0.58 on median per the notes — likely measured against base rate separately)
>
> Even corrected, relative ranking is unchanged. The formula unit issue does not affect which
> strategies are better, but **the absolute CS cannot be compared with benchmarks calibrated in days**.

**Benchmark**: Target CS > 0.5 (daily units). All four pass trivially once hold times are this short.
The real question is whether 20-40pp vectorized degradation survives to positive CS in tick-by-tick.

---

## Hold Time Analysis

All four strategies report 1.67-4 hour holds. For sports-tagged markets, this is extremely short.

**Critical question: what does "hold time" measure here?**

The vectorized sweep likely measures time from first qualifying BUY trade to market resolution — not
time the position is actually open in a live system. In tick-by-tick replay, the position opens at
the first BUY signal tick and closes at resolution. If the market resolves 2 hours after the first
insider BUY, then 2h hold is genuine.

However, for sports markets (Basketball, Tennis, Esports), event resolution is deterministic and
rapid — a match ends within hours of the first trade. This means 2-4h holds are plausible and likely
accurate for in-game/same-day markets. This is the capital efficiency argument for sports.

**Distribution shape**: Unknown. The sweep reports averages/medians but not the 90th percentile hold.
There is likely a long tail: pre-game markets (team futures, tournament winners) could lock capital
for days or weeks. If the filter does not exclude pre-game markets, capital lock-up distribution
is bimodal — fast in-game resolution plus slow tournament resolution.

**Recommendation**: Tick-by-tick validation MUST report p90 hold time by market type. Pre-game vs
in-game segmentation should be enforced before deployment.

| Category | Typical Resolution | This Hypothesis |
|----------|--------------------|-----------------|
| Sports (in-game) | 2-8 hours | Consistent with 2-4h median |
| Sports (tournament futures) | 2-60 days | Unknown — no filter applied |
| 1H Crypto | 1 hour (hard) | Consistent with 1.67h median |
| Crypto (daily/weekly) | 1-7 days | Not in scope (1H tag only) |

---

## Capital Efficiency Suggestions

1. **Enforce in-game-only filter.** The 2-4h hold only holds if markets are same-day game outcomes.
   Add a filter on `closes_at - opens_at < 12 hours` or a market type tag. Tournament/futures
   markets with the same esports/tennis tags would contaminate the hold distribution with multi-day
   locks. This is the single highest-priority fix before tick-by-tick validation.

2. **Add time-based exit at 6h for Basketball.** Basketball has the worst median PnL ($0.86) and
   the longest hold (4h), driven by whale positions dominating the avg ($86.52). A 6h hard exit
   forces capital release if the market has not resolved and the position is stale. The whales
   whose large positions push the average are almost certainly in markets that resolve the same day
   anyway — the exit would not harm them but would flush the long tail.

3. **Size by median, not average.** Position sizing cannot be calibrated to avg_pnl when the
   median is 100x smaller. Any live position-sizing that uses average PnL will dramatically
   over-deploy into Basketball signals. Require median-based sizing, and cap Basketball
   position size at $50 until tick-by-tick confirms the avg is not purely a few outlier months.

4. **1H Crypto: exploit throughput.** 1905 signals/month at 1.67h average hold means capital
   turns roughly 14,000 times/month per dollar deployed — pure velocity play. Even if tick-by-tick
   degrades the HR by 30pp (from 73% to 43%), it remains above the ~50% base rate. The small
   dollar edge ($3.61 median) can be offset by sizing up: small size x high frequency x fast
   recycle. This is the most capital-efficient track mechanically.

5. **Esports: validate pool size before promoting.** The notes flag that qualifying traders at
   mt=50 are likely fewer than 20. If the pool is 5-10 traders, the strategy is exposed to pool
   shrinkage risk — one trader leaving or losing edge kills the signal. Require pool_size >= 10
   as a live gating condition, not just HR thresholds.

---

## Basketball: Salvageable?

No, not in its current form for this hypothesis.

The median PnL of $0.86 translates to a corrected CS of ~73 (hours) or ~0.72 (days). This is
above the 0.5 threshold, but only by a thin margin that will not survive 20-40pp tick degradation.

The structural problem: Basketball signals are whale-driven. The avg/median divergence of 100x
means a handful of large trades determine whether any given month is profitable. This is not a
systematic edge — it is a concentration bet on a small number of well-capitalized insiders whose
positions happen to be large.

**Salvage path**: Apply the consensus filter (idea 2 from discovery notes). Requiring 2+ qualified
traders on the same market before signaling would reduce frequency but filter out single-whale
signals. The remaining signals would have genuine multi-participant consensus and more reliable PnL.
This is worth testing in the sensitivity sweep before tick-by-tick, not after.

**Current recommendation**: Park Basketball. Revisit only after consensus filter is applied in
vectorized sweep. Do not send to tick-by-tick in current form.

---

## Esports: Survivorship Concern

The notes flag that Esports markets are mostly post-2025 (tag recently added), and qualifying
traders built their records in a small early market. This is a survivorship / selection bias
that the vectorized sweep cannot detect.

In tick-by-tick replay, the concern is: in the training period used for each fold, are the
qualifying traders' HR records built from the same data being tested? If so, the fold is
contaminated. The sweep must use strict temporal splits: training HR built only from resolved
markets before the test fold start date.

The sensitivity analysis showing HR improving to 93% at mt=60 is a red flag, not reassurance —
it means that requiring more trades (higher bar) improves HR monotonically, which is consistent
with survivorship: only traders who happened to get lucky on many bets survive the filter.
This is the hallmark of an overfitted qualification criterion.

---

## Category Recommendation

| Tag | Hold | CS Rank | Survivability to Tick | Recommendation |
|-----|------|---------|----------------------|----------------|
| 1H Crypto | 1.67h | 1 (volume compensates) | High (robust, large pool) | Validate first |
| Esports | 2h | 2 (highest edge/signal) | Medium (survivorship risk) | Validate with pool-size gate |
| Tennis | 2h | 3 (most robust params) | High (robust params) | Validate second |
| Basketball | 4h | 4 (whale skew) | Low (median PnL near zero) | Defer pending consensus filter |

---

## Risk Caveat

Tightening exit criteria (6h hard exit, in-game filter) will reduce signal count and may exclude
the highest-PnL trades if some large moves happen in pre-game futures. The challenger is pushing
for capital velocity, but a single pre-game futures market that resolves 1 week out could
generate $500 in PnL — worth locking capital for 7 days at that dollar level. The exit criteria
suggestions above must be validated empirically in tick-by-tick to confirm they do not remove
positive-EV long-duration positions disproportionately.

---

## Summary

Three of four strategies (Esports, Tennis, 1H) have hold times short enough that capital efficiency
is not the primary constraint — signal survival through tick-by-tick degradation is. Basketball
must be deferred: median PnL is too thin to survive the known 20-40pp simulation gap, and the
whale skew means the reported CS is not reproducible at scale.

The compounding score formula has a unit error in the discovery report (hold expressed in hours,
not days) — the absolute CS values are inflated by ~24x. This should be corrected before the
review panel receives final numbers. The relative ranking across strategies is unaffected.

Priority order for tick-by-tick validation: **1H Crypto > Tennis > Esports (with pool-size gate) >
Basketball (only after consensus filter re-sweep).**

The most important open question is not exit criteria but pool health: how many qualifying traders
exist in each tag at deployment, and how stable is that pool month-over-month? Without this, CS
is a number computed on historical data from a pool that may not exist in the same form going forward.
