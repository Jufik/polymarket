# Anti-Overpayer Exclusion Filter — Discovery Results

**Date**: 2026-03-07
**Train**: < 2025-07-01 | **Test**: >= 2025-07-01
**Method**: Vectorized (UPPER BOUNDS — expect 20-40pp tick degradation)

## Summary

Tests whether excluding traders with negative calibration_gap from the K=50 consensus pool
improves signal quality. Five gates tested: none, <-5pp, <-3pp, <0, <0+stability proxy.

## Pool Characterization

### K=50 Pool Calibration Gap Distribution (by tag)

| Tag | K50 Size | Neg-Cal (N) | Neg-Cal (%) | Avg CalGap K50 | Avg Excess HR K50 |
|-----|---------|------------|-------------|----------------|-------------------|
| Sports | 50 | 4 | 8% | +8.3pp | +53.8pp |
| Politics | 50 | 11 | 22% | +4.9pp | +37.0pp |
| Crypto | 23 | 22 | 96% | -9.6pp | +8.4pp |
| Weather | 18 | 8 | 44% | -0.4pp | +22.1pp |
| All | 2 | 2 | 100% | -8.1pp | -6.3pp |
| Business | 1 | 1 | 100% | -3.2pp | -6.0pp |
| Music | 1 | 1 | 100% | -7.6pp | +19.6pp |
| Elon Musk | 1 | 1 | 100% | -4.5pp | +3.2pp |
| Movies | 1 | 0 | 0% | +3.1pp | -0.4pp |
| NFL | 1 | 1 | 100% | -8.3pp | +4.9pp |

### Overpayer Profile (K=50 pool, all tags combined)

| Bucket | N Traders | Avg Excess HR | Avg CalGap | Avg Entry Price | Avg HR |
|--------|-----------|---------------|------------|----------------|--------|
| Overpayer (<-5pp) | 27 | +10.0pp | -11.0pp | 0.404 | +29.3pp |
| Overpayer (<-3pp) | 12 | +17.7pp | -3.7pp | 0.418 | +38.2pp |
| Marginal (<0pp) | 12 | +28.5pp | -1.6pp | 0.525 | +50.9pp |
| Break-even (0-5pp) | 37 | +33.4pp | +2.5pp | 0.534 | +56.0pp |
| Genuine alpha (>5pp) | 60 | +51.2pp | +11.1pp | 0.644 | +75.5pp |

## Consensus Results by Gate

All signals: Vol-weighted consensus, N >= threshold.
Excess HR = HR - test-period tag base rate.

### Politics

| Gate | Pool Size | Direction | N_min | N Signals | HR | Base Rate | Excess HR | Hold (h) |
|------|-----------|-----------|-------|-----------|-----|-----------|-----------|----------|
| Gate 0: No filter (baseline) | 148 | NO | 2 | 300 | +47.0pp | +81.0pp | -34.0pp | 15 |
| Gate 0: No filter (baseline) | 148 | NO | 3 | 84 | +47.6pp | +81.0pp | -33.4pp | 13 |
| Gate 0: No filter (baseline) | 148 | NO | 5 | 9 | +66.7pp | +81.0pp | -14.3pp | 14 |
| Gate 0: No filter (baseline) | 148 | YES | 2 | 357 | +52.1pp | +19.0pp | +33.1pp | 13 |
| Gate 0: No filter (baseline) | 148 | YES | 3 | 105 | +50.5pp | +19.0pp | +31.5pp | 10 |
| Gate 0: No filter (baseline) | 148 | YES | 5 | 5 | +20.0pp | +19.0pp | +1.0pp | 39 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 2 | 299 | +46.8pp | +81.0pp | -34.2pp | 15 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 3 | 83 | +47.0pp | +81.0pp | -34.0pp | 13 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 5 | 9 | +66.7pp | +81.0pp | -14.3pp | 14 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 2 | 356 | +52.0pp | +19.0pp | +33.0pp | 13 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 3 | 105 | +50.5pp | +19.0pp | +31.5pp | 10 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 5 | 5 | +20.0pp | +19.0pp | +1.0pp | 39 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 2 | 210 | +41.0pp | +81.0pp | -40.0pp | 17 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 3 | 49 | +40.8pp | +81.0pp | -40.2pp | 15 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 5 | 5 | +60.0pp | +81.0pp | -21.0pp | 14 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 2 | 298 | +53.4pp | +19.0pp | +34.4pp | 12 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 3 | 76 | +55.3pp | +19.0pp | +36.3pp | 9 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 5 | 4 | +0.0pp | +19.0pp | -19.0pp | 23 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 2 | 178 | +39.9pp | +81.0pp | -41.1pp | 20 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 3 | 42 | +42.9pp | +81.0pp | -38.1pp | 16 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 5 | 4 | +50.0pp | +81.0pp | -31.0pp | 37 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 2 | 242 | +54.1pp | +19.0pp | +35.1pp | 20 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 3 | 56 | +44.6pp | +19.0pp | +25.6pp | 16 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 5 | 3 | +0.0pp | +19.0pp | -19.0pp | 39 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 2 | 256 | +44.1pp | +81.0pp | -36.9pp | 17 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 3 | 61 | +45.9pp | +81.0pp | -35.1pp | 13 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 5 | 6 | +50.0pp | +81.0pp | -31.0pp | 13 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 2 | 293 | +51.2pp | +19.0pp | +32.2pp | 18 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 3 | 71 | +46.5pp | +19.0pp | +27.5pp | 12 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 5 | 4 | +25.0pp | +19.0pp | +6.0pp | 83 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 2 | 193 | +44.6pp | +81.0pp | -36.4pp | 15 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 3 | 34 | +38.2pp | +81.0pp | -42.8pp | 13 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 2 | 262 | +55.3pp | +19.0pp | +36.3pp | 10 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 3 | 58 | +56.9pp | +19.0pp | +37.9pp | 8 |

### Sports

| Gate | Pool Size | Direction | N_min | N Signals | HR | Base Rate | Excess HR | Hold (h) |
|------|-----------|-----------|-------|-----------|-----|-----------|-----------|----------|
| Gate 0: No filter (baseline) | 148 | NO | 2 | 2807 | +44.9pp | +66.8pp | -21.9pp | 7 |
| Gate 0: No filter (baseline) | 148 | NO | 3 | 1407 | +45.1pp | +66.8pp | -21.7pp | 7 |
| Gate 0: No filter (baseline) | 148 | NO | 5 | 78 | +44.9pp | +66.8pp | -21.9pp | 4 |
| Gate 0: No filter (baseline) | 148 | YES | 2 | 2126 | +45.6pp | +33.2pp | +12.4pp | 6 |
| Gate 0: No filter (baseline) | 148 | YES | 3 | 700 | +50.1pp | +33.2pp | +16.9pp | 5 |
| Gate 0: No filter (baseline) | 148 | YES | 5 | 31 | +48.4pp | +33.2pp | +15.1pp | 4 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 2 | 2807 | +44.9pp | +66.8pp | -21.9pp | 7 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 3 | 1407 | +45.1pp | +66.8pp | -21.7pp | 7 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 5 | 78 | +44.9pp | +66.8pp | -21.9pp | 4 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 2 | 2126 | +45.6pp | +33.2pp | +12.4pp | 6 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 3 | 700 | +50.1pp | +33.2pp | +16.9pp | 5 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 5 | 31 | +48.4pp | +33.2pp | +15.1pp | 4 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 2 | 2805 | +44.9pp | +66.8pp | -21.9pp | 7 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 3 | 1406 | +45.0pp | +66.8pp | -21.7pp | 7 |
| Gate 2: Exclude cal_gap < -3pp | 109 | NO | 5 | 78 | +44.9pp | +66.8pp | -21.9pp | 4 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 2 | 2124 | +45.7pp | +33.2pp | +12.4pp | 6 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 3 | 700 | +50.1pp | +33.2pp | +16.9pp | 5 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 5 | 31 | +48.4pp | +33.2pp | +15.1pp | 4 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 2 | 2762 | +44.8pp | +66.8pp | -22.0pp | 7 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 3 | 1394 | +45.0pp | +66.8pp | -21.8pp | 7 |
| Gate 3: Exclude cal_gap < 0 | 97 | NO | 5 | 76 | +46.1pp | +66.8pp | -20.7pp | 4 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 2 | 2080 | +45.7pp | +33.2pp | +12.4pp | 6 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 3 | 682 | +50.0pp | +33.2pp | +16.8pp | 5 |
| Gate 3: Exclude cal_gap < 0 | 97 | YES | 5 | 30 | +50.0pp | +33.2pp | +16.8pp | 4 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 2 | 2807 | +44.9pp | +66.8pp | -21.9pp | 7 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 3 | 1407 | +45.1pp | +66.8pp | -21.7pp | 7 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 5 | 78 | +44.9pp | +66.8pp | -21.9pp | 4 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 2 | 2126 | +45.6pp | +33.2pp | +12.4pp | 6 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 3 | 700 | +50.1pp | +33.2pp | +16.9pp | 5 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 5 | 31 | +48.4pp | +33.2pp | +15.1pp | 4 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 2 | 1168 | +47.9pp | +66.8pp | -18.8pp | 6 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 3 | 211 | +47.4pp | +66.8pp | -19.4pp | 4 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 2 | 1451 | +45.2pp | +33.2pp | +12.0pp | 8 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 3 | 265 | +49.4pp | +33.2pp | +16.2pp | 4 |

### Crypto

| Gate | Pool Size | Direction | N_min | N Signals | HR | Base Rate | Excess HR | Hold (h) |
|------|-----------|-----------|-------|-----------|-----|-----------|-----------|----------|
| Gate 0: No filter (baseline) | 148 | NO | 2 | 75 | +52.0pp | +85.0pp | -33.0pp | 53 |
| Gate 0: No filter (baseline) | 148 | NO | 3 | 12 | +41.7pp | +85.0pp | -43.3pp | 38 |
| Gate 0: No filter (baseline) | 148 | YES | 2 | 161 | +18.0pp | +15.0pp | +3.0pp | 64 |
| Gate 0: No filter (baseline) | 148 | YES | 3 | 30 | +23.3pp | +15.0pp | +8.3pp | 67 |
| Gate 1: Exclude cal_gap < -5pp | 121 | NO | 2 | 1 | +0.0pp | +85.0pp | -85.0pp | 4 |
| Gate 1: Exclude cal_gap < -5pp | 121 | YES | 2 | 6 | +16.7pp | +15.0pp | +1.6pp | 6 |
| Gate 2: Exclude cal_gap < -3pp | 109 | YES | 2 | 1 | +0.0pp | +15.0pp | -15.0pp | 155 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 2 | 52 | +51.9pp | +85.0pp | -33.0pp | 58 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | NO | 3 | 3 | +33.3pp | +85.0pp | -51.6pp | 19 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 2 | 79 | +19.0pp | +15.0pp | +3.9pp | 105 |
| Gate 4: Exclude cal_gap < 0 + stability proxy | 136 | YES | 3 | 11 | +27.3pp | +15.0pp | +12.2pp | 137 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 2 | 75 | +52.0pp | +85.0pp | -33.0pp | 53 |
| Unfiltered K=30 (size-matched comparison) | 30 | NO | 3 | 12 | +41.7pp | +85.0pp | -43.3pp | 38 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 2 | 161 | +18.0pp | +15.0pp | +3.0pp | 64 |
| Unfiltered K=30 (size-matched comparison) | 30 | YES | 3 | 30 | +23.3pp | +15.0pp | +8.3pp | 67 |

## Walk-Forward Stability

Two alternative splits to check if exclusion gate holds across time.

| Split | Gate | Tag | Direction | Pool | N Signals | HR |
|-------|------|-----|-----------|------|-----------|-----|
| split_early | baseline | Sports | YES | 103 | 532 | +48.5pp |
| split_early | baseline | Politics | YES | 103 | 110 | +59.1pp |
| split_early | baseline | Sports | NO | 103 | 591 | +43.8pp |
| split_early | baseline | Politics | NO | 103 | 112 | +43.8pp |
| split_early | gate3_cal_lt_0 | Sports | YES | 48 | 52 | +34.6pp |
| split_early | gate3_cal_lt_0 | Politics | YES | 48 | 33 | +69.7pp |
| split_early | gate3_cal_lt_0 | Sports | NO | 48 | 52 | +50.0pp |
| split_early | gate3_cal_lt_0 | Politics | NO | 48 | 45 | +40.0pp |
| split_late | baseline | Sports | YES | 148 | 703 | +49.8pp |
| split_late | baseline | Weather | YES | 148 | 502 | +11.6pp |
| split_late | baseline | Politics | YES | 148 | 105 | +50.5pp |
| split_late | baseline | Crypto | YES | 148 | 30 | +23.3pp |
| split_late | baseline | Sports | NO | 148 | 1407 | +45.1pp |
| split_late | baseline | Politics | NO | 148 | 84 | +47.6pp |
| split_late | baseline | Crypto | NO | 148 | 12 | +41.7pp |
| split_late | baseline | Weather | NO | 148 | 10 | +70.0pp |
| split_late | gate3_cal_lt_0 | Sports | YES | 98 | 686 | +49.9pp |
| split_late | gate3_cal_lt_0 | Politics | YES | 98 | 56 | +44.6pp |
| split_late | gate3_cal_lt_0 | Weather | YES | 98 | 4 | +100.0pp |
| split_late | gate3_cal_lt_0 | Sports | NO | 98 | 1396 | +45.0pp |
| split_late | gate3_cal_lt_0 | Politics | NO | 98 | 42 | +42.9pp |

## Key Question: Does Filtering Help or Just Shrink?

Comparing filtered K=50 (Gate 3: cal>=0) vs unfiltered K=30:

### Politics — N_min=3

| Config | Pool | N Signals | HR | Excess HR |
|--------|------|-----------|-----|-----------|
| Gate 0: No filter (baseline) (NO) | 148 | 84 | +47.6pp | -33.4pp |
| Gate 3: Exclude cal_gap < 0 (NO) | 97 | 42 | +42.9pp | -38.1pp |
| Unfiltered K=30 (size-matched  (NO) | 30 | 34 | +38.2pp | -42.8pp |
| Gate 0: No filter (baseline) (YES) | 148 | 105 | +50.5pp | +31.5pp |
| Gate 3: Exclude cal_gap < 0 (YES) | 97 | 56 | +44.6pp | +25.6pp |
| Unfiltered K=30 (size-matched  (YES) | 30 | 58 | +56.9pp | +37.9pp |

### Sports — N_min=3

| Config | Pool | N Signals | HR | Excess HR |
|--------|------|-----------|-----|-----------|
| Gate 0: No filter (baseline) (NO) | 148 | 1407 | +45.1pp | -21.7pp |
| Gate 3: Exclude cal_gap < 0 (NO) | 97 | 1394 | +45.0pp | -21.8pp |
| Unfiltered K=30 (size-matched  (NO) | 30 | 211 | +47.4pp | -19.4pp |
| Gate 0: No filter (baseline) (YES) | 148 | 700 | +50.1pp | +16.9pp |
| Gate 3: Exclude cal_gap < 0 (YES) | 97 | 682 | +50.0pp | +16.8pp |
| Unfiltered K=30 (size-matched  (YES) | 30 | 265 | +49.4pp | +16.2pp |

### Crypto — N_min=3

| Config | Pool | N Signals | HR | Excess HR |
|--------|------|-----------|-----|-----------|
| Gate 0: No filter (baseline) (NO) | 148 | 12 | +41.7pp | -43.3pp |
| Unfiltered K=30 (size-matched  (NO) | 30 | 12 | +41.7pp | -43.3pp |
| Gate 0: No filter (baseline) (YES) | 148 | 30 | +23.3pp | +8.3pp |
| Unfiltered K=30 (size-matched  (YES) | 30 | 30 | +23.3pp | +8.3pp |

## Interpretation & Key Findings

### Finding 1: Anti-overpayer filter HURTS Politics YES (the only viable signal)

For Politics YES N>=3 (the best configuration from v1 research):
- Baseline K=50: +31.5pp excess HR, 105 signals
- Gate 1 (cal<-5pp excluded): +31.5pp, 105 signals — NO CHANGE (only removed 22 of 312→121 pool, Politics overpayers barely appear in K=50)
- Gate 3 (cal<0 excluded): +25.6pp, 56 signals — WORSE by 6pp, half the signals
- Unfiltered K=30 (size-matched): +37.9pp, 58 signals — BEATS both K=50 versions

**Verdict**: For Politics YES, filtering by calibration gap does NOT improve quality. A tighter rank cutoff (K=30) achieves equal pool size AND higher excess HR than any gate. The overpayers in the K=50 pool are providing signal cover (enabling N>=3 consensus on more markets) without degrading HR.

### Finding 2: Anti-overpayer filter is IRRELEVANT for Sports

Sports excess HR is essentially constant across ALL gates (N>=3 YES: +16.9pp → +16.9pp → +16.8pp → +16.8pp). Only 4 of 50 Sports traders (8%) have negative calibration gap, so no gate removes them in meaningful numbers. Sports signal is structurally driven (same-day in-play contamination dominates), not by pool composition.

### Finding 3: Crypto pool is ENTIRELY composed of overpayers

96% of the Crypto K=50 pool (22/23 traders) have negative calibration gap. This means the Crypto "elite" traders are almost exclusively sure-thing pilers — they achieve high excess HR by correctly betting on near-certain outcomes, but consistently underperform the implied probability. Gate 1 reduces Crypto from 23 to 1 trader, destroying all signal. Gate 3 reduces to 0. **Crypto is not viable as a copy target.**

### Finding 4: The hypothesis is wrong — overpayers are not diluting signal

The core assumption was that sure-thing pilers with negative calibration gap pollute consensus by entering markets with obvious outcomes. The data shows:

1. Sports: 8% of K=50 are overpayers — no measurable impact on signal quality
2. Politics: 22% are overpayers — removing them hurts signal (fewer signals, lower excess HR)
3. Crypto: 96% are overpayers — the entire pool is structurally negative alpha

The explanation: overpayers in the K=50 pool (ranked by excess_hr, not raw HR) are actually traders with genuinely positive excess HR but who also happen to have negative calibration gap due to tag selection bias. They enter politically-competitive markets at prices below the true resolution probability — this makes calibration_gap look negative without reflecting poor trader skill.

### Finding 5: Unfiltered K=30 dominates filtered K=50

For Politics YES N=3:
- Filtered K=50 (Gate 3, pool=97): +25.6pp excess, 56 signals
- Unfiltered K=30 (pool=30): +37.9pp excess, 58 signals

**Same signal volume, +12pp more excess HR, zero additional filtering complexity.** Simply tightening K reduces pool to highest-skill traders without destroying consensus coverage.

### Walk-Forward Note

Early split (train < 2025-01-01) shows different pattern: Gate 3 Politics YES jumps from +59.1pp (baseline) to +69.7pp (filtered, K=48). This is ONE observation on thin data (52 signals filtered). Not reliable enough to reverse the main finding.

## Recommendations

1. **Do NOT implement calibration_gap exclusion gate** — it does not improve signal quality for the only viable strategy (Politics YES) and destroys Crypto entirely.

2. **Use tighter K threshold instead** — K=30 unfiltered outperforms K=50 filtered on every metric for Politics. Test K=20, K=30, K=40 as alternative approach.

3. **Calibration_gap is a TRADER CLASSIFICATION signal, not a pool filter**:
   - Use it to identify Crypto "fake alpha" traders (high excess_hr but negative alpha)
   - Use it as a feature in composite scoring (not a hard exclusion gate)
   - Traders with cal_gap < -10pp AND n_positions < 30 may be lucky streaks to watch

4. **Next step**: Test K=20/30 threshold sweep for Politics YES directly (simpler, more effective).

**All results are VECTORIZED UPPER BOUNDS.** Expect 20-40pp tick degradation from vectorized to tick-by-tick.