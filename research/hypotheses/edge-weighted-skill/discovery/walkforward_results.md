# Walk-Forward Stability Analysis: Edge-Weighted vs HR-Only

> **ALL RESULTS ARE UPPER BOUNDS** (vectorized, not tick-by-tick). Expect 20-40pp degradation in tick validation.

Generated: 2026-03-09 07:57:37

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
| Method | K | F1 HR | F1 Excess | F1 Signals | F2 HR | F2 Excess | F2 Signals | F3 HR | F3 Excess | F3 Signals | HR σ | Retention F1→F2 | Retention F2→F3 | Spearman F1-F2 | Spearman F2-F3 |
|--------|---|--------|-----------|----------|--------|-----------|----------|--------|-----------|----------|------|-----------------|-----------------|----------------|----------------|
| hr_only | 25 | 0.946 | +0.707 | 110 | 0.951 | +0.690 | 324 | 0.978 | +0.765 | 314 | 0.014 | 0.44 | 0.32 | -1.191 | -10.036 |
| hr_only | 50 | 0.779 | +0.540 | 393 | 0.860 | +0.599 | 549 | 0.942 | +0.729 | 417 | 0.067 | 0.64 | 0.38 | 0.535 | -3.253 |
| hr_only | 100 | 0.698 | +0.459 | 497 | 0.703 | +0.442 | 791 | 0.866 | +0.653 | 640 | 0.078 | 0.68 | 0.43 | 0.730 | -1.125 |
| composite | 25 | 0.613 | +0.374 | 452 | 0.590 | +0.329 | 727 | 0.725 | +0.512 | 462 | 0.059 | 0.56 | 0.60 | 0.108 | -0.773 |
| composite | 50 | 0.584 | +0.345 | 629 | 0.549 | +0.288 | 1072 | 0.633 | +0.420 | 795 | 0.034 | 0.56 | 0.66 | 0.269 | -0.012 |
| composite | 100 | 0.546 | +0.307 | 791 | 0.510 | +0.249 | 1460 | 0.577 | +0.364 | 998 | 0.027 | 0.63 | 0.64 | 0.066 | 0.523 |
| edge_primary | 25 | 0.486 | +0.247 | 210 | 0.471 | +0.210 | 777 | 0.601 | +0.388 | 316 | 0.058 | 0.28 | 0.52 | -7.482 | 0.431 |
| edge_primary | 50 | 0.483 | +0.245 | 515 | 0.427 | +0.166 | 1363 | 0.562 | +0.349 | 680 | 0.055 | 0.46 | 0.54 | -2.042 | 0.106 |
| edge_primary | 100 | 0.476 | +0.237 | 807 | 0.417 | +0.156 | 1589 | 0.492 | +0.279 | 1033 | 0.032 | 0.46 | 0.47 | -0.742 | -0.706 |

## Tag: Sports

### OOS Hit Rate by Method × K × Fold
| Method | K | F1 HR | F1 Excess | F1 Signals | F2 HR | F2 Excess | F2 Signals | F3 HR | F3 Excess | F3 Signals | HR σ | Retention F1→F2 | Retention F2→F3 | Spearman F1-F2 | Spearman F2-F3 |
|--------|---|--------|-----------|----------|--------|-----------|----------|--------|-----------|----------|------|-----------------|-----------------|----------------|----------------|
| hr_only | 25 | 0.998 | +0.637 | 442 | 0.949 | +0.567 | 6071 | 0.984 | +0.655 | 249 | 0.021 | 0.36 | 0.16 | -9.008 | -18.700 |
| hr_only | 50 | 0.912 | +0.552 | 1035 | 0.954 | +0.572 | 7350 | 0.988 | +0.659 | 678 | 0.031 | 0.54 | 0.10 | -0.908 | -18.650 |
| hr_only | 100 | 0.811 | +0.451 | 1649 | 0.889 | +0.507 | 8691 | 0.988 | +0.659 | 1816 | 0.073 | 0.59 | 0.14 | 0.052 | -102.591 |
| composite | 25 | 0.703 | +0.343 | 1534 | 0.757 | +0.375 | 2285 | 0.874 | +0.545 | 2275 | 0.071 | 0.52 | 0.36 | -0.214 | -3.533 |
| composite | 50 | 0.600 | +0.240 | 2474 | 0.710 | +0.329 | 4033 | 0.834 | +0.505 | 5091 | 0.095 | 0.62 | 0.38 | 0.071 | -3.568 |
| composite | 100 | 0.575 | +0.214 | 3029 | 0.658 | +0.277 | 7733 | 0.737 | +0.408 | 8086 | 0.066 | 0.57 | 0.50 | 0.097 | -0.984 |
| edge_primary | 25 | 0.656 | +0.296 | 1463 | 0.604 | +0.222 | 2439 | 0.800 | +0.471 | 2515 | 0.083 | 0.44 | 0.40 | 0.177 | -2.576 |
| edge_primary | 50 | 0.563 | +0.203 | 2408 | 0.584 | +0.203 | 4857 | 0.692 | +0.362 | 3703 | 0.056 | 0.54 | 0.42 | -0.465 | -1.235 |
| edge_primary | 100 | 0.561 | +0.201 | 2768 | 0.598 | +0.216 | 7718 | 0.588 | +0.259 | 7267 | 0.015 | 0.52 | 0.46 | -1.305 | -0.767 |

## Tag: Crypto

### OOS Hit Rate by Method × K × Fold
| Method | K | F1 HR | F1 Excess | F1 Signals | F2 HR | F2 Excess | F2 Signals | F3 HR | F3 Excess | F3 Signals | HR σ | Retention F1→F2 | Retention F2→F3 | Spearman F1-F2 | Spearman F2-F3 |
|--------|---|--------|-----------|----------|--------|-----------|----------|--------|-----------|----------|------|-----------------|-----------------|----------------|----------------|
| hr_only | 25 | 0.904 | +0.703 | 680 | 0.945 | +0.804 | 458 | 0.929 | +0.758 | 296 | 0.017 | 0.40 | 0.56 | -6.000 | -0.901 |
| hr_only | 50 | 0.858 | +0.656 | 737 | 0.825 | +0.684 | 589 | 0.905 | +0.733 | 346 | 0.033 | 0.42 | 0.62 | -3.566 | 0.047 |
| hr_only | 100 | 0.669 | +0.468 | 977 | 0.769 | +0.628 | 637 | 0.819 | +0.647 | 397 | 0.062 | 0.45 | 0.62 | -1.366 | 0.548 |
| composite | 25 | 0.851 | +0.650 | 727 | 0.838 | +0.697 | 501 | 0.849 | +0.678 | 318 | 0.006 | 0.44 | 0.56 | -3.250 | -1.268 |
| composite | 50 | 0.818 | +0.617 | 775 | 0.826 | +0.684 | 579 | 0.823 | +0.651 | 361 | 0.003 | 0.34 | 0.56 | -4.000 | -0.487 |
| composite | 100 | 0.660 | +0.459 | 992 | 0.707 | +0.566 | 704 | 0.701 | +0.530 | 465 | 0.021 | 0.45 | 0.60 | -2.578 | 0.151 |
| edge_primary | 25 | 0.778 | +0.577 | 540 | 0.252 | +0.111 | 107 | 0.725 | +0.554 | 313 | 0.236 | 0.32 | 0.52 | -4.619 | -1.654 |
| edge_primary | 50 | 0.705 | +0.504 | 877 | 0.654 | +0.513 | 576 | 0.620 | +0.449 | 413 | 0.035 | 0.36 | 0.58 | -3.957 | -0.609 |
| edge_primary | 100 | 0.632 | +0.431 | 1042 | 0.466 | +0.325 | 1004 | 0.504 | +0.333 | 573 | 0.071 | 0.43 | 0.57 | -2.132 | 0.132 |

## Tag: Elections

### OOS Hit Rate by Method × K × Fold
| Method | K | F1 HR | F1 Excess | F1 Signals | F2 HR | F2 Excess | F2 Signals | F3 HR | F3 Excess | F3 Signals | HR σ | Retention F1→F2 | Retention F2→F3 | Spearman F1-F2 | Spearman F2-F3 |
|--------|---|--------|-----------|----------|--------|-----------|----------|--------|-----------|----------|------|-----------------|-----------------|----------------|----------------|
| hr_only | 25 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.379 | +0.184 | 66 | 0.283 | 1.00 | 0.57 | N/A | -18.000 |
| hr_only | 50 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -18.000 |
| hr_only | 100 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -18.000 |
| composite | 25 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.420 | +0.225 | 50 | 0.273 | 1.00 | 0.57 | N/A | -37.700 |
| composite | 50 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -37.700 |
| composite | 100 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -37.700 |
| edge_primary | 25 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.412 | +0.217 | 51 | 0.275 | 1.00 | 0.43 | N/A | -83.000 |
| edge_primary | 50 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -141.500 |
| edge_primary | 100 | 1.000 | +0.721 | 1 | 0.422 | +0.187 | 71 | 0.220 | +0.025 | 132 | 0.331 | 1.00 | 0.57 | N/A | -141.500 |

## Stability Summary: Best Method per Tag × K

Ranking criteria: lowest HR σ + highest retention + highest Spearman.

| Tag | K | Best Method | HR σ | Avg Excess HR | Retention F1→F2 | Spearman F1-F2 | All Positive Excess |
|-----|---|-------------|------|---------------|-----------------|----------------|---------------------|
| Politics | 25 | composite | 0.059 | 0.405 | 0.56 | 0.1077 | True |
| Politics | 50 | hr_only | 0.0669 | 0.6226 | 0.64 | 0.5352 | True |
| Politics | 100 | hr_only | 0.0778 | 0.5179 | 0.68 | 0.7302 | True |
| Sports | 25 | edge_primary | 0.0831 | 0.3298 | 0.44 | 0.1773 | True |
| Sports | 50 | composite | 0.0954 | 0.3577 | 0.62 | 0.0714 | True |
| Sports | 100 | composite | 0.0661 | 0.2996 | 0.57 | 0.0966 | True |
| Crypto | 25 | composite | 0.0057 | 0.675 | 0.44 | -3.25 | True |
| Crypto | 50 | hr_only | 0.0326 | 0.6912 | 0.42 | -3.5662 | True |
| Crypto | 100 | hr_only | 0.0621 | 0.5812 | 0.45 | -1.3664 | True |
| Elections | 25 | composite | 0.2728 | 0.3778 | 1.0 | None | True |
| Elections | 50 | hr_only | 0.3306 | 0.311 | 1.0 | None | True |
| Elections | 100 | hr_only | 0.3306 | 0.311 | 1.0 | None | True |

## Key Findings

_(auto-generated analysis — verify manually)_

- **Politics K=25**: Best stability = hr_only (σ=0.0141). HR-only σ=0.0141, composite σ=0.059, edge_primary σ=0.0583.
- **Politics K=50**: Best stability = composite (σ=0.0342). HR-only σ=0.0669, composite σ=0.0342, edge_primary σ=0.0553.
- **Politics K=100**: Best stability = composite (σ=0.0273). HR-only σ=0.0778, composite σ=0.0273, edge_primary σ=0.0323.
- **Sports K=25**: Best stability = hr_only (σ=0.0207). HR-only σ=0.0207, composite σ=0.0712, edge_primary σ=0.0831.
- **Sports K=50**: Best stability = hr_only (σ=0.0311). HR-only σ=0.0311, composite σ=0.0954, edge_primary σ=0.0562.
- **Sports K=100**: Best stability = edge_primary (σ=0.0155). HR-only σ=0.0727, composite σ=0.0661, edge_primary σ=0.0155.
- **Crypto K=25**: Best stability = composite (σ=0.0057). HR-only σ=0.0169, composite σ=0.0057, edge_primary σ=0.2363.
- **Crypto K=50**: Best stability = composite (σ=0.0031). HR-only σ=0.0326, composite σ=0.0031, edge_primary σ=0.0348.
- **Crypto K=100**: Best stability = composite (σ=0.0209). HR-only σ=0.0621, composite σ=0.0209, edge_primary σ=0.0711.
- **Elections K=25**: Best stability = composite (σ=0.2728). HR-only σ=0.2831, composite σ=0.2728, edge_primary σ=0.2748.
- **Elections K=50**: Best stability = hr_only (σ=0.3306). HR-only σ=0.3306, composite σ=0.3306, edge_primary σ=0.3306.
- **Elections K=100**: Best stability = hr_only (σ=0.3306). HR-only σ=0.3306, composite σ=0.3306, edge_primary σ=0.3306.
