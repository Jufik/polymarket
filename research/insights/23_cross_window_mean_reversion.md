# Cross-Window Mean Reversion: 2-Streak Reversal on 1-Hour Crypto Markets

**Date**: 2026-02-20
**Method**: Outcome-based streak analysis + price-aware execution simulation on BTC/ETH/SOL/XRP 1-hour Up/Down markets
**Universe**: 24,141 1-hour markets (6,330 BTC, 6,003 ETH, 5,853 SOL, 5,666 XRP), May 2025 - Feb 2026

---

## TL;DR

**After 2 consecutive same-direction hours, the next hour reverses 53% of the time.** This is statistically significant (z=6.44, p<0.0001 across all 4 crypto assets) and survives walk-forward validation. The market does NOT price this in — T+1 trades at ~50c when the signal fires. BTC is the strongest asset (z=3.10). The user's hypothesis — "BTC UP at T trades at 95c → enter T+1 DOWN before T ends" — is validated, though the mechanism is outcome-based mean-reversion rather than price-level signal. Estimated $3/bet at $50 stakes, ~$450/month for BTC alone.

---

## The Hypothesis

> "BTC UP at T trades at 95c, N minutes before it ends. Check BTC DOWN at T+1 — there might be a profitable pattern."

The idea: use window T's live price as a proxy for its outcome before resolution, then enter T+1's market early enough that its price is still near 50c.

---

## Market Structure

1-hour crypto markets (BTC, ETH, SOL, XRP) resolve on the hour. Each market starts trading ~16 hours before its checkpoint:

```
T-1 (H-1)     T (H)      T+1 (H+1)
   |            |            |
   resolve      resolve      resolve
   ↓            ↓            ↓
---+---+--------+---+--------+---+--->
       ↑        ↑   ↑        ↑
     T-1 known  |   T at 95c T+1 at 50c
                 |   (signal  (entry point)
                 |    fires)
```

Key timing:
- T-1 resolves 1 hour before T → we know T-1's outcome
- T reaches 95c on average **102 minutes** before its resolution
- At that moment, T+1 is still trading at **50.4c** (market hasn't priced in any reversion)
- T+1 resolves ~1 hour after T

---

## Finding 1: Outcome-Based Mean Reversion (z=6.44)

After 2 consecutive same-direction outcomes, the next hour reverses more often than chance:

| Asset | After 2 Ups → Down | z-score | After 2 Downs → Up |
|-------|:---:|:---:|:---:|
| **BTC** | **53.9%** (1,423 cases) | **3.10** | 51.7% |
| **ETH** | **54.0%** (1,364 cases) | **2.92** | 53.3% |
| SOL | 51.8% (1,414 cases) | 1.33 | 55.5% |
| XRP | 52.3% (1,284 cases) | 1.62 | 52.0% |
| **ALL** | **53.0%** (5,485 cases) | **4.50** | 53.2% (5,242) |

**Symmetric strategy** (2 Ups→Down AND 2 Downs→Up): 10,727 trades, **53.1% win rate, z=6.44**.

Base rate comparison:
- After 1 Up: 50.7% Down (noise)
- After 2 Ups: **53.9% Down** (significant)
- After 3 Ups: 53.5% Down (no improvement)
- After 4 Ups: 54.4% Down (too few samples)

The edge is in the **2-streak**, not longer.

---

## Finding 2: The 95c Price Level Adds Nothing Beyond Outcome

| Signal | #Trades | Down% | $/bet |
|--------|:---:|:---:|:---:|
| 2 consecutive Up outcomes (no price) | 1,423 | 54.1% | $4.11 (at 50c) |
| T-1 Up + T at 95c (price-aware) | 1,481 | 54.3% | $3.15 (at 50.4c) |
| T-1 Up + T at 80c (price-aware) | 1,753 | 53.5% | $3.05 (at ~50c) |

Markets reaching 95c resolve Up 94.5% of the time. The 95c threshold is just a noisy proxy for "T was Up". The real signal is the outcome streak.

However, the price-aware approach is **necessary for execution** because:
- You can't wait for T to formally resolve (by then T+1 is at an extreme)
- T at 95c fires ~102 minutes before T's close, when T+1 is still at 50c
- T at 80c fires even earlier, giving more time and more trades

---

## Finding 3: Walk-Forward Validation Passes

| Period | Trades | Win% | $/bet |
|--------|:---:|:---:|:---:|
| **Train** (May-Oct 2025) | 5,594 | 53.4% | **$3.36** |
| **Test** (Oct 2025-Feb 2026) | 5,133 | 52.9% | **$2.87** |

The test period is slightly weaker but still positive. No evidence of decay.

Per-threshold walk-forward (BTC, T-1 Up + T at threshold → buy T+1 Down):

| T threshold | Train $/bet | Test $/bet |
|:-----------:|:-----------:|:----------:|
| 80c | $2.82 | **$3.30** |
| 85c | $2.46 | **$3.63** |
| 90c | $2.06 | **$3.69** |
| 95c | $2.39 | **$3.99** |

All positive in both train and test.

---

## Finding 4: Monthly Stability

All 4 assets combined, symmetric strategy:

| Month | Bets | Win% | PnL | Notes |
|-------|:---:|:---:|:---:|-------|
| May 2025 | 46 | 50.0% | $0 | Cold start |
| Jun 2025 | 795 | 49.6% | **-$350** | Only losing month |
| Jul 2025 | 1,357 | 53.7% | $5,050 | |
| Aug 2025 | 1,282 | 53.1% | $4,000 | |
| Sep 2025 | 1,325 | 55.4% | $7,150 | Best month |
| Oct 2025 | 1,410 | 50.1% | $200 | Flat |
| Nov 2025 | 1,186 | 52.6% | $3,100 | |
| Dec 2025 | 1,321 | 52.2% | $2,850 | |
| **Jan 2026** | **1,317** | **57.1%** | **$9,350** | Strongest |
| Feb 2026 | 688 | 52.9% | $2,000 | Partial |

9 out of 10 months positive. The single negative month (Jun) lost only $350 out of $33,350 total.

---

## Finding 5: Single-Window Signals Do NOT Work

When T reaches 95c without conditioning on T-1's outcome:

| Signal | #Trades | Down% | $/bet |
|--------|:---:|:---:|:---:|
| T at 95c → buy T+1 Down (no T-1 filter) | 3,160 | 50.8% | **$0.34** |
| T at 95c + T-1 Up → buy T+1 Down | 1,481 | 54.3% | **$3.15** |

The T-1 streak condition is essential. Without it, there's zero cross-window signal. The market correctly prices single-period transitions.

---

## Finding 6: Late Entry Is Impossible

When T formally resolves, T+1 has only ~47 minutes remaining. At that point:
- 1,411 out of 1,423 potential signals had T+1's price at >99c or <1c
- Only 1 trade was executable at non-extreme price
- **You cannot wait for T's outcome — you must use T's live price as a proxy**

This validates the user's hypothesis: early entry using live prices is the only viable execution method.

---

## Finding 7: Cross-Asset Correlation Is Priced In

When BTC hits 95c (strong Up), other assets' same-hour prices:

| Asset | Same-hour YES | Same-hour Up% | Buy DOWN $/bet |
|-------|:---:|:---:|:---:|
| ETH | 78.4c | 81.0% | **-$15.29** |
| SOL | 75.1c | 78.7% | **-$15.53** |
| XRP | 72.7c | 75.9% | **-$15.07** |

The 80% cross-asset correlation is fully priced in. No inter-asset edge within the same window.

---

## Execution Design

### Signal logic

```
1. Confirm T-1 outcome (resolved 1 hour ago)
2. If T-1 was Up:
     Watch T's YES price
     When T crosses 80-95c → buy T+1 DOWN (NO at ~50c)
3. If T-1 was Down:
     Watch T's YES price
     When T drops below 5-20c → buy T+1 UP (YES at ~50c)
4. Wait for T+1 resolution (~1-2 hours)
```

### Expected performance

| Config | Trades/mo | Win% | $/bet | $/month ($50/bet) |
|--------|:---:|:---:|:---:|:---:|
| BTC only, T at 95c | ~150 | 54.3% | $3.15 | **$470** |
| BTC only, T at 80c | ~175 | 53.5% | $3.05 | **$530** |
| BTC+ETH, symmetric | ~500 | 53.0% | $3.00 | **$1,500** |
| All 4 assets, symmetric | ~1,070 | 53.1% | $3.11 | **$3,330** |

Capital efficiency is high: 1-hour lockup means capital rotates 24x/day with 10 slots.

### Practical considerations

- **Timing**: 24 checkpoints/day per asset. Signal requires knowing T-1 outcome (already resolved) and watching T's live price.
- **Entry price**: T+1 is at 50c ± 1c when signal fires. Use market orders (taker).
- **Capital**: At $50/bet, 10 slots, $500 capital covers the rotation with margin.
- **Monitoring**: Need to check each hourly checkpoint across BTC/ETH. ~48 checks/day.

---

## Why This Works (and Why the Market Doesn't Price It)

1. **Short-term mean reversion in crypto**: BTC/ETH prices mean-revert over 1-3 hours. After 2 consecutive Up hours (2-4% move), the next hour is 53-54% likely to pull back.

2. **Independent market pricing**: Each 1-hour market is priced independently at ~50c. The market does not condition T+1's price on T-1's outcome. This is rational for single-period analysis (1 Up → 50.7% Down, noise), but leaves the 2-streak pattern unpriced.

3. **Structural market feature**: These markets open ~16 hours before resolution. By the time the 2-streak signal fires (100 min before T closes), T+1 has been trading for 14+ hours at ~50c. The signal fires well after T+1's price is established.

4. **Small edge, high volume**: A 3% edge per bet is below most traders' radar. Individual bets look like coin flips. Only systematic high-volume execution captures the statistical edge.

---

## Comparison with Other Strategies

| Strategy | HR | $/bet | $/month ($500) | Lockup | Data period |
|----------|:---:|:---:|:---:|:---:|:---:|
| **S3 Cross-window MR (this)** | **53%** | **$3.11** | **$1,500** | **1 hour** | **10 months** |
| S2 Crypto OTM NO (#21) | 98.9% | $12.72 | $802 | 4-8 hours | 7 months |
| S2 General "Will" NO (#19) | 85.5% | $5.38 | $968 | 6.5 days | 14 months |
| S1 Consistency Copy | ~55% | ~$3 | ~$50-100 | days | 12 months |

The mean reversion strategy has the **lowest $/bet** but the **highest trade volume** and **fastest capital rotation**. Combined with S2 Crypto OTM (different markets, no overlap), total expected monthly PnL at $500 capital: **$2,000-$2,500**.

---

## Limitations

- **10-month window**: Markets started May 2025. No prior data exists. The mean reversion could be a feature of the current market microstructure that may change.
- **Entry at 50c assumed**: Actual entry may be 49-51c depending on order book depth. A 1c slippage at 53% win rate reduces $/bet by ~$1.
- **Fee impact**: Polymarket fees on winnings reduce net $/bet by ~$0.20-$0.50.
- **Execution complexity**: Requires monitoring 4 assets × 24 hours/day = 96 checkpoints. Signal fires for 2-3 hours each, requiring real-time price watching.
- **No adverse selection model**: Taker fills may concentrate on losing trades if informed flow exists.
- **Correlation with other strategies**: Both this strategy and S2 OTM are long-vol/reversal. A sustained crypto trend (bull run or crash) could cause correlated losses across strategies.
- **Regime sensitivity**: The mean reversion is strongest in BTC/ETH. SOL and XRP show weaker effects (z=1.3-1.6, not individually significant). If BTC/ETH microstructure changes, the edge may vanish.

---

## Recommendation

**Deploy as a complementary strategy alongside S2 OTM NO.** The two strategies target different markets (Up/Down vs above/below) with different timeframes (1-hour vs 4-hour) and different risk profiles (thin edge/high volume vs thick edge/low volume).

1. **Start with BTC only**, T at 80c threshold (most trades, best $/bet stability).
2. **Add ETH** after 2 weeks of live validation.
3. **Use symmetric signals** (2 Ups→Down AND 2 Downs→Up) for maximum trade count.
4. **Monitor**: If monthly win rate drops below 51% for 2 consecutive months, pause and re-evaluate.
5. **Do not add SOL/XRP** until their individual z-scores improve (currently 1.3-1.6, not significant alone).
