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

# CLI entry points (all registered in pyproject.toml)
uv run pm-explore --help          # Strategy exploration
uv run pm-live                    # Start live sync pipeline (FastStream + Redpanda)
uv run pm-panic                   # Emergency close all positions
uv run pm-recover                 # Subgraph gap recovery
uv run pm-sync                    # Gamma API -> PostgreSQL metadata sync
uv run pm-compact                 # Recompress raw parquet files
uv run pm-load                    # Load compact parquet into ClickHouse
uv run pm-build                   # Data build pipeline
uv run pm-migrate                 # ClickHouse schema migrations
uv run pm-api                     # Start FastAPI REST API

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
Alchemy RPC WS ───┤    Redpanda (Kafka)           └─ PostgreSQL (metadata only)
  (on-chain logs) │    ───────────────                events, markets, tags, token_map
Pending Block ────┤    trades.raw (main)
  (~1s early)     │    pending.signal (early)
CLOB WS ──────────┘    orderbooks.raw (prices)
  (orderbooks)         pipeline.status (heartbeats)
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
├── trade_id.py          # Deterministic trade ID: make_trade_id_chain(), make_trade_id_ws(), _pending()
├── constants.py         # Shared constants (exchange addrs, USDC scale, FEE_MODULE_ADDRS)
├── market_sync.py       # Gamma API fetcher: fetch_events() -> SyncResult
├── settings.py          # PipelineSettings (offline pipeline, PM_ prefix)
├── normalizers/
│   ├── sink.py          # Goldsky Parquet: drop taker dups, 1e6 scaling, bytes->hex
│   ├── rtds.py          # RTDS WS: float rounding, proxyWallet as maker
│   └── market_ws.py     # Market WS: last_trade_price only, fee from fee_rate_bps
├── loaders/
│   └── parquet.py       # ParquetLoader (fastparquet, ~2033 files)
├── sinks/
│   ├── clickhouse.py    # Batch insert to trades_raw (sync, clickhouse_connect)
│   └── postgres.py      # Async metadata upsert (asyncpg)
├── consumers/
│   └── rtds.py          # WebSocket consumer with PING/PONG heartbeat
├── quality/             # Shared quality types (no live/ dependency)
│   └── state.py         # PipelineState, ReadinessState, CheckResult
├── cli/
│   ├── backfill.py      # pm-backfill: Parquet -> ClickHouse + metadata sync
│   ├── sync.py          # pm-sync: Gamma API -> PostgreSQL standalone
│   ├── explore.py       # pm-explore: Strategy exploration CLI (Typer)
│   ├── live.py          # pm-live: Live sync pipeline entry point
│   ├── panic.py         # pm-panic: Emergency position close
│   ├── recover.py       # pm-recover: Subgraph gap recovery
│   ├── compact.py       # pm-compact: Recompress raw parquet
│   ├── load.py          # pm-load: Load compact parquet into ClickHouse
│   ├── build.py         # pm-build: Data build pipeline
│   ├── migrate.py       # pm-migrate: ClickHouse schema migrations
│   ├── strategy.py      # pm-strategy (planned)
│   └── bridge.py        # CLI bridge utilities
├── execution/           # Trade execution (CLOB API)
│   ├── clob_client.py   # Polymarket CLOB API client
│   ├── panic.py         # Panic close all positions
│   └── position_tracker.py  # PostgreSQL position tracking
├── strategies/          # Strategy framework (protocols + types)
│   ├── protocol.py      # Strategy, FeatureProvider, Executor protocols
│   ├── types.py         # TradeIntent, Position, Fill, OrderbookSnapshot
│   ├── config.py        # StrategyConfig dataclass
│   ├── registry.py      # Strategy discovery/registration
│   ├── context/         # InMemoryContext for strategy state
│   ├── execution/       # ExecutionGateway, SimulatedExecutor
│   ├── features/        # FeatureBackend (Polars, ClickHouse)
│   └── runners/         # LiveRunner, BacktestRunner + helpers
├── live/                # Live sync pipeline (FastStream + Redpanda)
│   ├── app.py           # FastStream app + ASGI health endpoints (<150 lines)
│   ├── orchestrator.py  # Ingestor lifecycle + recovery + quality loops
│   ├── protection.py    # Auto-protect: close positions on RED state
│   ├── settings.py      # Pydantic Settings (env-based, PM_ prefix)
│   ├── circuit_breaker.py  # Circuit breaker for Redpanda publishes
│   ├── dedup.py         # TTL-based trade deduplication
│   ├── ingestors/       # 5 sources + BaseIngestor ABC
│   │   ├── base.py      # BaseIngestor: shared heartbeat, counters, circuit breaker
│   │   ├── alchemy.py   # Polygon RPC logs (on-chain, ~3.7s latency)
│   │   ├── rtds.py      # RTDS WS pool with rotation + dedup
│   │   ├── pending_block.py  # Multi-endpoint pending block poller (~1s early)
│   │   ├── clob_orderbook.py # CLOB WS orderbook snapshots
│   │   ├── mempool.py   # Rust PyO3 mempool sidecar wrapper
│   │   └── subgraph.py  # Goldsky Subgraph gap recovery
│   ├── quality/         # Re-exports from shared quality/ module
│   │   ├── state.py     # Re-export shim for backward compat
│   │   └── checker.py   # QualityChecker: health checks + state machine
│   ├── consumers/       # Kafka consumers (signal evaluator, derived refresher)
│   ├── normalizers/     # PolygonRPCNormalizer, SubgraphNormalizer, PendingBlockNormalizer
│   └── dashboard.py     # HTML dashboard (async, quality metrics)
├── api/                 # FastAPI REST API
│   └── app.py           # pm-api entry point
├── strategies_impl/     # Concrete strategy implementations
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
| PostgreSQL 16 | 15432 | Metadata (set PM_PG_DSN in .env) |
| MLflow 2.19.0 | 5050 | Experiment tracking |
| Redpanda | 19092 (Kafka), 18082 (proxy) | Event streaming (live pipeline) |
| Redpanda Console | 18080 | Redpanda web UI |
