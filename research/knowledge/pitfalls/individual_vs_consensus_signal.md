# Individual Trade vs Consensus Signal

> [!CRITICAL]
> Vectorized sweeps that aggregate at market level implicitly measure consensus: N qualified traders
> must all be present for a market to appear in results. Tick-by-tick strategies that fire on the
> FIRST individual qualified trader's entry replicate a fundamentally different (weaker) signal.
> This mismatch is the primary cause of HR collapse in tag-hr-copy: 67% vectorized → 46% tick.

## Explanation

When a vectorized sweep counts markets where N qualified traders held a position, it measures
markets where consensus formed. The signal it captures is: "multiple independent informed traders
converged on this outcome." That convergence itself is the predictor.

A tick-by-tick strategy that copies each qualified trader's first BUY independently is measuring
something else: "one qualified trader entered." A single qualified trader entering a market is
much noisier than N traders converging. The vectorized HR reflects consensus quality; the tick HR
reflects individual quality.

The fix is to replicate the vectorized counting unit in execution: buffer qualified trades per
`condition_id` and emit a single entry intent only when N distinct qualified traders have entered
within a time window.

## Data / Evidence

From tag-hr-copy validation (2026-03):

| Tag | Vectorized HR | Tick HR | Degradation |
|-----|--------------|---------|-------------|
| Esports | 67.2% | 45.8% | -21.4pp |
| 1H | 78.0% | 49.8% | -28.2pp |
| Tennis | 72.4% | 40.6% | -31.8pp |

Vectorized sweep used `HAVING n_qualified >= {consensus}` at market level. Tick-by-tick strategy
fired on first individual trade. All three tags showed 20-32pp HR collapse, and median PnL went
deeply negative (-$102.50 on $100 positions), confirming structural mismatch rather than simulation
friction.

Correct implementation:
```sql
-- Vectorized counting (what the sweep measured):
SELECT condition_id, uniqExact(trader) AS n_qualified
FROM positions p JOIN qualified q ON p.trader = q.trader
GROUP BY condition_id
HAVING n_qualified >= {consensus}
```

```python
# Tick-by-tick equivalent:
# Buffer trades; fire intent only when len(qualified_set[cid]) >= consensus
```

## Methodology Fix

> [!CRITICAL]
> The vectorized counting unit must match the tick-by-tick trigger mechanism.
> If you aggregate differently in vectorized than how the strategy will actually fire,
> you're measuring a different signal.

**If the strategy fires on individual traders:**
```sql
-- WRONG: market-level (implicitly measures consensus)
SELECT condition_id, any(correct) as correct
FROM qualified_positions
GROUP BY condition_id

-- RIGHT: trader-level (matches tick-by-tick trigger)
SELECT trader, condition_id, correct
FROM qualified_positions
-- Each (trader, market) pair is one signal
-- HR = avg(correct) across all pairs
```

**If the strategy fires on consensus (N traders):**
```sql
-- Explicitly simulate the trigger
SELECT condition_id, correct,
       count(DISTINCT trader) as n_qualified
FROM qualified_positions
GROUP BY condition_id
HAVING n_qualified >= 3  -- explicit consensus threshold
```

**Three checkpoints before any vectorized sweep:**
1. **What fires the signal?** (individual entry / Nth trader / price threshold)
2. **Does the GROUP BY match?** (trader-level if individual, market-level if consensus)
3. **Is the entry time correct?** (first trader's entry vs Nth trader's entry vs signal time)

## Related

- `pitfalls/vectorized_counting_unit.md` — counting unit rule (market vs trader-position)
- `pitfalls/vectorized_vs_tick.md` — expected degradation bands (20-40pp)
- `pitfalls/consensus_dedup.md` — count unique traders, not trades

## Tags

`consensus`, `vectorized-vs-tick`, `signal-design`, `copy-trading`, `degradation`, `esports`, `1h`, `tennis`
