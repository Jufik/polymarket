# Copy vs Pooling v2 — 3-Month Analysis with Causal Fill Price

**Date**: 2026-03-09 08:47:18
**Label**: VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation; more for in-play)
**Training**: resolved before 2025-11-01
**Test**: Nov2025, Dec2025, Jan2026

---

## Bug Fixes from v1

1. **Consensus fill price (critical)**: Previously used `AVG(entry_price)` across ALL pool traders
   in a market. This is look-ahead bias — traders entering AFTER signal trigger were included.
   **Fix**: Fill price = entry price of the **Nth unique pool trader** (chronologically ordered).
   For N=2 this is the 2nd trader's actual price; for N=1 it's the 1st trader's price.

2. **Elections tag excluded**: Elections tag removed from tag assignment per Round 1 review.

3. **Training period**: Now uses positions resolved before 2025-11-01
   (previously used all data before 2026-01-01, overlapping with Nov/Dec test months).

---

## Dataset

| Metric | Value |
|--------|-------|
| Scored traders (≥20 pos, conviction ≥0.50, train period) | **634** |
| In-play specialists (≥50% in-play) | **95** |
| Test months | **Nov2025, Dec2025, Jan2026** |

### Per-Month Base Rates (YES, non-gambling, non-Elections)

| Month | Overall | Longshot | Mid | Sure-thing |
|-------|---------|----------|-----|------------|
| Nov2025 | 38.5% | 6.3% | 55.5% | 95.7% |
| Dec2025 | 39.5% | 6.5% | 54.0% | 96.3% |
| Jan2026 | 38.8% | 5.6% | 53.5% | 96.3% |

---

## Part A: Edge-Weighted Copy (N=1)

Pool ranked by `bucket_excess_hr × ln(n_positions + 1)`. Fill price = 1st pool trader's actual entry.

| Strategy | K | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals | Avg Hold |
|----------|---|--------|---------------|-------------------|-------------|----------|
| edge_copy_k5                           |     5 |   47.9% |   +9.0pp | $-727 / $+92 / $-575 |      21 |   10.2h |
| edge_copy_k10                          |    10 |   35.7% |   -3.2pp | $-858 / $-408 / $-651 |      29 |   21.4h |
| edge_copy_k25                          |    25 |   70.2% |  +31.2pp | $+249 / $-212 / $+2,486 |     128 |    7.1h |
| edge_copy_k50                          |    50 |   57.2% |  +18.2pp | $-4,045 / $-473 / $+181 |     358 |   12.5h |
| edge_copy_k100                         |   100 |   56.3% |  +17.3pp | $-4,201 / $-322 / $+1,079 |     793 |   13.1h |
| hr_copy_k25                            |    25 |   81.0% |  +42.0pp | $+7,078 / $+377 / $+794 |     202 |    4.9h |
| hr_copy_k100                           |   100 |   69.3% |  +30.4pp | $-862 / $-6,885 / $-11,518 |     975 |    4.7h |

---

## Part B: Consensus Pooling (N>=2, causal Nth price)

Fill price = the Nth pool trader's actual entry price (no look-ahead).

| Strategy | K | N | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals | Avg Hold | Avg Fill |
|----------|---|---|--------|---------------|-------------------|-------------|----------|----------|
| edge_consensus_k50_n1                  |    50 |   1 |   57.2% |  +18.2pp | $-4,045 / $-473 / $+181 |     358 |   12.5h | 0.547 |
| edge_consensus_k50_n2                  |    50 |   2 |   68.0% |  +29.0pp | $+628 / $-412 / $+158 |      12 |   12.3h | 0.628 |
| edge_consensus_k50_n3                  |    50 |   3 |  100.0% |  +60.5pp | N/A / $+20 / N/A |       1 |    2.6h | 0.832 |
| edge_consensus_k100_n1                 |   100 |   1 |   56.3% |  +17.3pp | $-4,201 / $-322 / $+1,079 |     793 |   13.1h | 0.534 |
| edge_consensus_k100_n2                 |   100 |   2 |   64.8% |  +25.9pp | $+1,937 / $-468 / $-498 |      59 |    6.7h | 0.582 |
| edge_consensus_k100_n3                 |   100 |   3 |   73.8% |  +34.9pp | $+240 / $+111 / $-132 |       5 |   70.0h | 0.583 |
| edge_consensus_k200_n1                 |   200 |   1 |   52.4% |  +13.5pp | $-12,233 / $-1,500 / $-15,283 |    1304 |   15.9h | 0.516 |
| edge_consensus_k200_n2                 |   200 |   2 |   61.6% |  +22.7pp | $+879 / $+3,852 / $-2,435 |     176 |    7.9h | 0.549 |
| edge_consensus_k200_n3                 |   200 |   3 |   70.7% |  +31.8pp | $+433 / $+42 / $+169 |      25 |   13.6h | 0.594 |

---

## Part C: In-Play Dedicated Track (hold < 4h)

| Strategy | K | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals |
|----------|---|--------|---------------|-------------------|-------------|
| inplay_copy_k10_n1                     |    10 |   76.7% |  +37.7pp | $-254 / $-292 / $+151 |      34 |
| inplay_copy_k25_n1                     |    25 |   64.6% |  +25.7pp | $-1,484 / $-1,113 / $-1,156 |      62 |
| inplay_copy_k50_n1                     |    50 |   78.2% |  +39.2pp | $+697 / $-3,589 / $-7,306 |     270 |
| inplay_copy_k100_n1                    |   100 |   61.1% |  +22.2pp | $-9,613 / $-13,268 / $-14,135 |     467 |
| edge_inplay_k25_n1                     |    25 |   82.6% |  +43.6pp | $+189 / $+551 / $+384 |      21 |
| edge_inplay_k50_n1                     |    50 |   70.1% |  +31.2pp | $+154 / $+672 / $+498 |      53 |
| edge_inplay_k100_n1                    |   100 |   75.4% |  +36.5pp | $+1,478 / $+20 / $+2,053 |     165 |

---

## Part D: Head-to-Head Summary (3-month averages, UPPER BOUNDS)

| Strategy | K | N | HR% | Excess HR | PnL/mo avg | PnL std | CS avg | Sigs/mo | Hold |
|----------|---|---|-----|-----------|-----------|---------|--------|---------|------|
| edge_copy_k50                          |    50 |   1 |   57.2% |  +18.2pp | $  -1,446 | ±$ 1,858 |  98.74 |     358 |  12.5h |
| edge_copy_k100                         |   100 |   1 |   56.3% |  +17.3pp | $  -1,148 | ±$ 2,234 |  68.09 |     793 |  13.1h |
| hr_copy_k25                            |    25 |   1 |   81.0% |  +42.0pp | $   2,750 | ±$ 3,065 | 2783.66 |     202 |   4.9h |
| edge_consensus_k200_n2                 |   200 |   2 |   61.6% |  +22.7pp | $     765 | ±$ 2,568 | 957.67 |     176 |   7.9h |
| edge_consensus_k100_n2                 |   100 |   2 |   64.8% |  +25.9pp | $     324 | ±$ 1,141 | 1679.31 |      59 |   6.7h |
| edge_consensus_k50_n2                  |    50 |   2 |   68.0% |  +29.0pp | $     125 | ±$   425 | 2961.00 |      12 |  12.3h |
| inplay_copy_k10_n1                     |    10 |   1 |   76.7% |  +37.7pp | $    -132 | ±$   201 | 2194.58 |      34 |   2.9h |
| inplay_copy_k25_n1                     |    25 |   1 |   64.6% |  +25.7pp | $  -1,251 | ±$   166 | 4459.66 |      62 |   2.8h |
| edge_inplay_k25_n1                     |    25 |   1 |   82.6% |  +43.6pp | $     375 | ±$   148 | 5823.35 |      21 |   3.2h |
| edge_inplay_k50_n1                     |    50 |   1 |   70.1% |  +31.2pp | $     441 | ±$   215 | 2235.46 |      53 |   3.2h |

---

## Per-Month Detail

### Edge Copy K=50 (best volume strategy)
- **Nov2025**: 433 signals, HR=52.7% (+14.1pp), PnL=$-4,045, fill=0.518, hold=14.0h
- **Dec2025**: 290 signals, HR=51.7% (+12.2pp), PnL=$-473, fill=0.510, hold=13.5h
- **Jan2026**: 351 signals, HR=67.2% (+28.4pp), PnL=$+181, fill=0.614, hold=10.1h

### Consensus K=200 N=2 (best consensus candidate)
- **Nov2025**: 223 signals, HR=62.8% (+24.3pp), PnL=$+879, fill=0.564 (Nth trigger price), hold=6.0h
- **Dec2025**: 142 signals, HR=63.4% (+23.8pp), PnL=$+3,852, fill=0.536 (Nth trigger price), hold=9.6h
- **Jan2026**: 162 signals, HR=58.6% (+19.9pp), PnL=$-2,435, fill=0.546 (Nth trigger price), hold=8.2h

### In-Play K=25 N=1
- **Nov2025**: 78 signals, HR=65.4% (+26.9pp), PnL=$-1,484, sure=38(97%), hold=2.8h
- **Dec2025**: 53 signals, HR=64.1% (+24.6pp), PnL=$-1,113, sure=21(100%), hold=2.8h
- **Jan2026**: 56 signals, HR=64.3% (+25.5pp), PnL=$-1,156, sure=22(100%), hold=2.7h

---

## Key Observations

### Fill Price Bug Impact
The consensus fill price fix changes the PnL picture for N>=2 strategies.
v1 used AVG(entry_price) across all pool traders — including traders who entered AFTER
the signal fired. Traders who enter later typically get better (lower) prices on losing
markets and worse (higher) prices on winning markets, creating artificial optimism.
The causal Nth-price should give more conservative PnL estimates.

### 3-Month Stability
PnL variance across months (std/mean ratio) indicates strategy robustness:
- High variance = regime-dependent, may be luck in single month
- Low variance = consistent signal worth validating tick-by-tick

### Recommended Next Steps
1. **Tick-by-tick validation**: Top 3 strategies from this analysis
2. **NO-direction sweep**: Add NO signal analysis (separate task #13)
3. **Pool re-ranking**: Monthly re-rank using trailing 6-month data

---

## Limitations (CRITICAL)

1. **VECTORIZED UPPER BOUNDS**: 20-40pp optimistic vs tick-by-tick
2. **In-play gap LARGER**: 50-60pp expected for in-play strategies (latency)
3. **Fill approximation**: Nth trader's recorded entry_price, not actual market fill
4. **YES-only**: NO direction not included (Task #13)
5. **Single-market, no capital constraint**: Infinite capital assumed

*All results are UPPER BOUNDS. Tick validation required before any deployment decision.*
