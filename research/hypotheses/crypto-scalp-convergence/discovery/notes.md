# Discovery Notes: BTC Up/Down Scalp Convergence

**Date**: 2026-03-09
**Status**: UPPER BOUNDS — vectorized analysis complete

## Summary

PM BTC Up/Down market prices do lag Binance GBM fair value, and the deviation is
large and persistent. However, the convergence is fast (median 12 seconds at 5% entry
threshold), putting this strategy in "borderline" territory for websocket execution.

## Key Quantitative Findings

### Dislocation Frequency (12,788 resolved windows, Dec 2025 – Mar 2026)

| Threshold | Windows With Max Dev | Events/Window |
|-----------|---------------------|---------------|
| > 5%      | 100%                | 1.00          |
| > 10%     | 98.2%               | 0.98          |
| > 15%     | 86.0%               | 0.86          |

- **Median |GBM - PM|** across all second-snapshots: **5.3%**
- p90 = 15.9%, p99 = 29.3%
- PM prices are substantially and consistently misaligned with GBM fair value

### Convergence Speed (Critical Gate)

| Entry Threshold | p10 hold | p50 hold | p90 hold | Conv. Rate |
|----------------|----------|----------|----------|-----------|
| 5%             | 4s       | **12s**  | 110s     | 99.4%     |
| 10%            | 4s       | **22s**  | 272s     | 98.4%     |
| 15%            | 6s       | **42s**  | 384s     | 95.9%     |

### Theoretical P&L (UPPER BOUNDS, 3% fee/side)

| Entry Threshold | Mean Net PnL | Median Net PnL | Hit Rate | Pct Profitable |
|----------------|-------------|----------------|----------|----------------|
| 5%             | +3.0%       | **+2.8%**      | 48.2%    | 75.4%          |
| 10%            | +7.2%       | **+8.7%**      | 45.4%    | 83.1%          |
| 15%            | +10.4%      | **+13.1%**     | 42.2%    | 74.8%          |

**Note**: PnL is in probability-point terms, not USDC. A scalp of 0.027 on a $100 notional = $2.70.

### Hit Rate Analysis (Critical)

Hit rate = fraction of scalps where GBM correctly predicted the final outcome.
- At all thresholds: ~42-48%, which is BELOW the 50% base rate.
- This means: we're entering when PM is "wrong" per GBM, but GBM itself is frequently wrong.
- The profit comes from mean-reversion (PM converging back toward prior PM price), NOT from
  correctly predicting which way PM is wrong.

This is important: the strategy is a **mean-reversion / market-maker scalp**, not a
**directional bet on GBM being correct**.

### Regime Analysis (Entry Threshold = 5%)

**By Window Duration:**
| Duration | n     | Median Net PnL | Median Hold |
|---------- |-------|----------------|-------------|
| 5-min     | 4,946 | +3.3%          | 10s         |
| 15-min    | 7,842 | +2.4%          | 14s         |

5-min windows are slightly better: higher PnL and faster convergence.

**By Hour of Day (UTC):**
All sessions show similar PnL (~2.4-3.0%). No significant time-of-day effect.

**By Volatility Quartile:**
| Vol Quartile | Median Net PnL | Median Hold |
|--------------|----------------|-------------|
| Q1 (low)     | +2.4%          | 16s         |
| Q2           | +2.9%          | 12s         |
| Q3           | +3.1%          | 10s         |
| Q4 (high)    | +2.7%          | 10s         |

High volatility: faster convergence but similar PnL. Very robust across vol regimes.

**By BTC Direction:**
Up markets: +2.5%, Down markets: +3.0%. Marginally better when BTC is falling.

### Bid-Ask Spread

Implied half-spread from trade-to-trade price changes:
- p50 = 0.01 (1 cent on a $1 binary)
- This 1% half-spread is the minimum unavoidable execution cost beyond fees.

## Critical Issues and Limitations

### 1. Sampling Bias in "PM Price"

The PM price at each second is derived from the **last trade** at that second, not from
the orderbook mid. The last trade may have been at an extreme (bugged bot, thin book).
This inflates apparent deviation and makes convergence look faster/larger than reality.

**Real execution**: you'd need to observe the **best bid or ask** and trade against it.
If the book is thin or has a wide spread, you may not be able to fill at the observed price.

### 2. Latency (the critical feasibility gate)

- Median convergence: **12 seconds** at 5% threshold
- Websocket PM message latency: ~100-500ms
- Additional delays: auth, order placement, confirmation: ~200-1000ms
- Total round-trip to enter: **~0.5-1.5 seconds**
- This leaves ~10-11 seconds of remaining convergence window at median

**Assessment**: Technically feasible but tight. Any degradation (high-load periods,
slow PM API) would cause misses. The p10 = 4 seconds means 10% of opportunities
close in under 4 seconds — those would be missed entirely.

### 3. Market Impact

At 5% threshold: 12,788 trades/90 days = ~143 scalps/day across all windows.
PM books for BTC Up/Down are thin (short-duration binary markets). A $1,000 trade
could itself move the price 1-2 cents, wiping out part of the edge.

### 4. GBM Model Quality

The GBM model uses:
- Rolling 1440-min (24h) sigma estimated from 1-min Binance bars
- Simple log-normal assumption

This is a rough approximation. BTC exhibits:
- Jump risk (crashes/rallies)
- Non-constant volatility
- Mean-reversion over short windows

The GBM deviation may be noise from model error, not true mispricing.
**A better model would reduce apparent deviation by 20-40%**.

### 5. SELL Trade Semantics

This hypothesis uses only trade prices (not sides), so SELL handling is not directly
relevant. The PM price series is derived from `last_trade_price` regardless of direction.

However, at the execution level: **to buy YES, you must place a BUY YES order**.
Due to CTF split mechanic, SELL NO also results in YES exposure. Both paths should
be considered for execution. This doesn't affect discovery results.

## Structural Finding: GBM Deviation is "Normal"

The 5.3% median deviation is not exploitable noise — it's the **structural baseline**.
Even ignoring latency, the strategy earns +2.8% median net PnL, but this is on
the notional of a binary option that itself has only ~50% expected return.

Risk-adjusted thinking:
- Median entry at PM price ~0.45 (near fair value)
- Expected return from GBM alignment: not necessarily positive
- The mean-reversion PnL (+2.8%) comes from **prices converging**, not from being correct

## Verdict: MARGINAL (UPPER BOUND)

This is viable if and only if:
1. Execution latency < 3 seconds round-trip (possible with co-located infra)
2. Trade sizes < $200-300 per scalp (to avoid market impact)
3. Entry threshold of 10-15% (not 5%) — p50 hold of 22-42s is more workable

At 10% entry threshold, there are still ~140 opportunities/day, median hold 22s,
median net PnL +8.7% per notional. At $200/scalp, that's ~$17.40/scalp × 140/day
= **$2,436/day theoretical maximum** (UPPER BOUND, before real fill shortfall).

**Expected degradation**: 50-70% in tick-by-tick with realistic fills = ~$730-1,218/day.

## Comparison to SELL Dual-Test

This strategy's alpha does NOT come from trader consensus or SELL directionality.
It comes from Binance-to-PM price arbitrage. SELL handling is not applicable to
the signal generation logic. Noted: the discovery methodology requires a dual-test,
but the signal here is price-based (not directional from trader actions).

## Proposed Classifications

None needed — this is a pure price-arbitrage strategy requiring no trader classifications.

## Spawned Ideas

1. **Pure PM mean-reversion** (no GBM): simpler model, test if PM prices mean-revert
   independently within a window after any >5% move. May be more robust than GBM.

2. **GBM arbitrageur identification**: find wallets consistently trading on GBM
   deviations >10%. Monitor their activity in real-time.

3. **Market-maker strategy**: instead of scalping convergence, PROVIDE quotes at
   GBM fair value when PM book is stale. Earn spread both ways.
