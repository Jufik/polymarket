# S2 Rotation Accelerators: 3x Capital Turnover from Smart Filtering

**Date**: 2026-02-19
**Method**: Analysis of 26,012 "Will" binary markets (YES 10-50%), 2025-01 to 2026-02
**Strategy**: S2 Favorite-Longshot NO on "Will" binary questions

---

## Problem

With $300 deployed in S2, capital lockup limits rotation. At 6.5-day median lockup and $50/bet:
- 6 concurrent slots × 4.6 rotations/month = 28 bets/month
- 28 × $2.48/bet = ~$69/month

**Can we accelerate rotation without sacrificing edge?**

---

## Key Finding

Yes. Three filters independently reduce lockup AND increase edge. Combined, they cut median lockup from 4 days to ~1 day and nearly triple PnL from the same capital.

---

## Accelerator 1: Target Low-Volume Markets (< $5K)

| Volume | Median Lockup | NO HR | PnL/$100 | PnL/day |
|--------|:---:|:---:|:---:|:---:|
| **< $1K** | **1 day** | **89.7%** | **$19.42** | **$19.42/day** |
| **$1K-$5K** | **2 days** | **86.1%** | **$15.15** | **$7.58/day** |
| $5K-$25K | 3 days | 81.9% | $10.67 | $3.56/day |
| $25K-$100K | 6 days | 78.4% | $7.24 | $1.21/day |
| $100K+ | 8 days | 73.6% | $1.76 | $0.22/day |

Low-volume markets resolve fastest because they are short-duration events (daily/weekly questions) with thin order books. The edge is also highest here — prices are most inefficient when few traders participate.

**Capital efficiency**: < $1K volume has **88x higher PnL/day** than $100K+ markets. At $50 bets, we are well within the liquidity of <$1K markets (our bet is 5-50% of total volume, which is fine for taking existing resting orders).

**Risk**: At <$1K volume, a $50 bet may be 5-50% of the order book. Execution may require limit orders rather than market orders. But at 88% HR, even partial fills are profitable.

---

## Accelerator 2: Keyword Filtering — "above"/"below" Resolve Same-Day

| Keyword | N Markets | Median Lockup | NO HR | PnL/$100 |
|---------|----------:|:---:|:---:|:---:|
| **"above"** | **1,274** | **0 days** | **87.9%** | **$18.28** |
| **"below"** | **61** | **2 days** | **88.5%** | **$17.63** |
| "today" | 75 | 0 days | 86.7% | $23.97 |
| "tonight" | 31 | 3 days | 93.5% | $34.43 |
| "this week" | 289 | 7 days | 76.8% | $3.76 |
| "by" | 738 | 17 days | 79.5% | $3.46 |
| "before" | 149 | 40 days | 80.5% | $5.79 |
| "reach" | 169 | 6 days | 71.0% | **-$9.21** |
| "hit" | 70 | 28.5 days | 77.1% | -$1.17 |

### "above"/"below" questions
"Will X close above $Y?" are daily/weekly price threshold markets. They resolve at market close on the target date — typically same-day or next-day. Volume is large (1,274 "above" markets in 2025), HR is 88%, and PnL is $18/bet.

### Keywords to AVOID
- **"reach"**: 6-day lockup, 71% HR, **-$9.21/bet** — negative edge. These are aspirational targets that the market prices more accurately.
- **"hit"**: 28.5-day lockup, negative PnL. Long-duration bets on milestone events.
- **"before"/"by"**: 17-40 day lockup. Calendar deadline markets tie up capital for weeks.
- **"in 2025"/"in 2026"**: 118+ day lockup. Annual prediction markets.

### Keyword classification

| Class | Keywords | Action |
|-------|----------|--------|
| **Fast + profitable** | "above", "below", "today", "tonight" | Target |
| **Slow but profitable** | "this week", "before", "by" | Skip (lockup too long) |
| **Negative edge** | "reach", "hit" | Avoid |
| **Ultra-slow** | "in 2025", "ever" | Avoid |

---

## Accelerator 3: Tighter YES Price Band (15-30%)

| YES Price | N | NO HR | PnL/$100 | PnL/day |
|:---------:|--:|:---:|:---:|:---:|
| 10-15% | 4,410 | 97.3% | $10.68 | $2.14 |
| **15-20%** | **3,950** | **95.7%** | **$15.63** | **$3.91** |
| **20-25%** | **3,671** | **92.0%** | **$18.02** | **$4.50** |
| **25-30%** | **4,168** | **86.7%** | **$18.37** | **$4.59** |
| 30-35% | 3,028 | 75.8% | $10.96 | $2.74 |
| 35-40% | 2,471 | 65.7% | $4.27 | $1.07 |
| 40-45% | 2,179 | 57.9% | -$0.27 | -$0.07 |
| 45-50% | 1,891 | 48.8% | -$8.12 | -$2.03 |

The sweet spot is **YES 15-30%**: highest PnL/day ($3.91-$4.59), high HR (87-96%), and substantial market count (11,789 markets).

Below 15%: HR is 97% but payoff per bet is small ($10.68) because NO costs 85-90c.
Above 35%: HR drops below 76% and PnL/day turns negative above 40%.

**Recommendation**: Narrow from YES 10-50% to **YES 15-30%** for S2. Sacrifices 55% of market count but improves PnL/day by 2x.

---

## Combined Filter Impact

### Lockup distribution: Fast vs All

| Filter | Markets | Median Lockup | NO HR | PnL/$100 | Total PnL |
|--------|--------:|:---:|:---:|:---:|:---:|
| **Fast (<3 days)** | **10,400 (40%)** | **1 day** | **83.7%** | **$13.60** | **$141,399** |
| Slow (3+ days) | 15,612 (60%) | 7 days | 80.6% | $8.80 | $137,450 |
| All | 26,012 | 4 days | 81.8% | $10.72 | $278,849 |

Fast markets are 40% of the pool but generate **51% of total PnL** with 4x faster rotation. They are strictly better on a capital-efficiency basis.

### $300 Capital Simulation (14 months)

| Filter | Total PnL | Total Bets | Improvement |
|--------|:---:|:---:|:---:|
| All markets, $50/bet | $6,323 | 810 | baseline |
| **Fast only (<3d), $50/bet** | **$17,872** | **2,700** | **+183%** |

Same $300 capital, 2.8x more PnL. The fast filter triples bet count (2,700 vs 810) by recycling capital 3x faster.

---

## Recommended S2 Filter Stack (Updated)

```
1. "Will" binary question                          (structural NO bias)
2. YES price 15-30%                                (best PnL/day)
3. Volume < $5K                                    (fastest lockup, highest edge)
4. Prefer "above"/"below"/"today" keywords         (same-day resolution)
5. Avoid "reach"/"hit"/"by"/"before" keywords      (long lockup or negative edge)
```

### Expected performance at $300

| Metric | Original S2 | Optimized S2 |
|--------|:---:|:---:|
| Median lockup | 6.5 days | ~1-2 days |
| Rotations/month/slot | 4.6 | ~15 |
| Bets/month (6 slots) | 28 | ~90 |
| Monthly PnL (upper bound) | ~$139 | ~$450 |
| Monthly PnL (50% haircut) | ~$69 | ~$225 |

---

## Risks of the Optimized Filter

1. **Execution in thin markets**: At <$1K volume, a $50 bet is significant. May need limit orders, not market orders. Partial fills likely.
2. **Market identification latency**: Must detect new "Will above/below" markets quickly (within hours of listing) to enter at favorable NO prices.
3. **Reduced diversification**: Fewer markets per month means more variance. A bad week of "above" questions could produce a loss streak.
4. **Selection bias in backtest**: FIFO ordering in the simulation selects fast-resolving markets, which naturally have higher HR. Live execution won't have perfect foresight on which markets resolve fastest.
5. **Capacity ceiling**: Even optimized, $300 in S2 caps at ~$225/month realistic. This is a supplemental income stream, not the core strategy (S1 is the core).
