# Polymarket Pipeline — Architecture

> Unified trade data pipeline + strategy execution framework for Polymarket prediction markets.

---

## System Overview

```
                     ┌────────────────────────────────────────────────────────────┐
                     │                     DATA SOURCES                           │
                     ├──────────┬──────────┬─────────────┬──────────┬────────────┤
                     │ Goldsky  │  RTDS    │  RPC (fka   │ Pending  │ CLOB WS    │
                     │ Parquet  │  WS      │  Alchemy)   │ Block    │ Orderbook  │
                     │(backfill)│ (~50/s)  │ (on-chain)  │(pre-1s)  │ (fastest)  │
                     └────┬─────┴────┬─────┴──────┬──────┴────┬─────┴─────┬──────┘
                          │          │            │           │           │
                          ▼          ▼            ▼           ▼           ▼
                  ┌────────────────────────────────────────────────────────────────┐
                  │               NormalizedTrade (canonical model)                │
                  │      Pydantic v2, frozen. SHA-256 deterministic IDs.           │
                  │      Version: 0=mempool, 1=off-chain, 2=on-chain              │
                  └───────────────────────┬───────────────────────────────────────┘
                                          │
               ┌──────────────────────────┼──────────────────────────┐
               ▼                          ▼                          ▼
      ┌────────────────┐      ┌───────────────────┐      ┌──────────────────┐
      │   ClickHouse   │      │    Redpanda        │      │   PostgreSQL     │
      │  trades_raw    │      │  5 Kafka topics     │      │   (metadata)     │
      │ ReplacingMerge │      │  trades.raw         │      │  events, markets │
      │    Tree        │      │  pending.signal     │      │  tags, token_map │
      │ + derived MVs  │      │  orderbooks.raw     │      │  positions,fills │
      └────────┬───────┘      │  markets.events     │      │  strategy_intents│
               │              │  pipeline.status    │      └──────────────────┘
               │              └─────────┬───────────┘
               │                        │
               │           ┌────────────┴────────────┐
               │           ▼                         ▼
               │  ┌──────────────────┐    ┌──────────────────┐
               │  │ pm-strategy      │    │ QualityChecker   │
               │  │ (LiveRunner +    │    │ (health checks + │
               │  │  providers +     │    │  auto-protect)   │
               │  │  gateway)        │    └──────────────────┘
               │  └──────────────────┘
               │
      ┌────────┴──────────────────────────────────────────┐
      │          CLICKHOUSE DERIVED LAYER                   │
      │  trader_volumes, trader_trade_agg,                  │
      │  trader_market_positions, trader_positions_resolved  │
      │  (SummingMergeTree + chained MVs)                   │
      └─────────────────────────────────────────────────────┘
```

---

## Key Architecture Decisions

| Decision | Rationale |
|---|---|
| **Deterministic trade IDs** | SHA-256 from (tx_hash, order_hash) or (asset, ts, price, size). Enables dedup across sources. |
| **Version-based dedup** | ClickHouse ReplacingMergeTree: on-chain (2) overwrites off-chain (1) overwrites mempool (0). |
| **fastparquet only** | pyarrow fails on DECIMAL(100,18) precision > 76. DuckDB casts to lossy DOUBLE. |
| **USDC 1e6 scaling** | 6 decimals, not 18. Critical for correct amount calculations. |
| **Protocol-first strategies** | `@runtime_checkable` duck typing. No base classes. Easy to test and mock. |
| **PostgreSQL = metadata truth** | ClickHouse reads metadata via PostgreSQL table engine. No duplicate writes. |
| **TEXT columns only** | PostgreSQL VARCHAR(n) causes truncation with real API data (471K+ markets). |
| **No ingestor restart** | Crashes detected via quality checker. Auto-protect closes positions. Prevents cascade failures. |
| **Private operator submission** | Operators bypass public mempool entirely (Flashbots/MEV relay). Pending block RPC is the only pre-chain access. |
| **Pending trades separate topic** | `pending.signal` is consumed by strategies but NOT persisted to ClickHouse. Different ID scheme. |

---

## Detailed Documentation

| Document | Contents |
|---|---|
| [**data-model.md**](data-model.md) | NormalizedTrade, metadata models, trade ID generation, PostgreSQL/ClickHouse schemas, Kafka topics, constants |
| [**ingestion.md**](ingestion.md) | All 6 ingestors (RPC, RTDS, PendingBlock, CLOBOrderbook, Mempool, Subgraph), normalizers, dedup strategies, gotchas |
| [**strategy-engine.md**](strategy-engine.md) | Protocols, types, config, runners, execution layer (gateway, executors, CLOB client, position tracker), how to implement a new strategy |
| [**operations.md**](operations.md) | Pipeline lifecycle, quality gate state machine, auto-protection, all CLI commands, Docker services, monitoring dashboard |
| [**ws-schemas.md**](ws-schemas.md) | Empirically verified WebSocket message schemas for CLOB WS, RTDS, timing measurements |

---

## Module Map

```
src/polymarket_pipeline/
├── models.py              # NormalizedTrade, Event, Market, Tag, TokenMarketEntry
├── trade_id.py            # Deterministic SHA-256 trade IDs (chain:, ws:, pending:, mempool:)
├── constants.py           # EXCHANGE_ADDRS, FEE_MODULE_ADDRS, USDC_SCALE
├── settings.py            # PipelineSettings (offline pipeline, PM_ prefix)
├── market_sync.py         # Gamma + CLOB API fetcher → SyncResult
├── normalizers/           # Offline normalizers
│   ├── sink.py            # Goldsky Parquet: 1e6 scaling, taker dedup, bytes→hex
│   ├── rtds.py            # RTDS WS: float rounding, proxyWallet→maker
│   └── market_ws.py       # CLOB WS: last_trade_price only, fee_rate_bps
├── loaders/
│   └── parquet.py         # fastparquet loader + vectorized fast path
├── sinks/
│   ├── clickhouse.py      # Sync insert (clickhouse_connect)
│   └── postgres.py        # Async metadata upsert (asyncpg)
├── consumers/
│   └── rtds.py            # RTDS WS consumer (PING/PONG + callback)
├── quality/               # Shared quality types (no live/ dependency)
│   └── state.py           # PipelineState, ReadinessState, CheckResult
├── cli/                   # 12 CLI entry points
│   ├── live.py            # pm-live: FastStream + ASGI
│   ├── strategy.py        # pm-strategy: run/reset
│   ├── backfill.py        # pm-backfill: Parquet → CH
│   ├── sync.py            # pm-sync: APIs → PG + Parquet
│   ├── recover.py         # pm-recover: Subgraph gap fill
│   ├── compact.py         # pm-compact: recompress Parquet
│   ├── load.py            # pm-load: compact → CH
│   ├── build.py           # pm-build: orchestrate all steps
│   ├── migrate.py         # pm-migrate: Alembic
│   ├── panic.py           # pm-panic: emergency close
│   ├── bridge.py          # JSON bridge (TS → Python)
│   └── explore.py         # pm-explore: (placeholder)
├── execution/             # Trade execution (CLOB API)
│   ├── clob_client.py     # Async CLOB REST client (retry, orderbook cache)
│   ├── panic.py           # Parallel panic close (cancel → sell)
│   └── position_tracker.py # PG-backed position tracking (atomic recompute)
├── strategies/            # Strategy framework (protocols + types)
│   ├── protocol.py        # Strategy, FeatureProvider, Executor, FeatureBackend, StrategyContext
│   ├── types.py           # TradeIntent, Position, Fill, OrderbookSnapshot, ExecutionMode
│   ├── config.py          # StrategyConfig, ProviderConfig (TOML)
│   ├── registry.py        # Strategy discovery/registration
│   ├── context/           # InMemoryContext (dict-backed)
│   ├── execution/         # ExecutionGateway, PaperExecutor, SimulatedExecutor
│   ├── features/          # PolarsBackend, ClickHouseBackend
│   └── runners/           # LiveRunner, BacktestRunner, VectorizedRunner, ParityRunner
├── strategies_impl/       # Concrete implementations (currently empty — ready for new strategies)
│   └── __init__.py
├── live/                  # Live sync pipeline
│   ├── app.py             # FastStream + ASGI health/dashboard
│   ├── orchestrator.py    # Ingestor lifecycle, recovery, quality loops
│   ├── protection.py      # Auto-protect: close positions on RED
│   ├── settings.py        # Pydantic Settings (PM_ prefix, env-based)
│   ├── schema.py          # ClickHouse DDL (Kafka engine + derived MVs)
│   ├── circuit_breaker.py # Publish circuit breaker
│   ├── dedup.py           # TTL-based trade dedup (OrderedDict)
│   ├── dashboard.py       # HTML monitoring dashboard
│   ├── ingestors/         # 5 ingestors + BaseIngestor ABC
│   │   ├── base.py        # Shared heartbeat, circuit breaker, metrics
│   │   ├── rpc.py         # RPCIngestor: OrderFilled + QuestionResolved
│   │   ├── alchemy.py     # Backward-compat shim (re-exports RPCIngestor)
│   │   ├── rtds.py        # RTDS WS pool with rotation + cross-connection dedup
│   │   ├── pending_block.py # Multi-endpoint pending block poller
│   │   ├── clob_orderbook.py # CLOB WS firehose + targeted subscriptions
│   │   ├── mempool.py     # Rust PyO3 mempool sidecar wrapper
│   │   └── subgraph.py    # Goldsky Subgraph gap recovery
│   ├── normalizers/       # Live pipeline normalizers
│   │   ├── polygon_rpc.py # RPC log decoder (eth_abi)
│   │   ├── pending_block.py # matchOrders calldata decoder
│   │   ├── subgraph.py    # GraphQL response normalizer
│   │   └── mempool.py     # Rust dict normalizer
│   ├── quality/           # Re-exports from shared quality/
│   │   ├── state.py       # Re-export shim
│   │   └── checker.py     # QualityChecker: 5 health checks + state machine
│   └── consumers/
│       └── market_events.py # MarketEventsConsumer: debounced pool refresh
└── api/                   # FastAPI REST API
    └── app.py             # pm-api: 9 routers (health, positions, intents, etc.)
```

---

## Data Flow Summary

### Live Path (Real-Time)

```
Polygon WS ──> RPCIngestor ──> trades.raw ──> ClickHouse trades_raw
RTDS WS ────> RTDSIngestor ──> trades.raw      (ReplacingMergeTree dedup)
Free RPC ───> PendingBlock ──> pending.signal   (strategies only, not persisted)
CLOB WS ────> CLOBOrderbook ─> orderbooks.raw   (ClickHouse + strategies)
                             └> markets.events   (resolution + new market signals)
```

### Offline Path (Backfill + Analytics)

```
Goldsky Parquet ──> pm-compact ──> data/compact/ ──> pm-load ──> ClickHouse
Gamma + CLOB API ─> pm-sync ──> PostgreSQL + data/metadata/
ClickHouse ───────> pm-build --step derived ──> data/derived/
```

### Strategy Path

```
Kafka trades.raw ──> LiveRunner._handle_trade()
    ├── FeatureProvider.on_trade()  (O(1) streaming)
    ├── Strategy.on_trade()         (emit TradeIntent)
    ├── RiskGate                    (capital, position, cooldown)
    └── ExecutionGateway.submit()
        ├── QualityGate             (pipeline health)
        ├── BudgetGate              (per-strategy cap)
        └── PaperExecutor / LiveExecutor → Fill
            └── PositionTracker.record_fill()
```

---

## Testing Architecture

```bash
# Unit tests (fast, no Docker)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Integration tests (requires Docker)
uv run pytest tests/test_sink_clickhouse.py -x -q
uv run pytest tests/test_sink_postgres.py -x -q

# Type checking + linting
uv run mypy --strict src/
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

~74 test files covering: models, normalizers, ingestors, execution, strategy framework, quality, CLI.

**Key patterns**: `AsyncMock` broker fixture, recording providers/strategies, mock CLOB client, builder functions for test data.
