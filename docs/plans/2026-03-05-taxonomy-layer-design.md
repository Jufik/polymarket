# Taxonomy Layer: Classification-as-Function for Research

**Date**: 2026-03-05
**Goal**: Replace monolithic inline CTEs with reusable classification functions. Classifications are computed on-demand with a cutoff date, ensuring point-in-time correctness for backtesting.

## Problem

Research SQL queries are 150-200+ lines because they re-derive entity classifications inline. The same `market_tags + susceptible_markets` CTE block is copy-pasted across 8+ queries. No trader taxonomy exists — every query re-derives trader types from scratch.

Static classifications leak future data into backtests. If a trader is classified as a "bot" using their full history through today, backtesting September 2025 uses 6 months of future information.

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
    │   Classification Tables (cache)      │
    │   repopulated before each use        │
    └──────────────────────────────────────┘
```

Temporal correctness lives in the **function** (which takes a `{cutoff}` param), not in the **table** (which is just a cache). The table is repopulated before each use:

- **Scheduled refresh**: cron runs `populate_all(cutoff=today)` daily
- **Walk-forward backtest**: harness runs `populate_all(cutoff=fold_end)` before each fold
- **Research iteration**: Researcher runs `populate(cutoff=test_date)` to explore

## Schema

### Two Classification Tables (cache)

```sql
CREATE TABLE trader_classifications (
    trader          String,
    label           String,          -- 'bot', 'sure_trader', 'sniper', ...
    tier            UInt8,           -- 1=strongest signal, 5=weakest
    score           Float64 DEFAULT 0,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, trader)

CREATE TABLE market_classifications (
    condition_id    String,
    label           String,          -- 'susceptibility', 'resolution_speed', ...
    tier            UInt8,
    score           Float64 DEFAULT 0,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, condition_id)
```

- `ReplacingMergeTree(rule_version)` — bumping version overwrites stale classifications.
- `ORDER BY (label, entity)` — queries filter by label first.
- `tier` standardized: 1 = strongest signal across all labels.
- `score` optional — boolean labels use tier only, scored labels use both.
- Table is a **cache** — repopulated by running classification functions before use.

## Classification Rules

### Rule Format

Each rule is a `.sql` file — a parameterized INSERT...SELECT with `{cutoff}` placeholder.
The `{cutoff}` ensures only data before that date is used (point-in-time correctness).

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
3. Rule executes against CH — TRUNCATES label, then INSERTs fresh rows
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
    1. populate_all(cutoff=fold_train_end)    # repopulate cache from training data only
    2. Run ReplayRunner on fold_test_window   # provider bootstrap JOINs fresh classifications
    3. Collect metrics
```

No look-ahead bias: classifications reflect only data available at the end of the training window.

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
- Truncates existing rows for that label before inserting (idempotent)
- Executes with `{cutoff}` substituted

## Composable Query Pattern

Research queries JOIN classification tables instead of inline CTEs:

```sql
-- Exclude bots
LEFT JOIN (SELECT trader FROM trader_classifications FINAL
           WHERE label = 'bot') bots
    ON t.maker = bots.trader
WHERE bots.trader IS NULL

-- Only insider-susceptible markets
INNER JOIN (SELECT condition_id FROM market_classifications FINAL
            WHERE label = 'susceptibility' AND tier >= 2) mkt
    ON t.condition_id = mkt.condition_id
```

Queries should be <50 lines by composing these building blocks.

## Ownership

- **Researcher**: writes rule `.sql` files in hypothesis folders, calls `populate()` for testing
- **Architect**: reviews and promotes rules to production, manages scheduled refresh, owns table schema
- **Lead**: routes proposals between Researcher and Architect
