# Consensus Must Count Unique Traders, Not Trades

> **TL;DR**: A consensus signal of "4+ traders agree" means 4 unique addresses, not 4 trade events from potentially one trader.

> [!CRITICAL]
> Use `set.add(maker)` for consensus tracking, never `counter += 1`. 72.6% of "consensus >= 4" by trade count is a single trader.

## Finding

Active traders make multiple trades per market (avg 12.9 trades per position, 70.9% have 2+ trades). If consensus counts trade events instead of unique maker addresses, a single active trader can single-handedly trigger a "consensus of 4" by trading 4 times.

Quantified: 72.6% of market-sides with "consensus >= 4" by trade count have only 1 unique qualified trader. This inflates the tradeable universe by ~11,500 fake-consensus markets per month.

Impact on PnL: trades dropped from 617 to 355 (42% were fake consensus) and YES HR jumped from 77% to 87% after fixing.

## Evidence

```sql
SELECT
    count(*) AS n_market_sides,
    countIf(uniq_traders = 1) AS single_trader,
    countIf(uniq_traders >= 4) AS real_consensus_4,
    round(single_trader / n_market_sides * 100, 1) AS single_pct
FROM (
    SELECT condition_id,
           multiIf(/* direction logic */) AS dir,
           uniqExact(lower(maker)) AS uniq_traders,
           count(*) AS trade_count
    FROM trades_raw FINAL
    WHERE lower(maker) IN (SELECT trader FROM _tmp_replay_qualified)
    GROUP BY condition_id, dir
)
WHERE trade_count >= 4
-- single_pct: 72.6% have only 1 unique trader despite 4+ trades
```

## Impact

- **Provider implementation**: Use `set.add(maker)` not `counter += 1`
- **Any multi-signal strategy**: Always deduplicate signal sources by unique entity
- **Vectorized simulation**: Already correct (uses per-trader positions, not per-trade)
- This is a general pattern: anywhere you count "how many X agree", ensure X is unique

## Related

- `pitfalls/vectorized_vs_tick.md` — Consensus dedup is Gap #1, largest universe inflation source
- `pitfalls/sell_is_exit.md` — SELL trades must be filtered BEFORE consensus counting
- `data/market_base_rates.md` — After dedup, check HR against base rates to confirm real signal

## Tags

`consensus`, `dedup`, `critical-bug`, `provider`, `signal-quality`
