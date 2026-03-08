# In-Play Signal Contamination: 63% of Sports Signals Are Uncopyable

> **TL;DR**: The majority of high-HR sports signals in vectorized backtests come from traders entering AFTER the outcome is effectively known (live-score watchers entering minutes before settlement). These signals are uncopyable in production.

> [!CRITICAL]
> Any vectorized sweep on sports-related tags WITHOUT a hold-time filter (≥4h minimum, ≥1d recommended) is inflated by 15-30pp. In-play entries have 99.8% HR at hold<1h but cannot be replicated by a copy/consensus strategy. ALL future vectorized sweeps MUST include hold-time filtering.

> [!WARNING]
> The contamination is not limited to obvious sports tags. Any tag with same-day resolution events (Esports, Culture events, Awards) can exhibit this pattern. Check hold-time distribution before trusting any vectorized HR.

## Finding

Across 3 strategy research tracks (tag-expert consensus, smart money pool, elite copy):

| Hold Duration | Signals | HR | Copyable? |
|--------------|---------|-----|-----------|
| 0h (<1h) | 486 | 99.8% | NO — in-play |
| 1-4h | 5,113 | 97.0% | UNLIKELY — still in-play for short games |
| 4-24h | 2,180 | 76.7% | YES — genuine predictions |
| 1-3d | 902 | 80.6% | YES |
| 3d+ | 1,086 | 79.5% | YES |

63% of elite copy signals (11,383/17,832) resolve same-day. After filtering to hold≥1d, elite copy HR drops from 59.2% to 51.4%.

## Mechanism

Sports markets (NBA, NFL, Soccer, etc.) remain tradeable during live games. Sophisticated traders watching live scores enter YES positions when the outcome is clear but before official settlement. This creates extreme apparent accuracy that is:
1. Not predictive (outcome is already known)
2. Not copyable (requires sub-minute execution during live events)
3. Inflates all aggregate metrics by 15-30pp

## Impact

- **Vectorized sweeps**: Add `hold_hours >= 4` (or `hold_hours >= 24`) to all sports-related queries
- **Signal counting**: Same-day signals inflate signal count by ~2-3x
- **Consensus trigger**: `max(first_trade)` across consensus traders partially mitigates (later entries more likely in-play)
- **Tags affected**: Sports, NBA, NFL, Soccer, NHL, NCAA, Esports, Tennis, MLB, Golf

## Recommended Filter

```sql
-- Add to all vectorized sweeps:
AND date_diff('hour', max(first_trade), resolved_at) >= 4  -- minimum 4h hold
-- Or for conservative estimates:
AND date_diff('day', CAST(max(first_trade) AS DATE), CAST(resolved_at AS DATE)) >= 1
```

## Related

- `pitfalls/vectorized_vs_tick.md` — in-play is a separate degradation source beyond the usual 20-40pp
- `execution/hold_time_capital.md` — hold time filtering affects capital efficiency estimates
- `signals/hr_persistence.md` — HR persistence analysis should also exclude in-play

## Tags

`in-play`, `sports`, `hold-time`, `contamination`, `critical`, `vectorized-bias`
