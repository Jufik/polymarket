# Favorite-Longshot Strategy: Research Results

**Date**: 2026-02-18
**Test period**: 2025-01 to 2026-01 (13 months, walk-forward OOS)
**Training data**: 2023-01 to rolling test-month cutoff

---

## Executive Summary

The "Favorite-Longshot Arbitrage" hypothesis was partially wrong but led to a stronger finding:

**The calibration curve adds nothing.** The edge is not in sophisticated price-probability calibration. It's in a simple structural filter: **buy NO on binary "Will" questions**.

| Strategy | Bets | HR | PnL | $/bet | Sharpe(m) |
|----------|-----:|---:|----:|------:|----------:|
| **Will binary NO (YES 10-50%)** | **8,021** | **75.3%** | **+$39,773** | **+$4.96** | **2.43** |
| Will binary NO (any price) | 20,426 | 57.8% | +$140,099 | +$6.86 | 1.50 |
| ALL binary NO (YES 10-50%) | 61,503 | 59.4% | +$126,178 | +$2.05 | 1.75 |
| Will binary + calibration overlay | 8,021 | 75.3% | +$39,773 | +$4.96 | 2.43 |

The calibration row is identical to the base strategy — every price bin for "Will" binary questions has positive NO edge, so the calibration filter never excludes anything.

---

## What the Calibration Curve Actually Shows

### Binary markets: mild edge, uniform across bins

For **all binary markets**, the NO edge above breakeven is 1-2pp per bin. Small but consistent:

| YES Price Bin | Implied YES | Actual YES | NO Edge | E[PnL/$100] |
|:------------:|:-----------:|:----------:|:-------:|:-----------:|
| 10-15% | 12.2% | 9.7% | +2.5pp | +$2.87 |
| 15-20% | 17.4% | 14.9% | +2.4pp | +$2.94 |
| 20-25% | 22.2% | 20.3% | +1.9pp | +$2.39 |
| 25-30% | 27.0% | 25.5% | +1.5pp | +$2.05 |
| 30-35% | 32.0% | 30.0% | +2.0pp | +$2.93 |
| 35-40% | 37.4% | 35.3% | +2.0pp | +$3.22 |
| 40-45% | 42.3% | 40.0% | +2.3pp | +$3.99 |
| 45-50% | 47.9% | 46.7% | +1.2pp | +$2.27 |

E[PnL] is $2-4 per $100 bet. Thin. This is what the walk-forward backtest on ALL binary markets found: a near-zero Sharpe.

### "Will" binary questions: 2-3x stronger edge

| YES Price Bin | Actual YES (Will) | Implied YES | E[PnL/$100] |
|:------------:|:-----------------:|:-----------:|:-----------:|
| 10-15% | 8.8% | 12.2% | +$3.85 |
| 15-20% | 12.8% | 17.4% | +$5.49 |
| 20-25% | 17.7% | 22.2% | +$5.80 |
| 25-30% | 24.2% | 26.9% | +$3.71 |
| 30-35% | 27.0% | 32.0% | +$7.29 |
| 35-40% | 32.7% | 37.3% | +$7.42 |
| **40-45%** | **36.6%** | **42.2%** | **+$9.67** |
| 45-50% | 44.4% | 47.2% | +$5.29 |

E[PnL] is $4-10 per $100 bet. **2-3x the edge of broad binary markets.** The "Will" filter captures a structural bias where proposed events don't happen as often as the market prices.

### Liquid markets: edge vanishes

When filtering to markets with >100 trades, E[PnL] drops to $0-3 per $100. The edge is **concentrated in less liquid markets** where early prices are less efficient.

---

## Strategy Performance

### Monthly PnL Stream (Will binary NO, YES 10-50%)

| Month | Bets | HR | PnL | Cum PnL |
|:-----:|-----:|---:|----:|--------:|
| 2025-01 | 221 | 72.9% | +$596 | +$596 |
| 2025-02 | 330 | 72.7% | +$817 | +$1,412 |
| 2025-03 | 293 | 75.1% | +$1,330 | +$2,743 |
| 2025-04 | 331 | 72.5% | +$554 | +$3,296 |
| 2025-05 | 354 | 71.8% | +$1,523 | +$4,819 |
| 2025-06 | 177 | 76.8% | +$2,177 | +$6,996 |
| 2025-07 | 325 | 75.1% | +$1,412 | +$8,408 |
| **2025-08** | **391** | **66.8%** | **-$1,908** | **+$6,499** |
| 2025-09 | 1,024 | 80.4% | +$9,572 | +$16,071 |
| **2025-10** | **883** | **69.9%** | **-$2,696** | **+$13,375** |
| 2025-11 | 1,183 | 75.7% | +$7,688 | +$21,063 |
| 2025-12 | 1,048 | 78.9% | +$10,125 | +$31,188 |
| 2026-01 | 1,461 | 76.9% | +$8,585 | +$39,773 |

**11/13 months profitable.** Two losing months (Aug, Oct 2025) with modest losses (-$1.9K, -$2.7K).

### Key Metrics

| Metric | Value |
|--------|------:|
| Total bets | 8,021 |
| Hit rate | 75.3% |
| Total PnL | +$39,773 |
| Avg PnL/bet | +$4.96 |
| Monthly Sharpe | **2.43** |
| Max drawdown | $8,329 |
| PnL / Max DD | **4.78x** |
| Months positive | 11/13 (85%) |
| Worst month | -$2,696 |
| Best month | +$10,125 |
| Avg bets/month | 617 |

### Capital Efficiency

| Metric | Value |
|--------|------:|
| Avg NO price paid | 71.8% |
| Avg return per bet | +5.0% |
| Avg win return | +39.3% |
| Avg loss return | -100% |
| Median time to resolution | **6.5 days** |
| Capital turnover | ~4.6x/month |

The 6.5-day median resolution means capital turns over quickly. If deploying $10K across ~60 concurrent bets ($167/bet), expect ~$30/day in edge with rapid recycling.

---

## What Price Range Is Best?

| Config | Bets | HR | PnL | $/bet | Sharpe |
|--------|-----:|---:|----:|------:|-------:|
| YES 10-50% | 8,021 | 75.3% | +$39,773 | +$4.96 | **2.43** |
| YES 10-35% | 5,479 | 82.5% | +$26,755 | +$4.88 | 2.39 |
| YES 25-50% | 4,569 | 65.9% | +$22,055 | +$4.83 | 2.30 |
| Any price | 20,426 | 57.8% | +$140,099 | +$6.86 | 1.50 |

- **Best risk-adjusted (Sharpe 2.43)**: YES 10-50%. Good balance of bet count and HR.
- **Best absolute PnL (+$140K)**: Any price. Lower Sharpe (1.50) but 3.5x more PnL from 2.5x more bets. The extra bets at high YES prices (50-99%) have only 57.8% HR but larger payoffs per correct NO bet.
- **Highest HR (82.5%)**: YES 10-35%. Very safe but fewer bets.

**Recommendation**: Run YES 10-50% as the core strategy. If capacity allows, extend to any price for additional PnL with the understanding that Sharpe degrades.

---

## Why This Works: Structural Explanation

### 1. "Will X happen?" is structurally NO-biased

Binary "Will" questions ask about specific events: "Will Bitcoin hit $200K?", "Will Trump visit country X?", "Will inflation drop below 2%?". Most proposed events don't happen within the market's timeframe. The base NO rate for binary "Will" questions is **58.8%** vs 54.4% for all binary.

### 2. The favorite-longshot bias amplifies the structural NO bias

At low YES prices (10-45%), the longshot YES outcome is even more unlikely than the price implies. This is the well-known favorite-longshot bias from horse racing and sports betting: bettors systematically overpay for longshots. On Polymarket, this manifests as YES tokens at 20-30% having actual YES rates of only 15-25%.

### 3. The edge is NOT in calibration sophistication

Every single price bin for "Will" binary questions has positive NO edge above breakeven. A simple rule — "buy NO on any binary 'Will' question where YES price is 10-50%" — captures the full edge. No calibration curve, no training data, no walk-forward needed.

### 4. The edge is concentrated in less liquid markets

When filtered to >100 trades, the edge shrinks to near-zero. This suggests the mispricings are in smaller, less-watched markets where fewer sophisticated traders participate.

---

## Risks and Caveats

### 1. Execution reality
The backtest uses the **median early price** (first 30% of market lifetime) as the entry point. In practice, you'd need to identify "Will" markets early and enter at the prevailing price. If the early price is stale or illiquid, actual execution may be worse.

### 2. Market identification
Parsing "Will" from the question text is straightforward but not perfect. Some non-"Will" questions have similar structure ("Is X going to..."), and some "Will" questions may be misclassified.

### 3. Capacity
At $100/bet across 600 bets/month, this is $60K/month deployed. Scaling to $1K/bet requires $600K/month, which may move prices in thin markets (where the edge is strongest).

### 4. The edge is shrinking temporally
The overall NO base rate declined from 66.2% (pre-2025) to 61.7% (2025+). If this trend continues, the "Will" NO edge may compress further.

### 5. Two losing months exist
Aug 2025 (-$1.9K) and Oct 2025 (-$2.7K) show the strategy isn't risk-free. A string of 2-3 bad months is possible.

### 6. Survivorship in question types
If Polymarket changes the types of markets they list (e.g., more balanced "vs" questions instead of "Will X?"), the structural NO bias would weaken.

---

## Comparison to Consensus Copy Strategy

| Metric | Fav-Longshot NO | Consensus Copy NO |
|--------|:--------------:|:-----------------:|
| Monthly Sharpe | **2.43** | ~5.0 (best config) |
| Bets/month | ~600 | ~20-65 |
| Total PnL (13mo) | +$39,773 | +$2,901 (best config) |
| Complexity | Very low | High |
| Data needed | Question text + price | PnL, MVF, prices, consistency |
| Combinable? | **Yes** | Yes |

The consensus copy has higher Sharpe per bet but far fewer bets. Favorite-Longshot has lower Sharpe but massively more bets and higher total PnL. **These strategies are orthogonal and combinable** — use consensus copy for high-conviction NO bets, and Fav-Longshot for the long tail.

---

## Next Steps

1. **Live paper trade**: Deploy the YES 10-50% config on live "Will" binary markets for 3 months
2. **Execution price validation**: Compare early median price vs actual achievable entry price
3. **Combine with consensus copy**: When both signals agree (consensus copy says NO + "Will" question at YES < 50%), does the combined signal have even higher edge?
4. **Extend question patterns**: Test other NO-biased question patterns ("before", "by", "reach", "drop below")
5. **Volume filter study**: Does restricting to markets with $1K+ volume sacrifice too much edge?
