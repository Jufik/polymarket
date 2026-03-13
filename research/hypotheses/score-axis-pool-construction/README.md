# Hypothesis: Score-Axis Pool Construction

**Status**: `discovery`
**Created**: 2026-03-11
**Category**: sports (primary), politics (secondary)

## Statement

Construct two disjoint trader pools ranked by orthogonal skill axes (excess_hr vs consistency_sharpe),
then require traders from BOTH axes to be present in a market before signaling. Vectorized discovery
from cross-pool-consensus showed +16pp HR for score_axis construction vs random split.

## Prior Art

- **Spawned from**: cross-pool-consensus discovery (2026-03-09)
- **Key finding**: Score-axis construction achieves Jaccard=0.000 (fully disjoint) and +16pp HR
  in directional mode vs random pool split (87.9% vs 71.4%)
- **Risk**: BUY-only mode had only 3-12 signals/8mo. Directional fires at 0.86 avg price (BE=86%).
  Pools fire simultaneously (no temporal structure).

## Success Criteria

- Excess HR > 10pp above tag-specific base rate (Sports YES: ~40.7%, Politics NO: ~62%)
- Positive PnL after realistic slippage
- Compounding score > 5 (must beat existing v3 strategies)
- Sample size > 50 trades tick-validated
- Must survive 20-40pp vectorized-to-tick degradation

## Scores

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | — | — | — |
| Sharpe | — | — | — |
| Avg Edge | — | — | — |
| Compounding | — | — | — |
| Trades/mo | — | — | — |

## Decision

{Pending discovery phase}

## Anti-Knowledge (if rejected)

What we learned from this failure:

- **Signal tested**: {what didn't work}
- **Why it failed**: {root cause}
- **Conditions for revisiting**: {what would need to change}
- **Generalizable lesson**: {what applies beyond this specific hypothesis}
