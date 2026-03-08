# Scorecard V2 Strategies — Synthesis

**Date**: 2026-03-07
**Tracks**: 4 parallel researchers, updated scorecard (excess_hr + stability + calibration_gap + bucket_excess_hr)
**All results are VECTORIZED UPPER BOUNDS — expect 20-40pp tick degradation**

---

## Bottom Line

The new calibration_gap signal **does NOT improve strategies as a filter or gate** — it actively hurts performance when used for exclusion. However, the full composite scorecard (4 signals) provides **dramatically better walk-forward stability** than HR-only ranking, at a modest cost in peak excess HR.

**The real breakthrough finding is direction decomposition**: Sports and Crypto signals are only viable in the YES direction. NO signals in these tags are structural bias (near-zero or negative excess HR). This filter alone is worth more than any scorecard refinement.

---

## Strategy Rankings (V2)

| Rank | Strategy | Config | Signals | Excess HR | Walk-Forward | Verdict |
|------|----------|--------|---------|-----------|-------------|---------|
| 1 | **Crypto HR-only** | K=50, N=2, YES-only | 516 | +72.0pp | Untested | Highest peak signal, 3h hold, needs tick |
| 2 | **Politics Composite** | K=100, N=5, YES+NO | 232 | +62.5pp | Stable (117 sig fold 3) | Most robust, both directions work |
| 3 | **Politics HR-only** | K=100, N=5, YES+NO | 186 | +60.9pp | Collapses (1 sig fold 3) | Higher peak but brittle |
| 4 | **Crypto Composite** | K=200, N=5, YES-only | 440 | +49.7pp | Stable | High volume, decent excess |
| 5 | **Sports Composite** | K=25, N=3, YES-only | 96 | +46.9pp | Stable (148 sig fold 3) | Must filter YES-only |
| 6 | **Value Hunter Copy** | cal_gap > +5pp | - | +4.6pp (adj) | - | Not viable — tiny positions |

---

## What Each Track Found

### Track A (Calibration-filtered pool): Filtering HURTS
- Calibration gate removes traders who have slightly negative cal_gap but high excess_hr
- Crypto: 96% of elite pool has negative cal_gap — filtering destroys the pool entirely
- Politics: strict gate (cal_gap >= 0) drops HR from 91.1% to 86.8% and loses 23% of signals
- **Verdict**: Do NOT use calibration_gap as an exclusion gate

### Track B (Value hunter copy): Genuine alpha but NOT scalable
- YES value hunters (948 traders, cal_gap > +5pp): +63.4pp price-adjusted excess
- FATAL: avg position size $0.50-$5.00, median hold = 0 days (post-resolution settlement noise)
- NO value hunters: +4.6pp price-adjusted (marginal, will vanish in tick)
- Zero overlap with HR-only top-50 — completely different population
- **Verdict**: Genuine skill exists but not commercially copyable

### Track C (Anti-overpayer filter): Smaller K beats filtering
- Unfiltered K=30 beats filtered K=50 by +6pp excess HR with same signal volume
- Calibration filtering merely shrinks the pool without improving quality
- **Verdict**: Use tighter K (20-30) instead of calibration gates

### Track D (Full composite scorecard): Walk-forward champion
- HR-only beats composite by 2-24pp in static test window
- BUT **HR-only collapses in walk-forward**: 1 signal in fold 3 vs composite's 117-148
- Composite selects fundamentally different traders (Jaccard 0.10-0.21 overlap)
- Gained traders: 2-5x higher consistency_sharpe, 5-12x higher avg_edge_usd
- **Verdict**: Use composite for production (robustness > peak performance)

---

## Critical Finding: Direction Decomposition

This is the most actionable discovery across all 4 tracks:

| Tag | YES Signals | YES Excess | NO Signals | NO Excess | Action |
|-----|------------|-----------|-----------|----------|--------|
| Politics | 85 | **+67.6pp** | 166 | **+14.5pp** | Both viable, YES stronger |
| Sports | 112 | **+39.1pp** | 443 | **-3.7pp** | YES-only (NO is structural bias) |
| Crypto | 39 | **+50.4pp** | 207 | **+0.4pp** | YES-only (NO is random) |

**80% of Sports composite signals are NO-direction with -3.7pp excess** — pure structural bias masquerading as signal. Filtering to YES-only transforms Sports from a marginal strategy to a genuine one.

---

## V2 vs V1 Comparison

| Metric | V1 Best (Politics NO K=50) | V2 Best (Politics Composite K=100 N=5) |
|--------|---------------------------|----------------------------------------|
| Excess HR | +6.2pp (semi-tick) | +62.5pp (vectorized UB) |
| Signals | 1,563 (semi-tick) | 232 (vectorized) |
| Walk-forward | Not tested | Stable across 3 folds |
| Direction | NO only | YES (+67.6pp) + NO (+14.5pp) |
| Expected tick | Unknown | ~22-42pp after 20-40pp degradation |

**Key difference**: V1 used semi-tick methodology (not real tick-by-tick), so the +6.2pp was likely inflated. V2 vectorized results are higher but will degrade in tick. The comparison isn't apples-to-apples — V2 tick validation is the critical next step.

**New opportunities V1 missed**:
- **Crypto YES consensus** (K=50, N=2): 516 signals at 98.5% HR — didn't exist in V1 because V1 killed Crypto via direction decomposition on aggregate (both YES+NO). V2 shows Crypto YES is strong, Crypto NO is what killed it.
- **Sports YES-only**: V1 dismissed Sports entirely due to in-play contamination. V2 with ≥4h hold + YES-only filter shows +39-47pp excess.

---

## Tick Validation Candidates (Priority Order)

| Priority | Strategy | Why | Expected Post-Tick |
|----------|----------|-----|-------------------|
| 1 | **Politics Composite K=100 N=5** | Walk-forward stable, both directions, 232 signals | +22-42pp excess |
| 2 | **Crypto HR-only K=50 N=2 YES-only** | Highest raw signal (98.5% HR), but 3h hold is suspicious | Uncertain — may be in-play |
| 3 | **Sports Composite K=25 N=3 YES-only** | New opportunity, walk-forward stable, 96 signals | +6-27pp excess |
| 4 | **Politics HR-only K=100 N=5** | Compare with composite to measure walk-forward penalty | +20-40pp but may collapse |

---

## Scorecard Architecture Recommendation

### For Production Consensus Pool

Use the **composite scorecard** for trader ranking (walk-forward stability matters more than peak HR):

```
composite = 0.45 * percentile(excess_hr)
          + 0.25 * percentile(consistency_sharpe)
          + 0.15 * percentile(avg_edge_usd)
          + 0.15 * percentile(bucket_excess_hr)
```

### Do NOT Use

- Calibration_gap as an exclusion gate (hurts across all tags)
- Sure-thing penalty (inverted — high sure-thing ratio = higher excess HR)
- Value hunter copy as standalone strategy (not scalable)

### DO Use

- **Direction filter**: YES-only for Sports and Crypto (most impactful single filter)
- **Composite ranking over HR-only** (prevents walk-forward collapse)
- **Tighter K (25-50)** over larger K with filters (simpler, more effective)
- **N >= 3-5** for consensus (late majority proven superior from V1)

---

## Open Questions for Tick Validation

1. **Are 3h hold Crypto signals real?** Or post-resolution settlement noise?
2. **Does composite walk-forward advantage survive in tick?** (The argument: stable pool = stable tick results)
3. **Sports YES-only at +39pp** — how much survives after realistic fill model + in-play removal?
4. **Can we combine tags?** Multi-tag portfolio (Politics + Crypto YES + Sports YES) for diversification

---

## Artifacts

```
research/hypotheses/scorecard-v2-strategies/
├── discovery/
│   ├── calibration_filtered_pool.md        # Track A
│   ├── calibration_filtered_pool_raw.json
│   ├── value_hunter_copy.md                # Track B
│   ├── anti_overpayer_filter.md            # Track C
│   └── composite_scorecard_pool.md         # Track D
├── scripts/
│   ├── calibration_pool.py
│   ├── value_hunter.py
│   ├── anti_overpayer.py
│   └── composite_pool.py
└── synthesis.md                            # This document
```
