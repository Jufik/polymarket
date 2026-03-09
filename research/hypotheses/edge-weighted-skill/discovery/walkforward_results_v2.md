# Walk-Forward Stability Analysis v2: Edge-Weighted vs HR-Only

> **ALL RESULTS ARE UPPER BOUNDS** (vectorized, not tick-by-tick). Expect 20-40pp degradation in tick validation.

Generated: 2026-03-09 08:50:10

## V2 Fixes
- **Spearman fix**: re-rank intersection members to 1..n before computing d². V1 used original list positions (1..K), which makes d² unbounded when pool overlap is small, producing impossible values like -102 or -141.
- **Minimum intersection**: Spearman reported only when ≥10 traders appear in both folds (otherwise N/A).
- **Elections excluded** from stability summary: pool degenerates (2-38 qualified traders, 1 signal in Fold 1).
- **Low-confidence flag**: configs with <100 signals in any fold are marked [LOW CONF].

## Fold Definitions
| Fold | Train Start | Train End | Test Start | Test End |
|------|-------------|-----------|------------|----------|
| 1 | 2024-07-01 | 2025-07-01 | 2025-07-01 | 2025-10-01 |
| 2 | 2024-10-01 | 2025-10-01 | 2025-10-01 | 2026-01-01 |
| 3 | 2025-01-01 | 2026-01-01 | 2026-01-01 | 2026-04-01 |

## Scoring Methods
1. **hr_only**: rank by `excess_hr` descending
2. **composite**: `0.45*excess_hr + 0.25*consistency + 0.15*avg_edge + 0.15*bucket_excess`
3. **edge_primary** (NEW): `0.45*bucket_excess + 0.25*consistency + 0.15*avg_edge + 0.15*excess_hr`

All composite/edge_primary components are percentile-rank normalized to [0,1] within fold-tag.

## Tag: Politics

### OOS Hit Rate by Method × K × Fold
| Method | K | Conf | F1 HR | F1 Excess | F1 N | F2 HR | F2 Excess | F2 N | F3 HR | F3 Excess | F3 N | HR σ | Ret F1→F2 | Ret F2→F3 | Spear F1-F2 (n) | Spear F2-F3 (n) |
|--------|---|------|--------|-----------|-----|--------|-----------|-----|--------|-----------|-----|------|-----------|-----------|-----------------|-----------------|
| hr_only | 25 | OK | 0.946 | +0.707 | 110 | 0.951 | +0.690 | 324 | 0.978 | +0.765 | 314 | 0.014 | 0.44 | 0.32 | 0.809 (n=11) | N/A (n=8) |
| hr_only | 50 | OK | 0.779 | +0.540 | 393 | 0.860 | +0.599 | 549 | 0.942 | +0.729 | 417 | 0.067 | 0.64 | 0.38 | 0.784 (n=32) | 0.967 (n=19) |
| hr_only | 100 | OK | 0.698 | +0.459 | 497 | 0.703 | +0.442 | 791 | 0.866 | +0.653 | 640 | 0.078 | 0.68 | 0.43 | 0.887 (n=68) | 0.855 (n=43) |
| composite | 25 | OK | 0.613 | +0.374 | 452 | 0.590 | +0.329 | 727 | 0.725 | +0.512 | 462 | 0.059 | 0.56 | 0.60 | 0.683 (n=14) | 0.304 (n=15) |
| composite | 50 | OK | 0.584 | +0.345 | 629 | 0.549 | +0.288 | 1072 | 0.633 | +0.420 | 795 | 0.034 | 0.56 | 0.66 | 0.749 (n=28) | 0.473 (n=33) |
| composite | 100 | OK | 0.546 | +0.307 | 791 | 0.510 | +0.249 | 1460 | 0.577 | +0.364 | 998 | 0.027 | 0.63 | 0.64 | 0.627 (n=63) | 0.744 (n=64) |
| edge_primary | 25 | OK | 0.486 | +0.247 | 210 | 0.471 | +0.210 | 777 | 0.601 | +0.388 | 316 | 0.058 | 0.28 | 0.52 | N/A (n=7) | 0.846 (n=13) |
| edge_primary | 50 | OK | 0.483 | +0.245 | 515 | 0.427 | +0.166 | 1363 | 0.562 | +0.349 | 680 | 0.055 | 0.46 | 0.54 | 0.326 (n=23) | 0.694 (n=27) |
| edge_primary | 100 | OK | 0.476 | +0.237 | 807 | 0.417 | +0.156 | 1589 | 0.492 | +0.279 | 1033 | 0.032 | 0.46 | 0.47 | 0.612 (n=46) | 0.624 (n=47) |

## Tag: Sports

### OOS Hit Rate by Method × K × Fold
| Method | K | Conf | F1 HR | F1 Excess | F1 N | F2 HR | F2 Excess | F2 N | F3 HR | F3 Excess | F3 N | HR σ | Ret F1→F2 | Ret F2→F3 | Spear F1-F2 (n) | Spear F2-F3 (n) |
|--------|---|------|--------|-----------|-----|--------|-----------|-----|--------|-----------|-----|------|-----------|-----------|-----------------|-----------------|
| hr_only | 25 | OK | 0.998 | +0.637 | 442 | 0.949 | +0.567 | 6071 | 0.987 | +0.658 | 604 | 0.021 | 0.36 | 0.08 | N/A (n=9) | N/A (n=2) |
| hr_only | 50 | OK | 0.912 | +0.552 | 1035 | 0.954 | +0.572 | 7350 | 0.972 | +0.643 | 1051 | 0.025 | 0.54 | 0.18 | 0.957 (n=27) | N/A (n=9) |
| hr_only | 100 | OK | 0.811 | +0.451 | 1649 | 0.888 | +0.506 | 8706 | 0.972 | +0.642 | 2175 | 0.066 | 0.58 | 0.14 | 0.978 (n=58) | -0.099 (n=14) |
| composite | 25 | OK | 0.703 | +0.343 | 1534 | 0.757 | +0.375 | 2285 | 0.874 | +0.545 | 2275 | 0.071 | 0.52 | 0.36 | 0.731 (n=13) | N/A (n=9) |
| composite | 50 | OK | 0.600 | +0.240 | 2474 | 0.710 | +0.329 | 4033 | 0.834 | +0.505 | 5091 | 0.095 | 0.62 | 0.38 | 0.742 (n=31) | 0.517 (n=19) |
| composite | 100 | OK | 0.575 | +0.214 | 3029 | 0.658 | +0.277 | 7733 | 0.737 | +0.408 | 8086 | 0.066 | 0.57 | 0.50 | 0.809 (n=57) | 0.709 (n=50) |
| edge_primary | 25 | OK | 0.656 | +0.296 | 1463 | 0.604 | +0.222 | 2439 | 0.800 | +0.471 | 2515 | 0.083 | 0.44 | 0.40 | 0.900 (n=11) | 0.491 (n=10) |
| edge_primary | 50 | OK | 0.563 | +0.203 | 2408 | 0.584 | +0.203 | 4857 | 0.692 | +0.362 | 3703 | 0.056 | 0.54 | 0.42 | 0.797 (n=27) | 0.823 (n=21) |
| edge_primary | 100 | OK | 0.561 | +0.201 | 2768 | 0.598 | +0.216 | 7718 | 0.588 | +0.259 | 7267 | 0.015 | 0.52 | 0.46 | 0.487 (n=52) | 0.710 (n=46) |

## Tag: Crypto

### OOS Hit Rate by Method × K × Fold
| Method | K | Conf | F1 HR | F1 Excess | F1 N | F2 HR | F2 Excess | F2 N | F3 HR | F3 Excess | F3 N | HR σ | Ret F1→F2 | Ret F2→F3 | Spear F1-F2 (n) | Spear F2-F3 (n) |
|--------|---|------|--------|-----------|-----|--------|-----------|-----|--------|-----------|-----|------|-----------|-----------|-----------------|-----------------|
| hr_only | 25 | OK | 0.904 | +0.703 | 680 | 0.945 | +0.804 | 458 | 0.929 | +0.758 | 296 | 0.017 | 0.40 | 0.56 | 0.151 (n=10) | 0.574 (n=14) |
| hr_only | 50 | OK | 0.858 | +0.656 | 737 | 0.822 | +0.681 | 591 | 0.905 | +0.733 | 346 | 0.034 | 0.40 | 0.62 | 0.838 (n=20) | 0.941 (n=31) |
| hr_only | 100 | OK | 0.669 | +0.468 | 977 | 0.769 | +0.628 | 637 | 0.819 | +0.647 | 397 | 0.062 | 0.45 | 0.62 | 0.954 (n=45) | 0.975 (n=62) |
| composite | 25 | OK | 0.851 | +0.650 | 727 | 0.838 | +0.697 | 501 | 0.849 | +0.678 | 318 | 0.006 | 0.44 | 0.56 | 0.382 (n=11) | 0.574 (n=14) |
| composite | 50 | OK | 0.818 | +0.617 | 775 | 0.826 | +0.684 | 579 | 0.823 | +0.651 | 361 | 0.003 | 0.34 | 0.56 | 0.436 (n=17) | 0.575 (n=28) |
| composite | 100 | OK | 0.660 | +0.459 | 992 | 0.707 | +0.566 | 704 | 0.701 | +0.530 | 465 | 0.021 | 0.45 | 0.60 | 0.648 (n=45) | 0.809 (n=60) |
| edge_primary | 25 | OK | 0.778 | +0.577 | 540 | 0.252 | +0.111 | 107 | 0.725 | +0.554 | 313 | 0.236 | 0.32 | 0.52 | N/A (n=8) | 0.445 (n=13) |
| edge_primary | 50 | OK | 0.705 | +0.504 | 877 | 0.654 | +0.513 | 576 | 0.620 | +0.449 | 413 | 0.035 | 0.36 | 0.58 | 0.701 (n=18) | 0.375 (n=29) |
| edge_primary | 100 | OK | 0.632 | +0.431 | 1042 | 0.466 | +0.325 | 1004 | 0.504 | +0.333 | 573 | 0.071 | 0.43 | 0.57 | 0.588 (n=43) | 0.768 (n=57) |

## Tag: Elections
> **EXCLUDED from stability conclusions** — insufficient pool size or signals.

### OOS Hit Rate by Method × K × Fold
| Method | K | Conf | F1 HR | F1 Excess | F1 N | F2 HR | F2 Excess | F2 N | F3 HR | F3 Excess | F3 N | HR σ | Ret F1→F2 | Ret F2→F3 | Spear F1-F2 (n) | Spear F2-F3 (n) |
|--------|---|------|--------|-----------|-----|--------|-----------|-----|--------|-----------|-----|------|-----------|-----------|-----------------|-----------------|
| hr_only | 25 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.393 | +0.199 | 61* | 0.279 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| hr_only | 50 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| hr_only | 100 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| composite | 25 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.420 | +0.225 | 50* | 0.273 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| composite | 50 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| composite | 100 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| edge_primary | 25 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.412 | +0.217 | 51* | 0.275 | 1.00 | 0.43 | N/A (n=2) | N/A (n=3) |
| edge_primary | 50 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |
| edge_primary | 100 | [LOW CONF] | 1.000 | +0.721 | 1* | 0.422 | +0.187 | 71* | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A (n=2) | N/A (n=4) |

## Stability Summary: Best Method per Tag × K

Includes only conclusion tags: ['Politics', 'Sports', 'Crypto']. Elections excluded.
Low-confidence configs (min signals < 100) included for completeness but marked.

| Tag | K | Best Method | HR σ | Avg Excess | Ret F1→F2 | Spear F1-F2 | All Pos Excess | Conf |
|-----|---|-------------|------|------------|-----------|------------|----------------|------|
| Politics | 25 | hr_only | 0.0141 | 0.7203 | 0.44 | 0.809 | True | OK |
| Politics | 50 | composite | 0.0342 | 0.3509 | 0.56 | 0.749 | True | OK |
| Politics | 100 | hr_only | 0.0778 | 0.5179 | 0.68 | 0.887 | True | OK |
| Sports | 25 | composite | 0.0712 | 0.421 | 0.52 | 0.731 | True | OK |
| Sports | 50 | hr_only | 0.0252 | 0.589 | 0.54 | 0.957 | True | OK |
| Sports | 100 | hr_only | 0.0656 | 0.533 | 0.58 | 0.978 | True | OK |
| Crypto | 25 | composite | 0.0057 | 0.675 | 0.44 | 0.382 | True | OK |
| Crypto | 50 | hr_only | 0.0337 | 0.6903 | 0.4 | 0.838 | True | OK |
| Crypto | 100 | hr_only | 0.0621 | 0.5812 | 0.45 | 0.954 | True | OK |

## Key Findings

> ALL RESULTS ARE UPPER BOUNDS. Expect 20-40pp degradation in tick-by-tick validation.

### Politics
- **K=25**: Best HR stability = **hr_only** (σ=0.0141). hr_only σ=0.0141, composite σ=0.0590, edge_primary σ=0.0583. Avg excess: hr_only=0.72, composite=0.405, edge_primary=0.282.
- **K=50**: Best HR stability = **composite** (σ=0.0342). hr_only σ=0.0669, composite σ=0.0342, edge_primary σ=0.0553. Avg excess: hr_only=0.623, composite=0.351, edge_primary=0.253.
- **K=100**: Best HR stability = **composite** (σ=0.0273). hr_only σ=0.0778, composite σ=0.0273, edge_primary σ=0.0323. Avg excess: hr_only=0.518, composite=0.307, edge_primary=0.224.

### Sports
- **K=25**: Best HR stability = **hr_only** (σ=0.0211). hr_only σ=0.0211, composite σ=0.0712, edge_primary σ=0.0831. Avg excess: hr_only=0.621, composite=0.421, edge_primary=0.33.
- **K=50**: Best HR stability = **hr_only** (σ=0.0252). hr_only σ=0.0252, composite σ=0.0954, edge_primary σ=0.0562. Avg excess: hr_only=0.589, composite=0.358, edge_primary=0.256.
- **K=100**: Best HR stability = **edge_primary** (σ=0.0155). hr_only σ=0.0656, composite σ=0.0661, edge_primary σ=0.0155. Avg excess: hr_only=0.533, composite=0.3, edge_primary=0.226.

### Crypto
- **K=25**: Best HR stability = **composite** (σ=0.0057). hr_only σ=0.0169, composite σ=0.0057, edge_primary σ=0.2363. Avg excess: hr_only=0.755, composite=0.675, edge_primary=0.414.
- **K=50**: Best HR stability = **composite** (σ=0.0031). hr_only σ=0.0337, composite σ=0.0031, edge_primary σ=0.0348. Avg excess: hr_only=0.69, composite=0.651, edge_primary=0.488.
- **K=100**: Best HR stability = **composite** (σ=0.0209). hr_only σ=0.0621, composite σ=0.0209, edge_primary σ=0.0711. Avg excess: hr_only=0.581, composite=0.518, edge_primary=0.363.

### Elections
EXCLUDED from conclusions. Pool degenerates in walk-forward (2-38 qualified traders per fold; Fold 1 has only 1 OOS signal). No method comparison is meaningful.

### Summary Verdict
Across 9 high-confidence cells (tag × K, excluding Elections and low-conf):
- **composite** wins HR σ: 5/9
- **hr_only** wins HR σ: 3/9
- **edge_primary** wins HR σ: 1/9

**Conclusion**: composite ranking is optimal for deployment stability. edge_primary does NOT improve stability — it amplifies sample-period noise in bucket HR estimates, leading to pool instability (especially Crypto K=25 which collapses in Fold 2).
