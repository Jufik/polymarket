# Cross-Window Mean Reversion (1-Hour and 15-Minute)

**Status**: Active, HIGH statistical significance (z=9.11), needs liquidity validation
**Capital allocation**: $200-500
**Direction**: Reversal after 2-streak (2 Ups -> bet Down, 2 Downs -> bet Up)
**Source insights**: 23_cross_window_mean_reversion.md, 24_15min_cross_window_mean_reversion.md

---

## Edge Summary

After 2 consecutive same-direction windows, the next window reverses 53.1% of the time. Highly significant (z=9.11 on 21,263 trades across BTC/ETH/SOL/XRP). The market does NOT price this in — T+1 trades at ~50c when signal fires. Passes walk-forward validation with **test stronger than train** (54.0% vs 52.3%).

---

## 15-Minute Markets (PRIMARY — Insight #24)

### Per-Asset Streak Analysis

| Asset | #Trades | Win% | $/bet ($50) | z-score |
|-------|:---:|:---:|:---:|:---:|
| BTC | 5,776 | 53.1% | $3.06 | +4.66 |
| **ETH** | **5,633** | **53.8%** | **$3.83** | **+5.74** |
| SOL | 4,986 | 52.6% | $2.63 | +3.71 |
| XRP | 4,868 | 52.9% | $2.90 | +4.04 |
| **ALL** | **21,263** | **53.1%** | **$3.13** | **+9.11** |

All 4 assets individually significant at p<0.001. ETH is strongest ($3.83/bet).

### Walk-Forward (50/50 chronological split)

| Period | Trades | Win% | $/bet | z-score |
|--------|:---:|:---:|:---:|:---:|
| **Train** (Oct-Dec 2025) | 10,650 | 52.3% | $2.26 | +4.67 |
| **Test** (Dec 2025-Feb 2026) | 10,651 | **54.0%** | **$4.04** | **+8.34** |

**Test is stronger than train** — unusual, very encouraging.

### Monthly Stability (5/5 positive)

| Month | Bets | Win% | PnL ($50/bet) |
|-------|:---:|:---:|:---:|
| Oct 2025 | 2,378 | 51.3% | +$3,000 |
| Nov 2025 | 5,423 | 52.2% | +$12,050 |
| Dec 2025 | 5,345 | 53.2% | +$17,250 |
| **Jan 2026** | **5,391** | **55.2%** | **+$27,950** |
| Feb 2026 | 2,764 | 52.5% | +$6,900 |
| **TOTAL** | **21,301** | **53.2%** | **+$67,150** |

### Capital-Constrained Performance

| Capital | Bet Size | Slots | PnL/month (upper bound) |
|:---:|:---:|:---:|:---:|
| $200 | $50 | 4 | $12,060 |
| $500 | $50 | 10 | $13,430 |
| $500 | $100 | 5 | $25,800 |
| $1,000 | $100 | 10 | $26,860 |

**Warning**: These are upper bounds assuming entry at exactly 50c and zero fees. Heavy haircuts needed.

### Realistic Expectations ($50/bet, all 4 assets)

| Scenario | Monthly PnL |
|----------|:---:|
| Upper bound | $13,430 |
| 70% haircut | **$9,400** |
| 50% haircut | **$6,715** |
| **30% haircut (pessimistic)** | **$4,030** |

The **key unknown is liquidity** — whether 15-min markets have sufficient volume to absorb $50+ bets at ~50c.

---

## 1-Hour Markets (SUPPLEMENTAL — Insight #23)

### Per-Asset Results

| Asset | After 2 Ups → Down | z-score |
|-------|:---:|:---:|
| **BTC** | **53.9%** (1,423 cases) | **3.10** |
| **ETH** | **54.0%** (1,364 cases) | **2.92** |
| SOL | 51.8% (1,414 cases) | 1.33 |
| XRP | 52.3% (1,284 cases) | 1.62 |
| **ALL** | **53.0%** (5,485 cases) | **4.50** |

BTC and ETH individually significant. SOL/XRP are not.

### Walk-Forward

| Period | Trades | Win% | $/bet |
|--------|:---:|:---:|:---:|
| Train (May-Oct 2025) | 5,594 | 53.4% | $3.36 |
| Test (Oct 2025-Feb 2026) | 5,133 | 52.9% | $2.87 |

Positive in both. 9/10 months positive (single loss: Jun 2025 at -$350).

### Expected Performance

| Config | Trades/mo | $/month ($50/bet) |
|--------|:---:|:---:|
| BTC only, T at 95c | ~150 | $470 |
| BTC+ETH symmetric | ~500 | $1,500 |
| All 4 assets symmetric | ~1,070 | $3,330 |

---

## Signal Design

### 15-Minute Execution

```
Every 15 minutes:
1. T-2 and T-1 outcomes known (resolved >=15min ago)
2. If T-2 and T-1 both Up:
     Buy T+1 DOWN (NO at ~50c)
3. If T-2 and T-1 both Down:
     Buy T+1 UP (YES at ~50c)
4. Wait 15 minutes for resolution
5. Repeat
```

384 possible markets/day (96 windows x 4 assets). Max 4 concurrent trades per slot.

### 1-Hour Execution

```
1. Confirm T-1 outcome (resolved 1 hour ago)
2. If T-1 was Up:
     Watch T's YES price
     When T crosses 80-95c → buy T+1 DOWN (NO at ~50c)
3. If T-1 was Down:
     Watch T's YES price
     When T drops below 5-20c → buy T+1 UP (YES at ~50c)
4. Wait ~1-2 hours for resolution
```

Key: T at 95c fires ~102 minutes before resolution, when T+1 is still at 50.4c.

---

## Why It Works

1. **Short-term mean reversion**: Crypto mean-reverts over 15min-1hr after trending
2. **Independent market pricing**: Each market priced at ~50c independently — no conditioning on prior outcomes
3. **Small edge, massive volume**: 3% edge below most traders' radar. Law of large numbers at 5,000 trades/month
4. **24/7 operation**: No dead zones unlike equity markets

---

## What Does NOT Work (Insight #22)

Tested 7 strategies on Up/Down markets. All fail:
- **Serial autocorrelation single-window**: Market prices it in (entry at 48.2c not 50c)
- **Time-of-day bias**: Collapses out-of-sample
- **Momentum following**: Negative at every threshold
- **Cross-asset correlation**: 80% correlation but fully priced in, no lead-lag
- **Market-making straddle**: Adverse selection kills it (-$1.98/market)

**The only edge is the 2-streak cross-window reversal.**

---

## 15-Min vs 1-Hour Comparison

| Metric | 15-min | 1-hour |
|--------|:---:|:---:|
| z-score | **+9.11** | +6.44 |
| Trades/day (all 4) | **163** | ~11 |
| Walk-forward | **52.3% → 54.0%** | 53.4% → 52.9% |
| Capital lockup | **15 minutes** | 1 hour |
| Data period | 5 months | 10 months |

15-min has same per-trade edge but **15x more volume** and **4x faster rotation**.

---

## Risks and Limitations

- **5-month data** (15-min) / 10 months (1-hour): Could be microstructure artifact
- **Liquidity unknown on 15-min**: Biggest risk. Must validate before scaling
- **1c slippage** at 53% win rate reduces $/bet by ~$1
- **Correlated losses**: Sustained crypto trend hits all 4 assets simultaneously
- **Regime sensitivity**: BTC/ETH strongest; SOL/XRP weak individually
- **No adverse selection model**: Informed traders may provide other side only when signal is wrong
