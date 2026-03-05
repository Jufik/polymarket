# Taxonomy Layer: Classification-as-Function for Research

**Date**: 2026-03-05
**Goal**: Replace monolithic inline CTEs with reusable, temporal classification functions. Classifications are computed on-demand with a cutoff date, ensuring point-in-time correctness for backtesting.

## Problem

Research SQL queries are 150-200+ lines because they re-derive entity classifications inline. The same `market_tags + susceptible_markets` CTE block is copy-pasted across 8+ queries. No trader taxonomy exists — every query re-derives trader types from scratch.

Worse, static classifications leak future data into backtests. If a trader is classified as a "bot" using their full history through today, backtesting September 2025 uses 6 months of future information.

## Core Principle: Classification as Function

A classification is a **function** `f(cutoff) → rows`, not a static row dump.

```
                    ┌─────────────────────┐
                    │  Classification Rule │
                    │  f(cutoff) → rows    │
                    └─────┬───────────────┘
                          │
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
      Scheduler       Walk-forward    Researcher
      f(today)        f(fold_end)     f(test_date)
            │             │              │
            ▼             ▼              ▼
    ┌──────────────────────────────────────┐
    │   Classification Tables              │
    │   (entity, label, tier, score,       │
    │    as_of, rule_version, computed_at) │
    └──────────────────────────────────────┘
```

- **Scheduled refresh**: production cron runs `f(today)` daily
- **Walk-forward backtest**: harness runs `f(fold_end)` before each fold
- **Research iteration**: Researcher runs `f(test_date)` to explore

## Schema

### Two Classification Tables

```sql
CREATE TABLE trader_classifications (
    trader          String,
    label           String,          -- 'bot', 'sure_trader', 'sniper', ...
    tier            UInt8,           -- 1=strongest signal, 5=weakest
    score           Float64 DEFAULT 0,
    as_of           Date,            -- data cutoff: only data before this date was used
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, as_of, trader)

CREATE TABLE market_classifications (
    condition_id    String,
    label           String,          -- 'susceptibility', 'resolution_speed', ...
    tier            UInt8,
    score           Float64 DEFAULT 0,
    as_of           Date,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, as_of, condition_id)
```

- `as_of` — the data cutoff date. Only data before this date was used to derive the classification.
- `ReplacingMergeTree(rule_version)` — bumping version overwrites stale classifications for the same `(label, as_of, entity)`.
- `ORDER BY (label, as_of, entity)` — efficient filtering by label + time window.
- `tier` standardized: 1 = strongest signal across all labels.
- `score` optional — boolean labels use tier only, scored labels use both.

### Point-in-Time Queries

```sql
-- Discovery: get bot classifications as of a specific cutoff
SELECT trader FROM trader_classifications FINAL
WHERE label = 'bot' AND as_of = toDate('2025-09-01')

-- Backtest bootstrap: most recent classification at or before backtest start
SELECT trader FROM trader_classifications FINAL
WHERE label = 'bot' AND as_of <= toDate('2025-09-01')
ORDER BY as_of DESC LIMIT 1 BY trader
```

## Classification Rules

### Rule Format

Each rule is a `.sql` file — a parameterized INSERT...SELECT with `{cutoff}` placeholder:

```sql
-- file: classifications/trader_bot.sql
-- label: bot | entity: trader | version: 1
-- schedule: daily
-- description: Traders averaging >500 trades/day over 30 days before cutoff
INSERT INTO trader_classifications
SELECT
    maker AS trader,
    'bot' AS label,
    1 AS tier,
    avg_daily AS score,
    toDate('{cutoff}') AS as_of,
    1 AS rule_version,
    now64(3, 'UTC') AS computed_at
FROM (
    SELECT maker, count() / 30 AS avg_daily
    FROM (SELECT * FROM trades_raw FINAL)
    WHERE timestamp BETWEEN toDateTime('{cutoff}') - INTERVAL 30 DAY
      AND toDateTime('{cutoff}')
    GROUP BY maker
    HAVING avg_daily > 500
)
```

### Rule File Header Convention

```sql
-- file: classifications/{entity}_{label}.sql
-- label: {label} | entity: trader|market | version: {N}
-- schedule: daily|weekly|manual
-- description: Human-readable description of the classification logic
```

### Rule Storage Locations

| Location | Purpose |
|----------|---------|
| `docker/clickhouse/classifications/` | Production rules (Architect-reviewed, scheduled) |
| `research/hypotheses/{slug}/classifications/` | Hypothesis-local rules (Researcher testing) |

## Workflow

### Researcher Iteration (fast loop)

1. Researcher writes a `.sql` rule file in their hypothesis folder
2. Researcher calls `populate('trader_bot', cutoff=date(2025, 9, 1))` directly
3. Rule executes against CH, inserts into classification table with `as_of`
4. Researcher JOINs the classification in discovery queries or bootstrap
5. Iterate: tweak rule, re-populate, re-test

No Architect bottleneck during exploration.

### Promotion to Production

1. Researcher finds a useful classification → proposes in `discovery/notes.md`
2. Lead reviews, routes to Architect
3. Architect moves `.sql` file from hypothesis folder to `docker/clickhouse/classifications/`
4. Architect sets up scheduled refresh (cron or MV-driven)
5. All future research JOINs the production classification

### Walk-Forward Integration

```
For each fold in walk-forward:
    1. populate_all(cutoff=fold_train_end)    # compute classifications from training data only
    2. Run ReplayRunner on fold_test_window   # provider bootstrap JOINs with as_of filter
    3. Collect metrics
```

This ensures no look-ahead bias: classifications reflect only data available at the end of the training window.

## Python Runner Module

A utility module discovers rule files and executes them:

```python
# Usage:
#   populate('bot', cutoff=date(2025, 9, 1))          # single rule, specific cutoff
#   populate_all(cutoff=date.today())                  # all rules, today (scheduler)
#   populate_all(cutoff=fold_end, rules_dir=hyp_dir)   # hypothesis-local rules for backtest
```

The runner:
- Discovers `.sql` files in the rules directory
- Parses the header for metadata (label, entity, version, schedule)
- Executes with `{cutoff}` substituted
- Optionally cleans stale `as_of` entries before inserting (idempotent)

## Composable Query Pattern

Research queries JOIN classification tables instead of inline CTEs:

```sql
-- Exclude bots (point-in-time)
LEFT JOIN (
    SELECT trader FROM trader_classifications FINAL
    WHERE label = 'bot' AND as_of = toDate('{cutoff}')
) bots ON t.maker = bots.trader
WHERE bots.trader IS NULL

-- Only insider-susceptible markets (point-in-time)
INNER JOIN (
    SELECT condition_id FROM market_classifications FINAL
    WHERE label = 'susceptibility' AND tier >= 2
      AND as_of = toDate('{cutoff}')
) mkt ON t.condition_id = mkt.condition_id
```

Queries should be <50 lines by composing these building blocks.

## Available Building Blocks

| Table/View | Type | Use For |
|---|---|---|
| `trades_raw` | ReplacingMergeTree | Raw trades (use FINAL for dedup) |
| `trader_market_positions` | SummingMergeTree | Positions per (trader, condition_id) |
| `markets_resolved` | VIEW | Resolution data (condition_id, asset_id, outcome, token_won) |
| `trader_trade_agg` | SummingMergeTree | Per (trader, condition_id, asset_id) aggregation |
| `trader_volumes` | SummingMergeTree | maker_vol, taker_vol per trader |
| `trader_classifications` | ReplacingMergeTree | Trader taxonomy — temporal, function-derived |
| `market_classifications` | ReplacingMergeTree | Market taxonomy — temporal, function-derived |

## Ownership

- **Researcher**: writes rule `.sql` files in hypothesis folders, calls `populate()` for testing
- **Architect**: reviews and promotes rules to production, manages scheduled refresh, owns classification table schema
- **Lead**: routes proposals between Researcher and Architect
