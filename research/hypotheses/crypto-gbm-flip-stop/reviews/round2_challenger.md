# Challenger Review: crypto-gbm-flip-stop (Round 2)

**Date**: 2026-03-10
**Reviewer**: Challenger agent
**Data**: 15,889 BTC 15-min Up/Down markets (Sep 2025 – Mar 2026, 171 days)
**Simulation**: tick-by-tick, half-spread=1%, fee=3%, base_bet=$50

---

## Compounding Score Assessment

All hold times are sub-5-minute (BTC 15-min windows with ~4.5-min average holds). This is an
extraordinarily fast-cycling strategy — but the compounding score arithmetic must be done honestly.

| Config | Tick HR | Excess HR* | Avg Edge | Median Hold (days) | Comp Score |
|--------|---------|-----------|----------|-------------------|------------|
| Baseline (thr=0.35, d=1) | 77.1% | +27.1pp | $3.58 | 0.00242 | 400.7 |
| Primary (thr=0.25, d=3) | 81.5% | +31.5pp | $3.71 | 0.00248 | 470.8 |
| Aggressive (thr=0.20, d=1) | 81.1% | +31.1pp | $3.69 | 0.00249 | 461.4 |
| Delay-only (thr=0.35, d=5) | 82.4% | +32.4pp | $3.69 | 0.00243 | 491.3 |
| Sigma-cond (d=2) | 81.0% | +31.0pp | $3.73 | 0.00247 | 469.9 |

*Excess HR assumes a 50% base rate — these are binary markets. This is correct framing.

**Benchmark**: A compounding score of 0.5 is the minimum viable threshold per framework
convention. All configs exceed 400 — they are 800x above the threshold. The relevant question
is not "does this clear the bar" but "which config maximizes throughput given implementation cost."

**Key observation on compounding score sensitivity**: Median hold time for all configs ranges
0.00242–0.00249 days (3.5–3.6 minutes). The compounding score differences across configs
are driven almost entirely by the HR improvement (+0.5 to +5.3pp), not by hold time reduction.
Hold time variation is only 0.07 minutes across all configs — negligible. This means the
"capital efficiency" framing for this hypothesis is really about hit-rate improvement, not
hold-time compression.

---

## Hold Time Analysis

- **Median hold**: 3.5–3.6 minutes across all configs
- **Average hold**: 4.6–4.8 minutes (avg_hold_s ~270-275s)
- **Distribution shape**: Almost certainly left-skewed (trailing stop fires early; resolution
  takes 0-2 minutes at BTC window close). Exit type data confirms: 80-91% trailing stop exits,
  meaning the hold distribution has a sharp mode well below the window length.
- **Capital turns per day**: ~93 trades/day at $50/position = $4,650 notional deployed daily.
  At $500 capital allocation, each dollar turns over 9.3x per day. This is already near-maximal
  capital velocity for any prediction market strategy.
- **90th percentile hold**: Not in the data, but given the avg (4.6 min) and median (3.5 min)
  are both well below the 15-min window, the 90th is likely under 10 minutes — still intraday.

**Capital lock-up verdict**: Not a problem. This strategy never locks capital for meaningful
periods. The compounding framework was designed for sports (8d) and politics (30d) categories.
Applying it to 3.5-minute holds produces numbers that are technically correct but practically
irrelevant — capital recycling is not the bottleneck here.

---

## The Elephant: What the Compounding Score Obscures

The score comparison across configs (400.7 vs 491.3) looks like a 23% improvement. In
absolute terms it is:

- Total PnL lift from baseline to delay-only: $58,599 - $56,808 = **+$1,791** over 171 days
- Total PnL lift from baseline to primary: $58,898 - $56,808 = **+$2,090** over 171 days
- Per-trade improvement: **+$0.11-$0.13/trade**

On a $50 position, that is a 0.22-0.26% improvement per trade. This is real money at scale,
but it is thin. The headline "23% compounding score improvement" is an artifact of compounding
score's sensitivity to small HR changes when hold time is near-zero.

**The honest framing**: This hypothesis is about shaving 3-5pp of false stop triggers, recovering
~$0.13/trade, and adding 2 code paths (flip counter + confirmation state) to the strategy. The
edge is real and validated. The magnitude is modest.

---

## Capital Efficiency Suggestions

**1. Do not over-engineer the exit for the gain being captured.**

The primary candidate requires adding a per-market `_flip_consec` counter and a new config
parameter. The delay-only variant (thr=0.35, delay=5) achieves a higher compounding score
(491.3 vs 470.8) with a simpler change: just increase the confirmation tick count without
touching the threshold. From a capital efficiency standpoint, simpler code = fewer bugs = more
reliable production behavior = higher effective edge. The 0.3x lower HR is not enough reason
to add threshold complexity.

**Recommendation: deploy delay-only (thr=0.35, delay=5) as the simpler, higher-scoring option.**

**2. Scrutinize the false stop metric before treating 84% as a problem.**

The delay-only config has 84% false stops and is also the highest compounding score. This is
not a contradiction — it means the 16% true stops (genuine catastrophic reversals averted) are
doing heavy lifting, and the 84% false stops are minor-cost early exits that still resolve
profitably. The false stop rate is not a cost metric in isolation; it needs to be weighted by
the PnL delta at exit. The data shows delay-only has a higher HR than primary — the false stops
are not hurting it.

**3. Capital concentration check: 93 trades/day in the same underlying (BTC 15-min).**

All 93 daily trades are correlated. A single sharp BTC move will trigger many positions
simultaneously. At $50/position this is manageable, but the effective capital-at-risk in any
15-minute window is closer to $200-300 (4-6 concurrent positions). The compounding score
treats all trades as independent — they are not. This is not an argument against deploying,
but it is an argument against scaling capital linearly with trade count.

**4. The trailing stop is where the real edge lives.**

80-91% of exits are trailing stops, not flip stops. The trailing stop optimization
(`research/hypotheses/crypto-gbm-exit/`) is likely a higher-return research target than
further flip-stop tuning. If the challenger had one recommendation for where to spend the
next research cycle, it is there — not here.

---

## Category Context

This is a **crypto 15-min** strategy. Category resolution speed is effectively determined by
the BTC window close (~15 minutes from market open), not by any Polymarket category classification.
Sports markets resolve in ~8 days. This strategy resolves in 15 minutes. The capital efficiency
comparison is:

- Sports (8 days): 1 capital turn per position
- Crypto 15-min (15 min): ~768 capital turns per position equivalent per day

The compounding score framework correctly identifies this as a favorable category. No category
change is needed or possible — this is already the fastest-resolving category on Polymarket.

---

## Risk Caveat

The premortem's WARNING about confirmation delay asymmetry deserves weight: a 5-tick delay at 5s
cadence = 25 seconds of additional exposure. For positions late in a 15-minute window with
2 minutes remaining, 25 seconds = 21% of remaining hold time. The validation data shows this
risk was tested and does not materially degrade performance (degradation 2.6-3.1pp across all
configs). However, the risk is real: a sharp BTC reversal in the confirmation window on a
late-stage position hits harder than the model captures, because resolution value converges
faster near window close. Monitor late-window flip-stop behavior in paper trading.

The sigma-conditional config attempts to address this but shows no advantage over simpler configs
in the validation data. Do not add conditional logic without a clear win.

---

## Summary

The crypto-gbm-flip-stop hypothesis delivers a genuine, tick-validated edge: +2.6-4.4pp HR
improvement, +$1,791-$2,090 total PnL lift over 171 days, and compounding scores 17-23% above
baseline. Capital efficiency is not a bottleneck here — with 3.5-minute median holds, every
dollar turns over 9x per day regardless of which config is deployed.

The capital efficiency question reduces to: is the implementation complexity of thr=0.25 +
delay=3 (Primary) worth the $299 additional PnL over delay-only over 171 days? From a
capital efficiency standpoint: no. Delay-only (thr=0.35, delay=5) achieves a higher compounding
score (491.3), is a one-parameter config change, and avoids adding threshold-hunt complexity that
may overfit to this dataset. The validation recommends Primary; the challenger recommends
Delay-only. The difference is $1.74/day. Implement Delay-only first; re-evaluate if paper
trading shows Primary's lower false-stop rate materially improves realized PnL.

**Verdict: APPROVE for deployment. Recommend Delay-only as the first implementation.**
