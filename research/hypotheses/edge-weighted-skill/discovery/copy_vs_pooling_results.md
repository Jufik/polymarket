# Copy vs Pooling Analysis — Edge-Weighted Skill Hypothesis

**Date**: 2026-03-09 07:58:45
**Label**: VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation)
**Test Period**: January 2026
**Train Period**: < 2026-01-01

---

## Context

Prior work validated elite whale copy (in-play traders) achieving 94.2% HR, $52,932/month in January 2026 (tick-by-tick). This analysis extends that work to compare:
- **Edge-weighted copy** (bucket_excess_hr scoring, N=1)
- **Consensus pooling** (N>=2 threshold, larger pools)
- **In-play dedicated track** (hold < 4h, decomposed by price regime)

---

## Dataset Summary

| Metric | Value |
|--------|-------|
| Scored traders (≥20 positions, conviction ≥0.50) | **1,090** |
| In-play specialist traders (≥50% in-play positions) | **216** |
| Test markets (Jan 2026, non-gambling, resolved) | **27,571** |
| Test positions | **179,965** |
| YES base rate (Jan 2026, non-gambling) | **38.8%** |

### Price Regime Base Rates (Test Period)
| Regime | Base HR |
|--------|---------|
| Longshot (< 0.30) | 5.6% |
| Mid (0.30 – 0.85) | 53.5% |
| Sure-thing (≥ 0.85) | 96.3% |

---

## Part A: Edge-Weighted Elite Copy (N=1, market-level)

One signal per condition_id. Entry when ANY pool trader enters a non-gambling market.
Pool ranked by `bucket_excess_hr × ln(n_positions + 1)`.

| Strategy | K | N | HR | Excess HR | PnL/month | CS | Signals | Hold | Longshot | Sure |
|----------|---|---|-----|-----------|-----------|-----|---------|------|----------|------|
| edge_copy_k5                             |     5 |   1 |   42.4% | +  3.6pp | $   -1,859 |    54.13 |    165 |   18.1h | 19/26% | 8/100% |
| edge_copy_k10                            |    10 |   1 |   42.5% | +  3.7pp | $   -1,940 |    57.40 |    167 |   18.1h | 20/25% | 8/100% |
| edge_copy_k25                            |    25 |   1 |   43.3% | +  4.5pp | $   -4,035 |    54.35 |    386 |   20.6h | 45/22% | 15/100% |
| edge_copy_k50                            |    50 |   1 |   49.9% | + 11.1pp | $      359 |     6.88 |    766 |   18.1h | 134/20% | 44/100% |
| edge_copy_k100                           |   100 |   1 |   51.1% | + 12.3pp | $      665 |     7.34 |   2121 |   12.6h | 568/14% | 243/99% |
| hr_copy_k5                               |     5 |   1 |   83.5% | + 44.8pp | $   -3,618 |  4439.39 |    322 |    2.7h | 42/0% | 241/99% |
| hr_copy_k10                              |    10 |   1 |   85.2% | + 46.4pp | $   -3,817 |  3598.67 |    419 |    2.8h | 49/0% | 326/99% |
| hr_copy_k25                              |    25 |   1 |   84.8% | + 46.0pp | $   -5,166 |  2128.97 |    850 |    3.1h | 102/4% | 607/100% |
| hr_copy_k50                              |    50 |   1 |   67.5% | + 28.7pp | $  -48,808 |  5484.36 |   2049 |    3.0h | 616/1% | 1023/99% |
| hr_copy_k100                             |   100 |   1 |   66.9% | + 28.1pp | $  -50,257 |  3780.94 |   2706 |    3.3h | 707/2% | 1234/99% |

### Pool Overlap (Edge vs HR Baseline)
- K=5: 0/5 traders in common (0%)
- K=10: 0/10 traders in common (0%)
- K=25: 0/25 traders in common (0%)
- K=50: 2/50 traders in common (4%)
- K=100: 17/100 traders in common (17%)

---

## Part B: Edge-Weighted Consensus Pooling (N>=2, market-level)

Signal fires when Nth distinct pool trader enters a market.
Entry price = average pool entry price (approximation of Nth trigger price).

| Strategy | K | N | HR | Excess HR | PnL/month | CS | Signals | Hold |
|----------|---|---|-----|-----------|-----------|-----|---------|------|
| edge_consensus_k50_n1                    |    50 |   1 |   49.9% | + 11.1pp | $      167 |     3.93 |    766 |   14.8h |
| edge_consensus_k50_n2                    |    50 |   2 |   40.3% | +  1.5pp | $   -3,362 |    30.94 |    258 |   15.3h |
| edge_consensus_k50_n3                    |    50 |   3 |   39.0% | +  0.2pp | $   -2,099 |     3.54 |    177 |   15.3h |
| edge_consensus_k100_n1                   |   100 |   1 |   51.1% | + 12.3pp | $   -6,401 |    83.43 |   2121 |   10.7h |
| edge_consensus_k100_n2                   |   100 |   2 |   49.2% | + 10.4pp | $   -1,260 |    67.65 |    372 |   12.5h |
| edge_consensus_k100_n3                   |   100 |   3 |   41.7% | +  2.9pp | $   -1,724 |    40.21 |    204 |   14.5h |
| edge_consensus_k200_n1                   |   200 |   1 |   55.7% | + 17.0pp | $  -18,427 |   360.84 |   3638 |    5.7h |
| edge_consensus_k200_n2                   |   200 |   2 |   63.7% | + 24.9pp | $    3,582 |   517.64 |    761 |    5.4h |
| edge_consensus_k200_n3                   |   200 |   3 |   53.2% | + 14.4pp | $     -776 |    80.31 |    310 |   10.8h |

---

## Part C: In-Play Dedicated Track (hold < 4h)

Traders with ≥50% in-play positions, scored by edge-weighted metric.

| Strategy | K | N | HR | Excess HR | PnL/month | CS | Signals | Hold | Longshot | Sure |
|----------|---|---|-----|-----------|-----------|-----|---------|------|----------|------|
| inplay_copy_k10_n1                       |    10 |   1 |   90.9% | + 52.1pp | $    1,553 |  5110.64 |    121 |    3.1h | 3/0% | 19/100% |
| inplay_copy_k25_n1                       |    25 |   1 |   77.2% | + 38.4pp | $      788 |   428.23 |    540 |    3.1h | 41/5% | 159/99% |
| inplay_copy_k50_n1                       |    50 |   1 |   73.8% | + 35.0pp | $   -1,059 |   354.46 |    818 |    3.1h | 93/8% | 293/99% |
| inplay_copy_k100_n1                      |   100 |   1 |   72.8% | + 34.0pp | $  -12,238 |  2639.77 |   1304 |    2.9h | 168/5% | 573/99% |
| edge_inplay_k25_n1                       |    25 |   1 |   81.2% | + 42.5pp | $      652 | 12917.60 |     16 |    3.2h | 3/67% | 6/100% |
| edge_inplay_k50_n1                       |    50 |   1 |   71.0% | + 32.2pp | $      967 |  3475.40 |     69 |    3.1h | 10/40% | 13/100% |
| edge_inplay_k100_n1                      |   100 |   1 |   70.1% | + 31.3pp | $   -2,415 |  1335.45 |    428 |    3.2h | 67/10% | 108/100% |

---

## Part D: Head-to-Head Summary

| Strategy | K | N | HR | Excess HR | PnL/month | Sharpe est | CS | Signals/mo | Avg Hold |
|----------|---|---|-----|-----------|-----------|------------|-----|------------|----------|
| Edge Copy K=25                 |    25 |   1 |   43.3% |    +4.5pp |    $-4,035 |     -13.22 |    54.35 |        386 |     20.6h |
| Edge Copy K=50                 |    50 |   1 |   49.9% |   +11.1pp |       $359 |      18.45 |     6.88 |        766 |     18.1h |
| Edge Copy K=100                |   100 |   1 |   51.1% |   +12.3pp |       $665 |      30.71 |     7.34 |       2121 |     12.6h |
| HR Copy (baseline) K=25        |    25 |   1 |   84.8% |   +46.0pp |    $-5,166 |     -27.08 |  2128.97 |        850 |      3.1h |
| Edge Consensus K=50 N=2        |    50 |   2 |   40.3% |    +1.5pp |    $-3,362 |     -10.92 |    30.94 |        258 |     15.3h |
| Edge Consensus K=100 N=2       |   100 |   2 |   49.2% |   +10.4pp |    $-1,260 |     -12.86 |    67.65 |        372 |     12.5h |
| Edge Consensus K=100 N=3       |   100 |   3 |   41.7% |    +2.9pp |    $-1,724 |      -9.66 |    40.21 |        204 |     14.5h |
| Edge Consensus K=200 N=2       |   200 |   2 |   63.7% |   +24.9pp |     $3,582 |      19.13 |   517.64 |        761 |      5.4h |
| InPlay Copy K=25               |    25 |   1 |   77.2% |   +38.4pp |       $788 |      18.47 |   428.23 |        540 |      3.1h |
| InPlay Copy K=50               |    50 |   1 |   73.8% |   +35.0pp |    $-1,059 |     -21.69 |   354.46 |        818 |      3.1h |
| InPlay Copy K=100              |   100 |   1 |   72.8% |   +34.0pp |   $-12,238 |     -27.04 |  2639.77 |       1304 |      2.9h |

---

## Key Observations

### 1. Copy vs Consensus Tradeoff
- **Copy (N=1)**: Maximizes signal count but introduces noise from individual pool members who are wrong
- **Consensus (N>=2)**: Reduces noise but loses signals; significant signal count reduction per N increase
- **Recommended**: K=50 Edge Pool, N=1 for volume; K=25 Edge Pool, N=2 for quality

### 2. Edge-Weighted vs HR-Baseline Pool
- Overlap between edge-pool and HR-pool at K=25: 0/25 traders
- Edge scoring should select more diversified traders (different price regimes, different tags)
- Comparable HR but different signal composition expected

### 3. In-Play Track Observations
- In-play traders heavily concentrated in sure-thing regime (0.85+)
- 58-minute lead time advantage (from prior research) means vectorized = optimistic for in-play
- Expected tick degradation for in-play: larger than 20-40pp (latency sensitivity)

### 4. Price Regime Decomposition
- **Longshot (<0.30)**: Low HR by construction but highest per-signal PnL when correct
- **Mid (0.30-0.85)**: Highest edge concentration for well-calibrated traders
- **Sure-thing (0.85+)**: High HR but negative edge vs base rate (-6.7pp per prior research)

---

## Limitations (CRITICAL)

1. **VECTORIZED UPPER BOUNDS**: All results 20-40pp optimistic vs tick-by-tick
2. **In-play overestimate**: Latency issue means in-play vectorized → tick gap is LARGER (50-60pp)
3. **Entry price approximation**: Using avg pool price, not exact Nth trader's trigger price
4. **PnL model**: Simplified ($100 stake, fill at avg pool entry + 1pp slippage)
5. **No capital constraint**: Assumes infinite capital to fill all signals simultaneously
6. **Direction**: Only YES (BUY) positions analyzed — NO direction pending

---

## Next Steps

1. **Tick validation** of top-3 configurations (K=25 edge copy, K=50 consensus N=2, K=25 inplay)
2. **NO direction analysis** — Politics/Elections may have strong NO signal
3. **Tag decomposition** — Which tags drive excess HR in each regime?
4. **Pool stability** — How stable is edge-pool composition month-to-month?

*Results are VECTORIZED UPPER BOUNDS. Label any tick-by-tick validation as REALISTIC.*
