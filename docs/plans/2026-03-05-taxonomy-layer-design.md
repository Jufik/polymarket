# Taxonomy Layer: Classification Tables for Research

**Date**: 2026-03-05
**Goal**: Replace monolithic inline CTEs with reusable classification tables in ClickHouse. Researcher proposes new classifications, Architect creates migrations.

## Problem

Research SQL queries are 150-200+ lines because they re-derive entity classifications inline. The same `market_tags + susceptible_markets` CTE block is copy-pasted across 8+ queries. No trader taxonomy exists — every query re-derives trader types from scratch.

## Design

### Two Classification Tables

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

- `ReplacingMergeTree(rule_version)` — bumping version overwrites stale classifications
- `ORDER BY (label, entity)` — queries filter by label first
- `score` optional — boolean labels use tier only, scored labels use both
- `tier` standardized: 1 = strongest signal across all labels
- Tables start empty — Architect populates via numbered migrations as Researcher discovers useful classifications

### Researcher-Architect Workflow

1. **Researcher discovers** a useful classification during hypothesis work
2. **Researcher proposes** in hypothesis `notes.md`: rule description, SQL sketch, tier mapping
3. **Lead routes to Architect** for review
4. **Architect writes migration** — numbered SQL file, populates table, runs tests
5. **Researcher uses it** — future queries JOIN instead of inline CTEs

### Researcher Skill Update

The `research-discover` skill teaches composable patterns:
- JOIN `trader_classifications` / `market_classifications` instead of inline CTEs
- If a needed classification doesn't exist, propose it in `notes.md`
- Queries should be <50 lines by composing existing building blocks
