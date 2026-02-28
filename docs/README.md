# Polymarket Pipeline Documentation

Unified trade data pipeline + strategy execution framework for Polymarket prediction markets.

## Documentation Index

| Document | Contents |
|---|---|
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | System overview, module map, key design decisions, data flow diagrams |
| [**data-model.md**](data-model.md) | NormalizedTrade, metadata models, trade ID generation, PostgreSQL/ClickHouse schemas, Kafka topics, constants, fee structure |
| [**ingestion.md**](ingestion.md) | All 6 ingestors (RPC, RTDS, PendingBlock, CLOBOrderbook, Mempool, Subgraph), normalizers, dedup strategies, timing hierarchy, gotchas |
| [**strategy-engine.md**](strategy-engine.md) | Protocols, types, config, runners, execution layer (gateway, executors, CLOB client, position tracker), how to implement a new strategy |
| [**operations.md**](operations.md) | Pipeline lifecycle, quality gate state machine, auto-protection, all 12 CLI commands, Docker services, monitoring dashboard, environment variables |
| [**ws-schemas.md**](ws-schemas.md) | Empirically verified WebSocket message schemas for CLOB WS, RTDS, timing measurements |
| [**TODO-live-pipeline-bugs.md**](TODO-live-pipeline-bugs.md) | Known issues and bug tracking |

## Quick Start

```bash
# Install
uv sync --all-extras

# Start infrastructure
docker compose up -d

# Run live pipeline
uv run pm-live

# Run strategies (paper mode)
uv run pm-strategy run --config configs/my_strategy.toml

# Backfill historical data
uv run pm-backfill --parquet-dir order_filled/

# Run tests
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py
```

## Architecture at a Glance

```
Sources                     Streaming                Storage
────────                    ─────────                ───────
Polygon RPC WS ──┐                                ┌── ClickHouse (trades_raw)
  (on-chain)      │                                │    ReplacingMergeTree(_version)
RTDS WS ──────────┤     NormalizedTrade            │
  (live ~50/sec)  │     ──────────────>  Redpanda ─┤── ClickHouse (orderbook_snapshots)
Pending Block ────┤     trades.raw topic            │    7-day TTL
  (pre-chain)     │     pending.signal              │
CLOB WS ──────────┤     orderbooks.raw              └── PostgreSQL (metadata)
  (orderbooks)    │     markets.events                   events, markets, token_map
Subgraph ─────────┘     pipeline.status                  positions, fills, intents
  (gap recovery)
                           │
                    ┌──────┴───────┐
                    ▼              ▼
              LiveRunner     QualityChecker
              (strategies)   (auto-protect)
```

## Key Concepts

- **Trade dedup**: SHA-256 deterministic IDs. ClickHouse ReplacingMergeTree keeps highest version (on-chain > off-chain > mempool).
- **Taker dedup**: ~40.5% of on-chain events are taker duplicates. Filtered by matching taker against exchange contracts.
- **USDC scaling**: 6 decimals (1e6), NOT 1e18.
- **Parquet reader**: Only `fastparquet` works. pyarrow fails on DECIMAL(100,18).
- **PostgreSQL TEXT**: Never use VARCHAR(n) — truncation with real data.
- **No ingestor restart**: Quality checker detects stale heartbeats → auto-protect.

## In-Code Documentation

CLAUDE.md files are placed in key subdirectories for AI-assisted development:

```
src/polymarket_pipeline/
├── live/CLAUDE.md           # Live pipeline, ingestors, quality, schema
├── strategies/CLAUDE.md     # Strategy framework, protocols, runners
├── execution/CLAUDE.md      # CLOB client, position tracker, panic
├── cli/CLAUDE.md            # All CLI entry points
└── sinks/CLAUDE.md          # ClickHouse + PostgreSQL storage
```
