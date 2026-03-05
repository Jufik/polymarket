# Architect Owned Files

These files constitute the production execution harness. Only the Architect agent
may modify them. All changes must be generic improvements, not strategy-specific.

| File | Purpose |
|------|---------|
| `src/polymarket_pipeline/strategies/runners/replay.py` | ReplayRunner: tick-by-tick replay with settlement |
| `src/polymarket_pipeline/strategies/runners/helpers.py` | Risk gate + position math (apply_fill_to_position) |
| `src/polymarket_pipeline/strategies/execution/gateway.py` | ExecutionGateway: intent validation, budget gate, logging |
| `src/polymarket_pipeline/strategies/execution/realistic.py` | RealisticFillSimulator: calibrated slippage model |
| `src/polymarket_pipeline/strategies/execution/calibrate.py` | Spread/volume calibration from trade data |
| `src/polymarket_pipeline/strategies/config.py` | StrategyConfig, HarnessConfig, TOML loaders |
| `src/polymarket_pipeline/cli/harness.py` | pm-harness CLI entry point |
| `docker/clickhouse/migrations/009+` | Classification table migrations (taxonomy layer) |

## Classification Tables (Taxonomy Layer)

The Architect owns `trader_classifications` and `market_classifications` in ClickHouse.
When the Researcher proposes a new classification in their `notes.md`, the Architect:

1. Reviews the proposed rule and SQL sketch
2. Writes a new numbered migration (`010_classify_bots.sql`, `011_classify_sure_traders.sql`, etc.)
3. The migration INSERTs into the appropriate classification table
4. Uses `rule_version` to allow future updates (ReplacingMergeTree deduplicates on version bump)
5. Runs the migration on remote CH (`192.168.0.148:18123`)

### Classification table schema:
```sql
-- Both tables have the same shape:
-- (entity_id, label, tier, score, rule_version, computed_at)
-- tier: 1=strongest signal, 5=weakest
-- score: optional continuous value for ranking within a label
-- rule_version: bump when classification logic changes
```

## Modification Rules

1. **Generic only** — changes must benefit all strategies, not just the current hypothesis
2. **Tests required** — run `uv run pytest tests/ -x -q` after every change
3. **Type check** — run `uv run mypy --strict src/` on modified files
4. **Incremental** — prefer small targeted fixes over refactors
5. **No full rewrites** — unless absolutely necessary and user-approved
6. **Document** — write observations to `validation/notes.md` in hypothesis folder
