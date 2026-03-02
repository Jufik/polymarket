# Resolution Mechanics

> **TL;DR**: Resolution is asset_id-based (`token_won` boolean), never string-based. Use `markets_resolved.token_won`, not `winner_outcome`.

> [!CRITICAL]
> Never compare `outcome == winner_outcome`. Use `asset_id IN resolution.winning_asset_ids`. String comparison silently fails on multi-outcome markets.

## Finding

The `markets_resolved` view has one row per (condition_id, asset_id). Each row has:
- `outcome`: "YES" or "NO" (token label)
- `token_won`: 0 or 1 (boolean — did this token pay out $1?)
- `winner_outcome`: free-text string ("Blue Jays", "Yes", "No", "Up", etc.)

The `winner_outcome` field is **unreliable for programmatic use**:
- Multi-outcome markets use names like "Blue Jays", "Thunder" — not "YES"/"NO"
- Case varies: "Yes" vs "YES" vs "yes"
- Some markets have outcome names like "Under 2.5", "No Touchdown"

The `token_won` field is the ground truth: binary, unambiguous, works for all market types.

## Evidence

```sql
-- research/knowledge/queries/resolution_format.sql
SELECT winner_outcome, count(*) AS n
FROM markets_resolved WHERE winner_outcome != ''
GROUP BY winner_outcome ORDER BY n DESC LIMIT 10
-- Shows: "No" (271K), "Down" (102K), "Up" (102K), "Yes" (90K), "Under" (76K), ...
-- These are NOT the token outcome labels — they're the market question answers.
```

## Impact

- **NEVER compare `record.outcome == winner_outcome`** — will fail for multi-outcome markets
- **ALWAYS use `asset_id IN resolution.winning_asset_ids`** for PnL computation
- The `ReplayRunner` uses `MarketResolution(winning_asset_ids=frozenset)` — this is correct
- The old `enrich_resolutions(dict[str, tuple[str, float]])` is string-based — avoid for new code

## Related

- `execution/position_settlement.md` — Settlement uses asset_id-based resolution (this is correct)
- `data/market_base_rates.md` — Base rates computed via `token_won` (asset_id-based, correct)

## Tags

`resolution`, `data-quality`, `asset-id`, `critical-bug-source`
