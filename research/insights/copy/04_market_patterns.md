# 04 - Market-Level Patterns for Profitable Trading

> Analysis of market characteristics that predict profitability for skilled traders.
> Data: 70.9M trader-market positions across 350,674 resolved markets from 2,083,872 traders.

**Methodology**: Skilled traders = top 5% by total PnL across resolved markets (minimum 5 resolved markets traded).
- Skilled (top 5%): 69,875 traders (total PnL >= $2,912)
- Unskilled (bottom 5%): 69,875 traders (total PnL <= $-2,065)
- Median trader total PnL: $-0.39

**Key insight across all sections**: Skilled traders often have *lower* win rates than unskilled traders, but dramatically higher average PnL per position. They win bigger and lose smaller. The PnL edge (avg PnL gap) is the true measure of skill, not win rate.

---

## Key Findings Summary

1. **Category edge**: Largest PnL edges in **Celebrities** ($6,399/position), **Boxing** ($2,940), and **world affairs** ($2,149). Low-frequency, high-information categories reward skilled analysis most.

2. **Market timing**: Skilled traders enter **earlier** -- median entry at 85.7% of market lifetime vs 90.8% for unskilled. The 5pp gap represents meaningful alpha from earlier information processing.

3. **Market size**: Skilled-trader PnL edge scales with market volume. Biggest absolute edge in **mega (>$1M)** ($2,270/position). Only markets >$100K show skilled traders with higher win rates than unskilled.

4. **Market difficulty**: In "hard" markets (where <40% of traders profit), skilled traders earn $1,492/position more than unskilled. The PnL edge is largest where the crowd gets it wrong.

5. **Resolution speed**: Biggest PnL edge in **3-12 months** markets ($2,670/position). Skilled traders average 63 days to resolution vs 67 for unskilled -- slightly shorter hold periods.

6. **Market concentration**: Both skilled and unskilled have highly skewed participation (Gini ~0.82-0.86). But skilled traders have higher *average* market counts (167 vs 235) despite similar medians (~29).

7. **Neg risk markets**: Multi-outcome neg_risk markets amplify both gains and losses. PnL edge is $1,006/position in neg_risk vs $253 in standard binary markets. Unskilled traders lose almost 6x more per position in neg_risk markets.

---

## 1. Market Concentration

**How many markets do top traders participate in?**

| Tier | Count | Avg Markets | Median | P25 | P75 | Min | Max |
|------|-------|-------------|--------|-----|-----|-----|-----|
| middle | 1,257,748 | 32.9 | 15 | 8 | 29 | 5 | 77,332 |
| skilled | 69,875 | 166.9 | 29 | 12 | 80 | 5 | 93,616 |
| unskilled | 69,875 | 235.2 | 28 | 11 | 74 | 5 | 90,517 |

**Gini coefficient of market participation** (0 = perfectly equal, 1 = extreme concentration):
- Skilled: 0.8193
- Middle: 0.6186
- Unskilled: 0.8591

All tiers show highly skewed participation -- a few traders are in thousands of markets while most stay in under 30. The skilled and unskilled Gini values are similar (0.82 vs 0.86), meaning both groups have power-law participation patterns. The middle tier (90% of traders) is less skewed (0.62) because it excludes the extreme tails.

**Volume concentration (HHI)** -- higher = more volume concentrated in fewer markets:

| Tier | Avg HHI | Median HHI |
|------|---------|------------|
| middle | 0.3087 | 0.2136 |
| skilled | 0.3417 | 0.2562 |
| unskilled | 0.3457 | 0.2554 |

HHI is similar across tiers (~0.25 median), indicating no significant difference in volume concentration strategy. Skilled traders do not systematically concentrate more or less than unskilled ones.

---

## 2. Category Edge

**Win rate and average PnL by category** (sorted by PnL edge = skilled avg PnL - unskilled avg PnL):

Note: Skilled traders often have *lower* win rates because they take contrarian positions in larger size. The PnL edge captures both win rate and bet sizing.

| Category | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N |
|----------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|
| Celebrities | 52.2% | 52.3% | -0.1pp | $3,198.62 | $-3,200.26 | $6,399 | 10,268 |
| Boxing | 37.8% | 42.6% | -4.8pp | $1,381.03 | $-1,559.01 | $2,940 | 3,864 |
| world affairs | 57.3% | 58.6% | -1.4pp | $779.68 | $-1,369.65 | $2,149 | 4,178 |
| Politics | 51.6% | 51.8% | -0.2pp | $737.95 | $-1,218.23 | $1,956 | 1,462,578 |
| Bitcoin | 59.3% | 52.1% | +7.2pp | $436.80 | $-1,192.95 | $1,630 | 2,208 |
| patriots | 47.8% | 53.8% | -6.0pp | $602.26 | $-936.15 | $1,538 | 1,746 |
| Iran | 53.1% | 51.5% | +1.5pp | $470.96 | $-961.41 | $1,432 | 12,302 |
| Geopolitics | 51.8% | 52.0% | -0.2pp | $555.40 | $-693.42 | $1,249 | 3,345 |
| Soccer | 53.4% | 57.2% | -3.7pp | $248.01 | $-891.34 | $1,139 | 3,530 |
| General | 48.3% | 50.0% | -1.7pp | $406.58 | $-641.28 | $1,048 | 7,646 |
| USA Election | 51.1% | 54.0% | -2.9pp | $712.85 | $-325.93 | $1,039 | 3,023 |
| blockchain | 61.2% | 56.6% | +4.6pp | $517.96 | $-390.64 | $909 | 3,178 |
| Movies | 48.5% | 56.1% | -7.6pp | $407.65 | $-381.66 | $789 | 51,133 |
| TikTok | 54.2% | 55.1% | -1.0pp | $308.94 | $-476.88 | $786 | 2,308 |
| box office | 46.5% | 44.4% | +2.1pp | $232.42 | $-520.38 | $753 | 31,871 |
| eu | 47.4% | 40.8% | +6.6pp | $77.69 | $-653.26 | $731 | 3,696 |
| Esports | 49.7% | 56.7% | -6.9pp | $253.03 | $-472.93 | $726 | 68,329 |
| Elections | 50.3% | 54.6% | -4.3pp | $193.78 | $-478.10 | $672 | 16,004 |
| Fed Rates | 52.8% | 56.5% | -3.7pp | $287.37 | $-365.81 | $653 | 2,313 |
| russia | 55.6% | 57.5% | -1.9pp | $212.29 | $-427.08 | $639 | 3,595 |
| Science | 50.9% | 54.0% | -3.1pp | $221.52 | $-393.99 | $616 | 32,363 |
| MrBeast | 50.5% | 51.6% | -1.1pp | $218.34 | $-360.36 | $579 | 23,122 |
| Gaza | 56.0% | 57.8% | -1.8pp | $199.30 | $-368.62 | $568 | 6,455 |
| South Korea | 53.1% | 51.1% | +2.0pp | $126.45 | $-404.70 | $531 | 1,802 |
| Elon Musk | 52.7% | 54.2% | -1.5pp | $185.54 | $-329.26 | $515 | 47,907 |

**Top sub-categories by PnL edge** (min 500 positions per tier):

| Sub-category | Skilled WR | Unskilled WR | Skilled Avg PnL | PnL Edge | Skilled N |
|--------------|-----------|-------------|-----------------|----------|-----------|
| Politics > Joe Biden | 56.1% | 26.1% | $7,467.74 | $16,928 | 56,278 |
| Politics > Fed Rates | 47.6% | 59.5% | $2,418.78 | $7,233 | 5,668 |
| Politics > Finance | 53.6% | 59.6% | $2,354.51 | $6,114 | 18,923 |
| Crypto > Movies | 51.1% | 50.7% | $2,191.98 | $5,688 | 3,223 |
| Politics > Prediction Markets | 59.1% | 54.5% | $2,177.97 | $5,039 | 2,849 |
| Politics > republicans | 41.6% | 36.5% | $1,281.91 | $4,928 | 5,957 |
| Politics > Fed | 54.7% | 58.4% | $1,841.99 | $4,666 | 26,285 |
| Politics > january 6 | 66.5% | 51.6% | $2,025.60 | $3,779 | 10,490 |
| Politics > Business | 49.1% | 48.2% | $967.60 | $3,748 | 21,064 |
| Politics > DOGE | 44.2% | 46.8% | $1,338.95 | $3,647 | 712 |
| Politics > South Korea | 56.8% | 53.9% | $911.82 | $3,420 | 18,542 |
| world affairs > Geopolitics | 63.4% | 63.2% | $1,409.03 | $3,381 | 2,256 |
| Politics > Crypto | 54.0% | 56.8% | $992.54 | $3,269 | 17,518 |
| Politics > nyc | 52.3% | 51.4% | $1,077.41 | $3,158 | 11,543 |
| Science > Global Temp | 54.1% | 52.6% | $1,366.48 | $3,118 | 1,838 |

---

## 3. Market Timing

**Do profitable traders enter markets earlier or later?**

Entry percentile: 0.0 = enters at the very first trade, 1.0 = enters at the very last trade.

| Tier | Avg Entry | P25 | Median | P75 | N Positions |
|------|-----------|-----|--------|-----|-------------|
| skilled | 0.728 | 0.586 | 0.857 | 0.954 | 11,664,436 |
| unskilled | 0.792 | 0.727 | 0.908 | 0.972 | 16,431,887 |

Skilled traders enter markets meaningfully earlier across the distribution. The median skilled trader enters at 85.7% of market lifetime vs 90.8% for unskilled. At P25, the gap widens: 58.6% vs 72.7%. This means skilled traders are more likely to be among the early participants in a market.

**Entry timing by PnL quintile** (Q1 = worst performers, Q5 = best):

| Quintile | Avg Entry | Median Entry | N Positions |
|----------|-----------|-------------|-------------|
| Q1 | 0.787 | 0.899 | 28,034,344 |
| Q2 | 0.719 | 0.814 | 7,553,434 |
| Q3 | 0.700 | 0.772 | 4,289,046 |
| Q4 | 0.710 | 0.801 | 7,428,382 |
| Q5 | 0.732 | 0.852 | 22,223,274 |

Interestingly, the relationship is U-shaped: Q1 (worst) and Q5 (best) both enter later than Q2-Q4. This reflects two distinct populations in the tails: Q1 are late FOMO traders chasing prices, while Q5 are high-volume informed traders who time entries strategically. The Q3 middle quintile enters earliest (median 0.772), suggesting casual early participants who break even.

---

## 4. Market Size Effect

**In which volume tier do skilled traders have the biggest edge?**

| Volume Tier | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N |
|-------------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|
| micro (<$100) | 14.7% | 78.3% | -63.6pp | $-0.06 | $-1.97 | $2 | 310,062 |
| small ($100-1K) | 29.4% | 64.7% | -35.2pp | $2.06 | $-14.45 | $17 | 345,180 |
| medium ($1K-10K) | 41.1% | 52.5% | -11.4pp | $10.47 | $-24.86 | $35 | 1,282,618 |
| large ($10K-100K) | 46.8% | 48.4% | -1.6pp | $45.94 | $-44.79 | $91 | 3,911,974 |
| xlarge ($100K-1M) | 49.8% | 46.2% | +3.6pp | $136.10 | $-118.90 | $255 | 4,063,935 |
| mega (>$1M) | 52.9% | 51.0% | +1.9pp | $907.03 | $-1,363.37 | $2,270 | 1,750,667 |

**Market count by volume tier:**

| Tier | N Markets | Avg Volume |
|------|-----------|------------|
| 1_micro | 78,939 | $16 |
| 2_small | 45,984 | $416 |
| 3_medium | 79,752 | $4,433 |
| 4_large | 101,916 | $36,567 |
| 5_xlarge | 39,011 | $280,372 |
| 6_mega | 5,072 | $5,258,489 |

Key patterns:
- **Micro/small markets**: Unskilled traders have much higher win rates (78%/65% vs 15%/29%). These tiny markets likely have trivial outcomes where the "obvious" side wins but pays almost nothing. Skilled traders rarely bother.
- **Large markets ($10K-100K)**: Near parity in win rates, but skilled traders average +$46 vs -$45 per position -- a $91 edge from sizing and timing.
- **Mega markets (>$1M)**: Skilled traders earn $907/position vs -$1,363 for unskilled -- a $2,270 PnL edge. This is where skill pays off most in absolute dollars.
- The crossover happens at ~$100K volume: below this, unskilled win more often; above it, skilled traders dominate.

---

## 5. Consensus Markets

**Market difficulty = fraction of traders who end up profitable.**

"Hard" markets are those where few traders profit; "easy" markets are those where most do.

| Difficulty Bucket | N Markets | Avg Correct% | Avg Traders | Avg Volume |
|-------------------|-----------|-------------|-------------|------------|
| 01_<10% | 1,636 | 6.5% | 1036 | $175,081 |
| 02_10-20% | 7,428 | 15.9% | 318 | $161,849 |
| 03_20-30% | 21,748 | 25.3% | 139 | $75,232 |
| 04_30-40% | 45,257 | 35.2% | 232 | $92,796 |
| 05_40-50% | 75,163 | 45.0% | 249 | $156,192 |
| 06_50-60% | 86,789 | 54.3% | 229 | $167,940 |
| 07_60-70% | 35,458 | 63.8% | 236 | $163,455 |
| 08_70-80% | 10,307 | 74.0% | 297 | $159,550 |
| 09_80-90% | 4,031 | 84.0% | 507 | $123,938 |
| 10_90-100% | 1,034 | 93.4% | 971 | $97,800 |

**Skilled vs unskilled by market difficulty:**

| Difficulty | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge |
|-----------|-----------|-------------|---------|-----------------|-------------------|----------|
| very_hard (<20%) | 21.5% | 32.3% | -10.8pp | $439.77 | $-1,052.08 | $1,492 |
| hard (20-40%) | 36.9% | 35.2% | +1.6pp | $125.02 | $-120.11 | $245 |
| medium (40-60%) | 47.7% | 50.4% | -2.7pp | $209.76 | $-195.89 | $406 |
| easy (60-80%) | 54.8% | 63.7% | -8.9pp | $228.21 | $-210.71 | $439 |
| very_easy (80-100%) | 66.6% | 78.4% | -11.7pp | $192.25 | $-162.64 | $355 |

Key insights:
- **Very hard markets (<20% profitable)**: Only 1,636 markets but avg 1,036 traders each (high liquidity). These are the most contested markets. Skilled traders average $440/position even here, while unskilled lose $1,052.
- **The PnL edge is largest in hard markets**: Where the crowd gets it wrong, skilled analysis adds the most value.
- Win rates tell a counterintuitive story: unskilled traders have *higher* win rates in easy markets because they follow consensus on obvious outcomes. But their losses in hard markets wipe out those gains.
- The "very hard" bucket (<20% correct) contains large, contested markets where strong opinions collide -- exactly where information edges matter.

---

## 6. Time-to-Resolution

**Skilled vs unskilled by market duration:**

| Duration | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N | Unskilled N |
|----------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|-------------|
| <1 day | 43.2% | 45.9% | -2.6pp | $51.21 | $-36.37 | $88 | 4,562,140 | 10,558,258 |
| 1-7 days | 47.4% | 54.0% | -6.5pp | $153.81 | $-186.31 | $340 | 3,966,301 | 3,117,622 |
| 1-4 weeks | 50.1% | 55.9% | -5.8pp | $227.41 | $-350.45 | $578 | 1,700,958 | 1,313,241 |
| 1-3 months | 55.1% | 58.5% | -3.4pp | $457.11 | $-571.50 | $1,029 | 645,778 | 636,591 |
| 3-12 months | 49.7% | 48.6% | +1.1pp | $1,049.86 | $-1,620.35 | $2,670 | 752,435 | 765,029 |
| >1 year | 55.9% | 56.6% | -0.7pp | $534.93 | $-847.48 | $1,382 | 35,503 | 36,727 |

**Average market duration preference:**

| Tier | Avg Days to Resolution | Avg Median Days | N Traders |
|------|----------------------|-----------------|-----------|
| unskilled | 66.7 | 48.7 | 69,875 |
| skilled | 62.8 | 45.0 | 69,875 |

Key insights:
- **Short-duration (<1 day)**: Most positions land here (4.6M skilled, 10.6M unskilled). These are the crypto/sports recurring markets. PnL edge is modest ($88/position) because outcomes are more random.
- **3-12 month markets**: Largest PnL edge per position. These are the political/macro markets where fundamental analysis and patience pay off.
- Unskilled traders are disproportionately concentrated in sub-day markets (10.6M vs 4.6M) -- they prefer the dopamine of fast resolution.
- Skilled traders slightly prefer shorter hold periods (avg 63 vs 67 days), but this likely reflects broader participation rather than a duration preference.

---

## 7. Neg Risk Markets

**neg_risk markets** are multi-outcome markets (e.g., "Who will win the Super Bowl?") using negative-risk accounting. Standard markets are binary YES/NO.

**Market-level statistics:**

| neg_risk | N Markets | Avg Traders | Avg Volume | Median Volume |
|----------|-----------|-------------|------------|---------------|
| True | 81,084 | 250 | $217,111 | $5,428 |
| False | 269,590 | 188 | $89,416 | $5,083 |

**Skilled vs unskilled by neg_risk:**

| neg_risk | Tier | N Positions | Win Rate | Avg PnL | Total PnL | Avg Position Size |
|----------|------|-------------|----------|---------|-----------|-------------------|
| False | skilled | 8,853,733 | 46.7% | $145.85 | $1,291,296,332 | $1,031.28 |
| False | unskilled | 13,963,907 | 47.9% | $-106.95 | $-1,493,382,918 | $858.17 |
| True | skilled | 2,810,773 | 47.0% | $371.26 | $1,043,528,136 | $1,903.05 |
| True | unskilled | 2,468,098 | 54.1% | $-634.70 | $-1,566,504,703 | $3,171.63 |

**Neg risk allocation preference** (fraction of each trader's volume in neg_risk markets):

| Tier | Avg Fraction | Median Fraction |
|------|-------------|-----------------|
| unskilled | 44.0% | 29.5% |
| skilled | 44.0% | 32.5% |

Key insights:
- **Neg risk amplifies everything**: Unskilled traders lose $635/position in neg_risk markets vs $107 in standard -- a 6x difference. Skilled traders earn $371 vs $146.
- **Position sizes diverge**: Unskilled traders bet $3,172 avg in neg_risk vs $858 in standard. The larger sizing combined with lower skill creates outsized losses.
- **Both groups allocate ~44% to neg_risk** by volume, so the difference is not in allocation but in execution.
- Neg risk markets attract higher conviction bets (bigger positions) because traders feel they have edge in multi-outcome markets. But the complexity creates more opportunities for mispricing that skilled traders exploit.

---

## Actionable Implications for Strategy Design

1. **Focus on large/mega markets** (>$100K volume) -- this is where skilled analysis generates the highest absolute returns and where the skilled-trader edge is positive on a win-rate basis.

2. **Enter early** -- the earlier a signal identifies a market opportunity relative to the market lifecycle, the more alpha it captures. Target entry in the first 60% of market lifetime.

3. **Target "hard" markets** -- markets where consensus is split or where most traders lose are where analytical edge converts to the largest profits.

4. **Political/macro markets** (3-12 month horizon) offer the best PnL edge per position. Short-duration recurring markets (crypto up/down, sports) have smaller edges.

5. **Neg risk markets require extra caution** -- they amplify both skill and mistakes. Position sizing discipline is critical.

6. **Category specialization pays** -- categories like Biden/Politics, Celebrities, and Crypto have large PnL edges for skilled traders, suggesting domain knowledge matters.
