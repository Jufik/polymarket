# Tag-HR-Copy: Vectorized vs Tick-by-Tick Comparison

## Summary Table

| Tag | Vec HR | Tick HR | Degradation | Vec CS | Tick CS | Verdict |
|-----|--------|---------|-------------|--------|---------|---------|
| Esports | 67.2% | 45.8% | **-21.4pp** | 34.87 | N/A | none |
| 1H | 78.0% | 49.8% | **-28.2pp** | 19.71 | N/A | none |
| Tennis | 72.4% | 40.6% | **-31.8pp** | 9.67 | N/A | none |

**Degradation bands:** 0-10pp: suspicious (look-ahead), 20-40pp: expected, >40pp: excessive.
All 3 tags show 20-32pp degradation — within "expected" band on HR alone, but PnL is negative because
the simulation reveals the vectorized signal was measuring CONSENSUS, not individual trade quality.

## Detailed Comparison

### Esports

| Metric | Vectorized (UB) | Tick-by-Tick | Change |
|--------|----------------|-------------|--------|
| Hit Rate | 67.2% | 45.8% | -21.4pp |
| Excess HR | +35.7pp | +10.9pp | -24.8pp |
| Median PnL | $8.13 | -$102.50 | -$110.63 |
| Avg Hold | 2.0h | 12.9h | +10.9h |
| Signals | ~1,192/fold | ~452/year | <<< |
| CS | 34.87 | N/A | collapsed |
| Base Rate | 34.3% | 34.9% | ~same |

### 1H

| Metric | Vectorized (UB) | Tick-by-Tick | Change |
|--------|----------------|-------------|--------|
| Hit Rate | 78.0% | 49.8% | -28.2pp |
| Excess HR | +27.3pp | +2.5pp | -24.8pp |
| Median PnL | $4.01 | -$102.50 | -$106.51 |
| Avg Hold | 1.33h | 2.0h | +0.7h |
| Signals | ~5,009/fold | ~2,534/year | higher! |
| CS | 19.71 | N/A | collapsed |
| Base Rate | 49.7% | 47.3% | ~same |

**Flag: 1H 49.8% HR near base rate (47.3%) — gambling confirmed**

### Tennis

| Metric | Vectorized (UB) | Tick-by-Tick | Change |
|--------|----------------|-------------|--------|
| Hit Rate | 72.4% | 40.6% | -31.8pp |
| Excess HR | +33.6pp | +10.5pp | -23.1pp |
| Median PnL | $2.40 | -$102.50 | -$104.90 |
| Avg Hold | 2.0h | 15.4h | +13.4h |
| Signals | ~5,725/fold | ~271/year | much lower |
| CS | 9.67 | N/A | collapsed |
| Base Rate | 30.1% | 30.1% | same |

**Tennis 40.6% HR is BELOW base rate (30.1% + 15pp threshold = 45.1%) — net bearish copy**

## Degradation Analysis

All 3 tags show large degradation — 21-32pp on HR. This is within the 20-40pp "expected" band from
`pitfalls/vectorized_vs_tick.md`, BUT the median PnL going deeply negative (-$102.50 on $100 positions)
reveals a more fundamental issue than simulation friction.

### Root Cause: Missing Consensus Filter

The vectorized discovery sweep measured per-MARKET hit rate where **N qualified traders
(the consensus parameter) ALL had positions**. The tick-by-tick strategy fired on ANY
SINGLE qualified trader's trade.

**Analogy**: The vectorized signal is "5 experts agreed on YES" → high HR.
The tick-by-tick strategy was doing "1 expert said YES" → noise.

### Evidence: HR by Price Regime

The fill price distribution reveals the actual signal:

```
Fill Price    N      HR       Signal
< 0.20       225    12.4%    Very bad (below base rate)
0.20-0.40    462    35.3%    Below base rate
0.40-0.60   1977    49.4%    Near-random (1H base rate)
0.60-0.75    458    64.4%    STRONG positive signal
0.75-0.80    135    85.2%    Exceptional (small sample)
```

High-price entries (0.60-0.75+) are strongly predictive — these are likely the entries
AFTER consensus has already formed (price has moved up). Low-price entries are not.
The vectorized avg_entry_price filter (< 0.75) was accidentally capturing pre-consensus entries
where the price hadn't yet risen — but the HR measure was for the full consensus-triggered market.

### Surprising Finding: Price > HR Correlation

Counterintuitively, HIGHER fill prices → BETTER hit rate. This is consistent with
"informed traders entering late but at consensus price" being the actual signal.
The "buy below 0.75" filter was supposed to avoid chasing — but entries at 0.60-0.80
show the best HR. This should spawn a separate investigation.

## Path Forward

### Immediate Fix Required

1. **Consensus filter in tick-by-tick**: Buffer qualified trades per `condition_id`.
   Fire entry intent only when N distinct qualified traders have entered at qualifying prices
   within a time window. This matches what the vectorized signal measured.

2. **Price floor filter**: Require `trade.price >= 0.55` in addition to `<= 0.75`.
   Low-price entries (<0.40) have <35% HR and are net harmful.

### Spawned Hypothesis

**tag-hr-copy-consensus** [HIGH PRIORITY]:
- Same qualified pool (HR-filtered traders per tag)
- Signal fires on N-trader consensus in same market (e.g. min_consensus=3 or 5)
- Time window for consensus formation (e.g. 4h)
- Expected to restore HR to 60-70% range with fewer but higher-quality signals

This is functionally the same as the original S2 design but with tag-filtered pools instead of
global insider pools.
