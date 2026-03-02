# Market Base Rates

> **TL;DR**: 38.1% of resolved markets are won by YES, 61.9% by NO. Any strategy must beat these base rates.

> [!WARNING]
> NO-only strategies look deceptively good. A 65% NO hit rate is only 3pp above the 62% base rate — barely signal.

> [!TIP]
> Always report hit rate relative to base rate: `excess_hr = strategy_hr - base_rate_hr`. This is the real alpha.

## Finding

Across 390K+ resolved Polymarket markets, the NO outcome wins ~62% of the time. This means a naive "always bet NO" strategy has a 62% hit rate. Any directional signal must be evaluated against this asymmetric baseline, not 50/50.

The imbalance exists because many markets are framed as "Will X happen?" where X is unlikely (elections, crypto milestones, etc). The YES token is the speculative/hopeful side.

## Evidence

```sql
-- research/knowledge/queries/base_rates.sql
SELECT
    countIf(token_won = 1 AND outcome = 'YES') AS yes_won,
    countIf(token_won = 1 AND outcome = 'NO') AS no_won,
    yes_won / (yes_won + no_won) AS yes_pct
FROM markets_resolved
WHERE resolved_at > '1970-01-02'
-- Result: ~38.1% YES, ~61.9% NO
```

## Impact

- A "high hit rate" YES strategy needs HR > 38% to be above baseline
- A "high hit rate" NO strategy needs HR > 62% to be above baseline
- NO-only strategies look deceptively good until you adjust for base rate
- Always report hit rate relative to base rate, not absolute

## Related

- `data/resolution_mechanics.md` — How resolution is determined (asset_id, not strings)
- `pitfalls/vectorized_vs_tick.md` — Base rate comparison is essential when interpreting vectorized results

## Tags

`base-rate`, `data-quality`, `market-structure`, `bias`
