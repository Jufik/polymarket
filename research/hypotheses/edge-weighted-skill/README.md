# Hypothesis: Edge-Weighted Skill Scoring — Multi-Axis Trader Quality

**Status**: `discovery`
**Created**: 2026-03-09
**Category**: cross-category (global + per-tag decomposition)

## Statement

Trader skill should be measured as excess hit rate over the *price-level-specific* base rate
at each entry price bucket, not raw HR. This "edge-weighted" scoring, decomposed by tag,
direction, and time period, should identify a small pool of genuinely skilled traders whose
signals are profitable to copy, hedge, or scalp — across two distinct regimes:
(A) a real-time copy strategy for in-play/longshot elite traders, and
(B) a composite-pool consensus strategy for mid-price skilled traders.

## Prior Art

- **Entry price bucket analysis**: HR alone captures market structure, not skill. Only 7% overlap
  between top-200 by HR vs top-200 by edge-over-base. Widest skill spread in 0.05-0.30 range.
- **Elite whale copy (in-play-traders)**: Top-100 pool → 94.2% HR, $52,932/month tick-validated.
  Two regimes: high-price volume game + low-price information game.
- **Composite scorecard**: excess_hr×0.45 + consistency×0.25 + avg_edge×0.15 + bucket_excess×0.15
  provides walk-forward stability vs HR-only collapse.
- **Price-level base rates**: Market well-calibrated (favorite-longshot bias). 0.99 entry = +0.46pp
  structural alpha; 0.30-0.50 = -12pp structural headwind.

## Research Axes

### Axis 1: Global Analysis
- Compute bucket-excess-HR for all traders across all resolved markets
- Build composite score with bucket_excess_hr as primary signal (not secondary)
- Walk-forward stability: 12m train / 1m test, sliding window

### Axis 2: Per-Tag Decomposition
- Tag-specific base rates × price-level base rates = 2D base rate grid
- Identify which tags have the most exploitable edge concentration
- Sports, Esports, Crypto, Politics, Culture — separate pools per tag?

### Axis 3: Per-Direction Analysis
- YES-skill vs NO-skill at each price bucket (separate base rates)
- Some tags are YES-only (Sports, Crypto), others work both directions (Politics)
- Direction × price × tag = 3D decomposition of genuine skill

### Axis 4: Time Stability (Walk-Forward)
- Monthly/quarterly skill persistence
- Do edge-weighted rankings produce more stable OOS pools than HR rankings?
- Identify traders with multi-period, multi-bucket edge consistency

### Axis 5: Elite Copy vs Elite Pooling
- **Copy**: Real-time wallet following (1-2 traders, high concentration)
  - In-play track: longshot entries (<0.30), 58-min lead time
  - Sure-thing track: 0.90+ entries, volume game
- **Pooling**: Composite-scored pool, consensus trigger (N traders agree)
  - Mid-price regime (0.30-0.85), consensus N=2-3
  - More diversified, lower per-signal edge, higher signal count

### Axis 6: In-Play as Dedicated Strategy
- In-play is a feature, not contamination (for RT copy)
- Dedicated in-play track: real-time monitoring, <0.30 entry, min hold filter
- Capital allocation: separate from mid-price consensus

## Success Criteria

- Excess HR > 10pp above price-level base rate (per bucket)
- Walk-forward stable: skill rank correlation > 0.3 across periods
- Positive PnL after realistic slippage in tick-by-tick
- Compounding score > 5.0 for at least one strategy track
- Sample size > 200 trades OOS per strategy track

## Scores (Scorecard v3 — Tick-Validated)

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| **Sports YES K=25 N=2** | | | |
| Hit Rate | 86.7% | 63.3% | -23.4pp |
| Excess HR | +53.4pp | +30.0pp | -23.4pp |
| Sharpe | — | 5.23 | — |
| Fills (8mo) | 30 | 2,023 | — |
| **Politics NO K=100 N=2** | | | |
| Hit Rate | 81.8% | 83.0% | +1.2pp |
| Excess HR | +8.8pp | +9.3pp | +0.6pp |
| Sharpe | — | 0.55 | — |
| Fills (8mo) | 66 | 347 | — |
| **Sports InPlay K=25 N=1** | | | |
| Hit Rate | 67.5% | 60.2% | -7.3pp |
| Excess HR | +34.2pp | +26.9pp | -7.3pp |
| Sharpe | — | 0.27 | — |
| Fills (8mo) | 320 | 5,936 | — |

## Decision

**Partially promoted.** The original hypothesis (promote BEH to primary scoring weight) was
REJECTED — edge_primary is less stable than composite in walk-forward. But the research
produced three valuable outputs:

1. **BEH as qualification gate** (not ranking signal): `bucket_excess_hr >= 0.02` removes
   near-certainty bettors. Integrated into scorecard v3 pool builder.
2. **NO-direction consensus**: First tick-validated NO strategy (Politics NO K=100 N=2, +9.3pp).
3. **Direction decomposition**: 51% of traders are NO-skilled vs 12.6% YES-skilled.
   Per-tag profiles: Sports/Crypto=YES, Esports=pure NO, Politics=both.

**Sports YES v3 promoted to paper_dev** (`configs/sports_yes_v3.toml`).
Politics NO and InPlay classified as MARGINAL/VIABLE — monitoring.

## What Worked / Anti-Knowledge

- **BEH as primary weight**: REJECTED. Amplifies bucket-level noise in walk-forward.
  BEH converges with excess_hr at the top of the distribution (Jaccard=1.0 for top-100).
  Value is as a screening filter (gate >= 0.02), not as a ranking signal.
- **Generalizable lesson**: When two correlated signals converge at the extremes,
  re-weighting them produces identical rankings. The differentiation is in the mid-range
  (filtering out noise traders), not the top (selecting elite traders).
- **NO-direction is real but thin**: +9.3pp excess is genuine, but 54-day hold and
  Sharpe=0.55 make it a satellite position, not a core strategy.
- **In-play degradation is fill-model, not latency**: Only 7.3pp degradation because
  sub-second WS delivery captures the 58-min elite lead time.

## Spawned

- `scorecard-v3-strategies` — the v3 implementation (tick-validated)
- `portfolio-three-tracks` — combined Sports YES + Politics NO + InPlay portfolio
- `dual-skill-market-selector` — use 964 dual-skilled traders as market quality signal
- Knowledge entries: `signals/edge_weighted_skill.md`, `signals/no_direction_consensus.md`,
  `methodology/README.md`
