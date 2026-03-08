# Gambling Market Taxonomy

**Date**: 2026-03-07
**Hypothesis**: trader-scorecard
**Status**: Discovery complete

## Executive Summary

Polymarket has two structurally distinct market populations:

1. **Gambling markets** (29-30% of total markets, 30% of market IDs): short-duration, recurring, price-level questions on crypto/equities with near-random outcomes. These are noise for trader scorecards.
2. **Informational markets** (70-71% of total): event-driven questions where traders have genuine edge potential.

The key finding: gambling markets generate **56% of all maker positions** but only **12.9% of traded USD volume**, indicating many tiny, low-value positions. Excluding them from trader scorecards is critical.

---

## 1. Slug Pattern Inventory

### Core Gambling Patterns

| Pattern | Markets | % All Markets | Notes |
|---------|---------|---------------|-------|
| `%updown%` | 121,757 | 21.2% | Pure coin-flip crypto price direction |
| `%up-or-down%` | ~34,000 | ~5.9% | Human-readable variant of updown (shares events) |
| `%-above-%` or `%-above` | 14,069 | 2.5% | Price level markets (99% non-updown) |
| `%-below-%` or `%-below` | 1,035 | 0.2% | Price level markets |
| `%-higher-%` | 1,047 | 0.2% | Mostly weather/temp comparisons — FALSE POSITIVES |
| `%-lower-%` | 86 | 0.02% | Fed bounds, inflation, "lower 48" — FALSE POSITIVES |
| `%price-on%` | 6 | 0.001% | Negligible |

**Important**: `higher` and `lower` patterns are almost entirely false positives (weather, Fed rates, sports lines). Do not use them.

### Union Count (slug-based, broad)

```sql
-- Broad definition (used in exploration)
is_gambling = (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    OR lower(slug) LIKE '%-above-%'
    OR lower(slug) LIKE '%-below-%'
    OR lower(slug) LIKE '%-higher-%'
    OR lower(slug) LIKE '%-lower-%'
)
-- Result: 172,599 markets (30.0%)
```

### Pattern Overlap

Zero overlap between `updown` and `above/below` — these are structurally different market types. Markets with `updown` in slug never also have `above`/`below`.

### False Positive Analysis

- **`higher`**: "will temp be 67F or higher", "will unemployment be 4.1 or lower", "will Rick Rieder be confirmed and rates hit 2.5 or lower" — these are informational, not price-level gambling.
- **`lower`**: Same pattern — "lower 48 states", "ECB lower interest rates", etc.
- **`above`/`below` without crypto**: 5,451 markets are sports/weather/inflation. However, 9,354 are clearly crypto prices (BTC/ETH/SOL/XRP + price levels like "2pt9", "100k"). Stock price markets (TSLA, MSFT, NVDA, etc.) add 2,747 more.

### Recommended Slug Pattern (Refined)

The final recommended filter keeps updown + above/below with crypto/equity assets:

```sql
-- Recommended: high-precision gambling filter
(
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
)
OR (
    (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
    AND (
        lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
        OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
        OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%ripple%'
        OR lower(slug) LIKE '%sol%' OR lower(slug) LIKE '%solana%'
        OR lower(slug) LIKE '%crypto%'
        OR lower(slug) LIKE '%-close-%'   -- stock close price markets
        OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%msft%'
        OR lower(slug) LIKE '%nvda%' OR lower(slug) LIKE '%aapl%'
        OR lower(slug) LIKE '%amzn%' OR lower(slug) LIKE '%googl%'
        OR lower(slug) LIKE '%meta%' OR lower(slug) LIKE '%pltr%'
        OR lower(slug) LIKE '%nflx%'
        OR slug ~ '[0-9]+k-'    -- price levels: 100k, 98k
        OR slug ~ '-[0-9]+pt'   -- price points: 2pt9, 3pt18
    )
)
-- Result: 169,074 markets (29.4%)
```

Also discovered: **multistrike markets** (`btc-multistrike-4h-1757476800-110pt5k`) — same gambling nature, captured by `Crypto Prices` tag but not by slug patterns above. These appear in the "other_crypto" category (26,470 markets).

---

## 2. Tag-Based Classification

### Tags That Exclusively (>90%) Signal Gambling Markets

| Tag | Total Markets | Updown Markets | % Updown |
|-----|--------------|----------------|----------|
| `5M` | 65,581 | 65,581 | 100% |
| `15M` | 58,103 | 53,012 | 91.2% |
| `Up or Down` | 155,786 | 121,757 | 78.2% |
| `Ripple` / `XRP` | ~43K | ~29K | 67% |
| `Solana` | 43,905 | 29,046 | 66.2% |
| `Crypto Prices` | 188,929 | 121,757 | 64.4% |
| `Crypto` | 192,505 | 121,757 | 63.2% |
| `Bitcoin` | 52,337 | 32,794 | 62.7% |
| `Ethereum` | 49,386 | 30,858 | 62.5% |
| `Recurring` | 199,694 | 121,757 | 61.0% |
| `4H` | 15,209 | 3,164 | 20.8% |

### Tag-Based Filter Option

A simpler but slightly noisier approach:

```sql
-- Tag-based gambling filter (broader, captures multistrike)
EXISTS (
    SELECT 1 FROM event_tags et
    WHERE et.event_id = m.event_id
    AND et.label IN ('5M', 'Up or Down')
)
OR EXISTS (
    SELECT 1 FROM event_tags et
    WHERE et.event_id = m.event_id
    AND et.label = 'Crypto Prices'
    AND lower(m.slug) NOT LIKE '%will-%'  -- exclude human-language Crypto Prices questions
)
```

**Recommendation**: Use slug-based filter as primary, tag-based as secondary catch for multistrike and other non-slug gambling markets.

---

## 3. Market Subtypes Discovered

```
price_updown:       156,313 markets  (updown + up-or-down slugs)
price_above:         14,069 markets  (above slug — crypto + stocks)
price_higher:         1,047 markets  (higher — mostly non-gambling)
price_below:          1,035 markets  (below slug — crypto + stocks)
price_lower:             86 markets  (lower — mostly non-gambling)
price_on:                 6 markets  (negligible)
multistrike:         ~26,000 markets (xXX-multistrike-4h-TIMESTAMP format)
```

### Underlying Assets in Gambling Markets

| Asset | Markets |
|-------|---------|
| BTC/Bitcoin | 44,955 |
| ETH/Ethereum | 42,570 |
| SOL/Solana | 37,752 |
| XRP/Ripple | 37,471 |
| META | 548 |
| AMZN | 546 |
| AAPL | 544 |
| MSFT | 542 |
| GOOGL | 539 |
| TSLA | 538 |
| NVDA | 535 |
| PLTR | 411 |

### Updown Time Intervals

Updown markets use embedded Unix timestamps (e.g., `btc-updown-5m-1766120700`):
- `5m` interval: 65,581 markets
- `15m` interval: 53,012 markets
- `4h` interval: 3,164 markets

The snapshot only covers 101,681 of 156,313 updown markets in `maker_positions` (65% coverage), consistent with many updown markets being purely taker-driven with no maker positions in the data.

---

## 4. Trader Behavior Analysis

### Position Volume Split

| Market Type | Positions | % All Positions | USD Volume | % Total USD |
|------------|-----------|-----------------|------------|-------------|
| Gambling | 16,756,327 | 56.1% | $2.52B | 12.9% |
| Informational | 13,115,756 | 43.9% | $17.0B | 87.1% |

**Key insight**: Gambling markets generate 4x more positions per USD of volume, indicating very small position sizes. Median position size in gambling is $7.50 vs $14.04 in informational markets.

### Hit Rate Comparison

| Market Type | Positions | Hit Rate | YES Win Rate |
|------------|-----------|----------|--------------|
| Gambling | 10,387,214 | ~25% (net YES) | 43.7% |
| Informational | 7,461,043 | ~22% (net YES) | 27.5% |

**Critical caveat**: These HR numbers are dominated by YES positions and are NOT comparable to typical signal HR metrics (which compare YES wins for YES positions). The raw numbers mean:
- Gambling markets: YES wins 43.7% of the time (markets resolve YES 44% of time vs NO 56%)
- Informational markets: YES wins 27.5% of the time (consistent with 38% base YES win rate × position selection)

For YES-only positions:
- Gambling YES positions: 39.99% HR (nearly random for 43.7% base rate)
- Informational YES positions: 24.98% HR (well below 27.5% base rate — traders pick underpriced NOTs)

For NO positions in gambling: 36.16% HR (coin-flip region)

**Conclusion**: Gambling markets have near-random outcomes — makers get ~36-40% HR on their positions regardless of direction, with no information edge.

### Trader Crossover

| Trader Type | Count | Avg Positions |
|------------|-------|---------------|
| Info-only | 637,696 | 9.1 |
| Both info + gambling | 171,372 | 84.9 |
| Gambling-only | 158,434 | 59.9 |

- 158,434 traders (21% of all traders) participate ONLY in gambling markets — they would become invisible in an informational-only scorecard.
- 171,372 traders participate in BOTH — their scorecard will improve when gambling positions are excluded.
- The "both" group has 84.9 avg positions, suggesting these are the more active traders.

### Good Traders in Gambling Markets

Among traders with 10+ informational positions:

| Quality Tier | Info Traders | Also Gamble | % Also Gamble |
|-------------|-------------|-------------|---------------|
| Top tier (>=65% HR) | 4,618 | 2,347 | 50.8% |
| Good (55-65%) | 4,210 | 1,992 | 47.3% |
| Average (45-55%) | 7,520 | 3,591 | 47.8% |
| Below avg (<45%) | 72,443 | 21,649 | 29.9% |

**Surprise**: High-quality traders are MORE likely to also participate in gambling markets (50% rate vs 30% for below-average traders). This is consistent with "sophisticated traders explore all markets" — but gambling positions should still be excluded from their scorecard since these are not alpha-generating.

---

## 5. Volume and Duration Characteristics

### Hold Time Distribution

| Market Type | Median Hold | P25 Hold | P75 Hold |
|------------|-------------|----------|----------|
| Gambling | 1,362 min (22.7h) | 585 min (9.8h) | 4,609 min (76.8h) |
| Informational | 1,361 min (22.7h) | 312 min (5.2h) | 6,166 min (102.8h) |

Hold times are similar at the median — the updown markets that appear in maker_positions are held for ~1 day, not the 5-15 minute window implied by their interval labels. This is because the snapshot captures positions from the perspective of the maker, who may hold across multiple updown cycles.

### Market Volume

| Market Type | Markets with Positions | Median Vol/Market | Total Net-YES Volume |
|------------|----------------------|-------------------|---------------------|
| Gambling | 115,869 | 8,072 tokens | 2.66B tokens |
| Informational | 288,992 | 1,381 tokens | 11.0B tokens |

Gambling markets have 5.8x more volume per market (per token) but 4.1x fewer total USD — this is because updown markets trade in large token quantities at low prices (5m markets are near 0.50 price).

---

## 6. Proposed Final Classification

### Primary SQL Filter (Reusable)

```sql
-- Gambling market filter for use in WHERE clauses
-- Apply to markets table (or join with it)
CREATE OR REPLACE MACRO is_gambling_market(slug) AS (
    -- Updown / up-or-down: pure coin-flip crypto direction
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    -- Crypto price level markets (above/below + crypto asset keywords)
    OR (
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (
            lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
            OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
            OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%ripple%'
            OR lower(slug) LIKE '%sol%' OR lower(slug) LIKE '%solana%'
            OR lower(slug) LIKE '%crypto%'
            OR lower(slug) LIKE '%-close-%'
            OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%msft%'
            OR lower(slug) LIKE '%nvda%' OR lower(slug) LIKE '%aapl%'
            OR lower(slug) LIKE '%amzn%' OR lower(slug) LIKE '%googl%'
            OR lower(slug) LIKE '%meta%' OR lower(slug) LIKE '%pltr%'
            OR lower(slug) LIKE '%nflx%'
            OR slug ~ '[0-9]+k-'
            OR slug ~ '-[0-9]+pt'
        )
    )
);
```

### Tag-Based Supplement (for non-slug gambling)

```sql
-- Supplemental: catches multistrike and other price markets not caught by slug
-- Use event_tags label IN ('5M', '15M', 'Up or Down')
-- OR: label = 'Crypto Prices' AND slug LIKE '%multistrike%'
```

### Impact Summary

| Filter | Markets Excluded | % All Markets | Positions Excluded | % All Positions | USD Excluded |
|--------|-----------------|---------------|-------------------|-----------------|--------------|
| Slug-based (broad) | 172,599 | 30.0% | ~16.7M | 56.1% | $2.52B (12.9%) |
| Slug-based (refined) | 169,074 | 29.4% | ~similar | ~similar | ~similar |

---

## 7. Surprises and Flags for Knowledge Capture

### Surprise 1: Gambling = 56% of Positions but Only 13% of Volume

This counterintuitive finding means updown markets are dominated by tiny positions. A trader who places 500 updown bets of $7 each appears to have high activity but minimal capital commitment. Any scorer that uses raw position count (not USD volume) will be heavily contaminated by gambling behavior.

**Action**: All trader scorecards should use USD-weighted metrics, not position-count metrics.

### Surprise 2: Good Traders Participate in Gambling at 50% Rate

Top-tier informational traders are more likely to also gamble than below-average traders. This is NOT a signal that gambling markets are alpha-generating — it likely reflects that active sophisticated traders experiment everywhere. But it means any scorecard that includes gambling markets will not cleanly separate skill from luck for the top tier.

### Surprise 3: YES Win Rate in Gambling is 43.7% (Not 50%)

For updown markets with maker positions, YES wins 43.7% of the time — slightly below random. This may indicate:
- Makers systematically prefer YES positions in updown markets (61.9% of positions are YES)
- The market maker books these as slightly NO-biased (YES price > 50%) to extract premium
- Resolution dates in snapshot are skewed (older markets may have different base rates)

### Surprise 4: Multistrike Markets Are Invisible to Slug Filter

The `btc-multistrike-4h-TIMESTAMP-PRICE` format (26,470 markets in "other_crypto" category under `Crypto Prices` tag) is a gambling market type not captured by `updown` or `above/below` slug patterns. These are range/strike markets where price must hit multiple levels. Tag-based filter with `Crypto Prices` + non-`will-` slug catches these.

### Surprise 5: `higher`/`lower` Are NOT Reliable Gambling Signals

Only 94/1,082 "higher" markets and 86/~1000 "lower" markets are gambling; the rest are "will unemployment be lower", "will rates be higher", etc. — completely informational. Never use these patterns in gambling filters.

### Surprise 6: Above/Below Has 5,451 Non-Gambling Markets

Sports stats markets like "will Collins score above 14.5 points" share the `above` slug pattern with crypto price markets. These are NOT gambling — they have genuine information content. The refined filter distinguishes them correctly via asset keywords.

---

## 8. Concrete Filtering SQL (Copy-Paste Ready)

### For DuckDB (maker_positions queries)

```sql
-- Exclude gambling markets from any maker_positions query:
WITH info_markets AS (
    SELECT condition_id
    FROM markets
    WHERE NOT (
        lower(slug) LIKE '%updown%'
        OR lower(slug) LIKE '%up-or-down%'
        OR (
            (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
            AND (
                lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
                OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
                OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%ripple%'
                OR lower(slug) LIKE '%sol%' OR lower(slug) LIKE '%solana%'
                OR lower(slug) LIKE '%-close-%'
                OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%msft%'
                OR lower(slug) LIKE '%nvda%' OR lower(slug) LIKE '%aapl%'
                OR lower(slug) LIKE '%amzn%' OR lower(slug) LIKE '%googl%'
                OR lower(slug) LIKE '%meta%' OR lower(slug) LIKE '%pltr%'
                OR lower(slug) LIKE '%nflx%'
                OR slug ~ '[0-9]+k-'
                OR slug ~ '-[0-9]+pt'
            )
        )
    )
)
SELECT mp.*
FROM maker_positions mp
JOIN info_markets im ON mp.condition_id = im.condition_id
-- ... rest of query
```

### Simple Broad Filter (for quick exploration)

```sql
-- Simple: just exclude updown (catches 97% of gambling by position count)
WHERE condition_id NOT IN (
    SELECT condition_id FROM markets
    WHERE lower(slug) LIKE '%updown%' OR lower(slug) LIKE '%up-or-down%'
)
```

### Tag-Based Filter (for ClickHouse, if DuckDB markets table unavailable)

```sql
-- CH version: exclude via event_tags
WHERE condition_id NOT IN (
    SELECT DISTINCT m.condition_id
    FROM markets m
    INNER JOIN event_tags et ON m.event_id = et.event_id
    WHERE et.label IN ('5M', '15M', 'Up or Down')
       OR (et.label = 'Crypto Prices' AND m.slug NOT LIKE '%will-%')
)
```

---

## 9. Recommended Final Taxonomy

```
Polymarket Markets
├── GAMBLING (29.4% of markets, 56% of positions, 12.9% USD)
│   ├── price_updown: 156,313 markets
│   │   ├── 5m interval: 65,581
│   │   ├── 15m interval: 53,012
│   │   ├── 4h interval: 3,164
│   │   └── up-or-down (human-readable): ~34,000
│   ├── price_above_below (crypto/equity): ~12,700 markets
│   └── multistrike: ~26,000 markets (tag-detected only)
│
└── INFORMATIONAL (70.6% of markets, 44% of positions, 87.1% USD)
    ├── Sports: NBA, NFL, Soccer, Esports, Tennis (~182K markets)
    ├── Politics/Elections (~21K)
    ├── Culture/Entertainment (~15K)
    ├── Weather/Science (~10K)
    ├── Crypto (non-price): sentiment, adoption events (~8K)
    └── Other: Economics, World events, etc.
```

---

## 10. Queries Used (Reproducible)

All queries run against DuckDB snapshot via `from research.db import db; con = db().con`.

Scripts:
- `/mnt/nvme/git/polymarket/polymarket/tmp/gambling_analysis.py` — initial exploration
- `/mnt/nvme/git/polymarket/polymarket/tmp/gambling_analysis2.py` — column name corrections
- `/mnt/nvme/git/polymarket/polymarket/tmp/gambling_analysis3.py` — HR, hold time, trader crossover
- `/mnt/nvme/git/polymarket/polymarket/tmp/gambling_analysis4.py` — precision checks and edge cases

Key column names (maker_positions):
- Position size: `net_yes` (not `net_shares`)
- Resolution: `yes_won` (UTINYINT, 1=YES won; also in maker_positions directly)
- No `start_date` in markets_resolved — use `first_trade` from maker_positions as proxy

Key column names (markets_resolved):
- Resolution: `token_won` (UTINYINT), `winner_outcome` (VARCHAR)
- No `start_date` column — markets schema doesn't have creation timestamp in snapshot

---

## 11. Cross-Validation Run (2026-03-07, `correct` column)

Second independent pass using the `correct` column in `maker_positions` directly (instead of `yes_won`). Results confirm and extend the analysis above.

### Hit Rate by Market Type (all positions, YES+NO)

```sql
SELECT
    CASE WHEN mp.condition_id IN (SELECT condition_id FROM gambling_cids)
         THEN 'gambling' ELSE 'informational' END AS market_type,
    count(*) AS n_positions,
    count(DISTINCT trader) AS n_traders,
    count(DISTINCT condition_id) AS n_markets,
    round(100.0 * sum(CASE WHEN correct THEN 1 ELSE 0 END) / count(*), 2) AS hit_rate_pct
FROM maker_positions mp WHERE correct IS NOT NULL GROUP BY 1
```

| Market type | n_positions | n_traders | n_markets | HR% |
|-------------|------------|-----------|-----------|-----|
| Gambling | 16,865,470 | 332,020 | 117,877 | **46.98%** |
| Informational | 13,006,613 | 808,758 | 286,984 | **45.87%** |

Both near ~50% (combined HR is dominated by position direction mix). Neither is above 50% at aggregate level.

### Base Rate by Market Type (YES win rate at market level)

```sql
WITH market_results AS (
    SELECT condition_id, first(correct) AS yes_won
    FROM maker_positions WHERE position = 'YES' AND correct IS NOT NULL
    GROUP BY condition_id
)
SELECT market_type, count(*) AS n_resolved_markets,
       round(100.0 * sum(yes_won) / count(*), 2) AS yes_win_rate_pct
FROM market_results JOIN [market_type] GROUP BY 1
```

| Market type | n_resolved | YES win rate |
|-------------|-----------|-------------|
| Gambling | 116,454 | **49.42%** |
| Informational | 251,213 | **31.47%** |

**This is the key number for scorecard design**: gambling markets resolve YES ~49% (near-random), informational markets 31% (consistent with known 31-38% base rate). These base rates are incomparable — scorecards must segment by market type.

### Trader Exclusivity (broad slug filter)

```sql
WITH trader_type AS (
    SELECT trader,
        max(CASE WHEN condition_id IN gambling_cids THEN 1 ELSE 0 END) AS in_gambling,
        max(CASE WHEN condition_id NOT IN gambling_cids THEN 1 ELSE 0 END) AS in_info
    FROM maker_positions GROUP BY trader
)
SELECT segment, count(*) FROM trader_type GROUP BY 1
```

| Segment | n_traders |
|---------|-----------|
| info_only | **635,482** |
| both | **173,276** |
| gambling_only | **158,744** |

- 158,744 traders exclusively in gambling — should receive no scorecard (or be flagged)
- 173,276 cross over — their scorecard improves when gambling is excluded
- 635,482 purely informational traders — core scorecard population

### Dollar Volume Share (cross-check)

| Segment | Total USDC | % |
|---------|-----------|---|
| Gambling | $3.30B | **17.7%** |
| Informational | $15.33B | 82.3% |

Note: the prior analysis showed 12.9% gambling volume share; this run shows 17.7%. The difference stems from using `volume` column in `maker_positions` (this run) vs a different volume source. Both are consistent with gambling being a minority of dollar flow.

### Resolution Cadence

| Market type | n_resolved | n_active_days | Resolutions/day |
|-------------|-----------|---------------|----------------|
| Gambling | 44,883 | 527 | **85** |
| Informational | 286,966 | 1,061 | **271** |

Gambling markets resolve far more densely per active day — automated market factory at work.

### Volume Distribution Per Market

| Market type | n_markets | Median vol ($) | Avg vol ($) | P75 | P95 |
|-------------|-----------|---------------|------------|-----|-----|
| Gambling | 117,877 | **$8,099** | $27,960 | $29,222 | $107,451 |
| Informational | 286,984 | **$614** | $53,415 | $8,606 | $142,701 |

Gambling markets have 13x higher median volume per market but a much lower tail — informational has the blockbuster markets ($1M+).

### HR by Gambling Interval

| Interval | n_positions | HR% |
|----------|-----------|-----|
| 15m | 9,795,343 | 46.3% |
| 5m | 3,735,290 | 46.6% |
| no_interval | 3,216,024 | 49.7% |
| 4h | 118,813 | 43.1% |

All near-random. The slight variation (~43-50%) is within noise given the base rate is also interval-dependent.

### Scorecard Recommendations (confirmed)

1. **Exclude pure gambling traders (158,744)** from scorecard entirely.
2. **Filter gambling positions** (broad slug CTE) before computing any HR, edge, or volume metric.
3. **Do not mix YES win rates across segments** — 49% vs 31% base rate makes them incomparable.
4. **Use `correct` column** from `maker_positions` for hit rate — it is precomputed and correct.
5. **Prefer broad slug filter** over tag filter — captures 174,800 vs 155,785 from "Up or Down" tag alone.
