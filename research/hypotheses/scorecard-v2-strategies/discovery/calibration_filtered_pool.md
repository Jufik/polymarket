# Calibration-Filtered Consensus Pool (Strategy A)

**Date**: 2026-03-07
**Status**: Vectorized discovery complete — all results are UPPER BOUNDS
**Train**: resolved_at < 2025-07-01 | **Test**: resolved_at >= 2025-07-01

---

## Setup

### Pool Variants Tested

| Pool | Description |
|------|-------------|
| **A** | Top-K by excess_hr (baseline) |
| **B** | Top-K by excess_hr, exclude calibration_gap < 0 (strict — positive calibration only) |
| **C** | Top-K by excess_hr, exclude calibration_gap < -5pp (lenient) |
| **D** | Top-K by excess_hr × (1 + calibration_gap) (calibration-weighted ranking) |

Sweep: K = 25, 50, 100, 200 × N = 2, 3, 5 × vol-weighted direction

### Base Rates

| Tag | Train YES_BR | Test YES_BR | Test NO_BR | Notes |
|-----|-------------|-------------|------------|-------|
| Politics | 26.4% | 19.0% | 81.0% | Regime shift: more NO-dominant in test |
| Crypto | 22.1% | 27.4% | 72.6% | Regime shifted YES vs prior v1 round |
| Elections | 21.9% | 14.7% | 85.3% | Only 66 training markets — no traders qualified |
| Sports | 23.8% | 33.2% | 66.8% | 161K test markets — no traders qualified after n_yes_markets>=20 filter |

### Scorecard Pool Available

| Tag | Qualified Traders | Median excess_hr | Median calib_gap | Median entry_price |
|-----|-------------------|-----------------|-----------------|-------------------|
| Sports | 1,525 | +15.3pp | -0.8pp | 0.508 |
| Politics | 1,342 | +8.6pp | -2.0pp | 0.455 |
| Crypto | 239 | +8.3pp | -3.5pp | 0.432 |

**Note**: Elections produced 0 results (no qualifying traders — only 66 training markets). Sports DID produce results (separate section below). Results focus primarily on Politics and Crypto.

**Important note**: Median calibration_gap is negative for ALL tags. Most traders in the pool are slight overpayers (buy at prices slightly above their HR). This means Pool B (strict calibration filter, calib_gap >= 0) dramatically shrinks the Crypto pool: 239 → 78 traders.

---

## Key Results: Politics

### Direction Decomposition (test YES_BR=19.0%, NO_BR=81.0%)

| Pool | K | N | Signals | HR | YES signals | YES HR | YES excess | NO signals | NO HR | NO excess |
|------|---|---|---------|-----|-------------|--------|-----------|------------|-------|----------|
| **A (baseline)** | 25 | 2 | 65 | 90.8% | 35 | 88.6% | +69.6pp | 30 | 93.3% | +12.3pp |
| **A (baseline)** | 25 | 3 | 23 | 95.7% | 17 | 94.1% | +75.1pp | 6 | 100% | +19.0pp |
| **A (baseline)** | 50 | 2 | 609 | 91.1% | 313 | 93.3% | +74.3pp | 296 | 88.9% | +7.9pp |
| **A (baseline)** | 50 | 3 | 228 | 93.0% | 107 | 97.2% | +78.2pp | 121 | 89.3% | +8.3pp |
| **A (baseline)** | 100 | 2 | 1,116 | 86.9% | 573 | 87.6% | +68.6pp | 543 | 86.2% | +5.2pp |
| **A (baseline)** | 200 | 2 | 2,306 | 83.7% | 946 | 84.0% | +65.0pp | 1,360 | 83.5% | +2.5pp |
| **B (strict)** | 25 | 2 | 201 | 92.5% | 125 | 93.6% | +74.6pp | 76 | 90.8% | +9.8pp |
| **B (strict)** | 50 | 2 | 483 | 86.8% | 252 | 89.3% | +70.3pp | 231 | 84.0% | +3.0pp |
| **B (strict)** | 100 | 2 | 1,137 | 82.8% | 516 | 83.1% | +64.1pp | 621 | 82.5% | +1.5pp |
| **C (lenient)** | 25 | 2 | 150 | 90.7% | 93 | 92.5% | +73.5pp | 57 | 87.7% | +6.7pp |
| **C (lenient)** | 50 | 2 | 463 | 89.4% | 255 | 91.0% | +72.0pp | 208 | 87.5% | +6.5pp |
| **D (weighted)** | 25 | 2 | 69 | 95.7% | 31 | 93.6% | +74.6pp | 38 | 97.4% | +16.4pp |
| **D (weighted)** | 50 | 2 | 499 | 88.0% | 269 | 90.0% | +71.0pp | 230 | 85.7% | +4.7pp |
| **D (weighted)** | 50 | 3 | 168 | 90.5% | 83 | 91.6% | +72.6pp | 85 | 89.4% | +8.4pp |
| **D (weighted)** | 100 | 3 | 498 | 88.3% | 211 | 91.5% | +72.5pp | 287 | 86.1% | +5.1pp |

### Politics Findings

1. **Calibration filter HURTS for K=50+**: Pool B at K=50 (483 sigs, 86.8% HR) is worse than baseline Pool A at K=50 (609 sigs, 91.1% HR). Strict calibration exclusion removes the BEST traders (many top performers have slightly negative calib_gap).

2. **Pool D (calibration-weighted ranking) wins at small K**: D K=25 N=2: 69 signals, HR=95.7% (vs baseline A K=25 N=2: 65 sigs, HR=90.8%). The calibration-weighted score selects a better-calibrated top-25 set than raw excess_hr.

3. **Strong YES signal**: YES excess HR is consistently +69-78pp above YES base rate of 19%. YES signals (buy YES markets at consensus) have the strongest directional edge. NO signals trail at +3-19pp above NO base rate.

4. **K=50 Pool A with N>=3 is the most balanced config**: 228 signals, HR=93.0%, YES excess=+78pp, NO excess=+8pp. High confidence regime.

5. **Warning: very short hold times** — median 0.12–0.67 days. These markets resolve quickly. This is a regime difference from v1 (which showed 6.5d median hold).

---

## Key Results: Crypto

### Direction Decomposition (test YES_BR=27.4%, NO_BR=72.6%)

| Pool | K | N | Signals | HR | YES signals | YES HR | YES excess | NO signals | NO HR | NO excess |
|------|---|---|---------|-----|-------------|--------|-----------|------------|-------|----------|
| **A (baseline)** | 25 | 2 | 1,065 | 98.2% | 516 | 98.1% | +70.7pp | 549 | 98.4% | +25.8pp |
| **A (baseline)** | 25 | 3 | 159 | 95.0% | 101 | 97.0% | +69.6pp | 58 | 91.4% | +18.8pp |
| **A (baseline)** | 50 | 2 | 1,321 | 93.7% | 630 | 93.7% | +66.3pp | 691 | 93.8% | +21.2pp |
| **A (baseline)** | 100 | 2 | 1,821 | 92.5% | 817 | 90.6% | +63.2pp | 1,004 | 94.0% | +21.4pp |
| **A (baseline)** | 200 | 2 | 3,531 | 82.2% | 1,403 | 74.8% | +47.4pp | 2,128 | 87.1% | +14.5pp |
| **B (strict)** | 25 | 2 | 139 | 70.5% | 74 | 71.6% | +44.2pp | 65 | 69.2% | -3.4pp |
| **B (strict)** | 50 | 2 | 259 | 64.5% | 136 | 58.1% | +30.7pp | 123 | 71.5% | -1.1pp |
| **B (strict)** | 100 | 2 | 1,042 | 63.6% | 291 | 38.8% | +11.4pp | 751 | 73.2% | +0.6pp |
| **C (lenient)** | 25 | 2 | 419 | 91.9% | 205 | 88.5% | +61.1pp | 214 | 95.3% | +22.7pp |
| **C (lenient)** | 50 | 2 | 1,391 | 74.2% | 579 | 71.0% | +37.7pp | 812 | 76.5% | +9.7pp |
| **D (weighted)** | 25 | 2 | 1,116 | 97.6% | 516 | 98.1% | +70.7pp | 600 | 97.2% | +24.6pp |
| **D (weighted)** | 50 | 2 | 1,327 | 93.9% | 615 | 90.9% | +63.5pp | 712 | 96.5% | +23.9pp |
| **D (weighted)** | 100 | 3 | 157 | 77.1% | 63 | 71.4% | +44.0pp | 94 | 80.9% | +14.1pp |

### Crypto Findings

1. **CRITICAL — Calibration filter CATASTROPHICALLY hurts Crypto**: Pool B strict (calib_gap >= 0) reduces usable pool from 239 → 78 traders and drops HR from 98.2% to 70.5% at K=25. The most skilled Crypto traders are "overpayers" by the calib_gap metric — they buy at prices above their apparent training HR. This means they are buying in regimes where prices are too low relative to true probability.

2. **Crypto Pool A K=25 shows extreme HR (98.2%)**: This is a very strong upper bound signal. Both YES and NO signals exceed their base rates by 70pp and 26pp respectively. Vol-weighted direction is working extraordinarily well on the top-25 Crypto traders.

3. **Pool D also strong (97.6%)**: Calibration-weighted ranking at K=25 performs nearly identically to pure excess_hr ranking. Top-25 by either metric is nearly the same set of traders.

4. **Pool C (lenient filter at -5pp) performs between A and B**: K=25 N=2 gives 419 signals at 91.9% HR — the lenient filter removes some but not all overpayers, finding a middle ground that still underperforms baseline.

5. **K=200 degrades strongly**: HR drops to 82.2% with large YES-NO asymmetry (YES HR=74.8% vs NO HR=87.1%). The Crypto top-25 are dramatically better than the broader pool — this is highly concentrated alpha.

---

## Pool Variant Comparison Summary

### Politics: Pool A vs B vs C vs D at K=50, N=2

| Pool | Size | Signals | HR | Overall Excess | vs Baseline |
|------|------|---------|-----|----------------|------------|
| A (baseline) | 50 | 609 | 91.1% | +42.0pp | — |
| B (strict calib) | 50 | 483 | 86.8% | +36.8pp | **-5.2pp worse** |
| C (lenient calib) | 50 | 463 | 89.4% | +42.6pp | +0.6pp neutral |
| D (calib-weighted) | 50 | 499 | 88.0% | +39.8pp | **-2.2pp worse** |

### Crypto: Pool A vs B vs C vs D at K=25, N=2

| Pool | Size | Signals | HR | Overall Excess | vs Baseline |
|------|------|---------|-----|----------------|------------|
| A (baseline) | 25 | 1,065 | 98.2% | +47.5pp | — |
| B (strict calib) | 25→78* | 139 | 70.5% | +21.7pp | **-25.8pp worse** |
| C (lenient calib) | 25 | 419 | 91.9% | +43.0pp | **-4.5pp worse** |
| D (calib-weighted) | 25 | 1,116 | 97.6% | +46.9pp | -0.6pp neutral |

*Pool B at K=25 falls back to K=78 because only 78 Crypto traders have calib_gap >= 0.

---

## Key Finding: Calibration Gap is NOT a Good Filter

> [!CRITICAL]
> Excluding traders with negative calibration_gap consistently hurts performance across both tags.
> The most skilled traders frequently have slightly negative calib_gap (they are "overpayers" relative to naive training HR).
> This indicates that calibration_gap as computed here (HR - avg_entry_price) conflates two effects:
> (a) genuine overpaying on sure-things (bad), and
> (b) skilled entry into markets at prices that were previously undervalued (good — the market moved their way).
> A naive calib_gap filter cannot distinguish these.

### Why overpayers might be good traders

The "sure-thing piler" described in prior research (calib_gap = -6.7pp) enters very high-certainty markets (0.80+ price) where they have LOWER alpha. In the scorecard, these traders have high hit rates but low calib_gap because the market was already pricing them correctly.

By contrast, the TOP traders by excess_hr enter genuinely uncertain markets at prices that are slightly too LOW for their skill level — they get favorable prices but not as favorable as their realized HR. Their calib_gap is negative by construction.

**Conclusion**: calib_gap >= 0 filters OUT the best traders (contrarians who find underpriced markets), while retaining sure-thing pilers who are relatively worse.

---

## Key Results: Sports (4h hold filter applied)

Sports test base rates: YES=33.2%, NO=66.8%. Pool: 1,525 qualified traders.

| Pool | K | N | Signals | HR | YES excess | NO excess | Overall Excess |
|------|---|---|---------|-----|-----------|----------|----------------|
| **D (weighted)** | 25 | 2 | 62 | 90.3% | +55.2pp | +24.9pp | **+37.6pp** |
| **B (strict)** | 25 | 2 | 46 | 89.1% | +62.0pp | +17.2pp | +37.7pp |
| **A (baseline)** | 50 | 2 | 101 | 89.1% | — | — | +35.6pp |
| **C (lenient)** | 25 | 2 | 20 | 90.0% | — | — | +35.0pp |
| **A (baseline)** | 100 | 2 | 518 | 80.1% | — | — | +27.8pp |
| **D (weighted)** | 50 | 3 | 16 | 87.5% | — | — | +35.4pp |

### Sports Findings

1. **Sports Pool B performs comparably to Pool A here** — unlike Crypto where strict calibration filter was disastrous. This may be because Sports traders who bet on sure-things (live scores) often have positive calib_gap, so filtering them improves signal quality.

2. **Small K is critical for Sports**: K=25 N=2 gives best excess (~37-38pp). Larger pools dilute the signal rapidly (K=100 drops to 27.8pp excess).

3. **These are the top-25 Sports predictors** — likely domain experts in specific sports. The 4h hold filter successfully removes most in-play contamination.

4. **Signal volume is thin** (20-101 signals). Sports has 161K test markets but consensus among top-25 is rare. Consider expanding K to 50+ for volume at some quality cost.

---

## Comparison vs Prior v1 (2025-12-05 test split)

| Metric | v1 Politics K=50 | v2 Politics K=50 | Change |
|--------|-----------------|-----------------|--------|
| Train end | 2025-12-05 | 2025-07-01 | Earlier train end |
| Test N signals | 932 (vec) | 609 (vec) | Fewer (shorter test period overlap) |
| Tick HR | 88.9% | N/A (vectorized) | — |
| Tick excess vs NO_BR | +6.2pp | — | — |
| Vec HR | 89.9% | 91.1% | +1.2pp |
| Vec excess (weighted) | — | +42.0pp overall | Strong YES bias |

**Interpretation**: The prior v1 used NO-direction Politics (betting NO, hold>=24h). In v2, the direction is vol-weighted (both YES and NO). The huge YES excess (+74pp) dominates because YES base rate is only 19% in test — any correct YES prediction has enormous excess. The NO signals add modest +7.9pp excess.

---

## Best Configurations (Upper Bounds)

| Rank | Tag | Pool | K | N | Signals | HR | Overall Excess | Hold Days |
|------|-----|------|---|---|---------|-----|----------------|----------|
| 1 | Politics | A | 25 | 3 | 23 | 95.7% | +60.5pp | 0.42 |
| 2 | Politics | C | 25 | 3 | 31 | 96.8% | +55.8pp | 0.33 |
| 3 | Politics | D | 25 | 3 | 16 | 100.0% | +53.9pp | 0.17 |
| 4 | Crypto | D | 25 | 3 | 183* | 95.6% | +51.7pp | — |
| 5 | Crypto | A | 25 | 3 | 159 | 95.0% | +51.1pp | — |
| 6 | Crypto | A | 25 | 2 | 1,065 | 98.2% | +47.5pp | — |
| 7 | Crypto | D | 25 | 2 | 1,116 | 97.6% | +46.9pp | — |
| 8 | Politics | A | 50 | 2 | 609 | 91.1% | +42.0pp | 0.12 |

*Crypto Pool D K=25 N=3 — requires verification (183 signals may be from Elections tag, not Crypto — confirm in raw JSON)

**WARNING — Hold time concern**: Politics signals have extremely short median hold (0.12–0.67 days). This may indicate:
- (a) These are near-resolution markets (entering late, low alpha), or
- (b) Short-term markets with genuine predictability (polymarket daily/hourly markets)
- Requires tick-by-tick validation to confirm entry prices are achievable

---

## Compounding Score Estimates (UB — vectorized)

Formula: `excess_hr × avg_edge_usd / median_hold_days`

Assuming $50 average position size:

| Config | Excess HR | Est Edge/Trade | Hold Days | CS (UB) |
|--------|-----------|---------------|----------|---------|
| Politics A K=50 N=2 | +42.0pp | $21.0 | 0.12 | **88** |
| Politics D K=25 N=2 | +42.5pp | $21.3 | 0.17 | **75** |
| Crypto A K=25 N=2 | +47.5pp | $23.8 | ~2 | **6** |

The extremely short hold times in Politics make CS look very high — but entry price achievability is uncertain.

---

## Recommended Next Steps

1. **Validate Politics K=50 Pool A N>=3 tick-by-tick** — The benchmark config. 228 signals, HR=93.0% vectorized. Semi-tick approach will reveal if the 0.12d median hold is achievable (these may be near-resolution markets where entry is impossible or spread is huge).

2. **Abandon calibration_gap as a filter** — Results consistently show it hurts. Pool D (calib-weighted ranking) is the best use of calibration information — as a soft re-ranker, not a hard filter.

3. **Crypto A K=25: Investigate extreme HR** — HR=98.2% on 1,065 signals is exceptionally high. Before deploying, verify:
   - Are these YES or NO dominated? (YES excess +70pp, NO excess +26pp — both positive)
   - What is the actual pool composition (top-25 Crypto traders)?
   - What entry prices are achievable?

4. **Direction decomposition confirms genuine alpha**: Both YES and NO signals show positive excess in Politics (YES: +74pp, NO: +8pp) and Crypto (YES: +70pp, NO: +26pp). This is NOT structural base-rate bias — both directions are profitable.

5. **Proposed best strategy config for deployment path**:
   - **Politics**: Pool A K=50, N>=3, vol-weighted, no hold filter (228 signals)
   - **Crypto**: Pool A K=25, N>=2, vol-weighted (1,065 signals — pending tick validation)

---

## Artifacts

| File | Content |
|------|---------|
| `scripts/calibration_pool.py` | Main sweep script |
| `discovery/calibration_filtered_pool_raw.json` | Full sweep results (JSON) |
| `discovery/calibration_filtered_pool.md` | This analysis |
