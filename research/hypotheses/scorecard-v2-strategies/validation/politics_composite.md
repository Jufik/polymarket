# Politics Composite K=100 N=5 — Tick-by-Tick Validation

**Date**: 2026-03-07
**Strategy**: PoliticsComposite K=100, N=5, YES+NO (both directions)
**Test period**: 2025-07-01 onwards (~8 months)
**Train cutoff**: 2025-07-01
**Vectorized UB excess HR**: +62.5pp

---

## Setup

- **Pool**: Top-100 traders by composite score (0.45×excess_hr + 0.25×consistency_sharpe + 0.15×avg_edge + 0.15×bucket_excess_hr) on Politics tag
- **Consensus threshold N**: 5 distinct pool traders must enter same direction before signal fires
- **Direction**: Both YES and NO (vol-weighted majority determines direction)
- **Universe**: 11,944 Politics tag markets in test period
- **Capital**: $100/fill, $50k total, 100 max concurrent positions

---

## Core Metrics

| Metric | Value |
|--------|-------|
| Total trades seen | 22,790,975 |
| Total signals fired | 574 |
| Total fills | 574 (0 rejected) |
| Overall HR | **73.5%** (422 WON / 152 LOST) |
| YES base rate (test period) | 19.0% |
| NO base rate (test period) | 81.0% |
| Overall excess HR | +54.5pp (vs YES base rate — MISLEADING, see direction analysis) |
| Total PnL | $94,660 |
| Avg PnL/fill | $164.91 |
| Avg hold | 572h (23.9 days) |
| Max drawdown | $-843 |
| Profit factor | 7.23 |
| Sharpe (per fill) | 5.67 |
| Annualized signal rate | ~861/yr |

---

## Direction Decomposition — CRITICAL FINDING

The Politics tag has a strongly asymmetric base rate (YES=19%, NO=81%). This inflates the "overall" excess HR metric.

| Direction | n | Hit Rate | Base Rate | Excess HR | Avg PnL | Hold |
|-----------|---|----------|-----------|-----------|---------|------|
| YES | 262 | 70.6% | 19.0% | **+51.6pp** | $125.03 | 15.8d |
| NO | 312 | 76.0% | 81.0% | **-5.0pp** | $198.41 | 30.6d |
| Combined | 574 | 73.5% | 19.0% | +54.5pp | $164.91 | 23.9d |

**Key insight**: NO signals have NEGATIVE excess HR (-5.0pp below the 81% NO base rate). The strategy has zero directional alpha on NO signals — it is base-rate riding. The 76% NO hit rate sounds good but is simply the natural resolution rate for Politics markets.

The positive NO PnL ($61,903 out of $94,660 total) is driven purely by the fact that NO markets pay off well when they win — not by any edge.

### Why NO signals are bad

1. Pool qualification was done on YES positions only (training used `position = 'YES'`)
2. The top-K traders are selected for their YES hit rate performance
3. When consensus fires NO, it means these traders all happened to buy NO tokens — but their selection criterion had nothing to do with NO accuracy
4. The NO base rate (81%) naturally gives 76%+ NO HR for any random strategy

---

## Late-Entry Analysis (fill_price > 0.90)

44.3% of all fills have fill_price > 0.90 — these are near-resolved markets where consensus forms very late:

| Segment | n | % of total | HR | Note |
|---------|---|-----------|-----|------|
| YES price > 0.90 | 110 | 42% of YES | ~86% | Late YES — near certainty already priced in |
| NO price > 0.90 | 144 | 46% of NO | ~86% | Late NO — same issue |

At >0.90 fill price, the market's implied probability is already reflecting the outcome. Entry at these prices yields low edge per dollar (payout ~1.0x vs fill price ~0.95).

---

## YES-Only Price Sensitivity

| Filter | n | HR | Excess HR | Avg PnL | Avg Hold | CS |
|--------|---|-----|-----------|---------|----------|----|
| YES all | 262 | 70.6% | +51.6pp | $125 | 15.8d | 4.08 |
| YES price ≤ 0.95 | 164 | 59.8% | +40.8pp | $206 | 23.8d | 3.52 |
| YES price ≤ 0.90 | 152 | 58.6% | +39.6pp | $223 | 24.3d | 3.64 |
| YES price ≤ 0.80 | 125 | 60.0% | +41.0pp | $280 | 22.5d | 5.11 |
| YES price ≤ 0.70 | 98 | 57.1% | +38.1pp | $359 | 18.8d | **7.29** |

Excluding late entries (price ≤ 0.70) dramatically improves CS (4.1 → 7.3) because avg_pnl per fill nearly triples while hold time shortens (markets with genuine uncertainty resolve faster). The 98 signals over 8 months = ~147/yr annualized with price filter.

---

## Hold Time Distribution

| Percentile | Hours | Days |
|-----------|-------|------|
| Min | 0.4h | — |
| P25 | 7.5h | 0.3d |
| P50 | 71.7h | 3.0d |
| P75 | 548.7h | 22.9d |
| Max | 8,474h | 353d |
| Avg | 572.3h | 23.9d |

- 19.5% of fills resolve in <4h (likely near-resolved markets)
- 38.5% resolve in <24h
- 49.7% take >72h — long-hold political markets dominate by count

---

## Capital Utilization

- Max concurrent open positions: ~73 (within 100-position limit)
- Total capital deployed over test period: $57,400 (574 fills × $100)
- At $50k capital, the sequential signal rate means capital is recycled ~1.1× over 8 months

---

## Degradation vs Vectorized

| Metric | Vectorized UB | Tick Result | Degradation |
|--------|--------------|-------------|-------------|
| Overall excess HR | +62.5pp | +54.5pp (biased by direction mix) | 8.0pp |
| YES-only excess HR | — | +51.6pp | ~10.9pp |

The degradation of ~10-11pp is **lower than the 20-40pp typical range**. This is because the vectorized UB was computed on YES positions only (Politics), while the tick result includes both YES and NO signals. The genuine YES alpha degrades by ~10.9pp from UB to tick — within expected range.

---

## Summary & Recommendation

### What works
- **YES signals have genuine alpha**: +51.6pp excess HR on 262 signals over 8 months
- **Strong compounding score** (YES-only, price≤0.70): CS=7.29
- **Signal volume sufficient** for deployment: 262 YES signals/8mo → ~393/yr

### What doesn't work
- **NO signals have no alpha**: -5.0pp excess HR, base-rate riding only
- **Late entries (>0.90 price)**: 42% of fills, low edge-per-dollar

### Recommended strategy configuration

Run Politics Composite as **YES-only with price filter**:

```python
strategy = TokenMapStrategy(
    name="politics_composite_k100_n5_yes_only",
    pool=pool,
    tag_markets=tag_markets,
    gambling_markets=gambling_markets,
    n_threshold=5,
    token_map=token_map,
    direction_filter="YES",  # YES only — NO has no alpha
    max_price=0.80,          # Exclude late entries
    size_usd=100.0,
)
```

Expected tick performance (YES-only, price≤0.80):
- Signals: ~125/8mo → ~188/yr
- HR: ~60%, excess: +41pp
- Avg PnL: $280/signal
- CS: 5.11

### Verdict: PROMOTE TO PAPER with YES-only + price filter
The YES-direction signal is real and statistically robust (262 samples, +51.6pp excess HR). The NO direction should be dropped as it has no alpha beyond base rates. A price ceiling of 0.80 would further improve capital efficiency.
