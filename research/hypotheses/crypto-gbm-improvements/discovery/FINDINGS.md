# Vectorized Discovery: Crypto GBM Improvements
**Date**: 2026-03-10
**Status**: Discovery complete — methodology note required before interpreting results
**Universe**: 4,949 5-min + 16,170 15-min BTC up/down markets (Sep 2025 → Mar 2026)
**ALL RESULTS ARE UPPER BOUNDS**

---

## CRITICAL: Metric Mismatch vs Tick Baseline

The vectorized sweep measures **intra-window PM convergence** (does PM price revert to GBM fair value before the market closes?). This is NOT the same as the tick-by-tick baseline (+$2.10/trade) which measures **resolution PnL** (does the market resolve in the direction GBM predicted?).

The live strategy holds to resolution in most cases (96.2% convergence per FINDINGS.md). The vectorized model flags a "time-stop" any time PM doesn't converge within the window — but a YES position that time-stops still resolves YES ~50% of the time with profit.

**Implication**: The negative EVs below (-$2.99 to -$17.25/trade) reflect a simulation artifact, not actual strategy behavior. Do NOT compare absolute EV numbers to the tick baseline. Compare RELATIVE improvements between axes.

---

## Baseline (Vectorized, NOT resolution-based PnL)

| Window | n | Conv% | TStop% | Avg EV | Hold | HR |
|--------|---|-------|--------|--------|------|----|
| 5-min | 4,694 | 13.0% | 87.0% | -$9.70 | 4.3 min | 25.1% |
| 15-min | 15,695 | 33.3% | 66.7% | -$2.99 | 12.3 min | 34.1% |

- 5-min base rate: YES wins 51.2%, so excess HR = -24.9pp (pure chance)
- 15-min base rate: YES wins 49.9%, so excess HR = -15.9pp (pure chance)

The near-zero HR and heavy time-stops confirm this vectorized model doesn't predict resolution correctly — the GBM signal effect happens at resolution time, not intra-window.

---

## Axis 1: Dynamic Sizing (SKIP)

| Window | Avg Bet | Avg EV | vs Baseline |
|--------|---------|--------|-------------|
| 5-min | $87.3 | -$17.25 | -$7.55 worse |
| 15-min | $78.2 | -$4.86 | -$1.87 worse |

EV by lag bucket (15min):
- lag 0.10-0.12: n=3,307, avg EV = -$2.55, avg bet = $54.9
- lag 0.12-0.15: n=4,027, avg EV = -$3.45, avg bet = $67.1
- lag 0.15-0.20: n=4,356, avg EV = -$5.45, avg bet = $86.1
- lag >0.20: n=4,005, avg EV = -$7.50+, avg bet = $100.0

**Verdict: SKIP.** Larger bets on larger lags makes absolute losses bigger. No evidence that larger lags → better convergence rate. Dynamic sizing amplifies losses in this model. The analytical EV case from prior analysis assumed a positive baseline — that assumption is not supported here.

---

## Axis 2: Re-entry Logic (SKIP)

0 re-entries fired in EITHER window size. Within the 5-min window, if convergence occurs there's no time left for a second entry. Within the 15-min window, the simulation correctly never fires a re-entry because convergence rarely happens.

**Verdict: SKIP.** No signal at vectorized level. Also blocked by the data structure conflict identified in pre-mortem (strategy.py line 215).

---

## Axis 3: Fee-Aware Threshold (SKIP)

| Window | n_flat | n_fee_aware | % kept | Conv delta |
|--------|--------|-------------|--------|------------|
| 5-min | 4,694 | 4,391 | 93.5% | -0.003 |
| 15-min | 15,695 | 13,615 | 86.7% | -0.014 |

Fee-aware threshold filters out more entries near p=0.5 (higher fee) and allows entries near extremes. Convergence rate drops slightly (fewer entries near the mean where GBM is most uncertain = worse, not better).

**Verdict: SKIP.** The actual PM fee at entry range (0.40-0.55) is 1.5-1.56%, well below the 10% threshold. Fee correction is negligible. Confirmed: no material benefit.

---

## Axis 4: Late-Entry Size Reduction (IMPLEMENT — soft filter)

Convergence rate degrades sharply by elapsed fraction:

**5-min:**
- Early (0-33% elapsed): n=3,008, conv=14.3%, EV=-$8.91
- Mid (33-67% elapsed): n=1,632, conv=11.0%, EV=-$11.03
- Late (67-100% elapsed): n=54, conv=3.7%, EV=-$13.84

**15-min:**
- Early (0-67% elapsed): n=13,250, conv=34.4%, EV=-$2.62
- Mid (67-90% elapsed): n=2,309, conv=27.5%, EV=-$4.82
- Late (>90% elapsed): n=136, conv=17.6%, EV=-$7.57

Applying late cutoff (filter last 33% of window elapsed): removes only 54-136 trades, negligible EV improvement. But the gradient is real — late entries have 3-10pp worse convergence.

**Verdict: IMPLEMENT as soft filter.** Use `min_time_remaining_min = 1.5` already in config (filters final 30s of 5-min, 1.5min of 15-min). Consider tightening to `min_time_remaining_min = 2.0` for 15-min windows. No-entry-within 90s already handles this for 5-min windows.

---

## Axis 5: EWMA Sigma (MARGINAL — test at tick level)

| Window | Sigma | Conv% | Avg EV | Hold |
|--------|-------|-------|--------|------|
| 5-min | rolling_24h | 13.0% | -$9.70 | 4.3 |
| 5-min | ewma_hl60 | **15.1%** | **-$8.99** | 4.1 |
| 5-min | ewma_hl180 | 13.8% | -$9.48 | 4.2 |
| 5-min | ewma_hl360 | 12.9% | -$9.79 | 4.3 |
| 15-min | rolling_24h | 33.3% | -$2.99 | 12.3 |
| 15-min | ewma_hl60 | 33.4% | -$3.53 | 11.9 |
| 15-min | ewma_hl180 | 33.1% | -$3.42 | 12.1 |
| 15-min | ewma_hl360 | **33.4%** | -$3.14 | 12.1 |

EWMA hl60 on 5-min shows +2.1pp better convergence and -$0.71 better EV. This suggests faster sigma adaptation helps fire on more real convergence opportunities in short windows.

**Verdict: TICK-TEST REQUIRED.** The signal is marginal at vectorized level but in the right direction for 5-min windows. Given the metric mismatch, tick-by-tick validation with hl60 vs rolling is needed before implementing.

---

## Vol Regime Stratification (15-min)

| Regime | n | Conv% | Avg EV | HR |
|--------|---|-------|--------|-----|
| Q1 (low vol) | 3,924 | 32.6% | -$2.21 | 35.4% |
| Q2 | 3,924 | 33.7% | -$2.53 | 34.0% |
| Q3 | 3,923 | 33.4% | -$3.72 | 34.0% |
| Q4 (high vol) | 3,924 | 33.2% | -$3.48 | 33.2% |

Low-vol regime has marginally better HR (35.4% vs 33.2%) and lower time-stop losses. This supports the pre-mortem observation that low-vol periods reduce `min_gbm_deviation` firing frequency and likely produce cleaner signals.

**No action required** — the existing `min_sigma = 1e-7` and `min_gbm_deviation = 0.05` already filter extreme vol regimes.

---

## Summary: Axis Verdicts

| Axis | Status | Rationale |
|------|--------|-----------|
| 1. Dynamic sizing | SKIP | Amplifies losses, no convergence improvement |
| 2. Re-entry | SKIP | 0 re-entries fire; structural bug in codebase |
| 3. Fee-aware threshold | SKIP | Fee <1.6% at entry range; negligible vs threshold |
| 4. Late-entry filter | IMPLEMENT | Gradient confirmed; tighten min_time_remaining_min |
| 5. EWMA sigma (hl60) | TICK-TEST | +2.1pp convergence on 5-min; needs tick validation |

---

## Recommended Next Steps

1. **Tick-test EWMA hl60** on 5-min and 15-min windows against rolling_24h baseline using SyncReplayRunner. Target: confirm +2pp convergence rate or better.

2. **Config change** (no tick test needed): Tighten `min_time_remaining_min` from 1.5 to 2.0 for 15-min markets. Safe, removes clearly degraded late entries.

3. **No changes** to dynamic sizing, re-entry, or fee-aware threshold.

---

## Caveats

- All EVs in this document use intra-window convergence model — NOT resolution PnL
- Compare only relative deltas between axes, not absolute to tick baseline
- 5-min window baseline is particularly unreliable (only ~5 PM price observations per window)
- Re-entry: 0 fired is a ceiling artifact (per-market one-entry cap), not evidence it's useless in tick replay
