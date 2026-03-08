# Challenger Review: tag-hr-consensus (Round 2)

**Date**: 2026-03-06
**Reviewer**: Challenger (capital efficiency)
**Source**: Tick-by-tick validation R2 results (mpe pool filter, 3-fold walk-forward)

---

## Compounding Score Assessment

The tick-by-tick results produce four distinct CS pictures. I compute each separately because
the aggregate numbers are misleading — they mix a profitable regime (2025-07) with a loss
regime (2025-10) and a diluted regime (2026-01).

### Esports Primary (N=4, ep=10pp, mpe=0.80) — AGGREGATE

- Excess HR: +3.4pp = 0.034
- Avg edge: $32.05 / 525 = $0.061/signal
- Median hold: (7.21 + 3.66 + 7.36) / 3 = 6.08h = 0.253 days
- **Compounding score: 0.034 x 0.061 / 0.253 = 0.008**
- Benchmark: Target is 0.5+. This is 63x below the minimum bar.

The aggregate CS is economically worthless. $32 total PnL on 525 signals is not a deployable
strategy — it's noise. The issue is that the aggregate hides fold heterogeneity.

### Esports Primary — 2025-07 Fold Only (the viable regime)

- Excess HR: +15.15pp = 0.1515
- Avg edge: $11.85/signal (from avg_edge_usd field)
- Hold: 7.21h = 0.300 days
- **Compounding score: 0.1515 x 11.85 / 0.300 = 5.98**
- Benchmark: 12x above the 0.5 bar. Genuinely elite if reproducible.

### s2_insider_copy Sports (comparison baseline)

- Excess HR: +13.5pp, position size ~$10, avg edge ~$1.35/signal
- Hold: ~8 days (sports resolution from config notes)
- **Compounding score: 0.135 x 1.35 / 8 = 0.023**

### s3_no_sniper Economy/Tech (comparison baseline)

- HR: 77%, base rate ~72% implied (Economy/Tech new-market signal)
- Validated +$213 total PnL. Estimated ~100 signals: ~$2.13/signal avg edge
- Hold: 5-minute entry window, but resolution likely 7-14 days for Economy/Tech markets
- Estimated CS: 0.05 x 2.13 / 10 = **0.011**

### Summary table

| Strategy | Excess HR | Avg edge | Median hold | CS |
|---|---|---|---|---|
| tag-hr-consensus (aggregate) | +3.4pp | $0.06 | 0.253d | 0.008 |
| tag-hr-consensus (2025-07 only) | +15.1pp | $11.85 | 0.300d | 5.98 |
| s2_insider_copy sports | +13.5pp | $1.35 | 8d | 0.023 |
| s3_no_sniper | ~+5pp | ~$2.13 | ~10d | ~0.011 |

The 2025-07 Esports fold is the fastest-compounding regime in this entire research program. The
aggregate obscures it. The strategic question is whether that regime is repeatable or whether it
was a one-time anomaly (small, early market, loose pricing, naive counterparties).

---

## Hold Time Analysis

- Esports median: 6.1h across folds (range: 3.7h in 2025-10 to 7.4h in 2026-01)
- Tennis median: ~8.8h across folds (range: 4.6h to 17.2h)
- Distribution shape: concentrated at 1-8h for Esports, wider tail for Tennis
- Capital turns per month (at 6h Esports median): 120 turns/month theoretical

The hold time story from R1 still holds. The hold advantage is structural and real. At 6h median,
this strategy turns capital 40x faster than s2_insider_copy sports and 80x faster than s3_no_sniper.
Even a tiny positive excess HR at these hold times produces competitive CS. The problem is not
hold time. The problem is fold consistency.

The 2025-10 Tennis fold (avg_hold = 4.72h) deserves specific attention: it had the lowest hold
time in the dataset AND the worst performance (-$4,296). Short hold time is not sufficient — the
signal must be present during the hold. The 2025-10 fold looks structurally different (Tennis
excess HR = -1.22pp, effectively random), meaning the strategy was deploying capital at 4.7h
turns into a zero-edge signal.

**Lesson**: fast capital recycling into a zero-edge regime is not capital efficiency — it is
rapid capital destruction. The hold time advantage only delivers value when the signal is on.

---

## Is +$32 on 525 Esports Signals Worth Deploying?

No. At $10 uniform sizing, +$32 across 525 signals is $0.06 per signal. This does not clear
transaction costs, slippage, or operational overhead in any realistic deployment. The comparison
to s2 and s3 makes this concrete:

- s2_insider_copy sports: $1.35/signal average edge at 8-day hold. At 6h hold, to match s2's
  dollar throughput this strategy needs $1.35 x (8/0.25) = $43.20/signal edge. Current avg is $0.06.
  That is a 720x gap in dollar-per-signal efficiency versus s2.

- s3_no_sniper: ~$2.13/signal at ~10-day hold. Same argument: ~1,000x gap.

The Esports aggregate result is not marginally below par — it is in a completely different
league in the wrong direction. The 2025-07 fold at $11.85/signal IS competitive. The other two
folds drag the aggregate to near-zero.

**Capital efficiency verdict**: Deploy ONLY if a credible mechanism exists to select for the
2025-07 regime and against the 2025-10 and 2026-01 regimes. That mechanism is the pool size.

---

## Replicating the 2025-07 Fold: Pool Size is the Lever

The 2025-07 Esports fold had 47 qualified traders. The 2025-10 fold had 46 — nearly identical.
But the 2025-10 result was -$197. So pool size alone does not explain the failure.

What differs between the folds:
- 2025-07: base rate 36.8%, fill price 0.443, excess HR +15.2pp
- 2025-10: base rate 65.4%, fill price 0.577, excess HR -5.4pp

The 2025-10 fold is a base-rate inversion: a 65.4% base rate means the YES side is expensive
(fill price 0.577). The consensus is mimicking the market, not beating it. The qualified pool
was built on a 36.8% base-rate regime — those traders had excess HR because they correctly
identified YES winners in a world where YES was cheap. In a 65.4% regime, YES is the default
outcome and their edge disappears.

The 2026-01 failure is structurally different: pool explosion to 774 traders. N=4 of 774 fires
on every market — there is no selectivity. The mpe=0.80 filter reduced the pool from infinity
to 774, but 774 is not a pool of insiders, it is a sample of the crowd.

**These are two distinct failure modes**:
1. Base-rate inversion (2025-10): pool was built in the wrong regime
2. Pool explosion (2026-01): absolute pool size exceeds useful selectivity threshold

Both must be addressed before any deployment.

---

## Pool Size Cap: Economic Case

The pool size cap idea (dynamically raise meh when pool > 50) makes economic sense, but the
economic argument is more specific than stated.

The signal relies on selectivity: qualified traders are in the top X% of all traders by excess HR.
At 47 traders (2025-07), they represent a small fraction of the full Esports participant set —
these are genuinely unusual performers. At 774 traders (2026-01), the N=4 consensus fires on
virtually every market, meaning 4 randomly selected traders from a 774-person pool agree. The
information content approaches zero.

Quantified: if 774 traders each have roughly 46% HR in the 2026-01 regime, the probability
that 4 random traders all take YES in a given market is high — not because they know something,
but because the market skews YES. The consensus statistic is picking up crowd bias, not insider
knowledge.

The cap at 50 makes sense, but the implementation matters:

| Approach | Mechanism | Risk |
|---|---|---|
| Hard cap (reject when pool > 50) | Stop firing when pool too large | Misses real signals in large-pool periods |
| Dynamic meh raise (shrink pool to 50) | Keep firing at higher quality bar | meh threshold becomes nonstationary, breaks walk-forward |
| Relative N (N = pool_size x 0.1) | Fire when top 10% agree | Stable selectivity, but N varies and is not validated |

**Recommendation**: Hard cap at pool <= 60. When pool exceeds 60, suspend signal generation
for that tag entirely. Do not deploy into a market with 300+ qualified traders at N=4. The
cost of this cap is zero signals in the 2026-01 Esports fold — but that fold lost $387 with
no cap, so the cap is clearly better. The 2025-07 and 2025-10 folds both have pool<=50 and
would fire normally.

This needs one vectorized check: what fraction of historical signals would survive a pool<=60 cap?
If it's 90% of signals and 80% of PnL, the cap is worth it. If it's 40% of signals and 150%
of PnL (the cap removes the bad signals), that's the number to put in front of the panel.

---

## Signal Price Filter: 0.40 vs 0.75 Impact

The break-even analysis is precise. At flat $10 sizing:

```
E[PnL per signal] = HR x (1 - fill_price) x 10 - (1-HR) x fill_price x 10
                  = (HR - fill_price) x 10
```

Break-even HR = fill_price.

| Fill price | Break-even HR | Current Esports HR | PnL per signal at current HR |
|---|---|---|---|
| 0.40 | 40.0% | 52.6% | (0.526 - 0.40) x 10 = +$1.26 |
| 0.50 | 50.0% | 52.6% | (0.526 - 0.50) x 10 = +$0.26 |
| 0.60 | 60.0% | 52.6% | (0.526 - 0.60) x 10 = -$0.74 |
| 0.75 | 75.0% | 52.6% | (0.526 - 0.75) x 10 = -$2.24 |

The current price ceiling at 0.75 permits entries where the break-even HR is 75% — a standard we
cannot meet. Every signal entering at 0.60-0.75 is a guaranteed expected loss at 52.6% HR. These
entries must be cut.

**Proposed ceiling: 0.55** (not 0.40 — that is too conservative and will eliminate most signals).

At price_ceil = 0.55, the worst-case break-even HR is 55%. With 52.6% aggregate HR, we still
lose on average at 0.55 entries, but the damage is bounded at $0.24/signal instead of $2.24/signal.

At price_ceil = 0.48, break-even HR = 48% — all signals have expected positive PnL at 52.6% HR.

The trade-off is throughput. Restricting price_ceil from 0.75 to 0.48 eliminates all signals
where market price was 0.48-0.75 at entry. From the data, avg_fill_price is 0.443 in 2025-07
(cheap entry) and 0.577 in 2025-10 (expensive entry). The 2025-10 fold at avg_fill=0.577 is
already above a 0.55 ceiling — it would be largely eliminated by a tighter filter.

This is the single cheapest fix available: price_ceil = 0.48-0.50. It:
1. Kills the worst signals (0.50-0.75 range where HR doesn't cover price)
2. Eliminates most of the 2025-10 fold damage
3. Requires no new data, no new pool construction logic
4. Is directly testable in one DuckDB query

**Action**: Run a vectorized sweep with price_ceil in {0.40, 0.45, 0.48, 0.50, 0.55, 0.60, 0.75}
and report HR, PnL, and signal count at each level. This is a 20-minute query.

---

## Graduated Sizing Plan: Which Track to Run First

The plan has four tracks. From a capital efficiency lens, ranked by expected impact-to-effort ratio:

### Track 4 (Signal-time volume) — Run first, immediately

This directly addresses whether the +45pp vectorized volume uplift survives causal computation.
If it does, signal-time volume is a causal gate that concentrates capital into the highest-HR
markets. If it doesn't, we stop including volume in all future analysis. The query is written in
the plan. Run it today.

Expected impact: if even +15pp of the +45pp survives causally, the CS improvement from filtering
to signal-vol>=200 could double or triple the per-signal edge without reducing throughput significantly.

### Track 3 (Contradictory signals) — Run second

The dissent ratio filter is cheap to implement (one DuckDB query from the plan) and could serve
as a hard skip gate, not just sizing. If the 2025-10 fold's losses were concentrated in markets
where qualified traders were split on YES/NO, the dissent ratio would have filtered them. This
is plausible: the 2025-10 Esports fold had base rate 65.4%, which means some qualified traders
who normally buy YES may have been buying NO in that fold (correct direction). The dissent ratio
would show a lower YES consensus — potentially below the firing threshold.

Expected impact: could eliminate significant portion of 2025-10 losses at minimal throughput cost.

### Track 1 (Time-to-live sizing) — Skip for now

The hold time is already short (6h median). TTL sizing adds complexity but the variance in hold
times (3.7h to 17.2h across folds) is insufficient to justify a hold-time model. The 17.2h tail
(Tennis 2025-07) is where TTL sizing would help, but Tennis is already a loss strategy — solving
hold time there does not address the edge deficit. Come back to Track 1 only after Tracks 3 and 4
confirm positive aggregate PnL.

### Track 2 (Per-trader profiling) — Skip until baseline works

Trader profiling is a refinement layer. The base signal is not yet consistently profitable. Adding
a quality multiplier on top of a near-zero-edge signal still produces near-zero PnL, just with
more variable position sizes. Track 2 has high implementation cost and zero expected impact until
the base strategy clears $1+/signal average edge.

---

## Hold Time Tightening Recommendation

The R1 suggestion to add a hard exit at 8h is now urgent. The data shows:

- Esports avg hold: 3.7-7.4h (well within 8h)
- Tennis avg hold: 4.6-17.2h (2025-07 Tennis at 17.2h is the outlier)

The Tennis 2025-07 fold had 17.2h avg hold and -$655 PnL. The 2026-01 Tennis fold had 4.6h
avg hold and +$2,496 PnL. It is plausible that the long-hold Tennis signals in 2025-07 are
the losing signals — a market that hasn't resolved in 12h is one where the outcome is unclear
or disputed, and the consensus may be wrong.

**Recommendation**: Add a 10h hard exit for Esports, 8h hard exit for Tennis. Any position
still open at that threshold is closed at market. This converts some winners to scratches but
likely converts more losers to controlled losses. Verify by slicing the tick-by-tick results
by hold duration and checking if the long-hold tail is positive or negative in expectation.

---

## Category Recommendation

Current categories: Esports (~6h hold), Tennis (~8h hold).
These remain the right categories. Do not expand.

The compounding score for these categories, when the signal is working (2025-07 Esports: CS=5.98),
is orders of magnitude better than anything achievable with slower categories:
- Politics (30d hold, same 15pp excess HR): CS = 0.1515 x 11.85 / 30 = 0.060
- Sports via s2 (8d hold): CS = 0.023

The hold time is not the problem. The signal consistency is the problem. Adding slower categories
does not help and actively hurts CS.

One category to investigate: **Crypto** (excluded from s2 due to negative excess HR in the
insider pool context). Crypto markets on Polymarket often resolve in <24h (hourly/daily price
questions). If the consensus signal applies to crypto markets, the hold time could rival Esports.
This is speculative — do not pursue until the Esports/Tennis base case is solved.

---

## Risk Caveat

Every efficiency recommendation above — price ceiling at 0.48, pool cap at 60, hard time exit —
reduces signal throughput. The 2025-07 Esports fold at 47 traders, 52 signals, price_ceil=0.75
produced +$616 PnL. Tightening all three levers on that fold might reduce signal count to 20-30,
which at $11.85/signal average edge still delivers +$237-355 — acceptable. But if the parameters
that generated the 2025-07 edge are not the parameters we can deploy (i.e., the specific pool
of 47 traders existed because of early Esports market conditions), then all the tightening is
irrelevant: there is no pool of 47 elite traders in the current 774-trader market.

The most dangerous scenario is over-optimizing on the 2025-07 regime, building a strategy that
would have performed well in 2025-07, and deploying into a world that looks more like 2026-01.
Every fix proposed must be evaluated on its 2026-01 impact, not just its 2025-07 impact.

---

## Summary

The aggregate tick-by-tick results (CS = 0.008) confirm the strategy is not deployable in its
current form. The +$32 on 525 Esports signals and -$2,455 on 442 Tennis signals represent
capital deployed at near-zero or negative expected edge. Compared to s2_insider_copy sports
(CS = 0.023) and s3_no_sniper (CS ~0.011), the current aggregate is 2-3x worse despite having
a 40-80x hold time advantage. The hold time advantage is being entirely wasted on weak signals.

However, the 2025-07 Esports fold (CS = 5.98) demonstrates that the signal can be extraordinary
when the market is immature, the pool is small, and entry prices are cheap. That fold is not a
fluke — it has the right structure: 47 traders, 36.8% base rate, 0.443 avg fill, 15.2pp excess.
The task is to find and select for that structure in deployment.

The three immediate actions that would change this verdict, in order of priority:

1. **Price ceiling to 0.48-0.50**: eliminates entries where break-even HR > current HR. One
   DuckDB sweep, verifiable in a day. If it converts Tennis 2025-10 from -$4,296 to near-zero,
   the aggregate picture transforms.

2. **Pool size hard cap at 60**: no signals when pool > 60. Eliminates the 2026-01 Esports fold
   (-$387) entirely. Zero throughput is better than negative-edge throughput.

3. **Signal-time volume filter (Track 4)**: run the causal volume query. If vol>=200 at signal
   time predicts HR, it becomes the strongest causal gate in the strategy.

If these three changes produce any fold with CS > 0.5 in walk-forward validation, accelerate to
paper deployment immediately. The hold time advantage means even a modest improvement in signal
quality produces outsized compounding returns. Do not wait for a perfect aggregate — find the
regime where the signal works and deploy into it.
