# Polymarket Pipeline — Monorepo Rework Blueprint

## Guiding Principles

1. **Packages with enforced boundaries** — uv workspace, explicit deps, no circular imports
2. **Protocols over implementations** — every cross-package interface is a Protocol in pm-core
3. **Hot path in SharedMemory** — orderbooks bypass Kafka/Redis, sub-μs reads
4. **Kafka = cold storage** — persistence path to CH, not the strategy hot path
5. **Dynamic config** — TOML defaults, Redis overrides, API read/write, Pub/Sub propagation
6. **Staged migrations** — versioned DDL for both CH and PG, dependency-ordered
7. **Harness is core** — replay/backtest/ledger are production packages, not research toys
8. **API & UI from scratch** — RESTful resources, pagination, filtering, OpenAPI codegen

---

## Package Layout

```
polymarket/
├── pyproject.toml                    ← uv workspace root
├── packages/
│   ├── pm-core/                      ← Layer 0
│   ├── pm-ingest/                    ← Layer 1
│   ├── pm-store/                     ← Layer 1
│   ├── pm-strategy/                  ← Layer 2
│   ├── pm-backtest/                  ← Layer 2
│   ├── pm-pipeline/                  ← Layer 3
│   └── pm-api/                       ← Layer 3
├── research/                         ← Consumer (NOT a package)
├── ui/                               ← Next.js (ground-up redesign)
├── configs/                          ← TOML baselines (checked into git)
├── migrations/                       ← Alembic (PG)
└── data/                             ← Parquet snapshots, metadata
```

### Dependency DAG

```
pm-core ───────────────────────────────────────────────
   ↑            ↑            ↑            ↑
pm-ingest    pm-store    pm-strategy      │
                ↑            ↑            │
                └── pm-backtest ──────────┘
                         ↑
                  pm-pipeline  (+ pm-api)
                         ↑
                   research/  (consumer)
```

No cycles. Layers only depend downward.

---

## Package 1: `pm-core` (Layer 0)

Zero internal deps. Only pydantic + stdlib.

```
packages/pm-core/
├── pyproject.toml
└── src/pm_core/
    ├── __init__.py
    ├── models.py              # NormalizedTrade, Event, Market, Tag, TokenMarketEntry
    ├── types.py               # Side, Source, FillStatus, ExecutionMode enums
    ├── trade_id.py            # make_trade_id_chain/ws/pending
    ├── constants.py           # EXCHANGE_ADDRS, USDC_SCALE, FEE_MODULE_ADDRS, topic names
    ├── token_map.py           # Immutable TokenMap (atomic swap pattern)
    ├── protocols.py           # Cross-package interfaces (see below)
    └── config/
        ├── __init__.py
        ├── store.py           # ConfigStore (layered resolution)
        ├── watcher.py         # ConfigWatcher (subscribe + validate)
        ├── schemas.py         # All config section schemas (Pydantic, strict)
        └── keys.py            # Redis key conventions + serialization
```

### `protocols.py` — Every Cross-Package Interface

```python
from typing import Protocol, runtime_checkable, Any, Sequence
from pm_core.models import NormalizedTrade, Event, Market

@runtime_checkable
class Publisher(Protocol):
    """Decouples ingestors from Kafka."""
    async def publish(self, topic: str, key: str, message: str) -> bool: ...

@runtime_checkable
class TradeSink(Protocol):
    """Write trades to storage (CH, file, mock)."""
    async def insert_trades(self, trades: Sequence[NormalizedTrade]) -> int: ...

@runtime_checkable
class TradeQuery(Protocol):
    """Read trades from storage."""
    async def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...

@runtime_checkable
class MetadataSink(Protocol):
    """Write metadata to storage (PG)."""
    async def upsert_events(self, events: Sequence[Event]) -> int: ...
    async def upsert_markets(self, markets: Sequence[Market]) -> int: ...
    async def fetch_token_map(self) -> dict[str, tuple[str, str]]: ...

@runtime_checkable
class Checkpoint(Protocol):
    """Resume support for long-running operations."""
    def save(self, cursor: str, progress: int, metadata: dict[str, Any]) -> None: ...
    def load(self) -> tuple[str, int, dict[str, Any]] | None: ...

@runtime_checkable
class BookReader(Protocol):
    """Read orderbook from SharedMemory (zero-copy)."""
    def get(self, asset_id: str) -> dict[str, Any] | None: ...
    def stale_ns(self, asset_id: str) -> int | None: ...

@runtime_checkable
class BookWriter(Protocol):
    """Write orderbook to SharedMemory."""
    def update(self, asset_id: str, bid: float, ask: float,
               bids: list[tuple[float, float]], asks: list[tuple[float, float]],
               bid_depth: float, ask_depth: float) -> None: ...
```

### `config/store.py` — Layered Config Resolution

Resolution order: **Redis override > ENV var > TOML file > Schema default**

```python
class ConfigStore:
    def __init__(
        self,
        toml_path: Path | None = None,
        redis: Redis | None = None,
        prefix: str = "pm:config",
    ): ...

    def get_section(self, section: str) -> dict[str, Any]:
        """Merged view across all layers."""

    def set_override(self, section: str, key: str, value: Any) -> None:
        """Write to Redis HASH + PUBLISH change + XADD changelog."""

    def clear_override(self, section: str, key: str | None = None) -> None:
        """Remove override → fallback to lower layer."""

    def diff(self, section: str) -> dict[str, LayeredValue]:
        """Per-field: default, toml, env, override, effective."""

    def subscribe(self, section: str, callback: Callable) -> None:
        """Register for Pub/Sub change notifications."""
```

### `config/watcher.py` — Consumer Side

```python
class ConfigWatcher(Generic[T]):
    """Typed, validated, auto-updating config for consumers."""

    def __init__(self, store: ConfigStore, section: str, schema: type[T]): ...

    @property
    def current(self) -> T:
        """Always valid. Bad overrides rejected, not applied."""

    def on_change(self, cb: Callable[[T, T], Awaitable[None]]) -> None:
        """Register callback: (old_config, new_config) → side effects."""
```

### `config/schemas.py` — All Section Schemas

```python
class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    mode: ExecutionMode = ExecutionMode.PAPER_DEV
    capital_usd: float = Field(ge=0, le=100_000)
    max_position_usd: float = Field(ge=0, le=50_000)
    max_open_positions: int = Field(ge=1, le=500)
    cooldown_s: float = Field(ge=0, le=3600)
    features: list[str] = []
    params: dict[str, Any] = {}

class CLOBWSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_slots: int = Field(default=4, ge=1, le=20)
    assets_per_slot: int = Field(default=500, ge=50, le=2000)
    redundancy: int = Field(default=1, ge=1, le=3)
    stale_timeout_s: float = Field(default=120, ge=10, le=600)
    rotation_s: float = Field(default=300, ge=60, le=3600)

class RTDSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pool_size: int = Field(default=2, ge=1, le=5)
    rotation_s: float = Field(default=300, ge=60, le=3600)
    dedup_ttl_s: float = Field(default=600, ge=60, le=3600)

class RPCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publish_workers: int = Field(default=8, ge=1, le=32)
    dedup_ttl_s: float = Field(default=60, ge=10, le=600)
    stale_timeout_s: float = Field(default=120, ge=30, le=600)

class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_liveness_timeout_s: float = Field(default=30, ge=5, le=300)
    volume_drop_red_pct: float = Field(default=10, ge=1, le=50)
    degraded_grace_s: float = Field(default=120, ge=30, le=900)
    enrichment_ratio_min: float = Field(default=0.8, ge=0.5, le=1.0)

class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_total_exposure_usd: float = Field(default=5000, ge=0, le=100_000)
    patient_timeout_s: float = Field(default=30, ge=5, le=300)
    fee_pct: float = Field(default=0.0, ge=0.0, le=0.05)

class LifecycleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reconcile_interval_s: float = Field(default=300, ge=60, le=3600)
    archive_after_days: int = Field(default=30, ge=7, le=365)
    resolution_poll_interval_s: float = Field(default=60, ge=10, le=600)
```

### Redis Key Structure

```
pm:config:strategy:{name}       → HASH  (strategy params)
pm:config:ingest:clob_ws        → HASH  (CLOB WS tuning)
pm:config:ingest:rtds           → HASH  (RTDS tuning)
pm:config:ingest:rpc            → HASH  (RPC tuning)
pm:config:ingest:pending        → HASH  (pending block tuning)
pm:config:quality               → HASH  (quality thresholds)
pm:config:execution             → HASH  (execution limits)
pm:config:lifecycle             → HASH  (market lifecycle)
pm:config:changed               → PUB/SUB channel
pm:config:changelog             → STREAM (audit trail)
```

### What Stays Hardcoded vs Static vs Dynamic

| Category | Where | Examples | Changeable at runtime? |
|----------|-------|---------|----------------------|
| **Hardcoded** | `constants.py` | EXCHANGE_ADDRS, USDC_SCALE, topic names, table names | Never |
| **Env-only** | `PM_*` env vars | Connection strings, API keys, feature flags | No (restart required) |
| **Dynamic** | TOML + Redis | Strategy params, ingestor tuning, quality thresholds | Yes (API + Pub/Sub) |

---

## Package 2: `pm-ingest` (Layer 1)

5 sources (no mempool). Publisher protocol injection.

```
packages/pm-ingest/
├── pyproject.toml               # depends on: pm-core
└── src/pm_ingest/
    ├── __init__.py
    ├── base.py                  # BaseIngestor (heartbeat, circuit breaker, Publisher injection)
    ├── ingestors/
    │   ├── rpc.py               # RPCIngestor (multi-endpoint racing + resolution detection)
    │   ├── rtds.py              # RTDSIngestor (connection pool + rotation)
    │   ├── clob_ws.py           # CLOBOrderbookIngestor (per-asset ownership, BookWriter)
    │   ├── pending.py           # PendingBlockIngestor (early signal)
    │   └── subgraph.py          # SubgraphPoller (gap recovery)
    ├── normalize/
    │   ├── decode.py            # All decode_*() functions (one per source)
    │   ├── enrich.py            # Shared: taker dedup, token_map, versioning
    │   └── validate.py          # Price clamping, size checks
    ├── dedup.py                 # TradeDedup (TTL-based, O(1) amortized)
    ├── circuit_breaker.py       # CircuitBreaker (closed/open/half_open)
    ├── publish.py               # safe_publish() wrapper
    ├── reconciler.py            # CLOB WS slot management
    └── asset_registry.py        # Asset subscription state (Redis-backed)
```

### Key Changes

- **No mempool** — dead, removed entirely.
- **Delete legacy normalizers** — `normalizers/sink.py`, `normalizers/rtds.py`, `normalizers/market_ws.py`, `live/normalizers/polygon_rpc.py` all gone. Single decode/enrich/validate pipeline.
- **Publisher protocol** — ingestors receive `Publisher` via constructor, not `KafkaBroker`.
- **BookWriter protocol** — CLOB WS ingestor writes orderbook to SharedMemory via `BookWriter`, not Redis.
- **ConfigWatcher** — CLOB WS, RTDS, RPC ingestors each hold a `ConfigWatcher[CLOBWSConfig]` etc. Params tunable at runtime.

### CLOB WS + SharedMemory Integration

```python
class CLOBOrderbookIngestor:
    def __init__(
        self,
        publisher: Publisher,          # For Kafka cold path (orderbook_l2 persistence)
        book_writer: BookWriter,       # For SharedMemory hot path (strategy reads)
        config: ConfigWatcher[CLOBWSConfig],
        token_map: TokenMap,
        ...
    ):
        config.on_change(self._on_config_change)

    async def _handle_book_update(self, asset_id, bids, asks):
        # Hot path: write to SharedMemory (sub-μs)
        self._book_writer.update(asset_id, best_bid, best_ask, bids, asks, ...)

        # Cold path: publish to Kafka for CH persistence (async, can lag)
        if top10_changed:
            await safe_publish(self._publisher, "orderbooks.raw", asset_id, json_msg)

    async def _on_config_change(self, old: CLOBWSConfig, new: CLOBWSConfig):
        if new.assets_per_slot != old.assets_per_slot:
            await self._reconciler.rebalance(new.assets_per_slot)
        if new.max_slots != old.max_slots:
            await self._reconciler.resize_pool(new.max_slots)
```

---

## Package 3: `pm-store` (Layer 1)

Storage backends + SharedMemory + migrations.

```
packages/pm-store/
├── pyproject.toml               # depends on: pm-core
└── src/pm_store/
    ├── __init__.py
    ├── clickhouse/
    │   ├── client.py            # Async CH client wrapper
    │   ├── sink.py              # TradeSink + TradeQuery implementation
    │   ├── queries.py           # Parameterized QueryBuilder (no SQL injection)
    │   └── migrations/
    │       ├── runner.py        # MigrationRunner (tracks applied versions)
    │       ├── registry.py      # Ordered migration registry
    │       └── versions/
    │           ├── 001_trades_raw.py
    │           ├── 002_orderbook_l2.py
    │           ├── 003_orderbook_bars.py
    │           ├── 004_trader_volumes.py
    │           ├── 005_trader_positions.py
    │           ├── 006_exchange_bars.py
    │           └── 007_kafka_engines.py
    ├── postgres/
    │   ├── pool.py              # asyncpg pool lifecycle
    │   ├── sink.py              # MetadataSink implementation (FK-ordered upserts)
    │   └── changelog.py         # market_changelog table operations
    ├── shmem/
    │   ├── book.py              # OrderbookSlot struct + BookRegion
    │   ├── ring.py              # Generic SharedMemory SPMC ring buffer
    │   └── index.py             # asset_id → slot hash index
    ├── parquet/
    │   ├── loader.py            # fastparquet + arrow readers
    │   ├── writer.py            # zstd compressed writers
    │   └── checkpoint.py        # File-based Checkpoint implementation
    ├── kafka/
    │   └── publisher.py         # Publisher protocol implementation (FastStream)
    └── market_sync.py           # Gamma/CLOB API → MetadataSink
```

### SharedMemory Layout

```
Region: "pm_orderbook" (~6 MB)
├── Header (64 bytes)
│   ├── magic: u32 = 0x504D4F42 ("PMOB")
│   ├── version: u32
│   ├── num_slots: u32
│   ├── slot_size: u32
│   ├── writer_pid: u32
│   └── last_write_ns: u64
├── Hash Index (256 KB)
│   └── 32K entries × 8 bytes (asset_id_hash → slot_index, open addressing)
└── Slots (15K × 320 bytes ≈ 5 MB)
    └── Per slot:
        ├── asset_id: char[66]
        ├── best_bid: f64, best_ask: f64
        ├── bid_depth_usd: f64, ask_depth_usd: f64
        ├── timestamp_ns: u64
        ├── sequence: u64          ← writer bumps atomically, readers detect torn writes
        ├── n_levels: u8, u8
        ├── bids: [(f64, f64)] × 10
        └── asks: [(f64, f64)] × 10
```

**Torn write detection:**
```python
class BookReaderImpl:
    def get(self, asset_id: str) -> OrderbookSnapshot | None:
        slot = self._index.lookup(asset_id)
        if slot is None: return None
        seq1 = self._read_sequence(slot)
        data = self._read_fields(slot)
        seq2 = self._read_sequence(slot)
        if seq1 != seq2: return None  # Writer was mid-update, skip
        return OrderbookSnapshot(**data)
```

### CH Migration Framework

```python
# versions/001_trades_raw.py
"""
trades_raw: ReplacingMergeTree for deduplicated trade storage.
Highest _version wins: on-chain(2) > off-chain(1) > pending(0).
"""

DEPENDS_ON: list[str] = []

UP = """
CREATE TABLE IF NOT EXISTS {database}.trades_raw (
    trade_id       String,
    condition_id   LowCardinality(String),
    ...
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
"""

DOWN = "DROP TABLE IF EXISTS {database}.trades_raw"


# versions/007_kafka_engines.py
"""
Kafka engine tables + MVs. Created AFTER target tables.
broker_list injected at runtime.
"""

DEPENDS_ON = ["001_trades_raw", "002_orderbook_l2"]

def up(client, *, broker_list: str, database: str) -> None: ...
def down(client, *, database: str) -> None: ...
```

```python
# runner.py
class MigrationRunner:
    TRACKING_TABLE = "schema_migrations"  # (version String, applied_at DateTime64)

    def pending(self) -> list[Migration]: ...
    def apply(self, target: str | None = None) -> list[str]: ...
    def status(self) -> list[dict]: ...        # version, applied, depends_on, docstring
    def validate_graph(self) -> list[str]: ... # detect cycles, missing deps
```

### Parameterized Queries

```python
# queries.py
class QueryBuilder:
    @staticmethod
    def trades_by_market(condition_id: str, limit: int = 100) -> tuple[str, dict]:
        return (
            """SELECT trade_id, side, price, amount_usd, timestamp
               FROM trades_raw
               WHERE condition_id = {cid:String}
               ORDER BY timestamp DESC
               LIMIT {lim:UInt32}""",
            {"cid": condition_id, "lim": limit},
        )

    @staticmethod
    def price_history(condition_id: str, start: float, end: float) -> tuple[str, dict]:
        return (
            """SELECT toStartOfMinute(timestamp) AS minute,
                      avg(price) AS avg_price, count() AS n
               FROM trades_raw
               WHERE condition_id = {cid:String}
                 AND timestamp BETWEEN {start:Float64} AND {end:Float64}
               GROUP BY minute ORDER BY minute""",
            {"cid": condition_id, "start": start, "end": end},
        )
```

---

## Package 4: `pm-strategy` (Layer 2)

Protocols, execution, context, features.

```
packages/pm-strategy/
├── pyproject.toml               # depends on: pm-core, pm-store
└── src/pm_strategy/
    ├── __init__.py
    ├── protocols.py             # Strategy, VectorizedStrategy, Executor, FeatureBackend, FeatureProvider
    ├── types.py                 # TradeIntent, Fill, Position, MarketInfo, OrderbookSnapshot
    ├── config.py                # TOML parsing (delegates schema to pm-core)
    ├── registry.py              # Auto-discovery via entry_points
    ├── promotion.py             # Gate checker (vectorized → paper → live)
    ├── context/
    │   ├── memory.py            # InMemoryContext
    │   ├── shmem.py             # SharedMemoryContext (reads orderbooks from BookReader)
    │   └── redis.py             # RedisContext (legacy compat, delegates to inner)
    ├── execution/
    │   ├── gateway.py           # ExecutionGateway (budget from ConfigWatcher, quality gate)
    │   ├── paper.py             # PaperExecutor (reads OB from BookReader, not Redis)
    │   ├── simulated.py         # SimulatedExecutor (deterministic fills)
    │   ├── realistic.py         # RealisticFillSimulator (calibrated slippage as fee)
    │   ├── live.py              # LiveExecutor (CLOB API, position limits from ConfigWatcher)
    │   ├── calibrate.py         # Spread/volume calibration
    │   ├── fees.py              # Fee schedules (NONE, CRYPTO, SPORTS)
    │   └── monitor.py           # PositionMonitor (trailing stop, [0.30, 0.70) gate)
    ├── features/
    │   ├── polars.py            # PolarsBackend (offline)
    │   └── clickhouse.py        # ClickHouseBackend (live, uses TradeQuery protocol)
    └── impl/                    # Concrete strategies (auto-discovered via entry_points)
        ├── tag_hr_copy/
        │   ├── __init__.py
        │   ├── strategy.py
        │   └── provider.py
        └── crypto_gbm/
            ├── __init__.py
            ├── strategy.py
            └── providers.py
```

### Auto-Discovery via Entry Points

```toml
# packages/pm-strategy/pyproject.toml
[project.entry-points."pm.strategies"]
tag_hr_copy = "pm_strategy.impl.tag_hr_copy:TagHRCopyStrategy"
crypto_gbm = "pm_strategy.impl.crypto_gbm:CryptoGBMStrategy"

[project.entry-points."pm.providers"]
tag_hr_provider = "pm_strategy.impl.tag_hr_copy:TagHRProvider"
crypto_windows = "pm_strategy.impl.crypto_gbm:CryptoWindowProvider"
exchange_prices = "pm_strategy.impl.crypto_gbm:ExchangePriceProvider"
```

```python
# registry.py
class StrategyRegistry:
    def __init__(self):
        self._strategies: dict[str, type] = {}
        self._providers: dict[str, type] = {}

    def discover(self) -> None:
        """Load from entry_points. Called once at startup."""
        for ep in importlib.metadata.entry_points(group="pm.strategies"):
            self._strategies[ep.name] = ep.load()
        for ep in importlib.metadata.entry_points(group="pm.providers"):
            self._providers[ep.name] = ep.load()

    def create_strategy(self, name: str, config: StrategyConfig) -> Strategy:
        cls = self._strategies[name]
        return cls(**config.params)

    def create_provider(self, name: str, **params) -> FeatureProvider:
        cls = self._providers[name]
        return cls(name=name, **params)
```

Adding a new strategy = add a module + add entry_point in pyproject.toml. No manual dict.

### SharedMemoryContext

```python
class SharedMemoryContext(InMemoryContext):
    """Hot path: reads orderbooks from SharedMemory, everything else from memory."""

    def __init__(self, book_reader: BookReader, token_map: TokenMap):
        super().__init__()
        self._book = book_reader
        self._token_map = token_map

    async def get_orderbook_by_asset(self, asset_id: str) -> OrderbookSnapshot | None:
        raw = self._book.get(asset_id)
        if raw is None: return None
        return OrderbookSnapshot(**raw)

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        lookup = self._token_map.lookup_yes_asset(condition_id)
        if lookup is None: return None
        return await self.get_orderbook_by_asset(lookup)
```

### ConfigWatcher Integration in Gateway

```python
class ExecutionGateway:
    def __init__(
        self,
        executor: Executor,
        config: ConfigWatcher[ExecutionConfig],
        strategy_configs: dict[str, ConfigWatcher[StrategyConfig]],
        quality_state: ReadinessState,
        log_path: Path | None = None,
    ):
        self._exec_config = config
        self._strat_configs = strategy_configs

    async def submit(self, intent: TradeIntent) -> Fill:
        # Budget gate reads LIVE config (not startup snapshot)
        strat_cfg = self._strat_configs[intent.strategy].current
        if self._spent[intent.strategy] + intent.size_usd > strat_cfg.capital_usd:
            return Fill(status=FillStatus.REJECTED, error="budget_exceeded")
        ...
```

---

## Package 5: `pm-backtest` (Layer 2)

Promoted from research/ to core. Production-grade backtesting infrastructure.

```
packages/pm-backtest/
├── pyproject.toml               # depends on: pm-core, pm-strategy, pm-store
└── src/pm_backtest/
    ├── __init__.py
    ├── replay.py                # ReplayTick (slots, 0.5μs), load_replay_trades, load_resolutions
    ├── sync_runner.py           # SyncReplayRunner (zero-async, manual coroutine stepping)
    ├── harness.py               # run_fast_backtest(), print_summary()
    ├── calibrate.py             # Spread/volume calibration from trade history
    ├── runners/
    │   ├── backtest.py          # BacktestRunner (event-driven async)
    │   ├── vectorized.py        # VectorizedRunner (batch)
    │   ├── replay.py            # ReplayRunner (with mid-replay settlement)
    │   ├── combined.py          # CombinedBacktestRunner (multi-strategy)
    │   └── parity.py            # ParityRunner (vectorized vs tick comparison)
    ├── ledger/
    │   ├── types.py             # LedgerRecord (frozen dataclass)
    │   ├── base.py              # make_ledger_record, compute_pnl
    │   ├── parquet.py           # ParquetLedger (buffer → flush → disk)
    │   └── analytics.py         # compute_summary → LedgerSummary
    └── conftest.py              # Shared pytest fixtures (permissive_config, sample_trades)
```

This package is importable by research/ AND by pm-pipeline (for promotion gate checks).

---

## Package 6: `pm-pipeline` (Layer 3)

Orchestration, quality, market lifecycle, unified CLI.

```
packages/pm-pipeline/
├── pyproject.toml               # depends on: pm-core, pm-ingest, pm-store, pm-strategy, pm-backtest
└── src/pm_pipeline/
    ├── __init__.py
    ├── app.py                   # FastStream app (thin — services injected)
    ├── orchestrator.py          # Ingestor lifecycle (with restart policy)
    ├── quality/
    │   ├── state.py             # PipelineState, ReadinessState (observable)
    │   ├── checker.py           # QualityChecker (grace periods from ConfigWatcher)
    │   └── observer.py          # QualityObserver protocol + auto-protect impl
    ├── lifecycle/
    │   ├── service.py           # MarketLifecycleService (state machine)
    │   ├── states.py            # DETECTED → ACTIVE → RESOLVING → RESOLVED → ARCHIVED / VOIDED
    │   └── reconciler.py        # Periodic CLOB API poll, catch missed events
    ├── protection.py            # Auto-protect (with timeout wrapper)
    ├── token_map_service.py     # Push-based TokenMap refresh with subscribers
    ├── settings.py              # Env-only settings (connection strings, feature flags)
    └── cli/
        ├── __init__.py          # `pm` command group (Typer)
        ├── ingest.py            # pm ingest {live, recover}
        ├── data.py              # pm data {sync, compact, load, build, export}
        ├── strategy.py          # pm strategy {run, promote, reset}
        ├── ops.py               # pm {panic, migrate}
        └── config.py            # pm config {get, set, diff, log}
```

### MarketLifecycleService

```python
class MarketState(str, Enum):
    DETECTED  = "detected"     # CLOB WS new_market / Gamma API
    ACTIVE    = "active"       # Metadata fetched, PG upserted, OB subscribed
    RESOLVING = "resolving"    # Past end_date, waiting for winner
    RESOLVED  = "resolved"     # Winner confirmed, positions settled
    VOIDED    = "voided"       # No winner (refund)
    ARCHIVED  = "archived"     # TTL expired, OB unsubscribed

class MarketLifecycleService:
    def __init__(
        self,
        metadata_sink: MetadataSink,
        token_map_service: TokenMapService,
        config: ConfigWatcher[LifecycleConfig],
    ): ...

    async def on_new_market(self, condition_id: str, source: str) -> None:
        """DETECTED → fetch metadata → ACTIVE."""

    async def on_resolution_event(self, condition_id: str, winner: str, source: str) -> None:
        """ACTIVE/RESOLVING → RESOLVED. Settle positions, log to changelog."""

    async def reconcile(self) -> int:
        """Periodic: find ACTIVE past end_date, poll CLOB API, transition."""

    async def archive_old(self) -> int:
        """Periodic: RESOLVED older than archive_after_days → ARCHIVED."""
```

**PG changelog:**
```sql
CREATE TABLE market_changelog (
    id           BIGSERIAL PRIMARY KEY,
    condition_id TEXT NOT NULL,
    old_state    TEXT,
    new_state    TEXT NOT NULL,
    source       TEXT NOT NULL,  -- 'clob_ws', 'clob_api', 'gamma_api', 'reconciler'
    detail       JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
```

### Ingestor Restart Policy

```python
class RestartPolicy:
    max_restarts: int = 3
    backoff_base_s: float = 5.0
    backoff_max_s: float = 300.0
    reset_after_s: float = 900.0  # Reset restart count if stable for 15 min

async def supervise_tasks(tasks: list[IngestorTask], checker: QualityChecker):
    while tasks:
        done, _ = await asyncio.wait([t.task for t in tasks], return_when=FIRST_COMPLETED)
        for t in done:
            info = find_task(t)
            if info.restarts < info.policy.max_restarts:
                delay = min(info.policy.backoff_base_s * 2**info.restarts, info.policy.backoff_max_s)
                await asyncio.sleep(delay)
                info.task = asyncio.create_task(info.ingestor.run())
                info.restarts += 1
            else:
                log.error("Ingestor exhausted restarts", source=info.name)
```

### Unified CLI

```
pm ingest live                          # Start live pipeline
pm ingest recover                       # Gap recovery (with checkpoint resume)

pm data sync [--force]                  # Gamma/CLOB → PG + Parquet
pm data compact                         # Recompress raw parquet
pm data load                            # Compact → ClickHouse
pm data build [--step ...]              # Full pipeline
pm data export [--only ...]             # CH → Parquet snapshot

pm strategy run --config configs/x.toml # Run strategies
pm strategy promote NAME --to paper_prod
pm strategy reset --yes

pm config get strategy:crypto_gbm       # Show effective + layers
pm config set strategy:crypto_gbm capital_usd=750
pm config diff strategy:crypto_gbm      # Show overrides vs defaults
pm config clear strategy:crypto_gbm     # Remove all overrides
pm config log --section strategy:crypto_gbm --limit 20

pm panic                                # Emergency close
pm migrate                              # Alembic upgrade
pm api start                            # FastAPI server
```

---

## Package 7: `pm-api` (Layer 3)

Ground-up RESTful API.

```
packages/pm-api/
├── pyproject.toml               # depends on: pm-core, pm-store
└── src/pm_api/
    ├── __init__.py
    ├── app.py                   # create_app() factory with DI
    ├── deps.py                  # FastAPI Depends() factories
    ├── pagination.py            # OffsetPagination, CursorPagination
    ├── filters.py               # FilterSet base, EqFilter, RangeFilter, EnumFilter
    ├── errors.py                # Consistent ErrorResponse envelope
    ├── resources/
    │   ├── health.py            # GET /health
    │   ├── config.py            # GET/PUT/DELETE /api/v1/config/:section
    │   ├── markets.py           # /api/v1/markets
    │   ├── trades.py            # /api/v1/trades
    │   ├── strategies.py        # /api/v1/strategies
    │   ├── intents.py           # /api/v1/intents
    │   ├── fills.py             # /api/v1/fills
    │   ├── roundtrips.py        # /api/v1/roundtrips
    │   ├── orderbooks.py        # /api/v1/orderbooks (reads from SharedMemory)
    │   ├── prices.py            # /api/v1/prices
    │   └── pipeline.py          # /api/v1/pipeline (ingestion, quality)
    └── schemas/
        ├── common.py            # PageResponse, ErrorResponse, FilterParams
        ├── market.py            # MarketResponse, MarketFilters
        ├── trade.py             # TradeResponse, TradeFilters
        ├── intent.py            # IntentResponse, IntentDetail
        ├── config.py            # ConfigResponse, LayeredValue
        └── ...
```

### Standard Response Envelopes

```python
# List
{
  "data": [...],
  "pagination": {
    "total": 1523,
    "limit": 50,
    "offset": 0,
    "has_more": true
  },
  "filters_applied": {"strategy": "crypto_gbm", "status": "filled"}
}

# Detail
{
  "data": { ... },
  "related": {
    "fill": "/api/v1/fills/abc123",
    "market": "/api/v1/markets/0xdef..."
  }
}

# Config
{
  "data": {
    "section": "strategy:crypto_gbm",
    "effective": {"capital_usd": 750, ...},
    "layers": {
      "default": {"capital_usd": 500, ...},
      "toml":    {"capital_usd": 500, "max_position_usd": 50},
      "env":     {},
      "override": {"capital_usd": 750}
    },
    "overridden_fields": ["capital_usd"],
    "last_modified": "2026-03-13T10:30:00Z"
  }
}

# Error
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "capital_usd must be <= 100000",
    "detail": {"field": "capital_usd", "value": 999999}
  }
}
```

### Resource Endpoints

```
GET  /api/v1/markets?status=active&category=crypto&search=bitcoin&sort=-volume&limit=50&offset=0
GET  /api/v1/markets/:cid
GET  /api/v1/markets/:cid/trades?limit=100
GET  /api/v1/markets/:cid/prices?zoom=1h|6h|24h|7d|30d|all

GET  /api/v1/trades?condition_id=...&source=rpc|rtds&side=BUY&after=...&min_usd=100&sort=-timestamp
GET  /api/v1/strategies
GET  /api/v1/strategies/:name/summary

GET  /api/v1/intents?strategy=...&side=BUY&status=filled&sort=-signal_time&limit=50
GET  /api/v1/intents/:id

GET  /api/v1/fills?strategy=...&condition_id=...&sort=-filled_at
GET  /api/v1/roundtrips?strategy=...&status=open|closed&sort=-entry_time
GET  /api/v1/roundtrips/:config/:cid

GET  /api/v1/orderbooks/:asset_id?depth=10                    ← reads SharedMemory
GET  /api/v1/pipeline/health
GET  /api/v1/pipeline/ingestion?hours=1|6|24
GET  /api/v1/pipeline/quality

GET  /api/v1/config
GET  /api/v1/config/:section
PUT  /api/v1/config/:section    body: {"capital_usd": 750}    ← writes Redis
DELETE /api/v1/config/:section  ?key=capital_usd              ← clears override
GET  /api/v1/config/changelog?section=...&limit=50
```

### Filter Implementation

```python
class FilterSet:
    def __init__(self, params: dict[str, Any], allowed: dict[str, FilterSpec]):
        self._predicates = []
        for key, spec in allowed.items():
            if key in params and params[key] is not None:
                self._predicates.append(spec.to_predicate(params[key]))

    def to_sql(self) -> tuple[str, dict[str, Any]]:
        """Returns (WHERE clauses joined by AND, params dict)."""
        clauses = [p.clause for p in self._predicates]
        params = {}
        for p in self._predicates:
            params.update(p.params)
        return (" AND ".join(clauses) if clauses else "1=1", params)


class TradeFilters(FilterSet):
    SPEC = {
        "condition_id": EqFilter("condition_id", param_type="String"),
        "source":       EnumFilter("source", Source),
        "side":         EnumFilter("side", Side),
        "after":        GteFilter("timestamp", param_type="Float64"),
        "before":       LteFilter("timestamp", param_type="Float64"),
        "min_usd":      GteFilter("amount_usd", param_type="Float64"),
    }
```

---

## `research/` — Consumer Directory

NOT a package. NOT in `src/`. Imports from packages above via `PYTHONPATH=.` or `uv run --extra research`.

```
research/
├── db.py                        # DuckDB singleton (imports pm_backtest for types)
├── server.py                    # Research HTTP server (port 9999)
├── export.py                    # CH → Parquet snapshot (with Checkpoint)
├── strategies/                  # Draft strategies (not registered via entry_points)
│   ├── example.py
│   └── consensus_v2.py
├── knowledge/                   # Markdown knowledge base
│   ├── README.md
│   ├── data/
│   ├── signals/
│   ├── pitfalls/
│   ├── execution/
│   └── queries/
├── hypotheses/                  # Per-hypothesis folders
│   ├── _template/
│   └── .../
└── notebooks/                   # Marimo notebooks
```

---

## `ui/` — Ground-Up Redesign

```
ui/
├── app/
│   ├── layout.tsx                    # Shell: sidebar nav + header
│   ├── page.tsx                      # Dashboard overview
│   ├── markets/
│   │   ├── page.tsx                  # Market list (DataTable, filterable)
│   │   └── [cid]/page.tsx            # Market detail + price chart
│   ├── strategies/
│   │   ├── page.tsx                  # Strategy list + summary cards
│   │   └── [name]/page.tsx           # Strategy detail (intents, fills, PnL)
│   ├── intents/
│   │   ├── page.tsx                  # Intent list (filterable, sortable)
│   │   └── [id]/page.tsx             # Intent detail (chart, OB, PnL)
│   ├── roundtrips/
│   │   ├── page.tsx                  # Roundtrip list + PnL summary
│   │   └── [config]/[cid]/page.tsx   # Roundtrip detail
│   ├── pipeline/
│   │   └── page.tsx                  # Ingestion + quality + source health
│   └── config/
│       └── page.tsx                  # Live config editor (layers, diff, changelog)
├── components/
│   ├── data-table/
│   │   ├── DataTable.tsx             # Generic: column defs + filter specs → rendered table
│   │   ├── Pagination.tsx            # Offset or cursor
│   │   ├── FilterBar.tsx             # Renders FilterSpec[] as inputs
│   │   └── SortHeader.tsx            # Click-to-sort column headers
│   ├── charts/
│   │   ├── PriceChart.tsx
│   │   ├── TpsChart.tsx
│   │   └── PnlChart.tsx
│   ├── cards/
│   │   ├── MetricCard.tsx
│   │   └── StrategyCard.tsx
│   └── config/
│       ├── ConfigEditor.tsx          # Per-section: show layers, edit overrides
│       └── ConfigDiff.tsx            # Visual diff (default vs override)
├── hooks/
│   ├── useApi.ts                     # SWR wrapper with auto-refresh
│   ├── usePagination.ts              # URL-synced pagination state
│   └── useFilters.ts                 # URL-synced filter state
├── lib/
│   ├── api-client.ts                 # Generated from OpenAPI or typed fetch
│   ├── types.ts                      # Response types
│   └── format.ts                     # Number, time, currency formatters
└── package.json
```

**Key UI patterns:**
- URL-synced state — filters, sort, page all in query params; shareable, back-button works
- SWR — stale-while-revalidate replaces manual setInterval; configurable refresh interval
- Generic DataTable — one component for all list views
- Config editor — shows all layers, lets you edit/clear overrides, shows changelog

---

## Data Flow Summary (Hot vs Cold)

```
                        HOT PATH (sub-μs reads)
                        ═══════════════════════
CLOB WS ──→ Ingestor ──→ SharedMemory Ring ──→ Strategy Process reads
                │                                  │
                │                                  ├── on_trade() → TradeIntent → Fill
                │                                  └── get_orderbook() → zero-copy read
                │
                │         COLD PATH (persistence)
                │         ═══════════════════════
                ├──→ Kafka (trades.raw) ──→ CH trades_raw (ReplacingMergeTree)
                ├──→ Kafka (orderbooks.raw) ──→ CH orderbook_l2 (TTL 1yr)
                └──→ Kafka (markets.events) ──→ MarketLifecycleService

RPC ────────→ Ingestor ──→ Kafka (trades.raw) ──→ CH
                └──→ Resolution detection ──→ Kafka (markets.events) ──→ Lifecycle

RTDS ───────→ Ingestor ──→ Kafka (trades.raw) ──→ CH

Pending ────→ Ingestor ──→ Kafka (pending.signal) ──→ Strategy (opt-in)

Gamma API ──→ MarketLifecycleService ──→ PG (metadata)
CLOB API ───→ MarketLifecycleService ──→ PG (resolution)
                                       → TokenMapService (push to subscribers)

PG ─────────→ CH PG Engine (metadata reads)
```

---

## Config Flow Summary

```
TOML file (configs/*.toml)
  ↓ parsed at startup (baseline)
ENV vars (PM_*)
  ↓ override TOML (per-deployment)
Redis HASH (pm:config:*)
  ↓ override ENV (runtime, API-writable)
Schema validation (Pydantic, extra=forbid)
  ↓ reject bad values at every boundary
ConfigWatcher.current → consumer reads effective config

           ┌──────────────────────────────────┐
           │  API PUT /api/v1/config/:section  │
           └─────────┬────────────────────────┘
                     │ validate against schema
                     │ HSET to Redis
                     │ XADD to changelog stream
                     │ PUBLISH to pm:config:changed
                     ▼
    ┌─────────────────────────────────┐
    │  Redis Pub/Sub: pm:config:changed  │
    └──────┬──────────┬───────────┬──┘
           │          │           │
    ConfigWatcher  ConfigWatcher  ConfigWatcher
    (strategy)     (CLOB WS)     (quality)
           │          │           │
    re-validate   rebalance    adjust
    budget caps   slot pool    thresholds
```

---

## Migration Plan

### Phase 1: `pm-core` (2 days)
Extract models, types, constants, trade_id, token_map.
Add protocols.py and config/ module.
Update all imports. Tests pass.

### Phase 2: `pm-store` (3 days)
Extract CH + PG sinks, loaders, market_sync.
Add SharedMemory module (book.py, ring.py, index.py).
Add CH migration framework.
Add QueryBuilder (parameterized queries).

### Phase 3: `pm-ingest` (3 days)
Extract ingestors + normalize pipeline.
Delete legacy normalizers and mempool.
Inject Publisher + BookWriter protocols.
Wire ConfigWatcher for CLOB WS / RTDS / RPC params.

### Phase 4: `pm-strategy` (2 days)
Extract protocols, execution, context, features.
Add SharedMemoryContext.
Add entry_point auto-discovery.
Move concrete strategies to impl/.

### Phase 5: `pm-backtest` (2 days)
Promote runners, replay, harness, ledger from research/ to package.
Wire pm-strategy + pm-store dependencies.
Update research/ to import from pm_backtest.

### Phase 6: `pm-pipeline` (3 days)
Extract orchestration, quality, protection.
Add MarketLifecycleService.
Add ingestor restart policy.
Unify CLI as `pm` command group.
Wire ConfigStore + ConfigWatcher throughout.

### Phase 7: `pm-api` + `ui/` (5 days)
Ground-up API with resources, pagination, filtering.
Config endpoints (GET/PUT/DELETE + changelog).
Ground-up UI with DataTable, SWR, URL-synced state.
Config editor page.

### Phase 8: Cleanup (2 days)
Remove old src/polymarket_pipeline/ tree.
Update CI, CLAUDE.md, pyproject.toml.
Verify all tests pass. Run mypy.
