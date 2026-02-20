# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (always use uv, never bare pip/python3)
uv sync --all-extras

# Start infrastructure (ClickHouse, PostgreSQL, MLflow)
docker compose up -d

# Unit tests (fast, no Docker needed)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Single test file
uv run pytest tests/test_models.py -x -q

# Single test function
uv run pytest tests/test_models.py::test_function_name -x -q

# Integration tests (requires Docker services running)
uv run pytest tests/test_sink_clickhouse.py -x -q
uv run pytest tests/test_sink_postgres.py -x -q

# All tests
uv run pytest tests/ -x -q

# Type checking (strict mode with Pydantic plugin)
uv run mypy --strict src/

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Backfill pipeline
uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/

# Sync market metadata (Gamma API -> PostgreSQL)
uv run python -m polymarket_pipeline.cli.market_sync

# Strategy exploration CLI
uv run pm-explore --help

# Data build pipeline (CLOB + Gamma metadata, compact trades, Polars derived tables)
uv run python scripts/build_data.py                        # all steps
uv run python scripts/build_data.py --step metadata        # CLOB + Gamma API fetch
uv run python scripts/build_data.py --step compact         # recompress raw parquet
uv run python scripts/build_data.py --step derived         # Polars PnL + MVF
uv run python scripts/build_data.py --step prices          # market price timeseries
uv run python scripts/build_data.py --force-metadata       # re-fetch even if fresh

# Backtester sweep (archived to research/)
uv run python -m research.strategies.consistency_copy.backtester
```

## Architecture

Unified trade data pipeline that ingests from three Polymarket sources, normalizes into a canonical model, deduplicates across sources, and stores in ClickHouse for analysis.

### Data Flow

```
Sources                    Normalizers              Storage
────────                   ───────────              ───────
Goldsky Parquet ──┐                              ┌─ ClickHouse (trades_raw)
  (backfill)      ├──> NormalizedTrade ──────────>│   ReplacingMergeTree(_version)
RTDS WebSocket ───┤    (canonical model)          │   ORDER BY (condition_id, timestamp, trade_id)
  (live ~50/sec)  │                               │
Market WebSocket ─┘                               └─ PostgreSQL (metadata only)
                                                      events, markets, tags, token_map
Gamma API ─────────────> PostgreSQL ───────────────> ClickHouse reads via PG engine
  (metadata sync)        (source of truth)
```

### Offline Data Pipeline (`scripts/build_data.py`)

```
CLOB API ──────────> data/metadata/markets.parquet     (condition_id, resolution, winner)
(~455K markets)      data/metadata/token_map.parquet    (asset_id → condition_id, outcome, winner)

Gamma API ─────────> merged into markets.parquet        (event_id, category, closed_at)

order_filled/ ─────> data/compact/                      (recompressed trades, resume-safe)
(2685 raw files)     compact_NNNN.parquet + _manifest.json

Polars scans ──────> data/derived/                      (pre-computed for fast downstream)
                     trader_market_pnl.parquet
                     maker_volume_fractions.parquet
                     markets_resolved.parquet
                     market_prices.parquet
```

- **Resolution source**: CLOB API `tokens[].winner` (ground truth, ~100% coverage). Gamma's `resolved` field is broken.
- **Derived tables**: Pure Polars lazy scans (no DuckDB). Streaming collect for larger-than-memory.
- **Token convention**: `token_index=0` = affirmative/"YES" side (CLOB API ordering matches Gamma's `token_yes`).

### Key Design Decisions

- **Dedup strategy**: Deterministic SHA-256 trade IDs (`chain:` prefix for on-chain, `ws:` prefix for off-chain). ClickHouse `ReplacingMergeTree` keeps highest `_version` — on-chain (2) overwrites off-chain (1).
- **Parquet reader**: Only `fastparquet` works. pyarrow fails on `DECIMAL(100,18)` precision > 76; DuckDB casts to lossy DOUBLE.
- **Amount scaling**: USDC uses 1e6 (6 decimals), NOT 1e18.
- **Taker dedup**: ~40.5% of Parquet rows are taker-focused duplicates, filtered by matching taker address against known exchange contracts.
- **Metadata flow**: PostgreSQL is single source of truth for events/markets/tags. ClickHouse reads metadata directly via PostgreSQL table engine (no duplicate writes).
- **PostgreSQL columns**: Use TEXT everywhere — VARCHAR(n) causes truncation with real Gamma API data (471K+ markets).

### Module Map

```
src/polymarket_pipeline/
├── models.py            # NormalizedTrade, Event, Market, Tag, TokenMarketEntry (Pydantic v2, frozen)
├── trade_id.py          # Deterministic trade ID: make_trade_id_chain(), make_trade_id_ws()
├── constants.py         # Shared constants (exchange addrs, USDC scale)
├── market_sync.py       # Gamma API fetcher: fetch_events() -> SyncResult
├── normalizers/
│   ├── sink.py          # Goldsky Parquet: drop taker dups, 1e6 scaling, bytes->hex
│   ├── rtds.py          # RTDS WS: float rounding, proxyWallet as maker
│   └── market_ws.py     # Market WS: last_trade_price only, fee from fee_rate_bps
├── loaders/
│   └── parquet.py       # ParquetLoader (fastparquet, ~2033 files)
├── sinks/
│   ├── clickhouse.py    # Batch insert to trades_raw
│   └── postgres.py      # Async metadata upsert (asyncpg)
├── consumers/
│   └── rtds.py          # WebSocket consumer with PING/PONG heartbeat
├── cli/
│   ├── backfill.py      # Parquet -> ClickHouse + metadata sync
│   ├── market_sync.py   # Gamma API -> PostgreSQL standalone
│   ├── explore.py       # Strategy exploration CLI (Typer)
│   └── live.py          # Live sync pipeline entry point (planned)
├── live/                # Live sync pipeline (FastStream + Redpanda)
│   ├── app.py           # FastStream app + lifespan hooks
│   ├── settings.py      # Pydantic Settings (env-based, PM_ prefix)
│   ├── ingestors/       # RTDS, Alchemy, Subgraph recovery producers
│   ├── consumers/       # Signal evaluator, dashboard, derived refresher
│   ├── quality/         # Data quality gate + readiness state machine
│   └── normalizers/     # PolygonRPCNormalizer, SubgraphNormalizer
└── exploration/         # ML experimentation: tree-based stages, Claude agent, MLflow

research/                # Archived exploration (not production)
├── insights/            # Strategy research findings (24 copy, overpriceNo, etc.)
├── scripts/             # One-off analysis scripts
└── strategies/          # Backtester, sweep results, configs
```

### Conventions

- Python 3.11+, async/await, Pydantic v2 with `frozen=True`
- All models have `.from_gamma(raw)` classmethod for API parsing (returns `None` on missing fields)
- pytest-asyncio with `asyncio_mode = "auto"`
- mypy strict mode with Pydantic plugin
- ruff: line-length 100, rules `E F I UP B SIM ASYNC`
- structlog for all logging
- `transaction_hash` and `order_hash` in Parquet are raw bytes — convert with `"0x" + val.hex()`

### Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| ClickHouse 24.8 | 18123 (HTTP), 19000 (native) | Trade storage (OLAP) |
| CH-UI | 15521 | ClickHouse web UI |
| PostgreSQL 16 | 15432 | Metadata (user: polymarket, pass: polymarket) |
| MLflow 2.19.0 | 5050 | Experiment tracking |
