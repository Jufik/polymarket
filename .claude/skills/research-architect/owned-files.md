# Architect Owned Files

These files constitute the production execution harness. Only the Architect agent
may modify them. All changes must be generic improvements, not strategy-specific.

## Production Harness (strategies framework)

| File | Purpose |
|------|---------|
| `src/polymarket_pipeline/strategies/runners/replay.py` | ReplayRunner: async tick-by-tick replay with settlement |
| `src/polymarket_pipeline/strategies/runners/helpers.py` | Risk gate + position math (apply_fill_to_position) |
| `src/polymarket_pipeline/strategies/execution/gateway.py` | ExecutionGateway: intent validation, budget gate, logging |
| `src/polymarket_pipeline/strategies/execution/realistic.py` | RealisticFillSimulator: calibrated slippage model |
| `src/polymarket_pipeline/strategies/execution/calibrate.py` | Spread/volume calibration from trade data |
| `src/polymarket_pipeline/strategies/config.py` | StrategyConfig, HarnessConfig, TOML loaders |
| `src/polymarket_pipeline/cli/harness.py` | pm-harness CLI entry point |

## Research Harness (fast paths — shared by all research agents)

| File | Purpose |
|------|---------|
| `research/db.py` | ResearchDB: DuckDB singleton over Parquet snapshot |
| `research/fast_replay.py` | Polars-based trade/resolution loading from Parquet |
| `research/sync_replay.py` | SyncReplayRunner: zero-async tick-by-tick (same semantics as ReplayRunner) |
| `research/harness.py` | `run_fast_backtest()` (sync) + legacy `run_backtest()` (async) |
| `research/server.py` | FastAPI research server (/query, /sweep, /replay) |
| `research/export_snapshot.py` | CH → Parquet snapshot exporter |

## Data Infrastructure

| File | Purpose |
|------|---------|
| `docker/clickhouse/migrations/009+` | Classification table migrations (taxonomy layer) |
| `docker/clickhouse/classifications/` | Production classification rule `.sql` files |

## Classification-as-Function (Taxonomy Layer)

The Architect owns `trader_classifications` and `market_classifications` in ClickHouse,
plus the production classification rule files.

### Core concept

A classification is a **function** `f(cutoff) → rows`, not a static row dump.
Each rule is a parameterized `.sql` file with a `{cutoff}` placeholder.
The table is a materialized cache of function outputs, tagged with `as_of`.

### Schema

```sql
-- Both tables have the same shape:
-- (entity_id, label, tier, score, rule_version, computed_at)
-- tier: 1=strongest signal, 5=weakest
-- score: optional continuous value for ranking within a label
-- rule_version: bump when classification logic changes
-- ORDER BY (label, entity_id)
-- Table is a CACHE — repopulated by running rule functions before use
```

### Rule file convention

```sql
-- file: classifications/{entity}_{label}.sql
-- label: {label} | entity: trader|market | version: {N}
-- schedule: daily|weekly|manual
-- description: Human-readable description
INSERT INTO {entity}_classifications
SELECT ..., toDate('{cutoff}') AS as_of, {version} AS rule_version, now64(3, 'UTC')
FROM (...)
WHERE timestamp <= toDateTime('{cutoff}')
```

### Rule storage

| Location | Purpose | Who writes |
|----------|---------|------------|
| `docker/clickhouse/classifications/` | Production rules (reviewed, scheduled) | Architect |
| `research/hypotheses/{slug}/classifications/` | Hypothesis-local rules (testing) | Researcher |

### Promotion workflow

1. Researcher writes rule in hypothesis `classifications/` folder, tests via `populate()`
2. Researcher proposes in `discovery/notes.md` when rule proves useful
3. Lead routes to Architect
4. Architect reviews rule, moves to `docker/clickhouse/classifications/`
5. Architect sets up scheduled refresh
6. Production rule available to all future research

### Architect responsibilities

1. Review proposed rules for correctness and performance
2. Move promoted rules to production directory
3. Set up scheduled refresh (cron-driven `populate_all(cutoff=today)`)
4. Manage schema migrations (009+)
5. Ensure point-in-time correctness (rules use `{cutoff}` parameter)

## Modification Rules

1. **Generic only** — changes must benefit all strategies, not just the current hypothesis
2. **Tests required** — run `uv run pytest tests/ -x -q` after every change
3. **Type check** — run `uv run mypy --strict src/` on modified files
4. **Incremental** — prefer small targeted fixes over refactors
5. **No full rewrites** — unless absolutely necessary and user-approved
6. **Document** — write observations to `validation/notes.md` in hypothesis folder
