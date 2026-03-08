# Volume-Weighted Consensus Direction Beats Head-Count

> **TL;DR**: When computing consensus direction, weighting by USD volume outperforms simple head-count by 5-16pp across all tags. When they disagree, vol-weighted wins 67-89% of the time.

> [!TIP]
> Always use volume-weighted direction for consensus strategies. Traders who commit more capital carry more information. Head-count (one-person-one-vote) dilutes signal from high-conviction large traders.

## Finding

Smart Money Pool strategy (Strategy 2) tested head-count vs vol-weighted direction across 4 tags:

| Tag | Head-Count HR | Vol-Weighted HR | Delta | Vol Wins When Disagree |
|-----|--------------|-----------------|-------|----------------------|
| Sports | 63.6% | 69.1% | +5.5pp | 67% |
| Esports | 71.7% | 78.7% | +7.0pp | — |
| Crypto | 78.4% | 84.2% | +5.8pp | 89% |
| Elections | 69.8% | 84.6% | +14.8pp | 85% |

Elections shows the largest gap (+14.8pp) — suggesting that in political markets, a few large-volume informed traders are much more informative than many small participants.

## Evidence

DuckDB analysis in `research/hypotheses/scorecard-strategies/strategy2_smart_pool.md`.

```sql
-- Vol-weighted direction:
vol_weighted_direction = sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END)
-- Direction = YES if vol_weighted_direction > 0, NO otherwise
```

## Impact

- **Production**: Use vol-weighted direction in consensus trigger, not head-count
- **Scorecard**: Consider weighting consensus votes by trader composite score (similar principle)
- **Capital allocation**: Larger positions = more information, not just more risk

## Related

- `signals/hr_persistence.md` — HR is the primary quality signal for pool selection
- `signals/stability_bonus.md` — stability filters the pool, vol-weighting ranks the signal

## Tags

`consensus`, `volume-weighted`, `direction`, `signal-quality`, `production-decision`
