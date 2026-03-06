# Vectorized Counting Unit Mismatch

> **TL;DR**: Vectorized sweeps that join traders back to positions count TRADER-POSITIONS (N rows per consensus market), not MARKET-SIGNALS (1 row per market). This inflates signal count ~3-5x and corrupts hold time and compounding score.

> [!CRITICAL]
> Every vectorized sweep MUST aggregate to market-level (one row per condition_id) before computing signal count, hold time, or compounding score. Trader-level aggregation produces metrics that are 2-5x too optimistic on capital recycling speed.

> [!CRITICAL]
> Hold time in vectorized = `dateDiff(trader.first_trade, resolved_at)` which is per-TRADER. The deployable strategy enters at the consensus TRIGGER moment (Nth qualified BUY). Use `max(first_trade)` across consensus traders as the signal entry time, not individual trader entry times.

## Finding

Vectorized SQL that joins consensus markets back to ALL qualified traders' positions produces ~N rows per market (one per qualified trader). Metrics computed over these rows reflect per-trader statistics, not per-market (deployable) statistics.

Typical inflation factors:

| Metric | Vectorized (per-trader) | Tick (per-market) | Inflation |
|--------|------------------------|-------------------|-----------|
| Signals/month | 3-5x inflated | correct | 3-5x |
| Median hold | 2-4x shorter | correct | 2-4x |
| Compounding | ~10-20% inflated | correct | compounds from hold + PnL |

## Three Bugs

### Bug 1: Signal Count Inflation

```sql
-- BAD: counts trader-positions (N per consensus market)
SELECT count(*) AS n_positions
FROM positions p
JOIN consensus c ON p.condition_id = c.condition_id

-- GOOD: counts market-signals (1 per consensus market)
SELECT count(DISTINCT p.condition_id) AS n_signals
FROM positions p
JOIN consensus c ON p.condition_id = c.condition_id
```

### Bug 2: Hold Time Definition

```sql
-- BAD: per-trader hold (trader's first_trade to resolution)
-- Some traders entered 300d ago, some 2d ago — median is meaningless
dateDiff('day', p.first_trade, p.resolved_at) AS hold_days

-- GOOD: per-signal hold (consensus trigger to resolution)
-- The strategy enters when the Nth qualified trader acts
dateDiff('day', max(p.first_trade), p.resolved_at) AS signal_hold_days
-- (max across consensus traders ≈ when the last required trader entered)
```

### Bug 3: Weighted Average of Medians

```python
# BAD: weighted mean of per-window medians (not a true median)
a["hold_sum"] += (rec["med_hold"] or 7) * rec["n_pos"]
med_hold = a["hold_sum"] / a["n_pos"]  # also: 'or 7' silently fills NULLs

# GOOD: collect all values, compute true median
all_holds.extend(rec["per_market_holds"])
med_hold = np.median(all_holds)
```

## Correct Vectorized Sweep Pattern

```sql
-- Step 1: Build consensus at MARKET level (one row per market)
WITH consensus_markets AS (
    SELECT
        p.condition_id,
        p.tag,
        p.position,
        uniqExact(p.trader) AS n_qualified,
        -- Signal entry = latest qualifying trader's first trade
        max(p.first_trade) AS signal_entry,
        -- Use any() for market-level fields (same for all traders)
        any(p.resolved_at) AS resolved_at,
        any(p.correct) AS market_correct,  -- all traders share resolution
        -- Market-level PnL: average across qualified traders, or simulate $50 entry
        avg(p.realized_pnl) AS avg_trader_pnl
    FROM positions p
    JOIN qualified_traders q ON p.trader = q.trader AND p.tag = q.tag
    WHERE p.resolved_date >= '{test_start}' AND p.resolved_date < '{test_end}'
    GROUP BY p.condition_id, p.tag, p.position
    HAVING n_qualified >= {consensus}
)
-- Step 2: Compute metrics at MARKET level
SELECT
    tag, position,
    count(*) AS n_signals,             -- market count, not trader count
    countIf(market_correct) / count(*) AS hit_rate,
    median(avg_trader_pnl) AS median_pnl,
    median(dateDiff('day', signal_entry, resolved_at)) AS median_hold_days
FROM consensus_markets
GROUP BY tag, position
```

## Checklist for Future Vectorized Sweeps

Before computing compounding score or presenting results:

1. **Signal count**: is it `count(DISTINCT condition_id)` or `count(*)`? Must be market-level.
2. **Hold time**: is it from trader's `first_trade` or from the consensus trigger? Must be trigger-to-resolution.
3. **PnL**: is it per-trader or per-market? For deployment, use per-market (simulate one entry at signal price).
4. **Aggregation**: are you averaging medians or computing a true median? Use true median.
5. **Compounding score**: recompute after fixing 1-4 above.

## Related

- `pitfalls/vectorized_vs_tick.md` — broader vectorized gap (20-40pp HR degradation)
- `pitfalls/vectorized_tick_gap_anatomy.md` — 6 compounding effects in the gap
- `execution/hold_time_capital.md` — hold time impacts capital recycling

## Tags

`vectorized`, `counting-unit`, `methodology`, `critical`, `compounding-score`, `hold-time`
