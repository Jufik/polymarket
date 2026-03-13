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
  --ignore=tests/test_sink_postgres.py \
  --ignore=tests/test_metrics.py \
  --ignore=tests/test_s2_insider_copy_prod.py \
  --ignore=tests/test_s3_no_sniper.py \
  --ignore=tests/test_s2_insider_copy.py \
  --ignore=tests/test_api.py \
  --ignore=tests/test_consensus_take_profit.py

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

# Lint and format (includes package sources)
uv run ruff check src/ packages/*/src/ tests/
uv run ruff format src/ packages/*/src/ tests/

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
uv run pm-strategy run --config configs/my_strategy.toml         # Run strategies against live Kafka
uv run pm-strategy run --config configs/my_strategy.toml --only my_strat  # Single strategy
uv run pm-strategy promote my_strat --to paper_dev --config configs/my_strategy.toml  # Check promotion gates
uv run pm-strategy reset --log-dir logs/paper --yes              # Clear paper state

# Data build pipeline (CLOB + Gamma metadata, compact trades, Polars derived tables)
uv run python scripts/build_data.py                        # all steps
uv run python scripts/build_data.py --step metadata        # CLOB + Gamma API fetch
uv run python scripts/build_data.py --step compact         # recompress raw parquet
uv run python scripts/build_data.py --step derived         # Polars PnL + MVF
uv run python scripts/build_data.py --step prices          # market price timeseries
uv run python scripts/build_data.py --force-metadata       # re-fetch even if fresh

# Research backtest harness (see research/harness.py)
# Fast path (preferred — sync, Parquet snapshot):
#   from research.harness import run_fast_backtest, print_summary
#   result, summary = run_fast_backtest(MyStrategy(), config, universe={"0xabc..."})
#   if summary: print_summary(summary, "my_strategy")
#
# Legacy path (async, compact parquet files):
#   from research.harness import load_compact_trades, run_backtest, print_summary
#   trades = load_compact_trades(max_files=10)
#   result, summary = asyncio.run(run_backtest(MyStrategy(), trades, config))
#
# Research server (HTTP — for notebooks):
#   PYTHONPATH=. uv run python research/server.py  # port 9999
#   curl -X POST http://localhost:9999/query -d '{"sql": "SELECT count() FROM maker_positions"}'
```

## Architecture

Unified trade data pipeline that ingests from three Polymarket sources, normalizes into a canonical model, deduplicates across sources, and stores in ClickHouse for analysis.

### Package Structure (uv workspace)

7 packages under `packages/`, with the root `polymarket_pipeline` providing backward-compat shims:

| Package | Layer | Purpose | Key Modules |
|---------|-------|---------|-------------|
| `pm-core` | 0 | Shared types, models, protocols, config | models, types, constants, trade_id, protocols, config/ |
| `pm-ingest` | 1 | Data ingestion from all sources | ingestors/, normalize/, dedup, circuit_breaker |
| `pm-store` | 1 | Storage backends | clickhouse/, postgres/, shmem/, parquet/, kafka/ |
| `pm-strategy` | 2 | Strategy framework + implementations | protocols, execution/, features/, ledger/, context/, impl/ |
| `pm-backtest` | 2 | Backtesting and replay runners | runners/, sync_runner, harness, replay |
| `pm-pipeline` | 3 | Live pipeline orchestration | app, orchestrator, runner (LiveRunner), quality/ |
| `pm-api` | 3 | REST API (FastAPI) | routes/, filters, pagination, deps |

Dependency DAG: Layer N depends only on Layer < N. No circular imports.

**Import convention**: New code should import from `pm_*` packages directly. Legacy `polymarket_pipeline.*` imports work via backward-compat shims in `src/polymarket_pipeline/`.

**Root-resident modules** (not yet extracted): `cli/`, `execution/` (clob_client, panic, position_tracker), `loaders/`, `normalizers/`, `consumers/`, `derived.py`, `settings.py`, `logging_config.py`.

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
packages/
├── pm-core/src/pm_core/          # Layer 0: shared foundation
│   ├── models.py                 # NormalizedTrade, Event, Market, Tag, TokenMarketEntry
│   ├── types.py                  # Side, Source, Outcome enums + TradeIntent, Position, Fill, etc.
│   ├── constants.py              # Exchange addrs, USDC scale, FEE_MODULE_ADDRS
│   ├── trade_id.py               # Deterministic trade IDs: make_trade_id_chain/ws/pending()
│   ├── protocols.py              # Strategy, FeatureProvider, Executor, BookWriter/Reader
│   ├── token_map.py              # Token-to-market resolution
│   └── config/                   # ConfigStore, ConfigWatcher, key schemas
│
├── pm-ingest/src/pm_ingest/      # Layer 1: ingestion
│   ├── base.py                   # BaseIngestor ABC: heartbeat, counters, circuit breaker
│   ├── circuit_breaker.py        # Circuit breaker for publishes
│   ├── dedup.py                  # TTL-based trade deduplication
│   ├── publish.py                # Protocol-injected safe_publish()
│   ├── asset_registry.py         # Dynamic asset tracking
│   ├── reconciler.py             # Cross-slot orderbook reconciliation
│   ├── ingestors/                # 5 sources: rpc, rtds, pending, clob_ws, subgraph
│   │   ├── managed_slot.py       # WS slot manager with rotation
│   │   └── exchange_feed.py      # Cryptofeed exchange ingestor
│   └── normalize/                # Decode, enrich, validate pipelines
│
├── pm-store/src/pm_store/        # Layer 1: storage
│   ├── clickhouse/               # Client, sink, queries, migrations/
│   ├── postgres/                 # Pool, sink, changelog
│   ├── shmem/                    # SharedMemory orderbook (sub-μs reads)
│   ├── parquet/                  # Loader, writer, checkpoint
│   ├── kafka/                    # Kafka publisher wrapper
│   └── market_sync.py            # Gamma API fetcher
│
├── pm-strategy/src/pm_strategy/  # Layer 2: strategy framework
│   ├── protocols.py              # Strategy, VectorizedStrategy, FeatureProvider
│   ├── types.py                  # TradeIntent, Position, Fill, OrderbookSnapshot
│   ├── config.py                 # StrategyConfig + ProviderConfig (TOML)
│   ├── promotion.py              # PromotionChecker: vectorized→paper→live gates
│   ├── registry.py               # Strategy discovery/registration
│   ├── introspect.py             # HTTP introspection server
│   ├── context/                  # InMemoryContext, RedisContext
│   ├── execution/                # Gateway, PaperExecutor, RealisticFillSimulator, LiveExecutor
│   ├── features/                 # PolarsBackend (offline), ClickHouseBackend (live)
│   ├── ledger/                   # LedgerRecord, ParquetLedger, analytics
│   ├── helpers.py                # Risk gate, position tracking helpers
│   └── impl/                     # Concrete strategies
│       ├── tag_hr_copy/          # Tag-based hit-rate copy trading
│       ├── consensus_v2/         # Consensus-weighted copy trading
│       └── crypto_gbm/           # Crypto GBM strategy
│
├── pm-backtest/src/pm_backtest/  # Layer 2: backtesting
│   ├── runners/                  # BacktestRunner, VectorizedRunner, ReplayRunner, ParityRunner
│   ├── sync_runner.py            # SyncReplayRunner (zero-async, tick-by-tick)
│   ├── harness.py                # run_fast_backtest() + run_backtest() convenience
│   ├── replay.py                 # Trade/resolution loading for replay
│   └── ledger/                   # Ledger integration for backtest output
│
├── pm-pipeline/src/pm_pipeline/  # Layer 3: live orchestration
│   ├── app.py                    # FastStream app + ASGI health
│   ├── orchestrator.py           # Ingestor lifecycle + recovery + quality loops
│   ├── runner.py                 # LiveRunner (event-driven strategy execution)
│   ├── protection.py             # Auto-protect: close positions on RED state
│   ├── settings.py               # Pydantic Settings (env-based, PM_ prefix)
│   ├── quality/                  # PipelineState, QualityChecker
│   ├── consumers/                # MarketEventsConsumer (debounced pool refresh)
│   ├── lifecycle/                # Ingestor lifecycle management
│   └── dashboard.py              # HTML dashboard
│
└── pm-api/src/pm_api/            # Layer 3: REST API
    ├── app.py                    # FastAPI app + dashboard HTML
    ├── routes/                   # trades, fills, intents, markets, prices, strategies, etc.
    ├── queries.py                # Parameterized CH/PG query builder
    ├── filters.py                # Query filter parsing
    ├── pagination.py             # Cursor/offset pagination
    ├── schemas.py                # Response schemas
    ├── errors.py                 # Structured error responses
    └── deps.py                   # FastAPI dependency injection

src/polymarket_pipeline/          # Root: backward-compat shims + not-yet-extracted code
├── models.py                     # Re-exports from pm_core
├── trade_id.py                   # Re-exports from pm_core
├── constants.py                  # Re-exports from pm_core
├── settings.py                   # PipelineSettings (offline pipeline, PM_ prefix)
├── logging_config.py             # Structured logging setup
├── normalizers/                  # Goldsky Parquet, RTDS WS, Market WS normalizers
├── loaders/                      # ParquetLoader (fastparquet)
├── sinks/                        # Re-exports from pm_store
├── consumers/                    # WebSocket consumer
├── quality/                      # Re-exports from pm_core/pm_pipeline
├── cli/                          # 12+ CLI entry points (pm-live, pm-strategy, etc.)
├── execution/                    # CLOB API client, panic, position tracker
├── strategies/                   # Re-exports from pm_strategy
├── strategies_impl/              # Re-exports from pm_strategy.impl
├── live/                         # Re-exports from pm_pipeline/pm_ingest
└── api/                          # Re-exports from pm_api

research/                         # Research sandbox (imports from pipeline, never imported BY it)
├── knowledge/                    # Structured research knowledge base
│   ├── data/                     # Data characteristics, base rates, distributions
│   ├── signals/                  # Alpha signals and features
│   ├── pitfalls/                 # Known biases, simulation gaps, critical bugs
│   ├── execution/                # Position lifecycle, slippage, capital
│   └── queries/                  # Reusable CH SQL snippets (.sql files)
├── db.py                         # DuckDB singleton over Parquet snapshot (ResearchDB)
├── fast_replay.py                # Polars-based trade/resolution loading
├── sync_replay.py                # SyncReplayRunner: zero-async tick-by-tick replay
├── harness.py                    # run_fast_backtest() (sync) + run_backtest() (async)
├── server.py                     # FastAPI research server (port 9999)
├── export_snapshot.py            # CH -> Parquet snapshot exporter
├── conftest.py                   # Shared pytest fixtures
├── strategies/                   # Draft strategy modules
└── output/                       # Ledger parquet output (gitignored)
```

### Strategy Framework

Protocol-based, async framework with concrete implementations in `pm_strategy.impl/`. Configuration lives in TOML files under `configs/`.

```
TOML Config ─────> StrategyConfig + ProviderConfig
                       │                 │
                       ▼                 ▼
                  Strategy impls    FeatureProviders
                       │                 │
                       ▼                 ▼
LiveRunner ─────── on_trade()      compute() / refresh()
    │                  │                 │
    ▼                  ▼                 ▼
ExecutionGateway   TradeIntent      InMemoryContext
    │                                    │
    ▼                                    ▼
Executor (Paper/Live/Realistic)     features dict
    │
    ▼
  Fill ──> LedgerRecord ──> ParquetLedger ──> LedgerSummary
```

**Execution Modes (with promotion gates):**

```
vectorized ──> paper_dev ──> paper_prod ──> live
  (backtest)    (loose)       (strict)      (real $)
```

Promotion enforced via `pm-strategy promote` — checks min trades, Sharpe, PnL, drawdown, runtime.

**Executors (3):**

| Executor | Mode | Price Source | Slippage |
|----------|------|-------------|----------|
| `SimulatedExecutor` | vectorized/replay | `max_price` or 0.50 | None |
| `RealisticFillSimulator` | vectorized/replay | `max_price` (slippage as fee) | Calibrated from trades |
| `PaperExecutor` | paper_dev/prod | WS orderbook → CLOB REST | None |

**RealisticFillSimulator**: Fills at `max_price` (same as SimulatedExecutor) but adds calibrated slippage cost to `fee_usd`. Spreads estimated from trade-to-trade price changes via `calibrate_spreads()` (median abs change or Roll estimator). Impact = `size_usd / estimated_liquidity`.

**Strategy Outcome Ledger** (`pm_backtest.ledger` / `pm_strategy.ledger`):
- `LedgerRecord`: frozen dataclass — signal->fill->resolution->PnL lifecycle per intent
- `ParquetLedger`: buffer in memory, flush to parquet for backtests
- `compute_summary()`: hit_rate, edge, Sharpe, max_drawdown, profit_factor
- `BacktestRunner` writes ledger records automatically when `ledger=` is provided

**Key patterns:**
- Strategies implement `Strategy` protocol (event-driven) and/or `VectorizedStrategy` (batch)
- Providers implement `FeatureProvider` protocol: `compute()` at startup, `refresh()` periodically, `on_trade()` per event
- `FeatureBackend` protocol: `PolarsBackend` for offline, `ClickHouseBackend` for live
- `ExecutionGateway`: pipeline health check -> per-strategy budget gate -> executor
- `LiveRunner._refresh_loop`: timer-based OR event-driven via `request_refresh()` + `asyncio.Event`

**Kafka Topics (strategy-relevant):**

| Topic | Purpose |
|-------|---------|
| `trades.raw` | Normalized trades (main feed) |
| `pending.signal` | Pre-confirmation trades (~1s early) |
| `orderbooks.raw` | CLOB WS best bid/ask snapshots |
| `markets.events` | Resolution + new market events (triggers pool refresh) |
| `pipeline.status` | Heartbeats from ingestors |

**Pool Refresh Automation:**
CLOB WS `market_resolved` -> `markets.events` topic -> `MarketEventsConsumer` (5s debounce) -> `LiveRunner.request_refresh()` -> providers re-query CH -> atomic context swap. Hot path never blocked.

### Conventions

- Python 3.11+, async/await, Pydantic v2 with `frozen=True`
- All models have `.from_gamma(raw)` classmethod for API parsing (returns `None` on missing fields)
- pytest-asyncio with `asyncio_mode = "auto"`
- mypy strict mode with Pydantic plugin
- ruff: line-length 100, rules `E F I UP B SIM ASYNC`
- structlog for all logging
- `transaction_hash` and `order_hash` in Parquet are raw bytes — convert with `"0x" + val.hex()`
- New code should import from `pm_*` packages; legacy `polymarket_pipeline.*` imports still work

### Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| ClickHouse 24.8 | 18123 (HTTP), 19000 (native) | Trade storage (OLAP) |
| CH-UI | 15521 | ClickHouse web UI |
| PostgreSQL 16 | 15432 | Metadata (set PM_PG_DSN in .env) |
| MLflow 2.19.0 | 5050 | Experiment tracking |
| Redpanda | 19092 (Kafka), 18082 (proxy) | Event streaming (live pipeline) |
| Redpanda Console | 18080 | Redpanda web UI |

### Research Workflow

Quantitative research is orchestrated via the `research` skill with a 5-phase workflow.
Knowledge is captured in `research/knowledge/`. Ideas are tracked in `research/ideas.md`.

```
LOAD KNOWLEDGE ──→ DISCOVER (vectorized) ──→ MANUAL GATE ──→ VALIDATE (tick-by-tick) ──→ CAPTURE & SCORE
  parallel agents     DuckDB sweeps            user reviews       SyncReplayRunner          knowledge entries
  parse admonitions   marimo notebook           decides next       Parquet snapshot          compounding score
  surface CRITICAL    UPPER BOUNDS only         validate/refine    compare with vectorized   idea backlog update
```

**Skills** (in `.claude/skills/`):
- `research` — main orchestrator, multi-agent review, manual gate
- `research-discover` — vectorized discovery agent, DuckDB sweeps, marimo notebooks
- `research-validate` — tick-by-tick validation, SyncReplayRunner + Parquet snapshot
- `research-knowledge` — knowledge loading, admonition parsing, enrichment

**Agents** (in `.claude/agents/`):
- `researcher` — heavy computation (DuckDB sweeps, tick-by-tick validation)
- `quant-research-strategist` — ad-hoc exploration (quick queries, sanity checks)
- `sim-fidelity-auditor` — simulation engine audit and gap diagnosis
- `skeptic`, `visionary`, `challenger`, `engineer`, `architect` — review panel

**Knowledge admonitions** (GitHub-flavored `> [!CRITICAL]` / `> [!WARNING]` / `> [!TIP]`):
- Parsed from `research/knowledge/` entries at session start
- CRITICAL: must be addressed or results are invalid
- WARNING: results will be biased if ignored

**Key pitfalls** (load before any research):
- Vectorized backtests are 20-40pp optimistic vs tick-by-tick (`pitfalls/vectorized_vs_tick.md`)
- SELL trades are exits, not directional signals (`pitfalls/sell_is_exit.md`)
- Consensus must count unique traders, not trade events (`pitfalls/consensus_dedup.md`)
- Resolution uses asset_id (boolean), never string matching (`data/resolution_mechanics.md`)

**After research**: if a query result surprised you, capture it in `research/knowledge/`.

**Compounding score**: `excess_hr × avg_edge_usd / median_hold_days` — higher = faster capital recycling.

**Simulation layers**:

| Layer | Speed | Accuracy | Use For |
|-------|-------|----------|---------|
| Vectorized (DuckDB) | ~46s/3-tag sweep | Low (upper bound) | Signal discovery, parameter sweep |
| SyncReplayRunner (tick-by-tick) | ~3s/small universe | High | Validation, PnL estimation |
| Paper trading (live) | Real-time | Highest | Pre-deployment confirmation |

**Research data infrastructure** (`data/research/`, ~17.6 GB Parquet snapshot):
- `research/db.py` — DuckDB singleton (3.4s startup, positions + metadata in-memory)
- `research/fast_replay.py` — Polars loader with predicate pushdown (ReplayTick ~0.5 us)
- `research/sync_replay.py` — SyncReplayRunner (zero-async, built-in settlement)
- `research/harness.py` — `run_fast_backtest()` (sync) + `run_backtest()` (async)
- `research/server.py` — FastAPI research server (port 9999: /query, /sweep, /replay)
- `research/export_snapshot.py` — CH -> Parquet snapshot refresh

**Remote ClickHouse**: `192.168.0.148:18123`, database `polymarket` (full dataset 2022-2026).
Used as fallback for classifications, live data, and tables not in Parquet snapshot.
