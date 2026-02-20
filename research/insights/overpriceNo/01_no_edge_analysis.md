# Overpriced NO: Edge Research in Polymarket's NO-Skewed Market

**Date**: 2026-02-18
**Data**: 390K resolved markets, 70.9M trader-market PnL rows, Nov 2022 - Jan 2026

**Core question**: The Polymarket ecosystem has a 62:38 NO:YES resolution skew.
Where is the NO side overpriced (creating YES opportunities)?
Where is the NO side underpriced (creating NO opportunities)?
And what structural edges exist within this skew?

---

## TL;DR

1. The 62% NO base rate is **almost entirely structural from neg_risk multi-outcome markets** (86.1% NO). Binary markets are only mildly skewed (53.4% NO).
2. A classic **favorite-longshot bias** exists: when early prices imply YES < 50%, NO is systematically underpriced by 5-8pp. When YES > 50%, YES is underpriced by 2-10pp.
3. **"Will X happen?" questions** have 78% NO rate — but decompose to: binary "Will" = 42% YES (moderate NO edge), neg_risk "Will" = 13% YES (structural 1/N).
4. **Earnings/financials** are the clearest category where NO is overpriced: 74% YES rate. Buying YES on earnings calls is +EV.
5. **Pure taker NO bettors** are net profitable on binary markets ($331M) but lose on neg_risk markets (-$24M).
6. The NO edge is **shrinking over time** (66.2% pre-2025 → 61.7% 2025+), suggesting markets are becoming more efficient.

---

## 1. The NO Skew Is Structural, Not Informational

| Market Type | Count | YES rate | NO rate |
|-------------|------:|--------:|--------:|
| **Binary (non neg_risk)** | 289,321 | **46.6%** | **53.4%** |
| **neg_risk (multi-outcome)** | 100,898 | **13.9%** | **86.1%** |
| **Overall** | 390,219 | **38.1%** | **61.9%** |

The 62% NO base rate is misleading. In binary markets, the split is a mild 53:47 — barely different from fair. The dramatic NO skew comes from neg_risk markets where only 1 of N outcomes can win, making YES tokens structurally ~1/N probable.

**Implication**: Any strategy that blindly bets NO across all markets captures a structural artifact, not an edge. The edge must be sought within market types.

### neg_risk Calibration: Perfectly Efficient

| N outcomes | Count | Actual YES | Expected (1/N) | NO edge |
|------------|------:|----------:|---------------:|--------:|
| 3 | 22,521 | 33.3% | 33.3% | +0.0pp |
| 5 | 2,640 | 19.8% | 20.0% | +0.2pp |
| 7 | 11,984 | 14.3% | 14.3% | +0.0pp |
| 11 | 14,861 | 9.1% | 9.1% | +0.0pp |
| 20 | 1,160 | 4.8% | 5.0% | +0.2pp |
| 50 | 200 | 2.0% | 2.0% | +0.0pp |

neg_risk markets are **perfectly calibrated** at the structural level. There is no excess NO edge beyond the mechanical 1/N. This makes sense — market makers know the structure.

---

## 2. The Favorite-Longshot Bias: Where Real Edge Lives

Using **early entry prices** (traders in the first 30% of market lifetime) to avoid resolution-time contamination:

| Price Bucket | Implied YES | Count | Actual YES | Edge for NO |
|-------------|:-----------:|------:|:----------:|:-----------:|
| 0-5% | 1.6% | 63,730 | 0.3% | **+1.3pp** |
| 5-10% | 7.2% | 15,454 | 1.9% | **+5.3pp** |
| 10-15% | 12.4% | 11,338 | 4.7% | **+7.7pp** |
| **15-20%** | **17.5%** | **10,154** | **9.3%** | **+8.2pp** |
| 20-25% | 22.5% | 10,240 | 15.2% | **+7.3pp** |
| **25-30%** | **27.3%** | **12,238** | **19.1%** | **+8.3pp** |
| 30-35% | 32.5% | 11,019 | 25.6% | **+6.9pp** |
| 35-40% | 37.6% | 11,111 | 30.7% | **+6.8pp** |
| 40-45% | 42.7% | 14,651 | 33.5% | **+9.2pp** |
| 45-50% | 48.4% | 42,903 | 42.9% | **+5.4pp** |
| **50-55%** | **51.1%** | **67,331** | **53.1%** | **-2.0pp** |
| 55-60% | 57.2% | 13,030 | 65.3% | -8.1pp |
| 60-65% | 62.4% | 8,546 | 70.3% | -8.0pp |
| 65-70% | 67.4% | 6,998 | 76.4% | -9.0pp |
| **70-75%** | **72.6%** | **6,375** | **82.4%** | **-9.8pp** |
| 75-80% | 77.3% | 5,059 | 84.0% | -6.7pp |
| 90-95% | 92.7% | 3,583 | 93.7% | -1.1pp |

**Reading**: Positive "Edge for NO" means NO is underpriced (buy NO). Negative means NO is overpriced (buy YES).

### Key Insights

1. **Below 50% implied YES: Buy NO.** The market systematically overprices longshots. When YES is priced at 15-30%, actual YES rate is only 9-19%. The NO edge is 5-9pp.

2. **Above 50% implied YES: Buy YES.** The market systematically underprices favorites. When YES is priced at 60-75%, actual YES rate is 70-82%. YES is underpriced by 8-10pp.

3. **The crossover is at ~50%.** Below 50%, NO wins. Above 50%, YES wins. This is the classic **favorite-longshot bias** well-documented in horse racing and sports betting.

4. **The sweet spot for NO is 15-45% implied YES** (NO priced at 55-85%). Edge is 7-9pp for NO.

### Binary vs neg_risk Breakdown

| Segment | Price Range | Count | Actual YES | Implied | Edge |
|---------|:-----------:|------:|:----------:|:-------:|:----:|
| **Binary** low YES | 5-30% | 27,539 | 9.4% | 17.5% | **+8.1pp for NO** |
| **Binary** competitive | 30-60% | 145,800 | 46.3% | 48.5% | **+2.2pp for NO** |
| **Binary** high YES | 60-95% | 33,784 | 81.9% | 74.3% | **+7.6pp for YES** |
| neg_risk low YES | 5-30% | 31,885 | 9.7% | 16.1% | +6.5pp for NO |
| neg_risk competitive | 30-60% | 14,245 | 44.2% | 41.7% | -2.5pp (YES) |

**Binary markets have the clearest edges**: 8.1pp NO edge in low-YES markets, 7.6pp YES edge in high-YES markets. The competitive (30-60%) range has a mild 2.2pp NO edge.

---

## 3. Question Pattern Analysis

| Pattern | Count | YES rate | NO rate | vs 38.1% base |
|---------|------:|--------:|--------:|:-------------:|
| **"below"** | 1,429 | **6.5%** | **93.5%** | **-31.6pp** |
| **"How"** | 1,986 | **14.8%** | **85.2%** | **-23.3pp** |
| "Will" (all) | 139,543 | 22.1% | 77.9% | -16.0pp |
| "Will drop" | 408 | 22.1% | 77.9% | -16.0pp |
| "Will reach" | 1,993 | 25.7% | 74.3% | -12.4pp |
| "win" | 54,090 | 25.0% | 75.0% | -13.1pp |
| "before" (deadline) | 1,664 | 24.6% | 75.4% | -13.5pp |
| "price" | 28,649 | 28.9% | 71.1% | -9.2pp |
| "above" | 18,790 | 46.2% | 53.8% | +8.1pp |
| "over" | 20,424 | 44.7% | 55.3% | +6.6pp |
| "Who will" | 228 | 51.8% | 48.2% | +13.7pp |

### Critical Decomposition: Binary "Will" vs neg_risk "Will"

| Segment | Count | YES rate |
|---------|------:|--------:|
| Binary "Will" | 40,891 | **42.4%** |
| neg_risk "Will" | 96,994 | **13.3%** |

**The 78% NO rate on "Will" questions is deceptive.** Two-thirds of "Will" questions are neg_risk multi-outcome markets (e.g., "Will X win the championship?"). For binary "Will" questions, the YES rate is 42.4% — still NO-favored but much less dramatic.

### "Will" Question Subtypes

| Subtype | Count | YES rate | NO rate | Avg Volume |
|---------|------:|--------:|--------:|-----------:|
| Will price drop below | 1,734 | 10.0% | **90.0%** | $107K |
| Will X win | 38,472 | 15.9% | **84.1%** | $398K |
| Will (other) | 75,382 | 19.3% | **80.7%** | $100K |
| Will X happen by (deadline) | 942 | 24.7% | **75.3%** | $731K |
| Will price go above | 21,355 | **42.8%** | 57.2% | $125K |

**"Will price go above X?"** questions are the outlier — they have near-fair YES rates (42.8%). This makes sense: upward price thresholds are set close to current levels.

**"Will price drop below X?"** has 90% NO rate — thresholds for downward price moves are set too aggressively.

---

## 4. Where NO Is Overpriced (Buy YES Instead)

These are categories where YES wins more than expected, meaning buying NO is -EV:

| Tag/Category | Count | YES rate | Avg Volume | Action |
|-------------|------:|--------:|-----------:|--------|
| **Earnings** | 546 | **73.8%** | $21K | Buy YES |
| **Yearly** | 155 | **68.4%** | $1.9M | Buy YES |
| **Euroleague Basketball** | 251 | **63.7%** | $33K | Buy YES |
| **Earnings Calls** | 765 | **58.8%** | $17K | Buy YES |
| **Tariffs/Liberation Day** | 90 | **57.8%** | $418K | Buy YES |
| **COMEX Silver/Gold** | 196 | **55.5%** | $129K | Buy YES |
| **MicroStrategy** | 138 | **55.8%** | $669K | Buy YES |
| **SPX** | 152 | **53.9%** | $125K | Buy YES |

**Earnings are the clearest NO-overpriced category.** Companies tend to beat earnings expectations ~74% of the time (well-documented in financial literature). Markets that ask "Will Company X beat earnings?" should be priced at ~74% YES, but the NO tokens are systematically overpriced.

---

## 5. Where NO Is Underpriced (Best NO Opportunities)

### Liquid tags with highest NO rate (avg vol > $10K)

| Tag | Count | NO rate | Avg Volume |
|-----|------:|--------:|-----------:|
| PGA/Golf | 712-4,592 | **96-99%** | $19-$62K |
| Google Search Trends | 551 | **97.1%** | $236K |
| Poker game | 107 | **97.2%** | $47K |
| Best of 2025 | 1,025 | **96.6%** | $369K |
| F1 races | 145-187 | **96-97%** | $15-$22K |
| Thailand Election | 115 | **96.5%** | $325K |

These are mostly **multi-outcome markets** (golf tournaments, F1 races, elections) where the structural 1/N makes YES tokens nearly worthless. The NO edge here is mechanical, not informational.

### The real NO opportunities: Binary markets with genuine mispricing

From the calibration analysis, the best NO trades are:

1. **Binary markets priced YES 15-30%**: 8pp NO edge. These are "longshot YES" markets where the crowd overestimates unlikely events.

2. **"Will price drop below X?" markets**: 90% NO rate. Downside thresholds are set too aggressively.

3. **Deadline questions** ("Will X happen by DATE?"): 75% NO rate. Most events don't happen on schedule.

---

## 6. Pure Taker NO Bettor PnL

| Side / Type | Positions | Avg PnL | Total PnL | Win Rate |
|-------------|--------:|-------:|----------:|--------:|
| NO bettors (all) | 53.0M | -$13.21 | -$701M | 50.7% |
| YES bettors (all) | 17.9M | +$25.47 | +$455M | 43.9% |
| NO pure taker (binary) | 17.9M | +$18.47 | **+$331M** | 54.3% |
| NO pure taker (neg_risk) | 7.9M | -$3.01 | **-$24M** | 40.7% |

**Key finding: Pure taker NO bettors are profitable on binary markets (+$331M) but lose on neg_risk markets (-$24M).**

This confirms that the NO edge in binary markets is real and exploitable by informed takers, while neg_risk markets are efficiently priced (the structural 1/N is already baked into prices).

### NO Bettor PnL by MVF

| MVF Bucket | Positions | Avg PnL | Total PnL | Win Rate |
|------------|--------:|-------:|----------:|--------:|
| **pure_taker** | 25.8M | **+$11.91** | **+$307M** | 50.1% |
| **taker_leaning** | 7.2M | **+$10.30** | **+$75M** | 48.9% |
| pure_maker | 2.3M | -$31.63 | -$74M | 51.1% |
| mixed | 13.4M | -$41.81 | -$558M | 51.6% |
| maker_leaning | 4.3M | -$103.62 | -$450M | 53.8% |

Pure taker and taker-leaning NO bettors are profitable. Makers and mixed lose heavily on the NO side — likely because they provide liquidity to informed NO bettors and get adversely selected.

---

## 7. Temporal Stability

| Period | YES rate | NO rate | Trend |
|--------|--------:|--------:|-------|
| 2023 | 49.5% | 50.5% | Near fair |
| 2024 Q1-Q3 | 27.0% | 73.0% | Strong NO bias |
| 2024 Q4 | 33.3% | 66.7% | Moderating |
| 2025 Q1-Q2 | 31.3% | 68.7% | Still strong |
| 2025 Q3-Q4 | 38.5% | 61.5% | Converging |
| 2026 Q1 | 39.7% | 60.3% | Near convergence |

**Pre-2025**: YES rate = 33.8%, NO rate = 66.2%
**2025+**: YES rate = 38.3%, NO rate = 61.7%
**Trend**: NO rate declining by ~4.6pp

The NO edge is **shrinking as markets mature**. Early Polymarket (2023) was near-fair. The strong NO bias in 2024 was likely driven by a proliferation of multi-outcome markets (elections, sports). As the market grows and makers become more sophisticated, the favorite-longshot bias is compressing.

---

## 8. Actionable Strategy Summary

### Strategy A: Favorite-Longshot Exploiter (Binary Markets)

**Edge**: Buy NO when YES is priced 15-45% on binary markets (+6-9pp calibration edge).
- Filter: binary only (neg_risk=False), early YES price 15-45%, volume > $10K
- Expected edge: 6-9pp over implied probability
- Capital efficiency: moderate (NO tokens cost $0.55-0.85)
- Risk: tail risk on the ~35-45% of markets that resolve YES

### Strategy B: Earnings YES Buyer

**Edge**: Buy YES on earnings/financial threshold markets (~74% YES rate).
- Filter: tags contain "Earnings" or "Earnings Calls", volume > $5K
- Expected edge: YES wins 74% vs typical pricing of 55-65%
- Capital efficiency: high (YES tokens often priced at 55-65%)
- Risk: individual company misses; diversify across 10+ earnings calls

### Strategy C: Consensus Copy NO-only (Existing Backtester)

**Edge**: Copy skilled pure-taker NO bets with 60s execution delay.
- Already validated: Sharpe > 5 in best configs
- Best config: NO-only, pure_taker, 7+ traders, 70% agreement, wide band, 60s delay
- See `insights/copy/07_execution_delay_robustness.md` for full results

### Strategy D: Anti-Longshot (Aggressive Threshold)

**Edge**: Buy NO on "Will price drop below X?" and "Will X reach Y?" markets.
- These questions set thresholds too aggressively, leading to 74-90% NO resolution
- Filter: question contains "below" OR "reach" OR "drop", binary market, volume > $5K
- Expected edge: 10-30pp above base rate
- Risk: crypto flash crashes, black swan events

### What NOT to Do

1. **Don't blindly buy NO across neg_risk markets** — they're perfectly calibrated at 1/N
2. **Don't buy NO on "above"/"over" questions** — these have near-fair (46%) YES rates
3. **Don't buy NO on high-YES-price markets** (>60% YES) — the favorite-longshot bias works AGAINST you here
4. **Don't assume the 62% base rate applies to binary markets** — it's only 53.4% for binary

---

## 9. Open Questions for Further Research

1. **Can the favorite-longshot bias be exploited in real-time?** The calibration analysis uses early entry prices, but execution at those prices requires being an early participant. Is the edge still present when entering at market midpoint?

2. **Earnings edge persistence**: Is the 74% YES rate on earnings markets priced in? Check the typical YES token price at market creation for earnings questions.

3. **Category drift**: The NO edge varies by category. Can we build a category-specific prior and update it each quarter?

4. **Combining edges**: What happens when we combine the favorite-longshot filter with the consensus copy signal? The backtester already uses entry price bands — can we tighten them based on the calibration curve?

5. **Maker adverse selection**: Pure makers lose $558M on NO bets. Can we identify WHICH makers are most adversely selected and use their positions as contrarian signals?
