# Vectorized vs Tick-by-Tick Simulation Gaps

> **TL;DR**: Vectorized (SQL aggregate) backtests are 20-40pp more optimistic than tick-by-tick replay. Every vectorized result must be discounted.

> [!CRITICAL]
> Never trust vectorized PnL as a deployment estimate. Multiply by 0.3-0.5 for realistic expectation.

> [!CRITICAL]
> Always validate vectorized discoveries with ReplayRunner tick-by-tick before allocating capital.

> [!WARNING]
> YES and NO signals degrade asymmetrically: NO HR can drop 30-50pp, while YES HR may actually improve in tick-by-tick due to fewer false entries.

## Finding

Running the same strategy logic in vectorized mode (SQL aggregates over resolved positions) vs tick-by-tick mode (replay individual trades with real consensus building) produces dramatically different results. The gap is not a single issue but a cascade of compounding effects.

## Nine Identified Gaps (ranked by impact)

### CRITICAL
1. **Consensus counts trades not unique traders** — 1 trader making 4 trades = fake "consensus 4". 72.6% of market-sides have only 1 unique trader. Fix: use sets, not counters.

### HIGH
2. **SELL trades != directional signals** — SELL is a position exit or split-entry. Blindly copying SELLs as directional signals corrupts signal quality. ~23% of trades are SELLs.
3. **Capital constraint** — Vectorized assumes unlimited positions. Reality: N max concurrent. Long-dated markets (politics 22d) block capital.

### MEDIUM
4. **Look-through bias** — Vectorized uses final net position (avg of many trades). Tick-by-tick enters at the Nth qualifying trade (usually worse price).
5. **Entry price divergence** — Trader's blended avg entry != price at copy time.
6. **Survivorship bias** — Many positions from recent months are still unresolved. Vectorized only counts resolved.

### LOW
7. **Consensus timing** — First N-1 traders are missed (below threshold). Capture rate < 100%.
8. **Correct definition** — Direction-correct vs PnL-correct: >97% alignment (negligible).

## Evidence

Key verification query:
```sql
-- How many market-sides have genuinely N+ unique qualified traders?
SELECT
    count(*) AS n_market_sides,
    countIf(uniq_traders >= {consensus_threshold}) AS has_real_consensus,
    has_real_consensus / n_market_sides AS pct
FROM (
    SELECT condition_id, side, uniqExact(lower(maker)) AS uniq_traders
    FROM (SELECT * FROM trades_raw FINAL)
    WHERE lower(maker) IN (
        SELECT trader FROM trader_classifications FINAL
        WHERE label = '{pool_label}'
    )
    GROUP BY condition_id, side
)
```

## Impact

**For any future vectorized research:**
1. Report vectorized results as UPPER BOUNDS, not expectations
2. Apply a discount factor: multiply vectorized PnL by 0.3-0.5
3. Always validate key findings with tick-by-tick replay before committing capital
4. Separate YES and NO signals — they have very different vectorized-to-tick degradation
5. Filter by hold time (max_hold_hours) to avoid long-dated capital traps

**For the research framework:**
- Vectorized is efficient for signal DISCOVERY (sweep parameters cheaply)
- Tick-by-tick is required for signal VALIDATION (real PnL estimation)
- The workflow is: vectorized sweep -> identify candidates -> tick-by-tick validate -> deploy

## Related

- `pitfalls/vectorized_counting_unit.md` — [CRITICAL] Trader-position vs market-signal counting unit mismatch
- `pitfalls/sell_is_exit.md` — SELL signals, biggest single contributor to NO HR collapse
- `pitfalls/consensus_dedup.md` — Fake consensus inflates tradeable universe
- `execution/hold_time_capital.md` — Capital constraint, vectorized assumes unlimited slots
- `execution/position_settlement.md` — Settlement is required for tick-by-tick to work at all

## Tags

`vectorized`, `tick-by-tick`, `backtest-bias`, `critical`, `simulation-gap`
