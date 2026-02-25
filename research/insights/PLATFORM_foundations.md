# Platform Foundations: Polymarket Ecosystem Data

Reference data underlying all strategy research. Not a strategy itself.

**Source insights**: copy/01 (profitability distribution), copy/03 (MVF patterns), copy/04 (market patterns), overpriceNo/01 (NO edge analysis)

---

## Profitability Distribution (copy/01)

**Dataset**: 2.08M traders, 70.9M trader-market positions, Nov 2022 - Jan 2026.

- **Only 46.5%** of traders are net profitable. Median PnL = -$0.20.
- **Aggregate PnL = -$393M** on $41.7B volume (0.94% fee drag).
- **Top 1%** account for 78.8% of all volume.
- **Mid-tier ($10K-$1M)** most likely profitable (52-54%). $1M+ tier flips negative.
- **Profitable traders need only ~53% win rate** (median).

### Volume Tiers

| Tier | % Profitable | Mean PnL |
|------|:----------:|--------:|
| < $100 | 39.9% | -$1.33 |
| $100-$1K | 47.8% | +$12 |
| $1K-$10K | 50.4% | +$120 |
| $10K-$100K | 52.2% | +$1,224 |
| $100K-$1M | 54.0% | +$10,768 |
| $1M+ | 45.0% | -$212,450 |

### Maker vs Taker (>=$10K volume)

| MVF Tier | % Profitable | Avg PnL |
|----------|:----------:|-------:|
| Heavy Taker (<20%) | **57.9%** | **+$7,433** |
| Taker-leaning (20-50%) | 50.8% | +$35 |
| Maker-leaning (50-80%) | 40.7% | -$17,168 |
| Heavy Maker (80%+) | 49.6% | -$23,507 |

Takers outperform makers. Market making on Polymarket is a losing proposition for most.

---

## Consistency Signal (copy/02)

Consecutive profitable months as a predictor of future performance.

| Lookback | Unique Traders | Forward Win Rate | Signal Half-Life |
|:--------:|:--------------:|:----------------:|:----------------:|
| 3 months | 304,366 (17.6%) | 69% | >12 months |
| 6 months | 39,713 (2.3%) | 83% | >12 months |
| 9 months | 8,409 (0.49%) | 87% | 8-10 months |
| 12 months | 1,008 (0.06%) | 90% | 5-6 months |

Signal is real (survives volume-matching), durable (76% at M+6 for 6-month lookback), and exponentially rare (each 3-month extension eliminates 80-90% of traders).

**Mean-median divergence**: At min_markets=1, median forward PnL near zero. At min_markets>=20, median jumps 67,000x. Critical to filter.

---

## MVF Patterns (copy/03)

**Distribution**: 62.7% pure takers (MVF<0.05), bimodal with secondary peak at 0.45-0.50.

### Key Findings

- **Profitability inverts at MVF 0.48**: Below = net profitable, above = net losing.
- **Transition zone (MVF 0.00-0.02)**: Best Sharpe-like ratio (0.034), highest aggregate PnL (+$388M).
- **Pure makers (MVF >0.95)**: Lost $220M despite 55.8% win rate. Classic negative skew.
- **Taker-leaning (0.1-0.3)**: Best consistency — positive mean AND median monthly PnL.

### Out-of-Sample Copy Test (top 50 traders per bucket)

| MVF Bucket | OOS % Profitable | Test PnL/$ | Train-Test Corr |
|------------|:---:|:---:|:---:|
| **Pure taker (<0.1)** | **74%** | **+19.9c** | **0.555** |
| Taker-leaning (0.1-0.3) | 66% | +19.5c | 0.114 |
| Maker-leaning (0.7-0.9) | **36%** | **-22.3c** | 0.154 |

**Rule**: Copy pure takers. Never copy makers.

---

## Market Patterns (copy/04)

Skilled traders (top 5% by PnL) vs unskilled (bottom 5%).

### Where Skill Pays Off Most

| Filter | PnL Edge/Position |
|--------|:---:|
| Mega markets (>$1M volume) | $2,270 |
| Hard markets (<40% correct) | $1,492 |
| 3-12 month resolution | $2,670 |
| neg_risk markets | $1,006 (vs $253 standard) |
| Early entry (85.7% vs 90.8% of lifetime) | ~5pp timing advantage |

### Key Pattern

Skilled traders often have LOWER win rates but dramatically higher PnL per position. They win bigger and lose smaller. The PnL edge is the true measure of skill, not win rate.

### Category PnL Edge (top)

| Category | PnL Edge |
|----------|:---:|
| Politics > Joe Biden | $16,928 |
| Politics > Fed Rates | $7,233 |
| Celebrities | $6,399 |
| Boxing | $2,940 |

---

## NO/YES Resolution Skew (overpriceNo/01)

### The 62% NO rate is structural, not informational

| Market Type | YES rate | NO rate |
|-------------|:---:|:---:|
| Binary (non neg_risk) | **46.6%** | **53.4%** |
| neg_risk (multi-outcome) | 13.9% | 86.1% |
| Overall | 38.1% | 61.9% |

neg_risk markets are **perfectly calibrated** at 1/N. No excess NO edge beyond mechanical structure.

### Favorite-Longshot Bias

| YES Price Range | Actual YES | Implied YES | Edge Direction |
|:---:|:---:|:---:|:---:|
| 15-45% | 9-37% | 15-45% | **Buy NO** (+5-9pp) |
| 50-75% | 53-82% | 50-75% | **Buy YES** (+2-10pp) |

Classic favorite-longshot bias. Below 50% = buy NO. Above 50% = buy YES. Crossover at ~50%.

### Temporal Trend

NO edge **shrinking over time**: 66.2% pre-2025 → 61.7% 2025+. Markets becoming more efficient.

---

## Pure Taker NO Bettors (overpriceNo/01)

| Side / Type | Total PnL |
|-------------|:---:|
| NO pure taker (binary) | **+$331M** |
| NO pure taker (neg_risk) | -$24M |
| NO pure maker | -$74M |

**Pure taker NO on binary = profitable. Everything else on NO side = not.**
