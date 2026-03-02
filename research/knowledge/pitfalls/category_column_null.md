# markets.category Column is 99.3% NULL

> **TL;DR**: The `markets.category` column in ClickHouse is NULL for 554,540 of 558,553 markets. Any filtering using `m.category` is effectively non-functional.

> [!CRITICAL]
> Never use `markets.category` for market classification or exclusion. Use the tag chain: `markets -> events -> event_tags -> tags`. The category column catches <1% of markets.

## Finding

The `markets.category` column is populated for only 4,013 markets (0.7%):
- Sports: 2,499
- Other categories: 1,514 total (Crypto 369, US-current-affairs 356, etc.)
- NULL: 554,540 (99.3%)

When the S2 strategy uses `exclude_categories=("Sports", "Weather")` with
`lower(coalesce(m.category, '')) NOT IN ('sports', 'weather')`:
- Sports excluded: 2,499 of 267,945 actual sports markets (0.9%)
- Weather excluded: 0 of 11,982 actual weather markets (0.0%)

The tag chain (`events -> event_tags -> tags`) has full coverage:
- Sports tags: 267,945 markets
- Weather tags: 11,982 markets

## Evidence

```sql
-- Category column distribution
SELECT coalesce(category, 'NULL') AS cat, count(*) AS n
FROM markets GROUP BY cat ORDER BY n DESC LIMIT 5
-- NULL: 554,540 | Sports: 2,499 | Crypto: 369 | US-current-affairs: 356

-- Tag-based vs category-based count
SELECT 'tag_sports', count(DISTINCT m.condition_id)
FROM markets m
INNER JOIN events e ON m.event_id = e.id
INNER JOIN event_tags et ON e.id = et.event_id
INNER JOIN tags t ON et.tag_id = t.id
WHERE lower(t.label) IN ('sports', 'basketball', 'soccer', 'nba', ...)
-- 267,945

SELECT 'category_sports', count(*)
FROM markets WHERE lower(coalesce(category, '')) = 'sports'
-- 2,499
```

## Impact

- **S2 strategy**: `exclude_categories` parameter is decorative. Must fix `qualified_traders_query()` to use tag chain.
- **Any market classification**: Never rely on `m.category`. Always use the tag join chain.
- **Tag-based susceptibility** (in `queries/tag_susceptibility.sql`) is the correct approach.

## Related

- `data/market_base_rates.md` -- base rates should exclude gambling via tags, not categories
- `signals/insider_copy.md` -- insider copy already uses tag-based susceptibility

## Tags

`data-quality`, `category`, `tags`, `market-classification`, `critical-bug`
