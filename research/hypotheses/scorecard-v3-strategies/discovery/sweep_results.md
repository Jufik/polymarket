# v3 Vectorized Sweep Results

> **ALL VALUES ARE UPPER BOUNDS** — vectorized backtests 20-40pp optimistic vs tick.

- Train: resolved < 2025-07-01
- Test: 2025-07-01 → 2026-03-01 (8 months)

## Pool Sizes & v2/v3 Overlap

| Leg | v2 Pool | v3 Pool (BEH gate) | Jaccard |
|-----|---------|-------------------|---------|
| Sports YES K=25 | 25 | 25 | 0.52 |
| Politics YES K=100 | 100 | 100 | 0.38 |
| Crypto YES K=50 | 50 | 37 | 0.28 |
| Politics NO K=100 | — | 100 | — |
| Sports NO K=50 | — | 50 | — |

## Sports YES K=25

### N=1

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 585 | 605 | — |
| Hit Rate | 65.0% | 68.8% | +3.8% |
| Excess HR | +31.7% | +35.5% | +3.8% |
| Base Rate | 33.3% | 33.3% | — |
| Avg PnL/trade | +0.0108 | +0.0241 | +0.0133 |
| Med hold (h) | 3.5 | 3.9 | — |
| Avg signal price | 0.64 | 0.66 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 69 | 65.2% | +0.0026 |
  | 2025-08 | 95 | 73.7% | +0.0492 |
  | 2025-09 | 89 | 67.4% | -0.0223 |
  | 2025-10 | 57 | 73.7% | +0.0835 |
  | 2025-11 | 57 | 71.9% | +0.0213 |
  | 2025-12 | 70 | 67.1% | +0.0078 |
  | 2026-01 | 89 | 62.9% | +0.0474 |
  | 2026-02 | 79 | 69.6% | +0.0122 |

### N=2

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 29 | 30 | — |
| Hit Rate | 82.8% | 86.7% | +3.9% |
| Excess HR | +49.5% | +53.4% | +3.9% |
| Base Rate | 33.3% | 33.3% | — |
| Avg PnL/trade | +0.1158 | +0.1067 | -0.0091 |
| Med hold (h) | 3.2 | 3.4 | — |
| Avg signal price | 0.71 | 0.76 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 2 | 50.0% | -0.0246 |
  | 2025-08 | 11 | 90.9% | +0.1405 |
  | 2025-09 | 2 | 50.0% | +0.0163 |
  | 2025-10 | 7 | 100.0% | +0.1373 |
  | 2025-11 | 2 | 100.0% | +0.2953 |
  | 2025-12 | 4 | 75.0% | +0.0204 |
  | 2026-01 | 2 | 100.0% | +0.0200 |

### N=3

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 1 | 2 | — |
| Hit Rate | 100.0% | 100.0% | +0.0% |
| Excess HR | +66.7% | +66.7% | +0.0% |
| Base Rate | 33.3% | 33.3% | — |
| Avg PnL/trade | +0.1404 | +0.0919 | -0.0485 |
| Med hold (h) | 3.2 | 3.0 | — |
| Avg signal price | 0.86 | 0.91 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-08 | 1 | 100.0% | +0.0434 |
  | 2025-09 | 1 | 100.0% | +0.1404 |

### N=5

v2: no signals after filters, v3: no signals after filters

## Politics YES K=100 (max_price=0.80)

### N=1

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 631 | 373 | — |
| Hit Rate | 24.6% | 28.1% | +3.6% |
| Excess HR | +5.7% | +9.3% | +3.6% |
| Base Rate | 18.8% | 18.8% | — |
| Avg PnL/trade | -0.0583 | -0.0360 | +0.0223 |
| Med hold (h) | 119.7 | 169.7 | — |
| Avg signal price | 0.30 | 0.32 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 28 | 57.1% | +0.1256 |
  | 2025-08 | 54 | 24.1% | -0.0601 |
  | 2025-09 | 30 | 33.3% | +0.0230 |
  | 2025-10 | 32 | 40.6% | +0.0142 |
  | 2025-11 | 44 | 29.5% | -0.0112 |
  | 2025-12 | 34 | 50.0% | +0.0355 |
  | 2026-01 | 94 | 8.5% | -0.1300 |
  | 2026-02 | 57 | 26.3% | -0.0585 |

### N=2

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 112 | 56 | — |
| Hit Rate | 28.6% | 25.0% | -3.6% |
| Excess HR | +9.8% | +6.2% | -3.6% |
| Base Rate | 18.8% | 18.8% | — |
| Avg PnL/trade | -0.0228 | -0.0360 | -0.0132 |
| Med hold (h) | 160.1 | 224.5 | — |
| Avg signal price | 0.31 | 0.29 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 5 | 20.0% | -0.2263 |
  | 2025-08 | 5 | 60.0% | +0.2204 |
  | 2025-09 | 3 | 0.0% | -0.1548 |
  | 2025-10 | 6 | 50.0% | +0.0723 |
  | 2025-11 | 10 | 10.0% | -0.1210 |
  | 2025-12 | 5 | 80.0% | +0.3444 |
  | 2026-01 | 14 | 0.0% | -0.1911 |
  | 2026-02 | 8 | 25.0% | +0.0263 |

### N=3

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 30 | 15 | — |
| Hit Rate | 26.7% | 20.0% | -6.7% |
| Excess HR | +7.8% | +1.2% | -6.7% |
| Base Rate | 18.8% | 18.8% | — |
| Avg PnL/trade | -0.0821 | -0.1167 | -0.0346 |
| Med hold (h) | 122.1 | 470.1 | — |
| Avg signal price | 0.35 | 0.32 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 2 | 0.0% | -0.3062 |
  | 2025-09 | 2 | 0.0% | -0.1887 |
  | 2025-10 | 2 | 50.0% | +0.0771 |
  | 2025-11 | 1 | 0.0% | -0.3091 |
  | 2026-01 | 6 | 0.0% | -0.2379 |
  | 2026-02 | 2 | 100.0% | +0.4113 |

### N=5

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 3 | 1 | — |
| Hit Rate | 0.0% | 0.0% | +0.0% |
| Excess HR | -18.8% | -18.8% | +0.0% |
| Base Rate | 18.8% | 18.8% | — |
| Avg PnL/trade | -0.3111 | -0.2250 | +0.0861 |
| Med hold (h) | 82.1 | 38.3 | — |
| Avg signal price | 0.31 | 0.23 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-09 | 1 | 0.0% | -0.2250 |

## Crypto YES K=50 (max_price=0.65)

### N=1

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 113 | 179 | — |
| Hit Rate | 14.2% | 16.8% | +2.6% |
| Excess HR | -0.9% | +1.7% | +2.6% |
| Base Rate | 15.1% | 15.1% | — |
| Avg PnL/trade | -0.1247 | -0.1043 | +0.0204 |
| Med hold (h) | 82.1 | 89.1 | — |
| Avg signal price | 0.27 | 0.27 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 24 | 37.5% | -0.0150 |
  | 2025-08 | 39 | 15.4% | -0.1457 |
  | 2025-09 | 32 | 9.4% | -0.1331 |
  | 2025-10 | 13 | 30.8% | +0.0033 |
  | 2025-11 | 20 | 25.0% | +0.0148 |
  | 2025-12 | 14 | 7.1% | -0.2709 |
  | 2026-01 | 25 | 8.0% | -0.1760 |
  | 2026-02 | 12 | 0.0% | -0.0421 |

### N=2

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 11 | 19 | — |
| Hit Rate | 9.1% | 5.3% | -3.8% |
| Excess HR | -6.0% | -9.8% | -3.8% |
| Base Rate | 15.1% | 15.1% | — |
| Avg PnL/trade | -0.1436 | -0.2657 | -0.1221 |
| Med hold (h) | 91.8 | 362.5 | — |
| Avg signal price | 0.23 | 0.32 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 1 | 0.0% | -0.2847 |
  | 2025-08 | 3 | 33.3% | -0.0885 |
  | 2025-09 | 6 | 0.0% | -0.2850 |
  | 2025-10 | 1 | 0.0% | -0.4700 |
  | 2025-11 | 1 | 0.0% | -0.0670 |
  | 2025-12 | 1 | 0.0% | -0.3100 |
  | 2026-01 | 6 | 0.0% | -0.3234 |

### N=3

| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |
|--------|------------|--------------|-------|
| Signals | 2 | 6 | — |
| Hit Rate | 0.0% | 0.0% | +0.0% |
| Excess HR | -15.1% | -15.1% | +0.0% |
| Base Rate | 15.1% | 15.1% | — |
| Avg PnL/trade | -0.1063 | -0.1870 | -0.0807 |
| Med hold (h) | 137.1 | 180.6 | — |
| Avg signal price | 0.11 | 0.19 | — |

**v3 Monthly Breakdown:**

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-09 | 4 | 0.0% | -0.2125 |
  | 2025-10 | 1 | 0.0% | -0.0694 |
  | 2026-01 | 1 | 0.0% | -0.2028 |

## Politics NO K=100 (hold>=24h)

### N=1

- Signals: 486
- HR: 75.7% (++2.7% vs 73.0% base)
- PnL: -0.0135/trade
- Med hold: 217.5h
- Avg signal price: 0.77

Monthly:

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 23 | 73.9% | -0.0319 |
  | 2025-08 | 48 | 77.1% | -0.0315 |
  | 2025-09 | 67 | 71.6% | -0.0519 |
  | 2025-10 | 74 | 71.6% | -0.0210 |
  | 2025-11 | 52 | 80.8% | +0.0188 |
  | 2025-12 | 48 | 54.2% | -0.0895 |
  | 2026-01 | 107 | 88.8% | +0.0535 |
  | 2026-02 | 67 | 74.6% | -0.0252 |

### N=2

- Signals: 66
- HR: 81.8% (++8.8% vs 73.0% base)
- PnL: +0.0174/trade
- Med hold: 477.7h
- Avg signal price: 0.80

Monthly:

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 2 | 50.0% | -0.3695 |
  | 2025-08 | 10 | 90.0% | +0.0259 |
  | 2025-09 | 6 | 83.3% | -0.0053 |
  | 2025-10 | 13 | 84.6% | +0.0315 |
  | 2025-11 | 6 | 83.3% | +0.0349 |
  | 2025-12 | 4 | 50.0% | -0.0402 |
  | 2026-01 | 15 | 93.3% | +0.0797 |
  | 2026-02 | 10 | 70.0% | +0.0006 |

### N=3

- Signals: 15
- HR: 93.3% (++20.3% vs 73.0% base)
- PnL: +0.0216/trade
- Med hold: 433.9h
- Avg signal price: 0.91

Monthly:

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-08 | 2 | 100.0% | +0.0243 |
  | 2025-09 | 3 | 100.0% | +0.0259 |
  | 2025-10 | 2 | 100.0% | +0.0208 |
  | 2025-11 | 2 | 100.0% | +0.0612 |
  | 2026-01 | 4 | 100.0% | +0.0257 |
  | 2026-02 | 2 | 50.0% | -0.0343 |

## Sports NO K=50 (hold>=24h)

_In-play dominated — thin signal count expected_

### N=1

- Signals: 6
- HR: 50.0% (++1.6% vs 48.4% base)
- PnL: -0.0332/trade
- Med hold: 88.3h
- Avg signal price: 0.53

Monthly:

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 1 | 0.0% | -0.0101 |
  | 2025-09 | 1 | 0.0% | -0.5882 |
  | 2025-10 | 1 | 100.0% | +0.1872 |
  | 2025-11 | 1 | 100.0% | +0.2400 |
  | 2025-12 | 1 | 100.0% | +0.0351 |
  | 2026-02 | 1 | 0.0% | -0.0633 |

### N=2

Error: no signals after filters

### N=3

Error: no signals after filters

## In-Play Track: Sports YES K=25 N=1 (hold<4h)

_Dedicated in-play sub-track. Sub-second WebSocket delivery means no latency penalty in production._
_These traders enter during live events — high HR but fill model gap may be significant._

- Signals: 320
- HR: 67.5% (++34.2% vs 33.3% base)
- PnL: +0.0130/trade
- Med hold: 2.9h
- Avg signal price: 0.66

Monthly:

  | Month | Signals | HR | PnL/trade |
  |-------|---------|----|-----------| 
  | 2025-07 | 39 | 71.8% | -0.0061 |
  | 2025-08 | 57 | 77.2% | +0.0559 |
  | 2025-09 | 54 | 68.5% | -0.0139 |
  | 2025-10 | 31 | 61.3% | +0.0222 |
  | 2025-11 | 34 | 73.5% | +0.0521 |
  | 2025-12 | 35 | 51.4% | -0.0490 |
  | 2026-01 | 29 | 58.6% | +0.0917 |
  | 2026-02 | 41 | 68.3% | -0.0356 |

## Summary — Best Configs (v3)

| Leg | N | Signals/8mo | HR | Excess HR | PnL/trade |
|-----|---|------------|----|-----------|-----------| 
| Sports YES v3 K=25 | 2 | 30 | 86.7% | +53.4% | +0.1067 |
| Politics YES v3 K=100 p≤0.80 | 1 | 373 | 28.1% | +9.3% | -0.0360 |
| Crypto YES v3 K=50 p≤0.65 | 1 | 179 | 16.8% | +1.7% | -0.1043 |
| Politics NO v3 K=100 h≥24h | 3 | 15 | 93.3% | +20.3% | +0.0216 |
| Sports NO v3 K=50 h≥24h | 1 | 6 | 50.0% | +1.6% | -0.0332 |
| Sports YES In-Play v3 | 1 | 320 | 67.5% | +34.2% | +0.0130 |

## Notes

- BEH gate (`bucket_excess_hr >= 0.02`): removes traders whose 'skill' is near-certainty bets
- Causal fill price: Nth trader's chronological entry price (NOT avg across pool)
- Phantom test signal filter: `first_trade >= test_start` (causal)
- NO direction hold filter: `hold >= 24h` removes in-play contamination
- In-play track: sub-second WebSocket delivery in production (no latency penalty)
- v2 tick reference: Sports YES K=25 N=3 → +39.8pp tick, Sharpe=11.94 (6-month window)
