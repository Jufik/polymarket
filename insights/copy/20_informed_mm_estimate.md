# Informed Market-Making on Consistency-Pool Signals: Does the Spread Save It?

**Date**: 2026-02-19
**Method**: Two-stage analytical estimate — per-bet MM overlay (Stage 1) + capital-constrained monthly sim (Stage 2)
**Windows**: All 12 dev windows (anchored-expanding, Jan 2025 - Dec 2025 holdouts) + single-window deep dive (Dec 2025)
**Capital**: $1,000, $100/bet

---

## TL;DR

**No. The spread does not save it.** Across all 12 dev windows, the best signal config (t5, 70% agreement) averages **-$4.22/bet as taker** and **+$1.83/bet as maker** at a 2c spread — a marginal, noise-level result. Hit rate ranges wildly from 21% to 78% across windows with no stability. The 2c spread converts a losing taker strategy into a barely-break-even maker strategy, but this is not a tradeable edge. Informed market-making inherits the signal's directional accuracy; it cannot rescue a fundamentally unreliable signal.

---

## Multi-Window Results (12 Dev Windows)

### Best configs at 2c spread, 100% fill, $100/bet, fee=0%

| Pool | Signal | #Windows | #Bets | Avg Taker $/bet | Avg Maker $/bet | Avg HR |
|------|--------|:---:|:---:|:---:|:---:|:---:|
| 6m/10mkts/pure_taker/e0.90 | t5, 70% agree | 9 | 239 | **-$4.22** | **+$1.83** | 49.1% |
| 6m/10mkts/pure_taker/e0.90 | t5, 80% agree | 8 | 186 | -$6.26 | -$0.23 | 48.1% |
| 6m/10mkts/pure_taker/e0.90 | t5, 90% agree | 5 | 60 | -$17.40 | -$11.30 | 35.2% |
| 6m/10mkts/pure_taker/e0.90 | t7, 70% agree | 4 | 38 | -$21.50 | -$16.80 | 42.0% |
| 6m/10mkts/pure_taker/e0.90 | t7, 80% agree | 3 | 21 | -$9.10 | -$3.50 | 47.6% |

The strict pool (9m/20mkts/pure_taker/e0.80) produced insufficient traders in most windows (< 65 qualified traders, yielding zero signal fires).

### Key findings

1. **The taker signal is negative in aggregate.** Even the best config (t5, 70%) averages -$4.22/bet across 239 bets in 9 windows. This is not a "nearly positive" signal — it's a losing strategy.

2. **The spread converts losing to marginal.** At 2c, the maker version of t5/70% becomes +$1.83/bet. But $1.83/bet on 239 bets over 9 months = $437 total — less than $50/month. At $100/bet capital allocation, this is noise.

3. **Hit rate has no stability.** The same config (t5, 70%) shows HR from 21% (Dec 2025) to 78% (early 2025 windows). A signal that oscillates this widely is not tradeable — you cannot size positions or manage risk.

4. **Higher agreement = worse, not better.** 90%+ agreement yields -$11 to -$17/bet. Unanimous consensus is anti-predictive on average, not just in December.

5. **More traders required = fewer windows with data.** t7 and t10 configs only fire in 3-4 windows (small samples), and when they do, results are deeply negative.

---

## Single-Window Deep Dive: December 2025

The initial analysis used dev_11_2025Q4 (holdout: Dec 2025) as a representative window.

### Stage 1: Per-Bet Economics

Only the relaxed pool (6-month consistency, 10+ markets, pure_taker, entry <= 0.90) produced enough traders (655). The strict pool (9m, 20mkts, entry <= 0.80) yielded only 65 traders and zero qualifying signal fires.

| Signal | Fires | Taker $/bet | Maker $/bet (2c spread) | Delta | HR |
|--------|:---:|:---:|:---:|:---:|:---:|
| t5, 70% agree | 46 | **-$1.01** | **+$10.86** | +$11.87 | 39.1% |
| t5, 80% agree | 43 | +$4.13 | +$16.85 | +$12.72 | 39.5% |
| t5, 90% agree | 19 | -$25.96 | -$18.10 | +$7.86 | 21.1% |
| t5, 100% agree | 19 | -$25.96 | -$18.10 | +$7.86 | 21.1% |
| t7, 70% agree | 12 | -$39.78 | -$37.86 | +$1.92 | 41.7% |
| t7, 80% agree | 7 | -$7.62 | -$4.22 | +$3.40 | 57.1% |

### Stage 2: Capital-Constrained Monthly Sim

All top-5 configs (by maker PnL/bet) LOST money in the capital simulation:

| Config | Bets | HR | Total PnL | $/month |
|--------|:---:|:---:|:---:|:---:|
| MM: t5, 80%, 2c spread | 21 | 33.3% | **-$979** | -$163 |
| MM: t5, 70%, 2c spread | 24 | 37.5% | **-$819** | -$102 |
| Taker: t5, 80% (baseline) | 41 | 39.0% | +$20 | +$3 |
| Taker: t5, 70% (baseline) | 45 | 40.0% | +$54 | +$9 |

The maker sim uses 50% fill rate, which halves the bet count. But fill-adjusted selection doesn't avoid losing bets — it just skips half randomly. With <40% HR, skipping half doesn't help.

### Monthly breakdown

| Month | Available | Placed | HR | PnL | Pattern |
|-------|:---:|:---:|:---:|:---:|---------|
| Apr-Oct 2025 | 1-3/mo | 1/mo | 100% | +$18-285 | Few fires, all correct |
| Nov 2025 | 4 | 2 | 0% | -$200 | Signal collapses |
| **Dec 2025** | **31-33** | **15-16** | **20-25%** | **-$989 to -$1,008** | **Catastrophic** |

December accounts for 67-75% of all signal fires AND nearly all losses. The signal works in thin months (1-3 fires) but collapses during resolution waves.

---

## Why the Signal Fails (Multi-Window Evidence)

The single-window analysis raised the question: "Was December 2025 an outlier?" The multi-window analysis answers: **No — the signal is weak everywhere.**

1. **Average taker PnL is negative across 9 windows.** This isn't a December problem. The consistency pool's NO consensus, filtered to pure_taker traders, does not outperform the NO base rate (~62%) in aggregate. The 49.1% average HR is well below the 62% needed for NO bets to break even.

2. **The spread only helps mechanically.** The 2c spread adds ~$6/bet improvement regardless of signal quality. This converts -$4.22 to +$1.83 — from losing to marginal. But the spread cannot create edge where none exists directionally.

3. **Window variance is enormous.** HR swings from 21% to 78%. Some windows show strong positive results; others are catastrophic. This pattern suggests the signal captures market-specific regimes (e.g., political clusters, resolution waves) rather than a stable edge in trader behavior.

4. **High agreement is anti-predictive in aggregate.** 90%+ agreement averages -$11 to -$17/bet. When all tracked traders agree unanimously, they tend to be wrong together. This suggests correlated errors (common positioning on the same losing theme).

---

## The Fundamental Problem with Informed MM on This Signal

The Strategy #5 hypothesis was: "Place NO limit orders inside the spread on markets where 5+ pure_takers agree NO. Earn both the spread AND the directional edge."

This fails because:

1. **There is no directional edge to capture.** The taker signal averages -$4.22/bet. MM execution can improve the entry price by 1-2c, but it cannot create a positive signal from a negative one.

2. **The spread is 1-2c on a 60-90c NO token** — that's 1-3% of the position. Even at 2c, it adds ~$6/bet. Meaningful per bet, but tiny relative to the $100 loss when the signal is wrong.

3. **Execution method cannot fix a bad signal.** The spread income ($6/bet at 2c) is dwarfed by directional loss ($100/bet when wrong). With 49% HR on NO bets (base rate 62%), you lose more often than you should.

4. **Adverse selection worsens the maker.** When the consensus is wrong, the informed flow going the opposite direction makes fills MORE likely, concentrating the maker on losing trades.

---

## Comparison with S2 MM Analysis

| Aspect | S2 MM (insight #18-19) | S5 Informed MM (this analysis) |
|--------|:---:|:---:|
| Signal source | Structural NO bias ("Will" markets) | Consistency pool (5+ pure_takers) |
| Market universe | 26K "Will" binary markets | 239 signal fires across 9 windows |
| NO HR | 82-85% (structural) | 49% avg, 21-78% range |
| Taker PnL/bet | +$16.67 (strongly positive) | -$4.22 (negative) |
| Spread uplift | +11% PnL ($0.77/bet) | Converts -$4.22 to +$1.83 |
| Verdict | MM marginally improves strong signal | **Signal too weak for MM to matter** |

The S2 analysis worked because the 82% structural NO HR is robust — it persists across thousands of markets. The S1 consistency pool signal, by contrast, varies wildly by window and averages below the NO base rate.

---

## Recommendation

**Do NOT pursue informed market-making on consistency-pool signals as a standalone strategy.** The multi-window analysis confirms the signal is fundamentally unreliable (-$4.22/bet taker, +$1.83/bet maker at best), and the spread capture cannot compensate.

### What to do instead

1. **Combine S1 + S2 signals**: Intersect the consistency pool with the S2 market-selection filter ("Will" binary, YES 15-40%, fast-resolving). The S2 filter provides structural NO bias (82% HR); the S1 pool adds conviction. MM execution on this intersection might produce a genuine edge.

2. **Regime detection**: The window variance (21-78% HR) suggests the signal captures regime-dependent effects. A circuit breaker — skip when fire count spikes (>10/month) or when recent HR drops below 50% — could stabilize returns.

3. **Stick with taker for S1**: The taker sweep (insight #15) identified profitable configs across multiple windows using different signal parameters. The MM overlay adds ~$6/bet at 2c spread. At S1 capital levels ($1K), this is $50-100/month extra — not worth the execution complexity.

4. **Focus on S2 for live deployment**: S2's 82% structural HR with $968/month upper bound (insight #19) is a far more reliable starting point. Deploy S2 taker-NO first, then overlay S1 pool signals as a conviction filter.

---

## Limitations

- **No adverse selection model**: Fill probability assumed independent of signal quality. In reality, wrong-direction fills are more likely (adverse selection), which would worsen maker results.
- **Forward pricing approximation**: Used 60s forward price as the entry. Real limit orders would have different fill dynamics.
- **Pool stability assumed**: The consistency pool is re-computed per window, but trader behavior may shift faster than the training period captures.
- **Relaxed pool only**: The strict pool (9m/20mkts) never produced enough traders for meaningful signal fires. Results are driven entirely by the relaxed pool (6m/10mkts).
