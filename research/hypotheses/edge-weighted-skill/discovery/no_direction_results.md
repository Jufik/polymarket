# NO-Direction Sweep Results — edge-weighted-skill

> **All results are UPPER BOUNDS** — vectorized, not tick-by-tick validated.
> Phantom filter applied: `first_trade >= test_start`. Market-level aggregation enforced.
> Median hold = 0d for most signals — in-play contamination highly likely for Sports.

Train through: 2025-11-01 | Test: 2025-11-01 → 2026-02-01 (3 months)

## Overview

- NO-direction qualified traders (train, >=20 NO positions): **10,463**
- Test window NO base rate: **0.5989** (59.9%)
- Test window YES base rate: **0.3506** (35.1%)

## Pool Metrics (train-scored, composite VW score)

| K | avg HR | avg BEH |
|---|--------|---------|
| 50 | 0.9713 | 0.2059 |
| 100 | 0.9576 | 0.1826 |
| 200 | 0.9499 | 0.1634 |

## Consensus Sweep (vectorized UPPER BOUNDS)

Signal = N distinct pool traders enter NO side of same market.
Entry price = Nth trader's actual NO-space entry price (max first_trade).
Hold filter: 0–30 days. Phantom filter: first_trade >= test_start.

| K | N | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ | Total Edge |
|---|---|-----------|----|-----------|-----------|---------|-----------:|-----------|
| 50 | 1 | 16,839 | 0.9029 | +0.3040 | 0.5000 | 0d | +0.3323 | +5595.8 |
| 50 | 2 | 4,685 | 0.9757 | +0.3768 | 0.5000 | 0d | +0.4463 | +2090.9 |
| 50 | 3 | 1,998 | 0.9980 | +0.3991 | 0.5000 | 0d | +0.4785 | +956.1 |
| 100 | 1 | 20,223 | 0.8763 | +0.2774 | 0.5000 | 0d | +0.2898 | +5860.2 |
| 100 | 2 | 7,217 | 0.9397 | +0.3408 | 0.5000 | 0d | +0.3532 | +2549.1 |
| 100 | 3 | 3,357 | 0.9657 | +0.3668 | 0.5000 | 0d | +0.3796 | +1274.5 |
| 200 | 1 | 24,362 | 0.8557 | +0.2568 | 0.5000 | 0d | +0.2618 | +6376.9 |
| 200 | 2 | 10,118 | 0.9256 | +0.3267 | 0.5000 | 0d | +0.3242 | +3280.0 |
| 200 | 3 | 5,413 | 0.9440 | +0.3451 | 0.5000 | 0d | +0.3307 | +1789.9 |

## Per-Tag Breakdown (K=100, N=2)

| Tag | NO Base Rate | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ |
|-----|-------------|-----------|----|-----------|-----------|---------|-----------:|
| Politics | 0.7677 | 1,435 | 0.8892 | +0.1215 | 0.8860 | 1d | +0.1344 |
| Esports | 0.4914 | 43 | 0.9535 | +0.4621 | 0.6106 | 0d | +0.2329 |
| Sports | 0.5078 | 4,881 | 0.9523 | +0.4445 | 0.5000 | 0d | +0.4179 |
| Crypto | 0.7591 | 830 | 0.9602 | +0.2011 | 0.5000 | 0d | +0.3679 |

## Monthly Signal Breakdown (K=100, N=2, hold 0–30d)

| Month | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |
|-------|-----------|----|-----------|---------|-----------:|
| 2025-11 | 2,188 | 0.9557 | 0.5000 | 0d | +0.3743 |
| 2025-12 | 2,576 | 0.9344 | 0.5000 | 0d | +0.3491 |
| 2026-01 | 2,425 | 0.9336 | 0.5000 | 0d | +0.3421 |

## Compounding Scores

Formula: `excess_hr × avg_edge_per_dollar / max(median_hold_days, 0.5)`

| K | N | n_signals | HR | Excess HR | Avg Edge/$ | Hold | CS |
|---|---|-----------|----|-----------|-----------:|------|----|
| — | — | — median hold=0, CS undefined (divide by 0) — | — | — | — | — | — |

## Critical Finding: Median Hold = 0 Days

**The median hold time across all configs is 0 days.** This is the in-play contamination
signal identified in prior research: elite sports/esports/NBA traders enter during live
matches and the market resolves same-day. The HR is inflated because these are NOT copyable
signals — by the time the copy strategy fires, the event has already resolved.

This is the dominant vectorized→tick gap source for this hypothesis.

**Politics is the exception**: med_hold = 1 day, HR = 88.9%, excess = +12.2pp. Likely copyable.

## YES vs NO Comparison

| Metric | YES | NO |
|--------|-----|-----|
| Test base rate | 0.3506 | 0.5989 |
| Signal volume (K=100, N=2) | unknown | 7,217/3mo |
| Top HR (K=50, N=3) | unknown | 0.9980 |
| In-play contamination | lower risk | HIGH — median hold=0d |
| Best actionable tag | Politics | Politics (1d hold) |

## Tag-Specific NO Base Rates (test window)

| Tag | NO Base Rate |
|-----|------------|
| Tennis | 0.9732 |
| Basketball | 0.9167 |
| Politics | 0.7677 |
| Crypto | 0.7591 |
| Sports | 0.5078 |
| Esports | 0.4914 |

## Key Findings

1. **Strong vectorized signal, but dominated by in-play**: K=50, N=3: HR=99.8%, +39.9pp excess.
   BUT median hold = 0 days → same-day resolution → in-play contamination.

2. **Politics NO is genuinely actionable**: 1,435 signals/3mo, HR=88.9%, +12.2pp excess over
   76.8% base rate, med hold = 1 day, avg edge/$ = +0.134. NOT in-play.

3. **Sports/Esports/Crypto signals are in-play**: med hold = 0 days. These inflate overall
   HR metrics dramatically. Must apply hold >= 1 day filter for tick validation.

4. **10,463 NO-direction qualified traders** (train), more than the 3,677 YES-skilled (all-time).
   The NO-direction pool is large and skill is concentrated in the top-50.

5. **Compounding score**: undefined for most configs (median hold = 0). Politics-only:
   estimated CS ≈ 0.122 × 0.134 / 1.0 = 0.016 (marginal, needs tick validation).

## Next Steps

- **Tick validate Politics NO** with hold >= 1 day filter (actionable path)
- Apply `date_diff('hour', signal_entry, resolved_at) >= 24` hold filter to all configs
- Check in-play contamination: filter Sports/Esports N=2+ to hold >= 24h
- Overlap analysis: NO pool vs elite whale copy pool (0x336151559e appears in both)
## Hold-Filtered Sweep (hold >= 24h) — Removing In-Play Contamination

Re-run with `date_diff('hour', signal_entry, resolved_at) >= 24` filter.

| K | N | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |
|---|---|-----------|----|-----------|---------|-----------:|
| 50 | 1 | 1,382 | 0.7873 | 0.8594 | 4d | +0.0352 |
| 50 | 2 | 47 | 1.0000 | 0.5000 | 2d | +0.2853 |
| 50 | 3 | 6 | 1.0000 | 0.5000 | 3d | +0.3352 |
| 100 | 1 | 2,401 | 0.8042 | 0.9055 | 3d | +0.0398 |
| 100 | 2 | 999 | 0.8418 | 0.9154 | 3d | +0.0519 |
| 100 | 3 | 449 | 0.8686 | 0.9090 | 3d | +0.0685 |
| 200 | 1 | 3,389 | 0.7392 | 0.7400 | 3d | +0.0219 |
| 200 | 2 | 1,370 | 0.8423 | 0.9318 | 3d | +0.0649 |
| 200 | 3 | 871 | 0.8462 | 0.9414 | 3d | +0.0625 |

### Hold-Filtered Per-Tag (K=100, N=2, hold >= 24h)

| Tag | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |
|-----|-----------|----|-----------|---------|-----------:|
| Politics | 826 | 0.8499 | 0.9194 | 3d | +0.0478 |
| Esports | 14 | 0.9286 | 0.9900 | 12d | +0.0093 |
| Sports | 91 | 0.7143 | 0.7776 | 4d | +0.0093 |
| Crypto | 68 | 0.8971 | 0.8139 | 5d | +0.1672 |

### Interpretation

After removing in-play signals (hold < 24h):
- **Signal volume drops sharply** — confirms in-play dominated the unfiltered results
- **Politics** retains signals (already had 1-day hold)
- **Sports/Esports/Crypto** lose most signals — these were in-play
- This is the tick-validation-relevant signal count