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
# Quick usage in Python:
#   from research.harness import load_compact_trades, run_backtest, print_summary
#   trades = load_compact_trades(max_files=10)
#   result, summary = asyncio.run(run_backtest(MyStrategy(), trades, config))
#   print_summary(summary, "my_strategy")
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
│   ├── strategy.py      # pm-strategy: Run strategies against live Kafka
│   └── bridge.py        # CLI bridge utilities
├── execution/           # Trade execution (CLOB API)
│   ├── clob_client.py   # Polymarket CLOB API client (submit, cancel, balances)
│   ├── panic.py         # Panic close all positions
│   └── position_tracker.py  # PostgreSQL position tracking
├── strategies/          # Strategy framework (protocols + types)
│   ├── protocol.py      # Strategy, FeatureProvider, Executor, FeatureBackend protocols
│   ├── types.py         # TradeIntent, Position, Fill, OrderbookSnapshot, ExecutionMode
│   ├── config.py        # StrategyConfig + ProviderConfig + PromotionThresholds (TOML)
│   ├── registry.py      # Strategy discovery/registration
│   ├── promotion.py     # PromotionChecker: vectorized→paper→live gate enforcement
│   ├── context/         # InMemoryContext for strategy state
│   ├── execution/       # ExecutionGateway, PaperExecutor, RealisticFillSimulator, calibrate
│   ├── features/        # FeatureBackend: PolarsBackend (offline), ClickHouseBackend (live)
│   ├── ledger/          # Strategy outcome ledger (LedgerRecord, ParquetLedger, analytics)
│   └── runners/         # LiveRunner (event-driven), BacktestRunner (+ optional ledger)
├── live/                # Live sync pipeline (FastStream + Redpanda)
│   ├── app.py           # FastStream app + market events subscriber + ASGI health
│   ├── orchestrator.py  # Ingestor lifecycle + recovery + quality loops
│   ├── protection.py    # Auto-protect: close positions on RED state
│   ├── settings.py      # Pydantic Settings (env-based, PM_ prefix)
│   ├── schema.py        # ClickHouse DDL: Kafka engine tables + derived MVs
│   ├── circuit_breaker.py  # Circuit breaker for Redpanda publishes
│   ├── dedup.py         # TTL-based trade deduplication
│   ├── ingestors/       # 5 sources + BaseIngestor ABC
│   │   ├── base.py      # BaseIngestor: shared heartbeat, counters, circuit breaker
│   │   ├── rpc.py       # RPCIngestor: Polygon RPC logs + on-chain resolution detection
│   │   ├── alchemy.py   # Backward-compat shim (re-exports RPCIngestor as AlchemyIngestor)
│   │   ├── rtds.py      # RTDS WS pool with rotation + dedup
│   │   ├── pending_block.py  # Multi-endpoint pending block poller (~1s early)
│   │   ├── clob_orderbook.py # CLOB WS orderbook + market event forwarding
│   │   ├── mempool.py   # Rust PyO3 mempool sidecar wrapper
│   │   └── subgraph.py  # Goldsky Subgraph gap recovery
│   ├── quality/         # Re-exports from shared quality/ module
│   │   ├── state.py     # Re-export shim for backward compat
│   │   └── checker.py   # QualityChecker: health checks + state machine
│   ├── consumers/       # Kafka consumers
│   │   └── market_events.py  # MarketEventsConsumer: debounced pool refresh on resolution
│   ├── normalizers/     # PolygonRPCNormalizer, SubgraphNormalizer, PendingBlockNormalizer
│   └── dashboard.py     # HTML dashboard (async, quality metrics)
├── api/                 # FastAPI REST API
│   └── app.py           # pm-api entry point
└── strategies_impl/     # Concrete strategy implementations (empty — ready for new)

research/                # Research sandbox (imports from pipeline, never imported BY it)
├── knowledge/           # Structured research knowledge base (see knowledge/README.md)
│   ├── data/            # Data characteristics, base rates, distributions
│   ├── signals/         # Alpha signals and features
│   ├── pitfalls/        # Known biases, simulation gaps, critical bugs
│   ├── execution/       # Position lifecycle, slippage, capital
│   └── queries/         # Reusable CH SQL snippets (.sql files)
├── harness.py           # Backtest entry point: load trades → calibrate → run → ledger → analytics
├── conftest.py          # Shared pytest fixtures (permissive_config, sample_trades)
├── strategies/          # Draft strategy modules (same protocol, not registered in CLI)
│   └── example.py       # Template strategy: buy YES below threshold
└── output/              # Ledger parquet output (gitignored content)
```

### Strategy Framework

Protocol-based, async framework. No strategy implementations are registered — the framework is ready for new strategies. Configuration lives in TOML files under `configs/`.

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

**Strategy Outcome Ledger** (`strategies/ledger/`):
- `LedgerRecord`: frozen dataclass — signal→fill→resolution→PnL lifecycle per intent
- `ParquetLedger`: buffer in memory, flush to parquet for backtests
- `compute_summary()`: hit_rate, edge, Sharpe, max_drawdown, profit_factor
- `BacktestRunner` writes ledger records automatically when `ledger=` is provided

**Key patterns:**
- Strategies implement `Strategy` protocol (event-driven) and/or `VectorizedStrategy` (batch)
- Providers implement `FeatureProvider` protocol: `compute()` at startup, `refresh()` periodically, `on_trade()` per event
- `FeatureBackend` protocol: `PolarsBackend` for offline, `ClickHouseBackend` for live
- `ExecutionGateway`: pipeline health check → per-strategy budget gate → executor
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
CLOB WS `market_resolved` → `markets.events` topic → `MarketEventsConsumer` (5s debounce) → `LiveRunner.request_refresh()` → providers re-query CH → atomic context swap. Hot path never blocked.

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

### Research Workflow

Quantitative research is orchestrated via the `quant-research` skill with a 5-phase workflow.
Knowledge is captured in `research/knowledge/`. Ideas are tracked in `research/ideas.md`.

```
LOAD KNOWLEDGE ──→ DISCOVER (vectorized) ──→ MANUAL GATE ──→ VALIDATE (tick-by-tick) ──→ CAPTURE & SCORE
  parallel agents     CH SQL sweeps            user reviews       ReplayRunner replay       knowledge entries
  parse admonitions   marimo notebook           decides next       realistic fills           compounding score
  surface CRITICAL    UPPER BOUNDS only         validate/refine    compare with vectorized   idea backlog update
```

**Skills** (in `.claude/skills/`):
- `quant-research` — main orchestrator, multi-track coordination, manual gate
- `research-track` — vectorized discovery agent, creates marimo notebooks
- `research-validate` — tick-by-tick validation agent, ReplayRunner + RealisticFillSimulator
- `research-knowledge` — knowledge loading, admonition parsing, enrichment

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
| Vectorized (CH SQL) | ~5s/sweep | Low (upper bound) | Signal discovery, parameter sweep |
| ReplayRunner (tick-by-tick) | ~2min/month | High | Validation, PnL estimation |
| Paper trading (live) | Real-time | Highest | Pre-deployment confirmation |

**ReplayRunner** (`strategies/runners/replay.py`):
- Asset_id-based resolution via `MarketResolution(winning_asset_ids=frozenset)`
- Tick-by-tick settlement frees capital mid-replay
- Provider hot-path: `on_trade()` → feature update → strategy decision per tick
- Pre-filter trades by qualified makers in CH (11x speedup)

**Remote ClickHouse**: `192.168.0.148:18123`, database `polymarket` (full dataset 2022-2026)
