# Tag-HR-Copy: Per-Tag Tick-by-Tick Validation Summary

Period: 2025-01-01 to 2026-01-01
Executor: RealisticFillSimulator (calibrated slippage)
Settlement: Enabled (asset_id-based)
Training pool: 6mo trailing (Sep 2025 - Mar 2026)
Capital: $1,000 @ $100/position, max 20 open

## Qualified Pools

| Tag | Pool Size | Base Rate | Threshold HR |
|-----|-----------|-----------|-------------|
| Esports | 319 | 34.9% | 49.9% (base + 15pp) |
| 1H | 131 | 47.3% | 62.3% |
| Tennis | 294 | 30.1% | 45.1% |

## Per-Tag Results

### Esports (mt=50, ep=15, pc=0.75)

| Metric | Value |
|--------|-------|
| Fills | 452 |
| Hit Rate | **45.8%** |
| Base Rate | 34.9% |
| Excess HR | +10.9pp (vs expected +35.7pp vectorized) |
| Median PnL | **-$102.50** |
| Avg Hold | 12.9h |
| Compounding Score | N/A (negative PnL) |

**Verdict: NONE as implemented**

### 1H (mt=50, ep=15, pc=0.75)

| Metric | Value |
|--------|-------|
| Fills | 2,534 |
| Hit Rate | **49.8%** |
| Base Rate | 47.3% |
| Excess HR | +2.5pp (vs expected +27.3pp vectorized) |
| Median PnL | **-$102.50** |
| Avg Hold | 2.0h |
| Compounding Score | N/A |

**Verdict: NONE as implemented — near-random, essentially gambling**

### Tennis (mt=20, ep=15, pc=0.80)

| Metric | Value |
|--------|-------|
| Fills | 271 |
| Hit Rate | **40.6%** |
| Base Rate | 30.1% |
| Excess HR | +10.5pp (vs expected +33.6pp vectorized) |
| Median PnL | **-$102.50** |
| Avg Hold | 15.4h |
| Compounding Score | N/A |

**Verdict: NONE as implemented — excess HR positive but PnL deeply negative**

## Critical Finding: Consensus Gap

The tick-by-tick strategy fired on ANY single qualified trader's BUY YES trade.
The vectorized discovery measured HR of markets where N qualified traders ALL had positions.

This is a structural simulation gap — the vectorized signal captured CONSENSUS formation,
not individual trades. Individual trades from high-HR traders are NOT individually predictive.

### HR by Fill Price (All Tags Combined)

| Fill Price | N | HR | Notes |
|-----------|---|-----|-------|
| < 0.20 | 225 | 12.4% | Below base rate — worst regime |
| 0.20-0.40 | 462 | 35.3% | Below base rate |
| 0.40-0.60 | 1977 | 49.4% | Near 1H base rate |
| 0.60-0.75 | 458 | **64.4%** | Clearly positive signal! |
| 0.75-0.80 | 135 | **85.2%** | Very strong — but only 135 fills |

The `0.60-0.75` and `0.75+` buckets show strong signals. This is the regime the vectorized
discovery was capturing via consensus (multiple traders entering at similar prices).
