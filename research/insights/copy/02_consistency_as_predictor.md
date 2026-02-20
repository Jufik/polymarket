# Consistency as a Predictor of Future Profitability

**Date**: 2026-02-16
**Data**: 70.9M trader-market PnL rows, 390K resolved markets, Feb 2023 - Dec 2025
**Methodology**: Monthly PnL bucketed by market resolution date. Consecutive profitable months with no gaps define "consistency". Forward returns measured on out-of-sample months after each consistency window.

## Baseline Statistics

| Metric | Value |
|--------|-------|
| Total traders (with resolved markets) | 1,727,226 |
| Average monthly PnL (all traders) | $-40.91 |
| Median monthly PnL (all traders) | ~$0.00 |
| Overall monthly win rate | 47.1% |
| Analysis period | Feb 2023 - Dec 2025 (34 months) |

The average trader loses money. The median monthly PnL is essentially zero, with a slight negative bias. Only 47.1% of trader-months are profitable -- worse than a coin flip.

---

## 1. How Many Traders Are Consistently Profitable?

A trader is "N-month consistent" if they have N **consecutive** months of positive PnL (on resolved markets, no gaps). Each month's PnL is the sum of all market PnLs resolving in that month.

| Lookback (months) | Unique Traders | % of Total | Windows Found |
|:-----------------:|:--------------:|:----------:|:-------------:|
| 3 | 304,366 | 17.6% | 667,743 |
| 6 | 39,713 | 2.3% | 93,910 |
| 9 | 8,409 | 0.49% | 15,550 |
| 12 | 1,008 | 0.06% | 1,590 |

**Key insight**: Consistency is exponentially rare. Each 3-month extension eliminates ~80-90% of traders. Only 1 in 1,700 traders achieves 12 consecutive profitable months. This rarity itself suggests these traders have genuine skill rather than luck -- under a random walk with 47% monthly win rate, the probability of 12 consecutive wins is 0.47^12 = 0.0001% (vs 0.06% observed, a 600x enrichment).

---

## 2. Forward Returns of Consistent Traders

For each consistency window ending at month M, we measure PnL in months M+1 through M+6.

### Average Forward PnL ($)

| Lookback | M+1 | M+2 | M+3 | M+4 | M+5 | M+6 |
|:--------:|------:|------:|------:|------:|------:|------:|
| 3 | 380.93 | 324.09 | 297.64 | 196.64 | 124.38 | 152.71 |
| 6 | 801.95 | 945.22 | 1,066.78 | 1,472.87 | 937.27 | 1,604.87 |
| 9 | 2,473.83 | 3,031.53 | 3,642.82 | 5,292.67 | 6,396.90 | 11,344.89 |
| 12 | 9,987.14 | 13,327.14 | 29,031.22 | 30,678.55 | 37,199.99 | 37,852.82 |

All values are positive (the baseline average is -$40.91). The signal is strongly positive at every lookback and every forward horizon.

### Median Forward PnL ($)

| Lookback | M+1 | M+2 | M+3 | M+4 | M+5 | M+6 |
|:--------:|------:|------:|------:|------:|------:|------:|
| 3 | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 | 0.04 |
| 6 | 0.04 | 0.04 | 0.05 | 0.06 | 0.09 | 0.10 |
| 9 | 0.07 | 0.11 | 0.09 | 0.23 | 0.33 | 0.70 |
| 12 | 0.71 | 1.19 | 522.04 | 1,343.95 | 1,418.37 | 225.64 |

**Interpretation**: The enormous gap between mean and median reveals a highly skewed distribution. For 3-month consistency, the median trader earns just $0.03/month forward -- the signal is carried by a small number of very profitable traders. At 12-month consistency, the median finally becomes meaningful ($0.71 at M+1, growing to $1,418 at M+5), suggesting this group contains genuinely skilled traders, not just lucky ones.

### Forward Win Rate (% of months profitable)

| Lookback | M+1 | M+2 | M+3 | M+4 | M+5 | M+6 |
|:--------:|------:|------:|------:|------:|------:|------:|
| 3 | 68.9% | 69.5% | 70.4% | 70.4% | 69.4% | 68.4% |
| 6 | 82.6% | 81.0% | 80.8% | 79.3% | 77.7% | 76.1% |
| 9 | 86.7% | 86.5% | 87.6% | 82.2% | 82.4% | 73.0% |
| 12 | 90.4% | 89.0% | 80.3% | 79.3% | 79.7% | 72.9% |

**This is the most important table.** Baseline win rate is 47.1%. Consistent traders massively outperform:
- **3-month consistent**: 69% win rate (+22 percentage points vs baseline)
- **6-month consistent**: 83% win rate (+36 pp)
- **9-month consistent**: 87% win rate (+40 pp)
- **12-month consistent**: 90% win rate (+43 pp)

The signal persists even 6 months forward, though it decays from the M+1 peak.

### Sample Sizes

| Lookback | M+1 | M+2 | M+3 | M+4 | M+5 | M+6 |
|:--------:|------:|------:|------:|------:|------:|------:|
| 3 | 442,924/667,743 | 364,557/667,743 | 303,576/667,743 | 240,972/667,743 | 186,785/667,743 | 137,405/667,743 |
| 6 | 65,348/93,910 | 49,768/93,910 | 33,649/93,910 | 20,042/93,910 | 12,552/93,910 | 8,293/93,910 |
| 9 | 8,237/15,550 | 4,919/15,550 | 3,131/15,550 | 1,505/15,550 | 805/15,550 | 326/15,550 |
| 12 | 644/1,590 | 335/1,590 | 122/1,590 | 82/1,590 | 59/1,590 | 48/1,590 |

Note: The denominator is total windows. The numerator is windows where the trader had data in that forward month (i.e., they traded in resolved markets that month). The dropoff is partly survivorship (traders leave the platform) and partly because later windows have fewer future months available before the Dec 2025 cutoff. The 12-month lookback has small samples at M+3 onward (N<150), so those estimates are noisy.

---

## 3. Signal Decay Curve

How quickly does the consistency signal fade? We look at the forward win rate relative to its M+1 value.

| Lookback | M+1 (base) | M+2 | M+3 | M+4 | M+5 | M+6 |
|:--------:|:----------:|:---:|:---:|:---:|:---:|:---:|
| 3 | 68.9% | 69.5% (+0.6) | 70.4% (+1.5) | 70.4% (+1.5) | 69.4% (+0.5) | 68.4% (-0.5) |
| 6 | 82.6% | 81.0% (-1.6) | 80.8% (-1.8) | 79.3% (-3.3) | 77.7% (-4.9) | 76.1% (-6.5) |
| 9 | 86.7% | 86.5% (-0.2) | 87.6% (+0.9) | 82.2% (-4.5) | 82.4% (-4.3) | 73.0% (-13.7) |
| 12 | 90.4% | 89.0% (-1.4) | 80.3% (-10.1) | 79.3% (-11.1) | 79.7% (-10.7) | 72.9% (-17.5) |

**Decay patterns**:
- **3-month**: Almost no decay. The signal is stable but weak (~69% win rate, only +22pp over baseline). It is more of a "persistent baseline" than a decaying signal.
- **6-month**: Slow, linear decay of ~1.3pp/month. Still 76% at M+6 (+29pp over baseline). **Half-life > 12 months.**
- **9-month**: Stable through M+3, then drops ~5pp. Still 73% at M+6. **Half-life ~8-10 months.**
- **12-month**: Sharpest decay -- drops 10pp by M+3, 17.5pp by M+6. But even at M+6, still 73% (vs 47% baseline). **Half-life ~5-6 months.** Sample sizes become very small here though.

**Bottom line**: The signal does NOT have a fast half-life. Even the most aggressive 12-month lookback still shows 73% win rate 6 months forward. The practical half-life of the consistency signal (measured as time for excess win rate over baseline to halve) is approximately:
- 3-month lookback: >12 months (essentially no decay)
- 6-month lookback: >12 months
- 9-month lookback: ~8-10 months
- 12-month lookback: ~5-6 months

---

## 4. Consistency vs Volume-Matched Random Traders

To test if the signal is real vs just reflecting higher-volume traders, we compare consistent traders against randomly selected traders from the same volume percentile bucket (100 buckets).

### Average Forward PnL: Consistent vs Random

| Lookback | Fwd Month | Consistent Avg PnL | Random Avg PnL | Difference |
|:--------:|:---------:|:------------------:|:--------------:|:----------:|
| 3 | M+1 | $380.93 | $-2,545.40 | +$2,926 |
| 3 | M+3 | $297.64 | $-2,576.18 | +$2,874 |
| 3 | M+6 | $152.71 | $-2,720.79 | +$2,873 |
| 6 | M+1 | $801.95 | $-3,179.92 | +$3,982 |
| 6 | M+3 | $1,066.78 | $-3,741.60 | +$4,808 |
| 6 | M+6 | $1,604.87 | $-5,036.98 | +$6,642 |
| 9 | M+1 | $2,473.83 | $-6,166.62 | +$8,640 |
| 9 | M+3 | $3,642.82 | $-7,566.09 | +$11,209 |
| 9 | M+6 | $11,344.89 | $-14,563.99 | +$25,909 |
| 12 | M+1 | $9,987.14 | $-14,831.45 | +$24,819 |
| 12 | M+3 | $29,031.22 | $-51,262.74 | +$80,294 |
| 12 | M+6 | $37,852.82 | $-90,054.93 | +$127,908 |

**The signal is real, not a volume artifact.** Volume-matched random traders have deeply negative forward PnL at every horizon. Consistent traders have positive PnL. The difference is enormous -- consistent traders outperform their volume-matched peers by thousands to tens of thousands of dollars per month. This rules out the hypothesis that "consistency is just a proxy for high volume."

---

## 5. Optimal Lookback Period

Which lookback gives the best 1-month-forward return?

| Lookback | Avg Fwd 1M PnL | Median Fwd 1M PnL | Win Rate | N Traders | Win Rate Lift |
|:--------:|:--------------:|:------------------:|:--------:|:---------:|:-------------:|
| 3 | $380.93 | $0.03 | 68.9% | 304,366 | +21.8pp |
| 6 | $801.95 | $0.04 | 82.6% | 39,713 | +35.5pp |
| 9 | $2,473.83 | $0.07 | 86.7% | 8,409 | +39.6pp |
| 12 | $9,987.14 | $0.71 | 90.4% | 1,008 | +43.3pp |

**Tradeoff**: Longer lookback gives a stronger signal but a much smaller universe.

- **For copy-trading** (follow a small set of traders): 9- or 12-month lookback is optimal. The 90% win rate at 12-month is exceptional, and 1,008 traders is enough to build a diversified portfolio.
- **For market-making/signal generation** (need broad coverage): 6-month lookback offers the best balance -- 83% win rate with 39,713 traders covering many markets.
- **3-month is too noisy**: 69% win rate with a $0.03 median is weak. The mean is pulled up by outliers.

---

## 6. Market Count Interaction

Does requiring a minimum number of markets per month improve the signal? (Higher min_markets = more diversified trader, less noise from single-market luck.)

### 1-Month Forward PnL by Min Markets and Lookback

| Min Markets | Lookback | Consistent Traders | Avg Fwd 1M PnL | Median Fwd 1M PnL | Win Rate |
|:-----------:|:--------:|:-----------------:|:--------------:|:------------------:|:--------:|
| 1 | 3 | 304,366 | $381 | $0.03 | 68.9% |
| 1 | 6 | 39,713 | $802 | $0.04 | 82.6% |
| 1 | 9 | 8,409 | $2,474 | $0.07 | 86.7% |
| 1 | 12 | 1,008 | $9,987 | $0.71 | 90.4% |
| 10 | 3 | 14,409 | $9,289 | $123 | 65.2% |
| 10 | 6 | 879 | $20,286 | $2,335 | 80.5% |
| 10 | 9 | 194 | $30,832 | $3,775 | 86.0% |
| 10 | 12 | 55 | $28,725 | $4,301 | 89.5% |
| 20 | 3 | 6,256 | $14,274 | $857 | 70.5% |
| 20 | 6 | 537 | $24,984 | $3,125 | 82.5% |
| 20 | 9 | 129 | $35,451 | $4,691 | 87.8% |
| 20 | 12 | 40 | $28,797 | $3,742 | 86.9% |
| 50 | 3 | 2,377 | $22,876 | $1,791 | 74.5% |
| 50 | 6 | 267 | $39,500 | $4,847 | 83.4% |
| 50 | 9 | 62 | $57,758 | $7,712 | 89.0% |
| 50 | 12 | 20 | $44,403 | $7,906 | 85.7% |
| 100 | 3 | 1,189 | $37,317 | $2,782 | 77.0% |
| 100 | 6 | 127 | $53,773 | $5,386 | 85.2% |
| 100 | 9 | 32 | $71,357 | $9,065 | 88.6% |
| 100 | 12 | 13 | $40,738 | $3,616 | 79.2% |

**Key findings**:

1. **Median PnL jumps dramatically with min_markets >= 10**. At min_markets=1, medians are near zero (signal carried by outliers). At min_markets=10+, medians are in the hundreds to thousands (signal is broadly shared across the group).

2. **Win rate is largely unchanged by min_markets** for the same lookback. The diversification filter does NOT improve win rate -- it primarily improves the dollar magnitude of returns per trader (because these are bigger traders).

3. **Sweet spot: min_markets=20, lookback=9** gives 129 traders with 87.8% win rate and $4,691 median forward PnL. This is a strong, reliable signal with enough traders to build a portfolio.

4. **Diminishing returns at min_markets=100**: Win rate actually drops at 12-month lookback (79.2%, down from 90.4% at min_markets=1). With only 13 traders, this is likely noise from tiny sample size.

---

## 7. Conclusions and Trading Implications

### Key Findings

1. **Consistency is a strong, real signal.** Traders with consecutive profitable months have dramatically higher future win rates (69-90%) vs the 47% baseline. The signal survives volume matching -- it is not just a proxy for trading more.

2. **The signal is durable.** Unlike many alpha signals that decay in days or weeks, consistency-based signals remain strong 3-6 months forward. The 6-month lookback maintains 76% win rate even at M+6.

3. **Longer lookback = stronger signal, smaller universe.** The classic precision-recall tradeoff. 12-month consistency gives 90% win rate but only 1,008 traders. 6-month gives 83% with 39,713 traders.

4. **Mean-median divergence is severe.** At low min_markets thresholds, the mean PnL is driven by a handful of whales. The median is near zero. This means a naive "follow all consistent traders equally" strategy would disappoint -- returns are concentrated in the top traders within the consistent group.

5. **Market count filter fixes the mean-median problem.** Requiring 10-20+ markets per month raises the median PnL by 100-1000x, making the signal actionable for position-weighted strategies.

### Recommended Strategy Parameters

| Parameter | Conservative | Aggressive |
|-----------|-------------|------------|
| Lookback | 9 months | 6 months |
| Min markets/month | 20 | 10 |
| Expected universe | ~129 traders | ~879 traders |
| Expected 1M win rate | ~88% | ~81% |
| Expected median 1M PnL | ~$4,700/trader | ~$2,300/trader |
| Rebalance | Monthly | Monthly |
| Signal half-life | ~8-10 months | >12 months |

### Caveats and Risks

- **Survivorship bias**: We only observe traders who continued trading. Traders who had a profitable streak and then quit are partially captured (their forward months show as missing data), but those who quit just before a loss are missed.
- **Resolution timing**: PnL is bucketed by market resolution date, not trade date. A trader who entered positions in January on markets that resolved in June has their PnL attributed to June. This creates a lag that may not reflect real-time signal quality.
- **Whale concentration**: A small number of very large traders drive the mean returns. Any copy-trading strategy must account for position sizing and capacity constraints.
- **Small samples at long lookbacks**: The 12-month lookback results are based on 1,008 traders and as few as 48 data points at M+6. These estimates have wide confidence intervals.
- **Market regime**: The analysis period (2023-2025) includes the 2024 US election cycle, which brought unusual volume and possibly unusual patterns. Out-of-sample validation on future data is essential.

### Next Steps

1. **Sharpe-ratio analysis**: Convert to risk-adjusted returns to account for volatility differences.
2. **Conditional analysis**: Does the signal differ by market type (politics, sports, crypto)?
3. **Trader clustering**: Among consistent traders, are there distinct archetypes (market makers vs directional traders)?
4. **Live implementation**: Build a monthly-rebalanced portfolio of 6-month consistent traders with 20+ markets/month, track paper PnL.
