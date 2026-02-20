# Up/Down Market Structure Analysis: No Exploitable Edge

**Date**: 2026-02-20
**Method**: Comprehensive analysis across 15-min, 1-hour, and daily "Up or Down" markets
**Universe**: 32,707 markets (BTC, ETH, SOL, XRP + equities), May 2025 - Feb 2026
**Strategies tested**: Time-of-day, momentum, contrarian, cross-asset, serial autocorrelation, straddle/MM

---

## TL;DR

**Up/Down markets are efficiently priced at every level.** We tested 7 distinct strategies across 3 timeframes on 32,707 markets. Every strategy either (a) fails walk-forward validation, (b) shows zero edge in recent data, or (c) relies on extreme variance with decaying returns. The market correctly prices the probability of direction at every threshold, leaving no systematic edge. The only structural edge on Polymarket remains the OTM NO strategy on "above/below" checkpoint markets (insight #21).

---

## Market Universe

| Timeframe | Markets | Resolved | Assets | Date Range |
|-----------|:---:|:---:|--------|------------|
| 15-min | 5,117 | 5,117 | BTC, ETH | Sep-Oct 2025 |
| **1-hour** | **24,141** | **23,880** | **BTC, ETH, SOL, XRP** | **May 2025 - Feb 2026** |
| Daily | 3,274 | 3,190 | Crypto + Equities | Mar 2025 - Feb 2026 |

All timeframes show ~50% Up/Down split at the aggregate level (50.0-51.5%).

---

## Strategy #1: Serial Autocorrelation (Streak Trading)

### 15-min: Statistically significant mean-reversion

BTC and ETH both show z=+3.84 (p<0.001) mean-reversion on 15-min markets. After 3 consecutive Ups, next Up = 44% (vs 50% base). But the market **prices this in**: after Up, median next-market YES = 48.2c; after Down, next YES = 51.5c. At actual entry prices, reversal strategies lose $10.19/bet.

### 1-hour: Weak, not significant

| Asset | Markets | Reversals | z-score | Pattern |
|-------|:---:|:---:|:---:|:---:|
| BTC | 6,320 | 50.8% | +1.35 | random |
| ETH | 6,003 | 50.9% | +1.42 | random |
| SOL | 5,853 | 50.7% | +1.02 | random |
| XRP | 5,666 | 50.1% | +0.15 | random |

After 3 consecutive Ups, next Up = 46-48% across all assets. Mild mean-reversion at longer streaks, but not statistically significant and too small to trade (2-4pp at ~50c entry).

### Daily: Random

All crypto assets show z near 0. No autocorrelation. Equities (DOW, SP500, NASDAQ) have slight upward bias (~55% Up) consistent with equity markets generally rising, but sample sizes are small (82-101 markets) and the bias is priced in.

---

## Strategy #2: Time-of-Day Bias

### Finding: Persistent in-sample, collapses out-of-sample

Consistent pattern across all crypto assets (1-hour):

| Hour (UTC) | BTC Up% | ETH Up% | SOL Up% | XRP Up% | Pattern |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 00:00 | 59.5% | 59.0% | 63.9% | 56.8% | UP bias |
| 01:00 | 44.8% | 43.0% | 42.6% | 36.2% | DOWN bias |
| 21:00 | 42.5% | 44.8% | — | — | DOWN bias |
| 23:00 | 57.8% | 57.7% | 59.4% | — | UP bias |

The market partially prices this: at 00:00 UTC, avg YES = 52.7c vs actual 59.5% Up. The apparent 6.8pp edge is real in-sample.

**Walk-forward result**: Training on first 50% of data, testing on second 50%:

| Threshold | Test Bets | Test HR | Test $/bet |
|:---:|:---:|:---:|:---:|
| ±3pp | 5,202 | 52.1% | **-$8.84** |
| ±5pp | 2,226 | 52.2% | **-$8.72** |
| ±7pp | 860 | 56.3% | **-$5.76** |

**All negative.** The time-of-day patterns from the first half don't persist in the second half.

---

## Strategy #3: Momentum Following

Enter on the same side as the market's price movement. When YES drops below a threshold (market says Down), buy Down.

| Threshold | Markets hit | HR | $/bet |
|:---:|:---:|:---:|:---:|
| YES ≤ 40c | 19,818 | 59.5% | **-$1.13** |
| YES ≤ 30c | 17,030 | 69.3% | **-$1.43** |
| YES ≤ 20c | 14,828 | 79.5% | **-$1.06** |
| YES ≤ 10c | 13,111 | 89.9% | **-$0.62** |
| YES ≤ 5c | 12,489 | 94.4% | **-$0.54** (sic) |

**Negative at every threshold.** Even at 90% HR (YES ≤ 10c), the small payout per win ($5.56 on $50 bet) can't overcome the 10% loss rate ($50 per loss). The market is perfectly calibrated: when YES = 10c, the actual reversal probability is ~10%.

---

## Strategy #4: Contrarian Reversal (Fade Early Moves)

Enter opposite to the market's early direction. When YES drops 15c+ in the first 10% of the market's trading life, buy Up.

### Walk-forward results

| Threshold | Train $/bet | Test $/bet | Test bets |
|:---:|:---:|:---:|:---:|
| ±5c | $8.41 | $5.52 | 6,274 |
| ±10c | $14.25 | $9.59 | 3,508 |
| ±15c | $24.29 | $18.57 | 1,672 |
| ±20c | $22.91 | $8.84 | 14,797 |

Appears profitable! But critical caveats:

### The edge is decaying to zero

| Month | Bets | HR | $/bet |
|-------|:---:|:---:|:---:|
| Jun 2025 | 1,673 | 19.4% | **$44.97** |
| Jul 2025 | 3,113 | 21.5% | $13.50 |
| Oct 2025 | 3,164 | 19.7% | $24.96 |
| Nov 2025 | 2,991 | 19.6% | $3.54 |
| Dec 2025 | 2,742 | 19.5% | $2.87 |
| Jan 2026 | 2,274 | 19.6% | $6.38 |
| **Feb 2026** | **1,388** | **18.6%** | **-$1.22** |

By February 2026, the strategy is break-even to negative. This is classic new-market arbitrage: 1-hour markets launched in mid-2025, early pricing was inefficient, now participants have learned.

### Extreme variance

- **Median bet PnL: -$50.** Most bets lose everything.
- **Top 10% of winners generate 243-394% of total PnL.** Remove a handful of extreme reversals and the strategy is deeply negative.
- **HR = 19-20%.** You lose 4 out of 5 bets.

### Verdict: Not tradeable

Even if the historical average is positive, the combination of (a) decaying edge, (b) extreme variance, and (c) 80% loss rate makes this strategy impractical. One month of bad luck wipes out multiple months of gains. And the most recent month (Feb 2026) is already negative.

---

## Strategy #5: Cross-Asset Correlation

### 80% same-direction correlation within the hour

| Pair | Same Direction |
|------|:---:|
| BTC-ETH | **79.9%** |
| BTC-SOL | 76.0% |
| BTC-XRP | 75.1% |
| ETH-SOL | 78.9% |

But this doesn't create a trading edge because:

1. **No lead-lag**: BTC at hour N does not predict ETH/SOL/XRP at hour N+1 (all deltas < 2pp, indistinguishable from noise)
2. **Same-hour resolution**: Both markets resolve simultaneously, so you can't use one's outcome to predict the other
3. **Correlation is priced in**: When BTC moves Up, the ETH market price moves Up in near-real-time

### All-4-crypto consensus signal

| Signal | BTC Up at H+1 |
|--------|:---:|
| All 4 Up at H | 47.4% |
| All 4 Down at H | 49.3% |

**No predictive value.** Unanimous crypto direction doesn't predict next-hour outcomes.

---

## Strategy #6: Market-Making Straddle (from prior analysis)

Post limit orders at 48c on both Up and Down sides:

- Both fill (88.6% of markets): guaranteed 4c profit
- One-side fill (11.4%): 99% adverse selection → lose ~$49
- Net: **-$1.98/market**

Tested all spread widths 30c-49c: **all lose money.** The adverse selection on one-sided fills overwhelms the spread capture.

---

## Strategy #7: Volume and Price Extremity

| Volume Band | Markets | Up% | Edge |
|-------------|:---:|:---:|:---:|
| Low (<$100) | 62 | 32.3% | +3.6pp |
| Medium ($100-500) | 121 | 47.9% | +2.0pp |
| High ($500+) | 23,636 | 50.5% | +0.5pp |

Low-volume markets show slight Down bias, but sample is tiny (62 markets) and impractical to trade.

---

## Why Up/Down Markets Are Efficient

The fundamental difference between Up/Down and above/below markets:

| Feature | Up/Down | Above/Below (OTM) |
|---------|---------|-------------------|
| Price structure | ~50c (symmetric) | YES 5-25c (asymmetric) |
| Payout | ~$50 win or $50 loss | $5-25 win or $100 loss |
| Market opens at | ~50c | Already OTM |
| Information | Reveals during market | Known at open |
| Volatility premium | None (symmetric) | **Yes (lottery ticket)** |
| Number of outcomes | 2 equally likely | 2, one heavily favored |
| Price discovery | Fast, efficient | Less liquid, premium persists |

**The key**: Up/Down markets are essentially coin flips where the market efficiently discovers the probability in real-time. There's no structural premium because both sides are equally valid. Above/below OTM markets have a structural lottery premium where speculators overpay for the upside.

---

## Recommendation

**Do not trade Up/Down markets directionally or via market-making.** Every strategy we tested either fails walk-forward or shows decaying returns converging to zero.

The confirmed exploitable edges remain:
1. **OTM NO on crypto above/below** (insight #21): 98.9% HR, $12.72/bet, stable across 7 months
2. **S2 NO on "Will" binary** (insight #19): 85.5% HR, $968/month upper bound, 14 months of data

---

## Appendix: Data Notes

- 5-minute and 4-hour Up/Down markets don't exist on Polymarket
- 4-hour resolution markets are the "above/below" checkpoint type (covered in insight #21)
- ETH/BTC ratio markets (1,725 markets) exist but were not analyzed separately due to small sample per pair
- Equity Up/Down (DOW, SP500, NASDAQ, TESLA) have only 82-106 markets each — insufficient for robust analysis
- All price data from `data/derived/market_prices.parquet` (35M ticks across 24K 1-hour markets)
