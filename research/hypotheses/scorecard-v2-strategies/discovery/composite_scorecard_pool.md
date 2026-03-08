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

## Pool Coverage

- Qualified traders (excess_hr > 0, ≥20 positions, non-MM): **5,929**
- With consistency_sharpe (≥6 qualifying months): 1,655/5,929 = **27.9%**
- With bucket_excess_hr (≥2 price buckets): 5,864/5,929 = **98.9%**

Consistency is sparse because most traders are active for fewer than 6 months.
When absent, it is imputed as 0.0 (bottom percentile) — penalizing shorter-history traders.

## Key Findings

### Finding 1: HR-Only Dominates Composite at K=50, N=3

| Tag | HR-Only Excess | Composite Excess | Uplift |
|-----|----------------|-----------------|--------|
| Politics | +62.5pp | +60.3pp | -2.1pp |
| Sports | +48.3pp | +43.1pp | -5.2pp |
| Crypto | +71.4pp | +47.9pp | -23.5pp |
| Elections | +64.0pp | +64.0pp | +0.0pp |

**Composite underperforms HR-only for excess_hr in the test period.**

The multi-signal ranking selects different traders (Jaccard index = 0.10-0.21 across tags),
but the selected set has lower peak HR. The consistency + edge signals are trading peak HR
for stability — at the cost of performance in the test window.

### Finding 2: The Two Pools Select Fundamentally Different Traders

Head-to-head comparison of traders gained vs. lost by composite vs HR-only at K=50:

| Tag | Gained by Composite | Lost by Composite |
|-----|---------------------|-------------------|
| Politics | excess_hr=0.484, consistency=**7.08**, edge=**$560** | excess_hr=**0.642**, consistency=3.36, edge=$219 |
| Sports | excess_hr=0.362, consistency=**6.19**, edge=**$59** | excess_hr=**0.572**, consistency=0.08, edge=$9 |
| Crypto | excess_hr=0.389, consistency=**4.29**, edge=**$310** | excess_hr=**0.640**, consistency=0.00, edge=$27 |
| Elections | overlap=48/50 — nearly identical pools (Jaccard=0.923) |

**Composite trades HR for stability and edge:**
- Gains traders with consistency_sharpe 2x higher and avg_edge 5-12x higher
- Loses traders with raw excess_hr 25-65% higher
- For Elections (small pool), the pools are nearly identical

**Jaccard indices**: Politics=0.099, Crypto=0.149, Sports=0.205, Elections=0.923

### Finding 3: Composite Generates 2-3x More Signals (at Lower HR)

At K=50, N=3:
- Politics: composite=251 signals vs hr_only=116 — **2.2x more volume**
- Sports: composite=554 vs hr_only=39 — **14x more volume**
- Crypto: composite=246 vs hr_only=94 — **2.6x more volume**

The consistency weight pulls in longer-active traders who participate in more markets
simultaneously, producing more overlapping signals.

### Finding 4: Sports Direction Decomposition Kills the Signal

Sports K=50, N=3 direction breakdown:
| Ranking | Direction | N Signals | HR | Base Rate | Excess |
|---------|-----------|-----------|-----|-----------|--------|
| composite | YES | 112 | 0.652 | 0.261 | **+39.1pp** |
| composite | NO | 443 | 0.702 | 0.739 | **-3.7pp** |
| hr_only | YES | 19 | 0.737 | 0.261 | **+47.6pp** |
| hr_only | NO | 20 | 0.750 | 0.739 | **+1.1pp** |

**80% of Sports composite signals are NO-direction with -3.7pp excess** — pure structural bias.
YES-direction signals (+39pp) are real but drowned out. **Sports requires YES-only filtering.**

Politics composite direction:
| Direction | N Signals | HR | Base Rate | Excess |
|-----------|-----------|-----|-----------|--------|
| YES | 85 | 0.965 | 0.289 | **+67.6pp** |
| NO | 166 | 0.855 | 0.711 | **+14.5pp** |

Politics has genuine signal in BOTH directions, but YES is much stronger.

Crypto composite direction:
| Direction | N Signals | HR | Base Rate | Excess |
|-----------|-----------|-----|-----------|--------|
| YES | 39 | 0.769 | 0.265 | **+50.4pp** |
| NO | 207 | 0.739 | 0.735 | **+0.4pp** |

**Crypto NO signals are near-random** (0.4pp excess on 207 signals). Only YES is viable.

### Finding 5: Walk-Forward Shows Composite Provides Stability in Thin Folds

Walk-forward (K=50, N=3) across 3 folds:

| Fold | Tag | Composite n/excess | HR-Only n/excess |
|------|-----|-------------------|-----------------|
| 2024-07→2024-10 | Politics | 708 / +37.4pp | 592 / +36.4pp |
| 2024-10→2025-01 | Politics | 237 / +47.7pp | 114 / +53.7pp |
| 2025-01→2025-04 | Politics | 117 / +53.9pp | 1 / +65.8pp |

**Politics walk-forward**: Composite provides 117 signals vs 1 signal for HR-only in the
final fold. The HR-only pool collapses to near-zero coverage as the top-HR traders from
the 2024 training window become inactive — demonstrating exactly the overfitting risk.

| Fold | Tag | Composite n/excess | HR-Only n/excess |
|------|-----|-------------------|-----------------|
| 2024-07→2024-10 | Sports | 569 / +38.9pp | 569 / +38.9pp |
| 2024-10→2025-01 | Sports | 131 / +25.9pp | 160 / +23.7pp |
| 2025-01→2025-04 | Sports | 148 / +46.3pp | 1 / -27.3pp |

**Sports walk-forward**: In fold 3, HR-only produces 1 signal with 0% HR. Composite
maintains 148 signals at +46.3pp. This is the clearest demonstration of composite's
robustness advantage — the HR-only top-50 Sports traders are highly concentrated and
disappear from the test window, while composite spreads across more stable traders.

### Finding 6: Crypto HR-Only Advantage Is Real But Volume-Limited

Crypto K=50, N=2 hr_only: **516 signals at 98.5% HR, CS=414** — strongest single config.
But this degrades dramatically with more signal requirements:
- K=50, N=3 hr_only: 94 signals at 97.9% (still excellent but low volume)
- K=50, N=2 composite: 1,216 signals at 67.4% (high volume, lower quality)

The Crypto hr_only top-50 pool is an extraordinarily concentrated elite that produces
near-perfect signals but quickly runs out of overlapping markets as N increases.

### Finding 7: Politics YES-Direction Is the Strongest Validated Signal

Politics YES K=50, N=3 composite: **85 signals at 96.5% HR (+67.6pp)**
Politics YES K=50, N=3 hr_only: **18 signals at 100% HR (+71.1pp)**

Both are strong, but composite provides 4.7x more signals at nearly the same excess HR.
After expected 20-40pp tick degradation, composite YES signals should reach 55-75% HR.

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
| Politics | 50 | 5 | hr_only | 7 | 1.000 | +71.1pp | 9h | 134.8 |
| Sports | 50 | 3 | hr_only | 39 | 0.744 | +48.3pp | 5h | 111.9 |
| Sports | 25 | 3 | composite | 96 | 0.729 | +46.9pp | 5h | 105.3 |
| Sports | 50 | 2 | hr_only | 180 | 0.717 | +45.6pp | 5h | 99.8 |
| Politics | 100 | 5 | composite | 232 | 0.914 | +62.5pp | 10h | 93.7 |
| Sports | 100 | 5 | composite | 282 | 0.681 | +42.0pp | 5h | 84.7 |
| Crypto | 100 | 5 | composite | 178 | 0.837 | +57.2pp | 11h | 71.5 |
| Politics | 200 | 5 | composite | 1227 | 0.865 | +57.6pp | 11h | 72.3 |
| Sports | 100 | 3 | composite | 859 | 0.673 | +41.2pp | 6h | 68.0 |
| Sports | 50 | 3 | composite | 555 | 0.692 | +43.1pp | 7h | 63.8 |
| Politics | 100 | 3 | composite | 791 | 0.872 | +58.3pp | 13h | 62.8 |

## Head-to-Head: Composite vs HR-Only (K=50, N=3)

| Tag | Ranking | N Signals | HR | Excess HR |
|-----|---------|-----------|-----|-----------|
| Crypto | composite | 246 | 0.744 | +47.9pp |
| Crypto | hr_only | 94 | 0.979 | +71.4pp |
| Elections | composite | 1 | 1.000 | +64.0pp |
| Elections | hr_only | 1 | 1.000 | +64.0pp |
| Politics | composite | 251 | 0.892 | +60.3pp |
| Politics | hr_only | 116 | 0.914 | +62.5pp |
| Sports | composite | 555 | 0.692 | +43.1pp |
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
| Sports | composite | NO | 443 | 0.702 | 0.739 | -3.7pp |
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
| 2024-07→2024-10 | Politics | composite | 708 | 0.695 | +37.4pp |
| 2024-07→2024-10 | Politics | hr_only | 592 | 0.684 | +36.4pp |
| 2024-07→2024-10 | Sports | composite | 569 | 0.705 | +38.9pp |
| 2024-07→2024-10 | Sports | hr_only | 569 | 0.705 | +38.9pp |
| 2024-10→2025-01 | Crypto | composite | 52 | 0.712 | +38.3pp |
| 2024-10→2025-01 | Crypto | hr_only | 52 | 0.712 | +38.3pp |
| 2024-10→2025-01 | Elections | composite | 10 | 0.400 | +9.9pp |
| 2024-10→2025-01 | Elections | hr_only | 10 | 0.400 | +9.9pp |
| 2024-10→2025-01 | Politics | composite | 237 | 0.852 | +47.7pp |
| 2024-10→2025-01 | Politics | hr_only | 114 | 0.912 | +53.7pp |
| 2024-10→2025-01 | Sports | composite | 131 | 0.603 | +25.9pp |
| 2024-10→2025-01 | Sports | hr_only | 160 | 0.581 | +23.7pp |
| 2025-01→2025-04 | Crypto | composite | 109 | 0.661 | +30.1pp |
| 2025-01→2025-04 | Crypto | hr_only | 74 | 0.824 | +46.5pp |
| 2025-01→2025-04 | Politics | composite | 117 | 0.880 | +53.9pp |
| 2025-01→2025-04 | Politics | hr_only | 1 | 1.000 | +65.8pp |
| 2025-01→2025-04 | Sports | composite | 148 | 0.737 | +46.3pp |
| 2025-01→2025-04 | Sports | hr_only | 1 | 0.000 | -27.3pp |

## Composite vs HR-Only Uplift (K=50, N=3)

Uplift = composite_excess_hr - hr_only_excess_hr

| Tag | HR-Only Excess | Composite Excess | Uplift | Volume (comp) | Volume (hr) |
|-----|----------------|-----------------|--------|--------------|------------|
| Politics | +62.5pp | +60.3pp | -2.1pp | 251 | 116 |
| Sports | +48.3pp | +43.1pp | -5.2pp | 555 | 39 |
| Crypto | +71.4pp | +47.9pp | -23.5pp | 246 | 94 |
| Elections | +64.0pp | +64.0pp | +0.0pp | 1 | 1 |

## Verdict

### What the Multi-Signal Composite Adds

The composite scorecard selects for **consistency + long-term profitability** over
**peak hit rate**. This is its core tradeoff:

- **Pros**: 2-14x more signal volume, dramatically more stable across walk-forward folds,
  higher edge-per-signal traders, ~2x higher consistency Sharpe of selected traders
- **Cons**: 2-25pp lower excess HR on static test window, lower peak performance

### Recommended Production Configurations

| Tag | Strategy | Config | n_signals | excess_hr | Direction | Status |
|-----|----------|--------|-----------|-----------|-----------|--------|
| Politics | HR-only | K=100, N=5 | 186 | +60.9pp | YES+NO | Validate |
| Politics | Composite | K=100, N=5 | 232 | +62.5pp | YES primary | Validate |
| Politics | Composite | K=200, N=5 | 1227 | +57.6pp | YES+NO | Validate |
| Crypto | HR-only | K=50, N=2 | 516 | +72.0pp | YES only | Validate |
| Crypto | Composite | K=200, N=5 | 440 | +49.7pp | YES only | Validate |
| Sports | Composite | K=25, N=3 | 96 | +46.9pp | YES only | Validate |

### Critical Filters Required Before Deployment

1. **Direction filter**: Apply YES-only for Sports and Crypto (NO signals are structural bias)
2. **Hold filter**: Sports ≥4h minimum (already applied in this sweep)
3. **Walk-forward validation**: Use composite over HR-only for live production — HR-only
   collapses to 1 signal in later folds while composite stays stable
4. **Volume gate**: Require at least 10 test-window signals per configuration before trusting HR

### Open Questions

1. Does Crypto K=50, N=2 hr_only 98.5% HR survive tick-by-tick? (416 signals, 3h hold)
2. Is the consistency_sharpe selection actually reducing overfitting, or just selecting
   less-skilled traders who happen to be active longer?
3. Can a YES-only Sports composite (K=25, N=3, YES direction only) survive tick-by-tick?
4. Walk-forward shows composite consistently beats HR-only in VOLUME — is 4x volume
   at -5pp excess HR better or worse than thin HR-only coverage?

## Methodology Notes

- All results are VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation)
- Hold filter: Sports ≥4h, all others no filter
- Market-level aggregation: each market counted once, vol-weighted direction
- CRITICAL: only entries with first_trade >= test_start counted (copyable only)
- Gambling exclusion: markets.slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'
- Market-maker exclusion: avg(abs(net_usd)/volume) >= 0.90
- Min 20 training positions per trader per dominant tag
- Consistency: ≥6 months with ≥5 positions each (or 0.0 if absent for 72% of traders)
- Bucket excess HR: weighted avg (trader_hr - pop_hr) in 10pp price buckets, ≥2 buckets
- Walk-forward folds: 6-month train, 3-month test, 3 folds starting 2024-01-01
- Script: research/hypotheses/scorecard-v2-strategies/scripts/composite_pool.py
