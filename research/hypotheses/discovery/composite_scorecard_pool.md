# Composite Scorecard Pool — Discovery Results

**Date**: 2026-03-07
**Status**: VECTORIZED UPPER BOUNDS — tick-by-tick validation required
**Train period**: before 2025-07-01
**Test period**: 2025-07-01 onwards

## Scorecard Composition

| Signal | Weight | Description |
|--------|--------|-------------|
| excess_hr | 0.45 | HR vs tag-specific base rate (IC=0.744) |
| consistency_sharpe | 0.25 | Monthly HR Sharpe (≥6 months) |
| avg_edge_usd | 0.15 | Average realized PnL per position |
| bucket_excess_hr | 0.15 | HR vs population in same 10pp price bucket |

All components percentile-rank normalized to [0,1] within each tag.
Composite = weighted sum. Compared against HR-only baseline.

## Pool Sweep Results (Test Period)

| Tag | K | N | Ranking | N Signals | HR | Excess HR | Med Hold h | CS |
|-----|---|---|---------|-----------|-----|-----------|------------|-----|
| Crypto | 50 | 2 | hr_only | 516 | 0.985 | +72.0pp | 3h | 414.4 |
| Crypto | 100 | 2 | hr_only | 854 | 0.940 | +67.5pp | 3h | 365.0 |
| Sports | 50 | 5 | hr_only | 1 | 1.000 | +73.9pp | 4h | 327.9 |
| Crypto | 100 | 3 | hr_only | 256 | 0.934 | +66.9pp | 4h | 268.4 |
| Crypto | 200 | 3 | hr_only | 1784 | 0.753 | +48.9pp | 3h | 191.0 |
| Crypto | 100 | 2 | composite | 3109 | 0.747 | +48.2pp | 3h | 185.9 |
| Crypto | 100 | 3 | composite | 1041 | 0.743 | +47.8pp | 3h | 182.6 |
| Crypto | 200 | 3 | composite | 1866 | 0.742 | +47.7pp | 3h | 182.4 |
| Politics | 25 | 5 | composite | 11 | 0.909 | +62.0pp | 6h | 153.7 |
| Crypto | 200 | 5 | composite | 440 | 0.761 | +49.7pp | 4h | 148.0 |
| Crypto | 200 | 2 | hr_only | 4008 | 0.761 | +49.6pp | 4h | 147.7 |
| Crypto | 200 | 5 | hr_only | 411 | 0.759 | +49.4pp | 4h | 146.6 |
| Crypto | 200 | 2 | composite | 4145 | 0.754 | +49.0pp | 4h | 143.8 |
| Politics | 50 | 5 | hr_only | 7 | 1.000 | +71.1pp | 9h | 134.8 |
| Sports | 50 | 3 | hr_only | 39 | 0.744 | +48.3pp | 5h | 111.9 |
| Sports | 50 | 2 | hr_only | 180 | 0.717 | +45.6pp | 5h | 99.8 |
| Politics | 100 | 5 | composite | 232 | 0.914 | +62.5pp | 10h | 93.7 |
| Sports | 25 | 3 | composite | 125 | 0.696 | +43.5pp | 5h | 91.0 |
| Sports | 50 | 5 | composite | 130 | 0.685 | +42.4pp | 5h | 86.3 |
| Sports | 100 | 5 | composite | 278 | 0.680 | +41.9pp | 5h | 84.3 |
| Sports | 100 | 5 | hr_only | 84 | 0.667 | +40.6pp | 5h | 79.1 |
| Politics | 200 | 5 | composite | 1227 | 0.865 | +57.6pp | 11h | 72.3 |
| Sports | 200 | 5 | hr_only | 419 | 0.647 | +38.6pp | 5h | 71.5 |
| Crypto | 100 | 5 | composite | 178 | 0.837 | +57.2pp | 11h | 71.5 |
| Sports | 200 | 5 | composite | 542 | 0.646 | +38.5pp | 5h | 71.2 |
| Sports | 25 | 2 | hr_only | 3 | 1.000 | +73.9pp | 19h | 69.0 |
| Sports | 100 | 3 | composite | 843 | 0.670 | +41.0pp | 6h | 67.1 |
| Sports | 50 | 3 | composite | 554 | 0.691 | +43.1pp | 7h | 63.6 |
| Politics | 100 | 3 | composite | 791 | 0.872 | +58.3pp | 13h | 62.8 |
| Crypto | 100 | 5 | hr_only | 40 | 0.925 | +66.0pp | 17h | 61.5 |
| Politics | 25 | 3 | hr_only | 11 | 1.000 | +71.1pp | 20h | 60.6 |
| Sports | 200 | 3 | hr_only | 1084 | 0.649 | +38.9pp | 6h | 60.5 |
| Sports | 25 | 5 | composite | 13 | 0.615 | +35.5pp | 5h | 60.4 |
| Sports | 100 | 3 | hr_only | 413 | 0.649 | +38.8pp | 6h | 60.3 |
| Sports | 25 | 2 | composite | 516 | 0.676 | +41.6pp | 7h | 59.2 |
| Sports | 200 | 3 | composite | 1321 | 0.639 | +37.8pp | 6h | 57.2 |
| Crypto | 50 | 3 | hr_only | 94 | 0.979 | +71.4pp | 22h | 56.9 |
| Sports | 100 | 2 | composite | 1499 | 0.662 | +40.1pp | 7h | 55.2 |
| Sports | 200 | 2 | hr_only | 1888 | 0.656 | +39.6pp | 7h | 53.6 |
| Politics | 200 | 3 | composite | 2852 | 0.864 | +57.5pp | 15h | 52.9 |
| Politics | 100 | 2 | composite | 1766 | 0.856 | +56.7pp | 15h | 51.5 |
| Sports | 100 | 2 | hr_only | 904 | 0.647 | +38.6pp | 7h | 51.2 |
| Politics | 100 | 5 | hr_only | 186 | 0.898 | +60.9pp | 18h | 50.8 |
| Sports | 200 | 2 | composite | 2214 | 0.640 | +37.9pp | 7h | 49.3 |
| Sports | 50 | 2 | composite | 1137 | 0.674 | +41.3pp | 9h | 45.5 |
| Politics | 200 | 5 | hr_only | 1154 | 0.868 | +57.9pp | 18h | 44.7 |
| Crypto | 50 | 5 | hr_only | 19 | 1.000 | +73.5pp | 30h | 43.2 |
| Politics | 50 | 5 | composite | 89 | 0.899 | +61.0pp | 21h | 42.5 |
| Politics | 100 | 3 | hr_only | 740 | 0.909 | +62.0pp | 24h | 38.5 |
| Politics | 25 | 2 | hr_only | 51 | 0.902 | +61.3pp | 24h | 37.6 |
| Politics | 200 | 2 | composite | 4514 | 0.862 | +57.3pp | 21h | 37.5 |
| Politics | 200 | 3 | hr_only | 2522 | 0.894 | +60.5pp | 28h | 31.4 |
| Politics | 50 | 3 | composite | 251 | 0.892 | +60.3pp | 29h | 30.1 |
| Crypto | 25 | 5 | composite | 8 | 1.000 | +73.5pp | 44h | 29.5 |
| Politics | 100 | 2 | hr_only | 1812 | 0.922 | +63.3pp | 33h | 29.1 |
| Politics | 200 | 2 | hr_only | 3629 | 0.900 | +61.1pp | 31h | 28.9 |
| Politics | 25 | 3 | composite | 51 | 0.902 | +61.3pp | 37h | 24.4 |
| Crypto | 25 | 3 | hr_only | 24 | 0.958 | +69.4pp | 48h | 24.3 |
| Crypto | 25 | 3 | composite | 136 | 0.750 | +48.5pp | 24h | 23.1 |
| Politics | 50 | 2 | composite | 751 | 0.819 | +53.0pp | 31h | 21.7 |
| Crypto | 25 | 2 | hr_only | 81 | 0.975 | +71.0pp | 58h | 20.9 |
| Politics | 50 | 2 | hr_only | 330 | 0.930 | +64.1pp | 53h | 18.6 |
| Politics | 25 | 2 | composite | 114 | 0.912 | +62.3pp | 54h | 17.1 |
| Politics | 50 | 3 | hr_only | 116 | 0.914 | +62.5pp | 59h | 15.9 |
| Crypto | 50 | 3 | composite | 246 | 0.744 | +47.9pp | 44h | 12.7 |
| Crypto | 50 | 5 | composite | 35 | 0.886 | +62.1pp | 86h | 10.8 |
| Crypto | 50 | 2 | composite | 1216 | 0.673 | +40.9pp | 46h | 8.7 |
| Crypto | 25 | 2 | composite | 999 | 0.671 | +40.6pp | 47h | 8.4 |
| Crypto | 25 | 5 | hr_only | 1 | 1.000 | +73.5pp | 264h | 4.9 |
| Elections | 25 | 2 | composite | 1 | 1.000 | +64.0pp | 683h | 1.4 |
| Elections | 50 | 3 | composite | 1 | 1.000 | +64.0pp | 683h | 1.4 |
| Elections | 50 | 3 | hr_only | 1 | 1.000 | +64.0pp | 683h | 1.4 |
| Elections | 50 | 2 | composite | 15 | 0.467 | +10.7pp | 131h | 0.2 |
| Elections | 50 | 2 | hr_only | 15 | 0.467 | +10.7pp | 131h | 0.2 |

## Head-to-Head: Composite vs HR-Only (K=50, N=3)

| Tag | Ranking | N Signals | HR | Excess HR |
|-----|---------|-----------|-----|-----------|
| Crypto | composite | 246 | 0.744 | +47.9pp |
| Crypto | hr_only | 94 | 0.979 | +71.4pp |
| Elections | composite | 1 | 1.000 | +64.0pp |
| Elections | hr_only | 1 | 1.000 | +64.0pp |
| Politics | composite | 251 | 0.892 | +60.3pp |
| Politics | hr_only | 116 | 0.914 | +62.5pp |
| Sports | composite | 554 | 0.691 | +43.1pp |
| Sports | hr_only | 39 | 0.744 | +48.3pp |

## Direction Decomposition (K=50, N=3)

| Tag | Ranking | Direction | N Signals | HR | Base Rate | Excess HR |
|-----|---------|-----------|-----------|-----|-----------|-----------|
| Crypto | composite | NO | 207 | 0.739 | 0.735 | +0.4pp |
| Crypto | composite | YES | 39 | 0.769 | 0.265 | +50.4pp |
| Crypto | hr_only | NO | 76 | 1.000 | 0.735 | +26.5pp |
| Crypto | hr_only | YES | 18 | 0.889 | 0.265 | +62.4pp |
| Elections | composite | YES | 1 | 1.000 | 0.360 | +64.0pp |
| Elections | hr_only | YES | 1 | 1.000 | 0.360 | +64.0pp |
| Politics | composite | NO | 166 | 0.855 | 0.711 | +14.4pp |
| Politics | composite | YES | 85 | 0.965 | 0.289 | +67.6pp |
| Politics | hr_only | NO | 98 | 0.898 | 0.711 | +18.7pp |
| Politics | hr_only | YES | 18 | 1.000 | 0.289 | +71.1pp |
| Sports | composite | NO | 442 | 0.701 | 0.739 | -3.8pp |
| Sports | composite | YES | 112 | 0.652 | 0.261 | +39.1pp |
| Sports | hr_only | NO | 20 | 0.750 | 0.739 | +1.1pp |
| Sports | hr_only | YES | 19 | 0.737 | 0.261 | +47.6pp |

## Walk-Forward Validation (3 Folds, K=50, N=3)

| Fold | Tag | Ranking | N Signals | HR | Excess HR |
|------|-----|---------|-----------|-----|-----------|
| 2024-07→2024-10 | Crypto | composite | 31 | 0.613 | +19.0pp |
| 2024-07→2024-10 | Crypto | hr_only | 31 | 0.613 | +19.0pp |
| 2024-07→2024-10 | Elections | composite | 12 | 0.583 | +27.5pp |
| 2024-07→2024-10 | Elections | hr_only | 12 | 0.583 | +27.5pp |
| 2024-07→2024-10 | Politics | composite | 706 | 0.693 | +37.2pp |
| 2024-07→2024-10 | Politics | hr_only | 590 | 0.683 | +36.3pp |
| 2024-07→2024-10 | Sports | composite | 568 | 0.706 | +39.0pp |
| 2024-07→2024-10 | Sports | hr_only | 568 | 0.706 | +39.0pp |
| 2024-10→2025-01 | Crypto | composite | 52 | 0.712 | +38.3pp |
| 2024-10→2025-01 | Crypto | hr_only | 52 | 0.712 | +38.3pp |
| 2024-10→2025-01 | Elections | composite | 10 | 0.400 | +9.9pp |
| 2024-10→2025-01 | Elections | hr_only | 10 | 0.400 | +9.9pp |
| 2024-10→2025-01 | Politics | composite | 237 | 0.852 | +47.7pp |
| 2024-10→2025-01 | Politics | hr_only | 114 | 0.912 | +53.7pp |
| 2024-10→2025-01 | Sports | composite | 131 | 0.603 | +25.9pp |
| 2024-10→2025-01 | Sports | hr_only | 160 | 0.581 | +23.7pp |
| 2025-01→2025-04 | Crypto | composite | 105 | 0.657 | +29.8pp |
| 2025-01→2025-04 | Crypto | hr_only | 76 | 0.842 | +48.3pp |
| 2025-01→2025-04 | Politics | composite | 211 | 0.806 | +46.4pp |
| 2025-01→2025-04 | Politics | hr_only | 1 | 1.000 | +65.8pp |
| 2025-01→2025-04 | Sports | composite | 148 | 0.737 | +46.3pp |
| 2025-01→2025-04 | Sports | hr_only | 1 | 0.000 | -27.3pp |

## Composite vs HR-Only Uplift (K=50, N=3)

Uplift = composite_excess_hr - hr_only_excess_hr

| Tag | HR-Only Excess | Composite Excess | Uplift |
|-----|----------------|-----------------|--------|
| Politics | +62.5pp | +60.3pp | -2.1pp |
| Sports | +48.3pp | +43.1pp | -5.2pp |
| Crypto | +71.4pp | +47.9pp | -23.5pp |
| Elections | +64.0pp | +64.0pp | +0.0pp |

## Methodology Notes

- All results are VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation)
- Hold filter: Sports ≥4h, all others no filter
- Market-level aggregation: each market counted once, vol-weighted direction
- CRITICAL: only entries with first_trade >= test_start counted (copyable only)
- Gambling exclusion: slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'
- Market-maker exclusion: avg_conviction >= 0.90
- Min 20 training positions per trader per dominant tag
- Consistency: ≥6 months with ≥5 positions each (or 0.0 if absent)
- Bucket excess HR: weighted avg (trader_hr - pop_hr) in 10pp price buckets
