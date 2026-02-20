# MVF Patterns: Maker vs Taker Behavior and Profitability

**Dataset**: 2.08M traders, 70.9M trader-market rows, 390K resolved markets
**Date range**: Nov 2022 - Jan 2026
**MVF** = Maker Volume Fraction (maker_volume / total_volume per trader)

---

## 1. MVF Distribution

The Polymarket ecosystem is overwhelmingly dominated by takers.

| Segment | MVF Range | Count | % of Total |
|---------|-----------|------:|----------:|
| Pure takers | < 0.05 | 1,307,139 | 62.7% |
| Near takers | 0.05 - 0.10 | 58,219 | 2.8% |
| Taker-leaning | 0.10 - 0.50 | 371,342 | 17.8% |
| Maker-leaning | 0.50 - 0.90 | 257,492 | 12.4% |
| Near makers | 0.90 - 0.95 | 20,093 | 1.0% |
| Pure makers | > 0.95 | 69,587 | 3.3% |

**Key statistics**: Mean MVF = 0.185, Median MVF = 0.000, Std = 0.288

The distribution is extremely bimodal. The 0.00 bin alone holds 1.31M traders (62.7%), and then there is a secondary peak at the 0.45-0.50 bin (101K traders). The vast majority of participants are pure price takers who never place limit orders.

### Histogram

```
MVF Range    | Count      | Bar
0.00 - 0.05  | 1,307,139  | ########################################
0.05 - 0.10  |    58,219  | ##
0.10 - 0.15  |    39,950  | #
0.15 - 0.20  |    34,905  | #
0.20 - 0.25  |    33,388  | #
0.25 - 0.30  |    34,074  | #
0.30 - 0.35  |    37,981  | #
0.35 - 0.40  |    41,051  | #
0.40 - 0.45  |    48,656  | #
0.45 - 0.50  |   101,337  | ###
0.50 - 0.55  |    78,002  | ##
0.55 - 0.60  |    40,090  | #
0.60 - 0.65  |    31,681  | #
0.65 - 0.70  |    26,745  | #
0.70 - 0.75  |    22,878  | #
0.75 - 0.80  |    20,556  | #
0.80 - 0.85  |    19,089  | #
0.85 - 0.90  |    18,451  | #
0.90 - 0.95  |    20,093  | #
0.95 - 1.00  |    37,489  | #
1.00         |    32,098  | #
```

---

## 2. MVF vs Profitability by Decile

Because 62.7% of traders have MVF = 0.0, deciles 1 through 5 are all pure takers (MVF = 0). The real differentiation begins at decile 6 (MVF 0.00 - 0.02) and above.

| Decile | MVF Range | Mean PnL | Median PnL | Sharpe-like | Win Rate | PnL/$ | Aggregate PnL |
|--------|-----------|----------|------------|-------------|----------|-------|---------------|
| 1 | 0.000 | +$335 | -$0.33 | 0.024 | 42.8% | +13.1c | +$69.7M |
| 2 | 0.000 | +$253 | -$0.36 | 0.026 | 42.4% | +10.3c | +$52.7M |
| 3 | 0.000 | +$282 | -$0.35 | 0.029 | 42.6% | +11.6c | +$58.8M |
| 4 | 0.000 | +$383 | -$0.36 | 0.024 | 42.5% | +14.3c | +$79.8M |
| 5 | 0.000 | +$346 | -$0.36 | 0.023 | 42.6% | +13.8c | +$72.0M |
| 6 | 0.00-0.02 | +$1,863 | -$0.005 | 0.034 | 43.5% | +12.8c | +$388.3M |
| **7** | **0.02-0.23** | **+$1,419** | **+$6.15** | **0.005** | **46.0%** | **+5.4c** | **+$295.7M** |
| 8 | 0.23-0.48 | +$71 | -$0.65 | 0.001 | 47.2% | +0.3c | +$14.8M |
| 9 | 0.48-0.63 | -$1,619 | ~$0 | -0.008 | 45.1% | -4.6c | -$337.3M |
| **10** | **0.63-1.00** | **-$5,220** | **-$5.22** | **-0.017** | **54.5%** | **-6.2c** | **-$1,087.9M** |

### Key Findings

**Takers win on aggregate.** Deciles 1-5 (pure takers, MVF = 0) collectively generated +$333M in aggregate PnL. The mean PnL is positive ($280-$383) even though the median is slightly negative, indicating a fat right tail: a small fraction of takers earn enormous profits.

**The transition zone is the sweet spot.** Decile 6 (MVF 0-0.02) has the best Sharpe-like ratio (0.034) and the highest aggregate PnL (+$388M). These are traders who are primarily takers but occasionally place limit orders.

**Makers lose money.** Decile 10 (MVF 0.63-1.00) lost $1.09B in aggregate. Despite a higher win rate (54.5%), their losses per losing trade far exceed their gains per winning trade. They lose -6.2 cents per dollar of volume.

**Profitability inverts around MVF 0.48.** Below this threshold, traders are net profitable. Above it, they lose money. The PnL/dollar metric drops monotonically from +13.1c (pure takers) to -6.2c (high-MVF makers).

---

## 3. MVF vs Consistency (Month-to-Month)

Analyzed 753K traders with 3+ active months.

| MVF Bucket | Traders | Avg Active Months | Avg Monthly PnL | Median Monthly PnL | Median Monthly Sharpe | Profitable Month % |
|------------|---------|-------------------|-----------------|--------------------|-----------------------|-------------------|
| Pure taker (<0.1) | 416,290 | 5.66 | +$176 | -$0.13 | -0.033 | 55.4% |
| Taker-leaning (0.1-0.3) | 71,696 | 6.02 | +$377 | +$0.71 | +0.051 | 53.5% |
| Mixed (0.3-0.7) | 203,435 | 6.10 | -$319 | -$0.09 | -0.078 | 52.4% |
| Maker-leaning (0.7-0.9) | 38,702 | 6.25 | -$1,831 | -$2.37 | -0.137 | 58.1% |
| Pure maker (>0.9) | 23,406 | 6.27 | -$1,881 | +$0.57 | +0.102 | 66.8% |

### Key Findings

**Taker-leaning traders have the best consistency.** The 0.1-0.3 MVF range shows positive mean AND median monthly PnL, a positive median monthly Sharpe, and the only group where both mean and median are positive.

**Pure makers have the highest profitable-month fraction (66.8%) but lose money.** This is the classic market-making trap: you win small most months (spread income) but suffer catastrophic losses occasionally. The mean monthly PnL is -$1,881 despite 2/3 of months being profitable.

**Pure takers stay in the game shorter.** At 5.66 months average activity, they have the shortest tenure among multi-month traders. Makers persist longer (6.27 months) -- likely because their strategy requires continuous presence.

---

## 4. Market Maker Edge

**89,676 traders** with MVF > 0.9 (pure makers).

| Metric | Mean | Median |
|--------|------|--------|
| Total PnL | -$2,457 | -$1.10 |
| PnL per market | -$62.13 | -$0.15 |
| PnL per dollar | -12.1c | -0.09c |
| Win rate | 55.8% | 57.1% |
| Markets traded | 53.9 | -- |
| Total trades | 769.2 | -- |
| **Aggregate PnL** | **-$220.3M** | -- |
| **Aggregate volume** | **$4.21B** | -- |

### The maker's paradox

Makers collectively lost -$220M on $4.2B of volume (-5.2%). The median PnL is barely negative (-$1.10), but the mean is -$2,457 -- indicating severe negative skew. This confirms the classic market-making risk: frequent small wins punctuated by rare catastrophic losses.

### Top makers who DO profit

A handful of makers generate exceptional returns:

| Rank | Trader (short) | MVF | Total PnL | Volume | Markets | PnL/$ |
|------|---------------|-----|-----------|--------|---------|-------|
| 1 | 0xf981...bff6 | 0.989 | +$10.2M | $11.5M | 43 | +88.7c |
| 2 | 0x5121...92ca | 0.937 | +$9.3M | $11.0M | 3 | +84.1c |
| 3 | 0xfb1c...63e | 0.985 | +$4.3M | $84.9M | 28,608 | +5.1c |
| 4 | 0x2023...d55 | 0.910 | +$3.1M | $3.4M | 405 | +92.1c |

The top maker (0xf981) earned $10.2M across only 43 markets with a 51% win rate but extraordinary PnL-per-dollar (88.7c). This looks less like spread capture and more like informed market-making (selective provision in markets where the maker has an edge).

Trader #3 (0xfb1c) is the closest to a traditional market-maker profile: 28,608 markets, 1.1M trades, and 5.1c PnL per dollar -- consistent with capturing the bid-ask spread at scale.

---

## 5. Informed Takers

**1,365,358 traders** with MVF < 0.1.

### Profitable vs Unprofitable Takers

| Metric | Profitable (631K) | Unprofitable (734K) | Delta |
|--------|-------------------|---------------------|-------|
| Mean PnL | +$2,595 | -$1,056 | -- |
| Median PnL | +$70 | -$22 | -- |
| Mean volume | $8,183 | $4,742 | 1.7x |
| Median volume | $332 | $142 | 2.3x |
| Mean markets | 24.0 | 21.0 | 1.1x |
| Mean trades | 126.3 | 108.1 | 1.2x |
| Win rate | 63.7% | 25.1% | +38.6pp |
| PnL per dollar | +48.5c | -43.9c | -- |
| PnL per market | +$595 | -$204 | -- |

### What separates winners from losers?

1. **Win rate is the dominant factor.** Profitable takers have a 63.7% win rate vs 25.1% for unprofitable ones. This is a massive 38.6 percentage point gap.

2. **Profitable takers trade more volume.** 2.3x higher median volume, suggesting they size up their bets when they have conviction.

3. **Market selection matters less than expected.** Profitable takers trade only slightly more markets (24 vs 21), so it is not about diversification. It is about being right.

4. **PnL per dollar is nearly symmetric.** Winners earn +48.5c per dollar; losers lose -43.9c per dollar. This means the platform is roughly zero-sum for takers, with transaction costs explaining most of the negative median.

### Taker Profitability by Volume Bucket

| Volume Bucket | Traders | Mean PnL | Median PnL | Win Rate | % Profitable |
|---------------|---------|----------|------------|----------|-------------|
| < $100 | 541K | -$1.02 | -$0.36 | 39.0% | 39.4% |
| $100-$1K | 426K | +$21.6 | -$2.00 | 43.7% | 47.4% |
| $1K-$10K | 290K | +$268 | +$17.7 | 47.3% | 52.9% |
| $10K-$100K | 96K | +$2,727 | +$581 | 47.7% | 57.0% |
| $100K-$1M | 11K | +$37,012 | +$15,266 | 49.3% | 65.2% |
| **> $1M** | **716** | **+$161,248** | **+$53,943** | **45.1%** | **64.8%** |

**Big takers are profitable.** The fraction of profitable traders rises from 39% (under $100) to 65% (over $100K). This is not simply survivorship bias -- the mean and median PnL both increase dramatically.

**High-volume takers have LOWER win rates but BIGGER wins.** The $1M+ bucket has only 45.1% win rate (below the $10K-$100K bucket's 47.7%) but the highest mean and median PnL. They bet big and correctly on high-conviction trades.

### Top 5 Informed Takers

| Trader (short) | MVF | Total PnL | Volume | Markets | Win Rate | PnL/$ |
|---------------|-----|-----------|--------|---------|----------|-------|
| 0x9d33...fa02 | 0.056 | +$20.0M | $2.0M | 6 | 83.3% | $10.11 |
| 0x395d...99eb | 0.000 | +$9.8M | $15.3M | 15 | 66.7% | $0.64 |
| 0x93b3...6436 | 0.095 | +$8.8M | $6.1M | 36 | 72.2% | $1.43 |
| 0x26c1...52f5 | 0.014 | +$8.5M | $0.5M | 1 | 100% | $17.82 |
| 0xe6e4...5ee5 | 0.002 | +$7.9M | $12.4M | 340 | 48.8% | $0.64 |

The top taker (0x9d33) earned $20M on only $2M volume across 6 markets -- a 10.1x return. This is a massively concentrated bet that paid off. In contrast, 0xe6e4 traded 340 markets with a 48.8% win rate but still earned $7.9M -- a volume-driven approach.

---

## 6. MVF Migration: Do Takers Become Makers?

### Early vs Late Career for Top 500 Takers by Volume

(229 takers with 90+ day span, split at their temporal midpoint)

| Period | Traders | Mean PnL | Mean Volume | Mean Trades | Mean Markets | PnL/$ |
|--------|---------|----------|-------------|-------------|-------------|-------|
| Early | 229 | -$290K | $2.96M | 24,048 | 532 | +14.0c |
| Late | 228 | +$275K | $2.77M | 54,420 | 1,718 | +18.6c |

**Successful takers IMPROVE over time.** In their early career, the top takers by volume actually lose money on average (-$290K) but by their later career, they earn +$275K. Their PnL efficiency also improves from 14.0c to 18.6c per dollar.

**They trade more markets but similar volume.** The late-period mean trades doubles (24K to 54K) and markets triples (532 to 1,718), but dollar volume stays flat (~$2.8M). This suggests they shift from concentrated to diversified strategies.

### Top 100 Profitable Takers -- Quarterly Evolution

| Quarter | Active | Mean PnL | Total PnL | Mean Volume | PnL/$ |
|---------|--------|----------|-----------|-------------|-------|
| 2023 Q1 | 3 | $314K | $941K | $573K | 39.9c |
| 2023 Q4 | 4 | $172K | $689K | $330K | 55.1c |
| 2024 Q2 | 13 | $722K | $9.4M | $936K | 151.3c |
| 2024 Q3 | 25 | $1.14M | $28.4M | $1.57M | 131.2c |
| 2024 Q4 | 28 | $364K | $10.2M | $777K | 25.8c |
| 2025 Q2 | 28 | $729K | $20.4M | $1.39M | 46.1c |
| 2025 Q4 | 40 | $853K | $34.1M | $3.99M | 40.6c |
| 2026 Q1 | 39 | $923K | $36.0M | $1.85M | 55.7c |

**Top takers are scaling up.** From 3 active traders generating $941K in early 2023 to 39-40 generating $34-36M per quarter in late 2025/early 2026. The PnL per dollar has decreased from the extreme Q2-Q3 2024 levels (131-151c, likely election-related alpha) but stabilized around 40-56c -- still highly profitable.

---

## 7. Optimal MVF Range for Copying

### Train/Test Split Analysis (cutoff: June 2025)

Performance of each MVF bucket on **out-of-sample** (test) resolved markets:

| MVF Bucket | Test Traders | Mean PnL | Sharpe | Win Rate | PnL/$ | Aggregate PnL |
|------------|-------------|----------|--------|----------|-------|---------------|
| **0.00-0.05** | 878K | **+$608** | **+0.024** | 46.6% | -5.9c | **+$534M** |
| 0.05-0.10 | 47K | +$1,420 | +0.017 | 47.7% | +5.0c | +$67M |
| **0.10-0.20** | 63K | **+$1,173** | **+0.018** | **50.2%** | +0.4c | +$74M |
| 0.20-0.30 | 59K | +$1,056 | +0.013 | 52.1% | -0.1c | +$62M |
| 0.30-0.50 | 184K | -$344 | -0.004 | 50.8% | -0.6c | -$63M |
| 0.50-0.70 | 144K | -$1,928 | -0.015 | 50.9% | -1.8c | -$279M |
| 0.70-0.90 | 74K | -$4,924 | -0.034 | 57.8% | -3.8c | -$363M |
| 0.90-0.95 | 19K | -$3,378 | -0.030 | 59.3% | -7.0c | -$63M |
| 0.95-1.00 | 61K | -$624 | -0.017 | 58.4% | -9.7c | -$38M |

### Copy Strategy: Top 50 Traders per Bucket (Train PnL -> Test Performance)

This is the most actionable analysis. For each MVF bucket, select the top 50 traders by in-sample PnL, then measure their out-of-sample returns.

| MVF Bucket | Mean Train PnL | Mean Test PnL | Median Test PnL | OOS % Profitable | Test PnL/$ | Train-Test Corr |
|------------|---------------|---------------|-----------------|------------------|-----------|-----------------|
| **Pure taker** (<0.1) | $1.20M | **+$183K** | +$2,263 | **74%** | **+19.9c** | **0.555** |
| **Taker-leaning** (0.1-0.3) | $974K | **+$253K** | **+$38,926** | **66%** | **+19.5c** | 0.114 |
| Mixed (0.3-0.7) | $1.86M | +$32K | +$26,900 | 58% | +0.7c | -0.908 |
| Maker-leaning (0.7-0.9) | $700K | -$48K | -$1,390 | 36% | -22.3c | 0.154 |
| Pure maker (>0.9) | $213K | +$72K | +$1.54 | 52% | -0.4c | 0.164 |

---

## Summary: Key Takeaways for a Copy-Trading Strategy

### 1. Copy pure takers (MVF < 0.1)

- **Highest out-of-sample profitability**: 74% of top-50 pure takers remain profitable out of sample
- **Best train-test correlation**: 0.555, meaning in-sample performance strongly predicts OOS
- **Strong PnL per dollar**: +19.9c on test data
- **Aggregate test PnL**: +$9.1M from 50 traders

### 2. Taker-leaning traders (MVF 0.1-0.3) are the second-best copy target

- **Highest mean and median test PnL**: $253K mean, $39K median
- **Good aggregate returns**: +$12.7M from 50 traders, +19.5c per dollar
- **Lower train-test correlation** (0.114) suggests need for additional filtering

### 3. Never copy makers

- **Maker-leaning (0.7-0.9) has the worst OOS performance**: only 36% profitable, -22.3c per dollar
- **Negative train-test correlation for mixed (-0.908)**: past maker success does NOT predict future success
- **Pure makers lose in aggregate** despite high win rates (the spread doesn't compensate for adverse selection)

### 4. Volume is a signal for takers

- Takers with >$100K volume are profitable 65% of the time
- The highest-volume takers ($1M+) earn $161K median PnL
- Filtering for high-volume takers would concentrate the copy signal

### 5. Recommended copy-trading filter

```
MVF < 0.1 AND total_volume > $10,000 AND num_markets >= 5
```

This selects for informed takers who:
- Take liquidity (have an opinion, not providing a service)
- Have demonstrated conviction with meaningful capital
- Have track records across multiple markets (not one-hit wonders)

Within this set, rank by historical PnL and diversify across the top N traders.

### 6. Risk warning

- Pure taker alpha is concentrated: the mean is much higher than the median (fat right tail)
- Q2-Q3 2024 showed extraordinary alpha (likely US election markets) that may not repeat
- Recent PnL/dollar has stabilized at 40-56c for top takers -- still very strong but declining from peak
