# Strategy Synthesis: Actionable Edges from Polymarket Data

**Date**: 2026-02-16
**Data**: 2.08M traders, 70.9M trader-market PnL rows, 390K resolved markets, Nov 2022 - Jan 2026

This document synthesizes findings from 4 parallel analyses into concrete, implementable strategy definitions.

---

## The Three Confirmed Edges

### Edge 1: Consistency Signal (strongest, most reliable)

**Source**: `02_consistency_as_predictor.md`

Traders with N consecutive profitable months have dramatically higher future win rates than baseline (47.1%). The signal survives volume-matching and persists 6+ months forward.

| Lookback | Forward Win Rate | Traders | Signal Half-Life |
|----------|-----------------|---------|-----------------|
| 6 months | 83% | 39,713 | >12 months |
| 9 months | 87% | 8,409 | 8-10 months |
| 12 months | 90% | 1,008 | 5-6 months |

**Critical implementation detail**: Without a `min_markets` filter, median forward PnL is near zero (mean is driven by whales). Adding `min_markets >= 20` raises median forward PnL from $0.07 to $4,691 (9-month lookback) -- a 67,000x improvement in median signal quality.

**Recommended config**: 9-month lookback, 20+ markets/month = **129 traders, 87.8% win rate, $4,691 median forward PnL/month**.

### Edge 2: MVF Filter (strong, directional)

**Source**: `03_mvf_patterns.md`

Pure takers (MVF < 0.1) are the best copy targets. In out-of-sample testing (train: pre-June 2025, test: post-June 2025):

| MVF Bucket | OOS % Profitable | Test PnL/$ | Train-Test Corr |
|------------|-----------------|------------|-----------------|
| Pure taker (<0.1) | **74%** | **+19.9c** | **0.555** |
| Taker-leaning (0.1-0.3) | 66% | +19.5c | 0.114 |
| Mixed (0.3-0.7) | 58% | +0.7c | -0.908 |
| Maker-leaning (0.7-0.9) | **36%** | **-22.3c** | 0.154 |

Pure takers have the highest OOS profitability rate AND the best train-test correlation (0.555). Maker-leaning traders are anti-signals -- only 36% remain profitable OOS.

**Key finding**: Profitability inverts around MVF 0.48. Below = net profitable, above = net losing.

### Edge 3: Market Selection (moderate, additive)

**Source**: `04_market_patterns.md`

Skilled traders' PnL edge is not uniform across market types:

| Market Filter | PnL Edge per Position | Context |
|--------------|----------------------|---------|
| Volume >$100K | $255-$2,270 | Skill only visible in liquid markets |
| Hard markets (<40% correct) | $1,492 | Biggest edge where crowd is wrong |
| 3-12 month resolution | $2,670 | Political/macro markets |
| Enter in first 60% of lifetime | ~5pp earlier entry | Skilled enter at 85.7% vs 90.8% |
| neg_risk markets | $1,006 (vs $253 standard) | Amplifies skill but also risk |

---

## Combined Strategy: "Informed Taker Consistency"

Combine all three edges into a single trader selection filter:

### Selection Criteria

```
1. CONSISTENCY:  9+ consecutive profitable months (min_markets >= 20/month)
2. MVF:          MVF < 0.3 (pure taker or taker-leaning)
3. VOLUME:       Total volume > $50K (not micro-traders)
4. RECENCY:      Last active within 2 months
```

### Expected Universe

From the intersection:
- 129 traders pass the consistency filter (9-month, 20+ markets)
- ~80% are likely MVF < 0.3 (takers dominate the consistent cohort)
- ~90%+ have >$50K volume (high min_markets implies high volume)
- **Expected: ~90-110 traders**

### Expected Performance

| Metric | Conservative Estimate | Aggressive Estimate |
|--------|----------------------|---------------------|
| Forward win rate | 85-88% | 88-92% |
| Median monthly PnL | $3,000-$5,000 | $5,000-$10,000 |
| Signal refresh | Monthly | Monthly |
| Signal half-life | 8-10 months | 8-10 months |
| OOS % profitable (from MVF) | 70-74% | 74-80% |

### Market Weighting (optional overlay)

When copying trades, weight toward:
- Markets with >$100K volume (where skill edge is measurable)
- Categories with high skilled-trader PnL edge (Politics, Geopolitics, Bitcoin, world affairs)
- Markets in the 3-12 month resolution bucket
- Avoid micro/small markets (<$1K volume) entirely

### Position Sizing

- **Equal-weight across selected traders** as baseline
- Size down on neg_risk markets (higher variance per position)
- Cap maximum exposure per market at 5% of portfolio
- Cap maximum exposure per trader at 10% of portfolio

---

## Risk Factors

### Known Risks

1. **Whale concentration**: Even with min_markets=20, top 5-10 traders may drive >50% of aggregate returns. Monitor concentration.

2. **Regime dependence**: 2024 US election cycle created unusual alpha opportunities. Post-election markets may have lower edge.

3. **Latency**: We observe resolution-date PnL, not trade-date PnL. The consistency signal has a built-in lag of weeks to months. This is fine for monthly rebalancing but too slow for intraday copying.

4. **Capacity**: If copying $1M+ per trader, market impact in smaller markets could erode alpha. Focus on $100K+ volume markets to mitigate.

5. **Mean-median divergence**: The consistency signal's mean returns are 10-100x the median at low min_markets thresholds. This makes the strategy vulnerable to whale departures. The min_markets >= 20 filter partially mitigates this but doesn't eliminate it.

6. **Survivor bias**: Traders who had profitable streaks then quit are partially missed. The true OOS performance may be slightly lower than measured.

### Mitigations

- Monthly rebalancing (drop traders who lose consistency, add new qualifiers)
- Diversify across 50+ traders minimum
- Equal-weight (don't overweight whales)
- Market-type overlay (avoid micro/small, prefer liquid political/macro)
- Track live paper PnL for 3+ months before deploying real capital

---

## Anomalies Worth Investigating

1. **The maker's paradox**: 89,676 pure makers lost $220M collectively despite 55.8% win rate. A few informed makers ($10M+ PnL) are the exception. Can we identify them a priori?

2. **Taker improvement over time**: Top takers go from -$290K (early career) to +$275K (late career). A "taker trajectory" signal could identify improving traders before they reach peak performance.

3. **Very hard markets**: 1,636 markets where <10% of traders profit but average 1,036 traders each. These high-liquidity contested markets are where information edges are largest ($1,492 PnL edge for skilled traders). A market-difficulty filter could amplify edge.

4. **Category specialization**: Politics > Joe Biden sub-category has $16,928 PnL edge. Do category-specialist traders exist, and can we identify them?

5. **Entry timing as signal**: Skilled traders enter 5pp earlier (85.7% vs 90.8%). Can we use a trader's average entry timing as an additional filter?

---

## Next Steps

1. **Build the combined filter** in the backtester: consistency + MVF + volume thresholds
2. **Run parameter sweep**: lookback (6/9/12), min_markets (10/20/50), MVF cap (0.1/0.2/0.3)
3. **Measure Sharpe ratio** not just win rate (risk-adjusted returns)
4. **Time-series backtest**: Monthly rebalance, track cumulative PnL with realistic execution assumptions
5. **Compare to naive strategy**: "Copy top-N by historical PnL" without consistency filter
6. **Live paper trading**: Deploy the best configuration on current data, track forward performance
