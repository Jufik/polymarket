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

## Scores

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | — | — | — |
| Sharpe | — | — | — |
| Avg Edge | — | — | — |
| Compounding | — | — | — |
| Trades/mo | — | — | — |

## Decision

{Pending discovery}

## Anti-Knowledge (if rejected)

What we learned from this failure:

- **Signal tested**: {what didn't work}
- **Why it failed**: {root cause}
- **Conditions for revisiting**: {what would need to change}
- **Generalizable lesson**: {what applies beyond this specific hypothesis}
