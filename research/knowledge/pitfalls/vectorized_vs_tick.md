# Vectorized vs Tick-by-Tick Simulation Gaps

> **TL;DR**: Vectorized (SQL aggregate) backtests are 20-40pp more optimistic than tick-by-tick replay. Every vectorized result must be discounted.

> [!CRITICAL]
> Never trust vectorized PnL as a deployment estimate. Multiply by 0.3-0.5 for realistic expectation.

> [!CRITICAL]
> Always validate vectorized discoveries with ReplayRunner tick-by-tick before allocating capital.

> [!WARNING]
> YES and NO signals degrade asymmetrically: NO HR drops ~48pp, YES HR may actually improve ~7pp in tick-by-tick.

## Finding

Running the same strategy logic in vectorized mode (SQL aggregates over resolved positions) vs tick-by-tick mode (replay individual trades with real consensus building) produces dramatically different results. For S1 copy-trading:

| Metric | Vectorized | Tick-by-Tick | Gap |
|--------|---:|---:|---|
| YES HR | 80% | 87% | +7pp (tick better — fewer false entries) |
| NO HR | 82% | 34% | -48pp (vectorized wildly optimistic) |
| Fills/month | 1,377 | 355 | 26% coverage |

## Nine Identified Gaps (ranked by impact)

### CRITICAL
1. **Consensus counts trades not unique traders** — 1 trader making 4 trades = fake "consensus 4". 72.6% of market-sides have only 1 unique trader. Fix: use sets, not counters.

### HIGH
2. **SELL trades ≠ directional signals** — SELL is a position exit. Copying SELL YES as "go NO" is wrong. 22.8% of qualified trades are SELLs.
3. **Capital constraint** — Vectorized assumes unlimited positions. Reality: 50 max concurrent. Long-dated markets (politics 22d) block capital.

### MEDIUM
4. **Look-through bias** — Vectorized uses final net position (avg of 13 trades). Tick-by-tick enters at the Nth qualifying trade (usually worse).
5. **Entry price divergence** — Trader's blended avg entry ≠ price at copy time.
6. **Survivorship bias** — 50.3% of positions from Jul 2025+ still unresolved. Vectorized only counts resolved.

### LOW
7. **Consensus timing** — First 3 traders missed (below threshold). 60.5% capture rate.
8. **Correct definition** — Direction-correct vs PnL-correct: 97.5% alignment (negligible).

## Evidence

Full analysis with CH queries: `research/S1_VECTORIZED_GAPS.md`

Key verification query:
```sql
-- How many market-sides have genuinely 4+ unique qualified traders?
SELECT
    count(*) AS n_market_sides,
    countIf(uniq_traders >= 4) AS has_real_consensus,
    has_real_consensus / n_market_sides AS pct
FROM (
    SELECT condition_id, side, uniqExact(lower(maker)) AS uniq_traders
    FROM trades_raw FINAL
    WHERE lower(maker) IN (SELECT trader FROM _tmp_replay_qualified)
      AND toStartOfMonth(timestamp) = '2025-07-01'
    GROUP BY condition_id, side
)
```

## Impact

**For any future vectorized research:**
1. Report vectorized results as UPPER BOUNDS, not expectations
2. Apply a discount factor: multiply vectorized PnL by 0.3-0.5
3. Always validate key findings with tick-by-tick replay before committing capital
4. Separate YES and NO signals — they have very different vectorized→tick degradation
5. Filter by hold time (max_hold_hours) to avoid long-dated capital traps

**For the research framework:**
- Vectorized is efficient for signal DISCOVERY (sweep parameters cheaply)
- Tick-by-tick is required for signal VALIDATION (real PnL estimation)
- The workflow is: vectorized sweep → identify candidates → tick-by-tick validate → deploy

## Related

- `pitfalls/sell_is_exit.md` — Gap #2 (SELL signals), biggest single contributor to NO HR collapse
- `pitfalls/consensus_dedup.md` — Gap #1 (fake consensus), inflates tradeable universe by 42%
- `execution/hold_time_capital.md` — Gap #3 (capital constraint), vectorized assumes unlimited slots
- `execution/position_settlement.md` — Settlement is required for tick-by-tick to work at all

## Tags

`vectorized`, `tick-by-tick`, `backtest-bias`, `critical`, `simulation-gap`
