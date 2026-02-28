# cli/ — CLI Entry Points

12 CLI commands registered as entry points in `pyproject.toml`.

## Commands

| Command | File | Purpose |
|---------|------|---------|
| `pm-live` | `live.py` | Start live sync pipeline (FastStream + Redpanda + ASGI health/dashboard) |
| `pm-strategy run` | `strategy.py` | Run strategies against live Kafka (paper or live mode) |
| `pm-strategy reset` | `strategy.py` | Clear paper state (PG tables, JSONL logs, Kafka topic) |
| `pm-backfill` | `backfill.py` | Historical Parquet → ClickHouse (ProcessPoolExecutor or streaming) |
| `pm-sync` | `sync.py` | Gamma + CLOB API → PostgreSQL + Parquet metadata |
| `pm-recover` | `recover.py` | Subgraph gap recovery (resumable via PG cursor) |
| `pm-compact` | `compact.py` | Recompress raw Parquet (parallel pass 1, optional global sort pass 2) |
| `pm-load` | `load.py` | Compact Parquet → ClickHouse (constant-memory streaming) |
| `pm-build` | `build.py` | Orchestrate: sync → compact → load → derived → prices |
| `pm-migrate` | `migrate.py` | Alembic `upgrade head` wrapper |
| `pm-panic` | `panic.py` | Emergency close all positions |
| `pm-api` | — (`api/app.py`) | FastAPI REST API on port 8001 |
| `pm-explore` | `explore.py` | Strategy exploration (placeholder) |

## Strategy CLI (strategy.py)

### Registries

```python
_STRATEGY_FACTORIES: dict[str, Callable] = {}   # strategy name → factory
_PROVIDER_REGISTRY: dict[str, type] = {}          # provider name → class
```

Currently empty — all implementations removed in cleanup. Ready for new strategies.

### Assembly (`_build_runner`)

1. Load configs from TOML (enabled_only=True)
2. Validate feature dependencies
3. Create providers from registry + config params
4. Create strategies from registry (factory pattern)
5. Assemble: InMemoryContext → ClobClient → PaperExecutor → ExecutionGateway → LiveRunner

### Kafka Groups

Each process uses unique consumer group: `strategy-{config_stem}`. Prevents partition starvation when multiple strategy processes run.

### Signals

- `SIGUSR1` → `runner.reset()` (clears paper state without restart)

## Bridge CLI (bridge.py)

JSON-in/JSON-out subprocess dispatcher for TypeScript → Python calls.

```bash
python -m polymarket_pipeline.cli.bridge \
  --module polymarket_pipeline.cli.bridge \
  --func read_parquet \
  --args '{"path": "data/derived/trader_market_pnl.parquet", "n_rows": 10}'
```

Built-in helpers: `read_json_file`, `read_text_file`, `read_parquet`, `describe_parquet`, `_sleep`.

## Backfill Modes (backfill.py)

- **Standard**: ProcessPoolExecutor (fork on Linux/COW, spawn on macOS) + CH semaphore
- **Compact** (`--compact`): Constant-memory streaming via `iter_row_groups_arrow()`

## Sync Freshness Gate (sync.py)

24h cache via `_fetch_meta.json` marker. `--force` bypasses.
