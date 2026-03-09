# Phase 0 Discovery Notes: Crypto Scalp (BTC 5-min Up/Down)

**Date**: 2026-03-08
**Analyst**: Researcher agent
**Status**: PROMISING (proceed to Phase 1 validation)
**All results are UPPER BOUNDS.**

## What We Tested

Phase 0 GBM calibration on 30 days of Binance BTCUSDT 1-minute candles.
- 43,200 1m candles (Feb 6 – Mar 8, 2026)
- 8,640 non-overlapping 5-minute windows
- 2,879 non-overlapping 15-minute windows

## Key Findings

### 1. Base Rate: Near-Perfect 50/50

5-minute BTC Up/Down is **50.68% Up / 49.32% Down** over 30 days.

This is NOT the 62% NO base rate that applies to other Polymarket markets. For crypto scalp
analysis, use the empirical 50/50 base rate. Profitable threshold: **>53% accuracy** (after 3% fees).

### 2. GBM Is Well-Calibrated (Not the Source of Alpha)

Standard GBM (no drift, 30-minute realized vol annualized) is well-calibrated:
- Regression slope = 0.94 (ideal = 1.0) — mild mean-reversion, not exploitable standalone
- Brier skill score = 0.217 — GBM explains ~22% of outcome variance at t=2 min
- No bucket shows >5pp systematic GBM mispricing

**Implication**: Market makers using GBM are not systematically wrong. Alpha is NOT from GBM model error — it must come from momentum persistence that GBM ignores.

### 3. Momentum is the Signal (Strongest Feature)

Prior 5-minute momentum is highly significant (chi2=107.67, p<0.001):
- **Top Q (return > +0.09%)**: 58.8% UP accuracy → **+8.1pp above base rate**
- **Bottom Q (return < -0.08%)**: 56.7% DOWN accuracy → **+6.1pp above base rate**

Both directions exceed the 53% profitability threshold. This is textbook short-term momentum.

Prior 15-minute momentum also significant but weaker (Q4 = 53.8% UP).

**Taker buy ratio**: NOT significantly predictive (p=0.118). Volume ratio weakly predictive (p=0.023).

### 4. Combined Conditions

Best signals exceed 53% threshold:

| Condition | Direction | Hit% | vs Base | E[PnL/$] | n/day |
|---|---|---|---|---|---|
| Top Q 5m momentum | UP | 58.8% | +8.1pp | +$0.073 | 72 |
| High taker + UP momentum | UP | 57.9% | +7.2pp | +$0.064 | 35 |
| Low taker + DN momentum | DN | 57.0% | +6.3pp | +$0.055 | 34 |
| High vol + DN momentum | DN | 56.9% | +6.2pp | +$0.054 | 24 |
| Bottom Q 5m momentum | DN | 56.7% | +6.1pp | +$0.052 | 72 |

### 5. GBM vs Reality at Mid-Window (t=2.5 min)

No systematic GBM mispricing found. All return buckets show <5pp deviation between GBM
prediction and actual outcome. The correlation between GBM P(Up) and actual Up is r=0.47.

This means Polymarket market makers using GBM at mid-window would NOT be systematically
mispriced by the model. However, if they are NOT updating prices mid-window, then the
momentum signal is still exploitable via market orders.

### 6. Hour of Day

Hour 15 UTC (US market open) shows elevated Up rate (56.9%), but the effect is small and
the sample per hour is only ~360 windows. Not a standalone signal.

## Critical Open Questions for Phase 1

1. **Chainlink basis risk**: Binance BTCUSDT is NOT the Polymarket oracle. Chainlink BTC/USD
   aggregates multiple exchanges and has different latency. Need to verify momentum signal
   survives on oracle data.

2. **Polymarket mid-window pricing**: Do Polymarket market makers update odds during a 5-min
   window? If YES, the "start-of-window momentum" is already priced in and this signal is useless.
   If NO, there's a systematic lag that's exploitable.

3. **Execution timing**: Signal requires knowing the prior 5-min window return at the OPEN of
   the new window. Feasible in theory (prior candle closed 0 seconds ago), but need to verify
   Polymarket contract opens are synchronized with 5-min candle boundaries.

4. **Liquidity and spreads**: 5-minute markets may have very wide spreads or thin liquidity.
   The 3% fee assumption may be optimistic — actual spread + fee may be 5-10%.

## Parameter Notes

- Rolling vol window: 30 minutes (annualized). Tested implicitly in GBM calibration — matches data well.
- Momentum lookback: prior 5 minutes. Stronger than 15m.
- Quantile threshold: top/bottom quartile (Q4/Q1). Top decile would be even higher accuracy but fewer signals.

## SELL Dual-Test

Not applicable. This is a directional price prediction hypothesis. Signal direction is determined
by price momentum, not by copy-trade SELL/BUY semantics.

## Spawned Ideas

1. **HIGH PRIORITY**: Chainlink oracle validation — test on Chainlink BTC/USD feed, not Binance
2. **HIGH PRIORITY**: Check if Polymarket 5-min contract odds update mid-window (live data needed)
3. **MEDIUM**: ETH and SOL — data already available (`ETHUSDT_1m_30d.parquet`, `SOLUSDT_1m_30d.parquet`)
4. **MEDIUM**: 15-minute markets — weaker signal but potentially better liquidity
5. **LOW**: Hour-of-day filter (15 UTC) — small effect, combine with momentum

## What Didn't Work

- Taker buy ratio: not significantly predictive standalone (p=0.118)
- Volatility regime: not significant (p=0.186)
- Volume ratio: only weakly predictive and not directional
- GBM mispricing: GBM is well-calibrated, no model error to exploit

## Proposed Classification

None needed for this hypothesis — it's based purely on price momentum features, not
trader behavior classifications.
