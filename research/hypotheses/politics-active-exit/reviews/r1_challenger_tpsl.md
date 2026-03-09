# Challenger Review: politics-active-exit (Round 1)

> **Scope**: TP/SL + demand-driven eviction design for Politics NO v3 K=100 N=2.
> All sweep figures are vectorized UPPER BOUNDS unless noted. Tick-validated base
> strategy: +9.3% excess HR, Sharpe=0.55 (from `tick_results_v3.md`).

---

## Compounding Score Assessment

Working from the data provided:

| Config | Excess HR | Avg Edge (USD) | Median Hold (days) | Compounding Score |
|--------|-----------|----------------|--------------------|-------------------|
| Hold-to-resolution P=20 | +9.3% | $64.19 | 4.7 | **1.27** |
| Exit@25% P=20 (vectorized) | ~+20pp* | $102.48 | 1.4 | **14.6** |
| Exit@50% P=20 (vectorized) | ~+17pp* | $115.62 | 2.0 | **10.0** |

*Exit strategies report exit-adjusted HR (~92%), but the base rate is 73.6%. These excess figures
are inflated because early exits on winning positions count as "wins" even if they capture only
partial payout. The true alpha is the underlying +9.3pp excess HR — the exit mechanism is
a capital-recycling tool, not a new signal.

**Compounding score at Hold: 1.27**. This is reasonable for a politics leg (category benchmark
~2-3 days median hold is already 7-15x slower than Sports). Exit@25% pushes the score to
~14.6 — an 11x improvement if the sweep is believed at face value.

**Benchmark**: Sports YES v3 compounding score (tick): +30pp excess / $0.1067 avg edge / 6.9h hold
= **30 x 0.1067 / 0.29 days ≈ 11.0**. Interestingly, if Exit@25% works in production at even 30%
of its vectorized upper bound, Politics NO would rival Sports YES on capital efficiency. That is
the bullish case. The challenger must interrogate whether that assumption is warranted.

---

## Challenge 1: Is TP/SL Just "Don't Enter at 0.90+"?

The design posits a two-layer system: TP/SL for high-price entries (>0.80) plus demand-driven
eviction. The bucket data demolishes the case for this design:

**Bucket PnL (346 resolved positions, hold-to-resolution):**

| Bucket | N | Total PnL | Avg PnL | ROC/day |
|--------|---|-----------|---------|---------|
| <0.50 | 60 | +$35,854 | +$597.57 | +$0.141 |
| 0.50-0.70 | 12 | -$162 | -$13.50 | -$0.001 |
| 0.70-0.80 | 31 | -$314 | -$10.13 | -$0.001 |
| 0.80-0.90 | 53 | -$68 | -$1.28 | -$0.000 |
| 0.90+ | 190 | -$1,368 | -$7.20 | -$0.002 |

The strategy generates $35,854 of PnL from 60 longshot positions (<0.50) and loses $1,912 on
everything above 0.50. That is the entire picture. The 0.90+ bucket is not a TP/SL candidate
— it is an entry-filter candidate.

**The simple alternative**: `max_price = 0.50`. This eliminates 286 slots of deadweight (82.7%
of the 346-position ledger) that net -$1,912. It concentrates the 20-slot portfolio entirely
in the $35,854 PnL pool. No exit logic required.

**Why TP/SL instead?** The only defensible reason to retain 0.90+ entries and manage them
with TP/SL is if: (a) those entries are necessary to maintain the N=2 consensus pool, or
(b) early exit converts them to positive PnL. The sweep shows that Exit@80% on very_expensive
produces ROC/day = -0.00175 (less negative than hold's -0.002) — barely better than breakeven.
The TP/SL on expensive entries is not rescuing the bucket; it is rearranging deck chairs.

**Challenger verdict**: A `max_price = 0.45` filter is simpler, more interpretable, and
eliminates the operational complexity of real-time exit monitoring. The burden of proof is
on TP/SL to show it generates MORE PnL than entry filtering, not less.

---

## Challenge 2: Capital Recycling Math — Does TP/SL Pay for Itself?

The proposed TP for a 0.93-fill entry triggers at `0.93 + TP_pct * (1 - 0.93)`.

Using Exit@80% (the best threshold for the 0.90+ bucket by ROC/day):
- Trigger: 0.93 + 0.80 * 0.07 = 0.986
- Gross gain captured at exit: $100 * (0.986 - 0.93) / 0.93 = **$6.02 per $100 position**
- Expected gain at resolution (hold, 90% HR): $100 * 0.90 * (1/0.93 - 1) - $100 * 0.10 = **-$3.26**

So the TP rescues a losing position — that is real. But the capital freed is $93 (cost basis
at 0.93 fill). To justify TP over simple filtering, the freed $93 must earn more than $0 in
its next deployment (since the alternative — not entering at 0.93 — earns $0 and keeps the
slot free from the start).

**Breakeven calculation**: Freed $93 needs to earn $X in $Y days at the portfolio's marginal
rate. If slots are often idle (capital utilization < 100%), the freed capital earns nothing.
The capital efficiency curve shows Hold P=20 accepts only 197/346 signals (43% rejection).
This means 57% of the time a slot is free when a signal arrives. The eviction logic — where
TP/SL fires to accept a new signal — only adds value when: (a) a slot is occupied AND (b) a
new signal simultaneously arrives AND (c) the incoming signal has positive edge.

From the P=20 constrained simulation, Hold rejects 149/346 signals. Exit@25% rejects only
5/346. The exits are doing real work in the capital-constrained regime. But the challenger
asks: why take the 0.93-fill position at all if it has negative expected PnL? The "eviction"
is solving a problem created by bad entry selection.

**Breakeven framing for TP/SL on 0.93 entry vs filter:**

- TP/SL path: Enter at 0.93, TP fires at 0.986, capture $6.02. Slot now free for next signal.
  Expected value of TP: $6.02 * (fraction of winners that reach 0.986) minus losses on non-exiters.
  From trajectory data: 100% of WON positions reach 90% target (median 101.5h). Only 12% of
  losers reach 90% target. So TP at 80% fires on virtually all winners (high certainty) and
  almost no losers. Expected TP PnL ≈ 0.895 * $6.02 - 0.105 * $100 = $5.39 - $10.50 = **-$5.11**.

  Wait — this is wrong. At 0.93 fill, the COST is $93 per 100 tokens. The resolution win payout
  is $100 per 100 tokens. TP at 0.986 sells 100 tokens at $0.986, receiving $98.60.
  Profit on TP exit = $98.60 - $93 = $5.60. Profit on hold-win = $100 - $93 = $7.00.
  So TP captures $5.60 instead of $7.00 — a $1.40 sacrifice. On losers, TP fires for 12%:
  those earn $5.60 instead of -$93. For 88% of losers, TP never fires and position resolves
  at -$93. Expected PnL with TP@80% threshold vs hold:
  - Win (90%): TP captures $5.60 vs hold $7.00. Delta = -$1.40.
  - Loss (10%): 12% TP escape at $5.60, 88% unrescued at -$93.
    TP loss contribution = 0.12 * $5.60 + 0.88 * (-$93) = $0.67 - $81.84 = -$81.17.
    Hold loss contribution = -$93.00.
  - Expected TP PnL = 0.90 * $5.60 + 0.10 * (-$81.17) = $5.04 - $8.12 = **-$3.08**
  - Expected Hold PnL = 0.90 * $7.00 + 0.10 * (-$93.00) = $6.30 - $9.30 = **-$3.00**

  TP/SL at 80% threshold makes the 0.93-fill position WORSE than holding (-$3.08 vs -$3.00)
  because only 12% of losers escape, but winners are truncated by $1.40.

- Filter path: Reject the 0.93-fill signal entirely. Slot stays free. Next signal arrives with
  +9.3pp excess HR at avg fill 0.77 (N=2 pool avg signal price). Expected PnL on a
  0.77-fill position: significantly positive for the <0.50 bucket, modestly negative for 0.70-0.80.

**Conclusion**: TP/SL on the 0.90+ bucket does not convert it to positive expected value.
It marginally improves a losing bucket while sacrificing $1.40 per winner. Entry filtering
dominates because it avoids deploying capital into negative-edge positions entirely.

---

## Challenge 3: Compounding Score — TP/SL on Expensive Bucket vs Filtering

Under Entry Filter (max_price = 0.45):
- Deployable pool shrinks to ~60 positions (the <0.50 bucket has N=60 over 8 months)
- At P=20, capital utilization would be low (signal frequency drops dramatically)
- Compounding score would be high per-signal but throughput collapses

Under TP/SL on 0.90+ + Eviction:
- 346 total signals, 190 in the negative-expectation bucket
- TP/SL keeps those 190 slots semi-liquid but at negative average edge
- Eviction replaces them with new signals from the same pool — same quality distribution

The core problem: if 55% of signals are above 0.50 fill price and all have negative or near-zero
edge at hold, the eviction system recycles capital through zero-edge or negative-edge positions.
Churning capital faster through bad signals does not improve portfolio compounding — it amplifies
the drag.

**The right compounding score target for this strategy**: Focus exclusively on the <0.50 bucket.
Exit@25% on longshot positions (fill 0.00-0.50) captures half the $35,854 PnL in ~0.5d median
hold instead of 5.0d. Compounding score for longshot-only with Exit@25%:
- Excess HR: approximately the portfolio excess HR (+9.3pp) — the longshots drive all the edge
- Avg edge: $35,854 / 60 = $597.57 per position (unconstrained)
- At P=20, constrained to fewer fills but higher quality
- Median hold with Exit@25%: ~0.5 days
- Compounding score: 9.3 * 597 / 0.5 / 100 ≈ **111** (astronomical, but based on vectorized UB)

The point is: the longshot bucket is so dominant that filtering to it and applying aggressive exits
is the real compounding engine. TP/SL on the 0.90+ bucket is a distraction.

---

## Challenge 4: Opportunity Cost — The Real Optimization is Entry Sizing

Every $100 deployed at 0.93 fill earns approximately -$3 in expectation.
Every $100 deployed at 0.20 fill earns approximately +$597 / 60 * (100/100) ≈ +$10 per $100.

The opportunity cost of a 0.93-fill position holding a slot for 5.7 days (median for that bucket)
vs the next available longshot (<0.50 fill) is:

- Longshot throughput at P=20 with 60 signals over 8 months: ~0.25 signals/day
- Each slot occupied by a 0.93-fill position for 5.7 days blocks ~1.4 potential longshot fills
- Forgone PnL per blocked fill: $597 / 60 ≈ $10
- Total opportunity cost of 190 expensive positions: 190 * 5.7d * 0.25 signals/day * $10 = **$2,708**
  (rough upper bound — assumes sufficient longshot supply, which is unlikely at 60 signals/8mo)

With only 60 longshot signals over 8 months, P=20 is never capital-constrained by longshots alone.
The real optimization is therefore NOT eviction — there is nothing to evict FOR. The longshot pool
is too thin to benefit from freed capital.

This is the core structural problem: the strategy has 60 good signals and 286 bad signals.
The eviction design assumes there is always a better signal waiting for a freed slot. But with
only 60 good signals over 8 months (7.5/month), P=20 slots are almost never competing for good
signals. Eviction frees slots that then sit idle or fill with more sub-0.50 signals (negative edge).

**The REAL optimization is**: (a) increase signal frequency in the <0.50 bucket (relax pool
construction constraints), or (b) allocate capital to a higher-throughput track (Sports, Esports)
and treat Politics NO as a small-allocation opportunistic leg.

---

## Challenge 5: Transaction Costs at Scale

The sweep estimates slippage at $0.10/exit (MAC half-spread 0.001 for politics). On 291 exits
over 8 months, total slippage = $29. Against an $18,722 benefit, this is indeed 0.16% — negligible.

However, TP/SL adds a different cost: **operational complexity**. Each active position requires:
- Continuous CLOB WS orderbook monitoring for NO token best_bid
- Exit order submission on trigger (market or limit sell)
- State management: position tracking, fill confirmation, ledger update
- Error handling: what if the sell order partially fills? What if the market closes between
  trigger detection and order submission?

For 291 exits over 8 months (~36 exits/month, ~1.2/day), this is manageable. But the SELL
pathway has known pitfalls in this codebase (see `pitfalls/sell_is_exit.md`). Each exit
adds an event that must be correctly classified as a position close, not a new directional
signal. The risk of operational bugs introducing silent losses is non-zero.

At $100 per position with ~$0.10 slippage, the economics are fine. The concern is whether
the operational overhead (monitoring, order management, error handling) is worth the benefit
over the simpler alternative (entry filter + hold).

**Break-even operational cost**: The active exit design generates $18,722 in extra PnL over
Hold at P=20. If implementation + ongoing monitoring costs more than ~$2,300/year in developer
time (assuming 10% risk-adjusted discount), the entry-filter alternative dominates. This is
a real consideration for a small-capital deployment.

---

## Challenge 6: The TP/SL Design Conflates Two Separate Problems

**Problem A**: The 0.90+ bucket has negative expected PnL at resolution.
- Solution A: Entry filter at max_price = 0.45-0.80. Simple, one parameter.
- Solution B (proposed): TP/SL. Adds real-time monitoring, exit order management.
- Solution A is strictly simpler and addresses the root cause.

**Problem B**: Capital is locked in slow-resolving markets when better signals arrive.
- Solution B (eviction): Free slots on demand by exiting any position.
- Solution C: Run a smaller P (fewer slots) concentrated in high-quality bucket.
- Solution C is simpler and avoids the eviction logic entirely.

The two-layer design (TP/SL + eviction) tries to solve both problems simultaneously.
This creates four behavioral regimes that must each be tested and validated:
1. TP fires before eviction needed
2. SL fires (losing position stops out)
3. Eviction fires (no TP/SL, but new signal needs the slot)
4. Neither fires (hold to resolution)

Each regime has different PnL characteristics and different simulation requirements.
The sweep only tests regime 1 (TP fires). Regime 3 (eviction) requires knowing the
timing of incoming signals relative to open positions — a more complex simulation.

Regime 2 (SL) is the most concerning. The sweep does NOT show SL data. From the
trajectory analysis: only 12% of losers ever reach 50% of max payout. For a position
entered at 0.93 with a 10% loss rate, the price path on losers typically moves away
from 0.93 quickly (market consensus shifts). An SL below fill price would trigger on
most losers — but at what level? If SL = fill - 0.05 (selling a 0.93 position at 0.88),
you lock in a $5 loss instead of waiting for resolution. Given the 10% loss rate and
base-rate-breakeven at 93% HR, an SL will frequently trigger on positions that would
have resolved at $0 (locking in $5 loss instead of $100 loss — genuinely helpful)
but also on temporary drawdowns that recover (locking in $5 loss on what would have
been a $7 win). The SL parameters are not specified in the design proposal.

**Without SL calibration data, the two-layer design is half-specified.**

---

## Capital Efficiency Suggestions

1. **Replace TP/SL for 0.90+ bucket with max_price filter at 0.45-0.50.**
   The bucket ROC/day is negative regardless of exit strategy. Filter at entry, not exit.
   Net effect: portfolio concentrates on the only positive-edge bucket. Simpler code path.

2. **Apply Exit@25% only to the <0.50 bucket (longshots).**
   These 60 positions generate 106% of total PnL. Exit@25% drops median hold from 5.0d to
   ~0.3d for this bucket. Compounding score explodes without touching the 0.90+ problem.
   This is the high-priority optimization: one parameter, one bucket, captures the bulk of
   the active-exit benefit.

3. **Size down (not evict) expensive entries, if retained at all.**
   If the pool construction requires 0.80-0.90 entries to maintain consensus quality,
   allocate $25 (quarter-size) instead of $100. At $25 size, a 0.93 loss costs $25 vs $100.
   The TP earns $1.40 instead of $5.60. Still negative edge but lower variance contribution.
   This is cleaner than TP/SL because it requires no real-time monitoring.

4. **Demand-driven eviction only above a quality gate.**
   If eviction must be implemented, require the incoming signal to clear the <0.50 bucket
   filter before triggering an eviction. Evicting a 0.93 position to accept a 0.85 position
   is trading one bad entry for another. The gate should be: only evict if incoming signal
   fill price < 0.45.

5. **Rebalance capital from Politics NO to Sports YES / Esports.**
   At $100/position with P=10, Exit@50% on Politics NO generates ~$26,834 over 8 months
   ($40,251/yr extrapolated). The same $1K in Sports YES K=25 N=2 generates $162,157 over
   8 months. Politics NO with active exit has Sharpe=5.66 (politics track alone). But the
   portfolio allocation question is whether $2K in Politics NO or $2K in Sports YES is better.
   The answer from the tick data: Sports YES wins by 5x. Politics NO is worth a small
   allocation for diversification, not a primary capital destination.

---

## Hold Time Analysis

| Config | Median Hold | 90th Pct Hold | Capital Turns/Month |
|--------|-------------|---------------|---------------------|
| Hold-to-resolution | 4.7d | ~18d (estimated) | 6.4 turns |
| Exit@25% | 1.4d | ~3d (estimated) | 21.4 turns |
| Exit@50% | 2.0d | ~5d (estimated) | 15 turns |
| Sports YES (benchmark) | 0.29d | ~1d | 103 turns |

Politics median hold (12.3d in the knowledge base for all politics markets; 4.7d for this
filtered N=2 strategy) is 7-40x longer than sports. Even with Exit@25%, Politics NO is
3-5x slower than Sports YES in capital turns per month.

**Category recommendation**: Politics is a structural capital lock-up. Even the best active
exit design cannot close the gap with Sports or Esports. The framework recommendation
(from `hold_time_capital.md`) applies here: size Politics inversely to hold time.
At 4.7d median vs 0.29d Sports YES, the same capital allocation should be 16x smaller
in Politics. At P=10 ($1K), this is already approximately sized.

---

## Risk Caveat

The challenger's aggressive stance should not obscure two real findings:

1. **Exit@25% and Exit@50% genuinely work at P=20** (vectorized upper bound). The capital
   recycling effect is not illusory — accepting 338 signals instead of 197 at the same capital
   level is a real benefit if signal quality is maintained. The extra 141 fills show 92.3%
   win rate and $132.4 avg PnL — higher quality than the constrained fills, not lower.

2. **Politics NO v3 K=100 N=2 IS tick-validated** at +9.3pp excess HR, Sharpe=0.55. It has
   real edge. The Sharpe is low but the profit factor is 6.75 (the wins are large relative to
   losses). Active exit improves Sharpe to 5.66 (politics track) — a meaningful improvement.

3. **The eviction mechanism has portfolio-level value** even if the per-position math is
   negative: it prevents a single slow-resolving politics market from blocking all slots for
   30+ days. The structural protection against capital starvation is worth something.

The challenger's point is not "abandon the strategy." It is: "simplify the mechanism. Filter
bad entries before they occupy slots. Apply exits only where they genuinely improve edge
(the <0.50 longshot bucket). Do not build a two-layer TP/SL + eviction system to solve a
problem that a one-line entry filter solves more cleanly."

---

## Summary

The two-layer TP/SL + eviction design for Politics NO v3 is over-engineered for the
underlying signal structure. The strategy has exactly one positive-PnL cohort: the <0.50
fill price bucket (60 positions, +$35,854). Everything above 0.50 contributes net -$1,912
over 8 months. TP/SL on the 0.90+ bucket improves that bucket's ROC/day from -0.00194 to
-0.00175 — still negative. The simpler intervention is a max_price filter at 0.45-0.50, which
eliminates negative-edge entries without requiring real-time price monitoring or exit order
management.

The active exit mechanism IS valuable — but its value comes from freeing capital to accept
more <0.50 signals, not from rescuing 0.90+ positions. Exit@25% on the longshot bucket
alone captures the majority of the capital recycling benefit with a single parameter. The
demand-driven eviction adds value only if there is a queue of high-quality signals waiting
for slots — but with 60 longshot signals over 8 months (7.5/month), P=20 is rarely capacity-
constrained by good signals. Eviction solves a congestion problem that mostly does not exist
for the positive-edge cohort.

**Recommended simplified design**: max_price = 0.45 entry filter + Exit@25% on all positions.
No TP/SL. No eviction logic. One parameter each. Expected outcome: higher compounding score,
lower operational complexity, same or better PnL from the longshot bucket, with $1K freed
for redeployment to Sports YES or Esports.

The TP/SL + eviction design should not proceed to tick validation as specified. It should
first demonstrate — in vectorized simulation — that it outperforms the simpler (filter + exit)
design on the metrics that matter: total P=20 PnL, compounding score, and signal fill rate.
