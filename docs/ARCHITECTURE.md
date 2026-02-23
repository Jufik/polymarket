# Polymarket Pipeline — Architecture

> Unified trade data pipeline + strategy execution framework for Polymarket prediction markets.

---

## System Overview

```
                       ┌──────────────────────────────────────────────────────┐
                       │                    DATA SOURCES                      │
                       ├──────────┬──────────┬─────────────┬────────────────┤
                       │ Goldsky  │  RTDS    │  Alchemy    │ Pending Block  │
                       │ Parquet  │  WS      │  WS (logs)  │ RPC poll       │
                       │(backfill)│ (~50/s)  │ (on-chain)  │ (~1s early)    │
                       └────┬─────┴────┬─────┴──────┬──────┴───────┬────────┘
                            │          │            │              │
                            ▼          ▼            ▼              ▼
                    ┌───────────────────────────────────────────────────────┐
                    │              NormalizedTrade (canonical model)         │
                    │    Pydantic v2, frozen. SHA-256 deterministic IDs.    │
                    └──────────────────────┬────────────────────────────────┘
                                           │
                 ┌─────────────────────────┼─────────────────────────┐
                 ▼                         ▼                         ▼
        ┌────────────────┐     ┌───────────────────┐     ┌──────────────────┐
        │   ClickHouse   │     │    Redpanda        │     │   PostgreSQL     │
        │  trades_raw    │     │  trades.raw topic  │     │   (metadata)     │
        │ ReplacingMerge │     │  (fan-out to       │     │  events, markets │
        │    Tree        │     │   consumers)       │     │  tags, token_map │
        └────────┬───────┘     └─────────┬─────────┘     └──────────────────┘
                 │                       │
                 │              ┌────────┴────────┐
                 │              ▼                  ▼
                 │    ┌──────────────┐   ┌──────────────────┐
                 │    │ LiveRunner   │   │ Other consumers  │
                 │    │ (strategies) │   │ (dashboard, etc) │
                 │    └──────────────┘   └──────────────────┘
                 │
        ┌────────┴──────────────────────────────────────┐
        │          OFFLINE ANALYTICS                     │
        │  Polars scans → derived tables                │
        │  trader_market_pnl, maker_volume_fractions    │
        │  Exploration tree, backtester, parity gate    │
        └───────────────────────────────────────────────┘
```

---

## 1. Data Layer

### Canonical Model: `NormalizedTrade`

Every trade from every source normalizes into this shape:

| Field | Type | Notes |
|---|---|---|
| `trade_id` | `str` | Deterministic SHA-256. `chain:` (on-chain) or `ws:` (off-chain) prefix. |
| `condition_id` | `str` | Market identifier. |
| `asset_id` | `str` | Token identifier. |
| `side` | `Side` | BUY (buying YES tokens) or SELL (selling YES = betting NO). |
| `price` | `Decimal` | In [0, 1], rounded to 4 decimals. |
| `size` | `Decimal` | Token quantity. |
| `amount_usd` | `Decimal` | USDC amount (1e6 scaling, NOT 1e18). |
| `maker` / `taker` | `str \| None` | Nullable — WS sources lack identity. |
| `source` | `Source` | GOLDSKY_SINK, WEBSOCKET, RTDS, ALCHEMY, MEMPOOL, PENDING_BLOCK. |
| `version` | `int` | 0=mempool, 1=off-chain, 2=on-chain. Higher wins in dedup. |
| `published_at` | `float` | Epoch seconds, set by the ingestor. |

**Dedup strategy**: ClickHouse `ReplacingMergeTree` ordered by `(condition_id, timestamp, trade_id)`. Highest `version` survives — on-chain (2) overwrites off-chain (1).

### Storage

| Store | Purpose | Key decisions |
|---|---|---|
| **ClickHouse** | Trade OLAP (trades_raw) | ReplacingMergeTree, HTTP API on :18123 |
| **PostgreSQL** | Metadata (events, markets, tags, token_map) | TEXT columns everywhere (no VARCHAR truncation). Single source of truth for metadata. |
| **Redpanda** | Live stream fan-out | Kafka-compatible. `trades.raw` topic. |

### Offline Derived Tables

Built by `scripts/build_data.py` using pure Polars lazy scans:

```
data/
├── metadata/
│   ├── markets.parquet          # condition_id, resolution, winner, event_id, category
│   └── token_map.parquet        # asset_id → condition_id, outcome, winner
├── compact/
│   └── compact_NNNN.parquet     # Recompressed trades (resume-safe)
└── derived/
    ├── trader_market_pnl.parquet
    ├── maker_volume_fractions.parquet
    ├── markets_resolved.parquet
    └── market_prices.parquet
```

---

## 2. Live Ingestors

All ingestors normalize to `NormalizedTrade` and publish to Redpanda.

| Ingestor | Source | Latency | Identity | Notes |
|---|---|---|---|---|
| **CLOB WS** | Polymarket WebSocket | T+0s (fastest) | None | `last_trade_price` only. |
| **Alchemy** | Polygon `eth_subscribe` | T+3.7s | maker + taker | OrderFilled on-chain logs. Primary identity source. |
| **RTDS** | Polymarket real-time | T+4.2s | taker only | `orders_matched` channel. |
| **Pending Block** | `eth_getBlockByNumber("pending")` | T-1.1s before chain | Full calldata | Free RPC. Polls at 500ms. |
| **Subgraph** | Goldsky GraphQL | Minutes | Full | Gap recovery only. |

**Arrival order** (empirically verified): Pending Block → CLOB WS → Alchemy → RTDS.

**Key insight**: Polymarket operators submit via private channels (Flashbots/MEV relay). Public mempool sees nothing. The pending block RPC exposes the validator's candidate block before finalization.

### Quality Gate

`QualityChecker` monitors source liveness, volume drops, and enrichment ratios. Triggers subgraph recovery if data lag exceeds `gap_threshold_s` (default 600s).

---

## 3. Strategy Framework

### Design Principles

1. **Protocol-first**: All interfaces are `@runtime_checkable` structural protocols (no inheritance).
2. **Frozen types**: All configs and domain objects are immutable.
3. **Async throughout**: All I/O is non-blocking.
4. **Backend-agnostic**: Strategies never know if data comes from Polars or ClickHouse.
5. **Parity-validated**: Same strategy implements both event-driven and vectorized paths; a gate checks they agree.

### Module Map

```
strategies/
├── protocol.py          # StrategyContext, Strategy, VectorizedStrategy, Executor,
│                        # FeatureBackend, FeatureProvider
├── types.py             # TradeIntent, Position, MarketInfo, OrderbookSnapshot,
│                        # Fill, ExecutionMode, FillStatus
├── config.py            # StrategyConfig, ProviderConfig, TOML loaders
├── registry.py          # StrategyRegistry (name → class mapping)
├── context/
│   └── memory.py        # InMemoryContext (dict-backed StrategyContext)
├── features/
│   ├── backend_polars.py     # PolarsBackend (backtest, in-memory)
│   └── backend_clickhouse.py # ClickHouseBackend (live, SQL over HTTP)
├── execution/
│   ├── gateway.py       # ExecutionGateway (intent logging + routing)
│   ├── paper.py         # PaperExecutor (orderbook-aware simulation)
│   └── simulated.py     # SimulatedExecutor (instant fills)
└── runners/
    ├── vectorized.py    # VectorizedRunner (batch DataFrame)
    ├── backtest.py      # BacktestRunner (event-driven replay)
    ├── parity.py        # ParityGate (vec vs replay validation)
    └── live.py          # LiveRunner (Kafka dispatch + providers)

strategies_impl/
└── consensus_copy/
    ├── config.py        # ConsensusCopyConfig
    ├── strategy.py      # ConsensusCopyStrategy (both protocols)
    └── providers.py     # SkilledTradersProvider
```

### Protocols

**StrategyContext** — Read-only view of the world:

```python
async def get_position(condition_id) -> Position | None
async def get_market(condition_id) -> MarketInfo | None
async def get_orderbook(condition_id) -> OrderbookSnapshot | None
async def get_price(condition_id, outcome) -> float | None
async def now() -> float
async def get_features(key) -> Any
```

**Strategy** (event-driven):

```python
name: str
async def on_trade(trade, ctx) -> list[TradeIntent] | None
async def on_market_update(update, ctx) -> list[TradeIntent] | None
async def on_timer(now, ctx) -> list[TradeIntent] | None
```

**VectorizedStrategy** (batch):

```python
def compute_signals(trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame
```

**FeatureProvider** — Independent computation unit:

```python
name: str
async def compute(backend) -> None      # Startup batch computation
async def on_trade(trade) -> None       # O(1) hot-path update
async def refresh(backend) -> None      # Periodic expensive recomputation
def get_features() -> dict[str, Any]    # Current feature values
```

**FeatureBackend** — Data access abstraction:

```python
async def query_trades(condition_ids=None) -> pl.DataFrame
async def query_markets() -> pl.DataFrame
async def query_custom(query, **params) -> pl.DataFrame
```

### Execution Modes

| Mode | Runner | Executor | Backend | Use case |
|---|---|---|---|---|
| **VECTORIZED** | VectorizedRunner | N/A | Polars LazyFrame | Batch backtesting, parameter sweeps |
| **REPLAY** | BacktestRunner | SimulatedExecutor | Polars | Event-driven replay validation |
| **PAPER_DEV** | LiveRunner | PaperExecutor | PolarsBackend | Live paper trading (real Kafka, no money) |
| **PAPER_PROD** | LiveRunner | PaperExecutor | ClickHouseBackend | Scale testing with real-time data |
| **LIVE** | LiveRunner | (real executor) | ClickHouseBackend | Production trading |

### LiveRunner Hot Path

```
Trade arrives from Kafka
    │
    ├─ 1. Providers: on_trade(trade)          # O(1) streaming update
    ├─ 2. Context:   update_features()         # Inject provider features
    ├─ 3. Context:   set_time(published_at)    # Advance clock
    ├─ 4. Strategy:  on_trade(trade, ctx)      # Read updated context
    └─ 5. Gateway:   submit(intent)            # Log + execute
         └─ Executor: execute(intent) → Fill
```

Hot-path timing enforcement: warns if any step exceeds `hot_path_warn_ms` (default 5ms).

Background loops run concurrently:
- **Timer loop**: Calls `strategy.on_timer()` every 60s.
- **Refresh loop**: Calls `provider.refresh()` every 900s (expensive recomputation).

---

## 4. Configuration

TOML-based, loaded at startup:

```toml
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
[provider.skilled_traders.params]
min_trades = 50

[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["skilled_traders"]           # declares provider dependency
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
base_bet_usd = 10.0
```

The CLI validates that every entry in `features` has a matching provider configured. Provider params are passed as kwargs to the provider constructor. Strategy params are passed to the strategy factory.

---

## 5. Consensus-Copy Strategy

The first concrete strategy implementation. Follows skilled-trader consensus to generate trade signals.

### Signal Logic

1. Observe each trade. Filter to skilled traders only.
2. Track per-market: how many skilled traders bet YES vs NO.
3. When `min_traders` reached AND `agreement_pct` exceeded AND direction matches config:
   - Fire a `TradeIntent(side=BUY, outcome=signal_direction, size_usd=base_bet_usd)`.
   - Mark market as "signal fired" (one signal per market).

BUY = buying YES tokens (betting YES). SELL = selling YES tokens (betting NO).

### Backtester Findings

- **Best direction**: NO-only dominates. YES-only is anti-predictive (18.5% hit rate vs 38.1% base).
- **Best filter**: pure_taker (MVF < 0.10) > informed_taker > all traders.
- **Execution delay is beneficial**: Signal improves at 60-300s delay. No latency race needed.
- **Parity gate**: Vectorized and event-driven paths produce identical signals.

### SkilledTradersProvider

Counts distinct markets per maker from historical data. Traders with >= `min_trades` unique markets are "skilled". Recomputed every 15 minutes via `refresh()`.

---

## 6. Exploration System

Autonomous strategy refinement using a tree-based exploration model.

```
ExplorationTree
├── Stage: "Baseline" (root)
│   ├── metrics: sharpe=1.2, win_rate=0.58
│   ├── analysis: ClaudeAnalysis(...)
│   └── refinements: [filter_by_taker, add_delay, ...]
│       ├── Stage: "Taker-only filter"
│       │   ├── metrics: sharpe=1.8, win_rate=0.62
│       │   └── ...
│       └── Stage: "Add 60s delay"
│           └── ...
```

Each `ExplorationStage` captures:
- **StageMetrics**: sharpe, return, win_rate, drawdown, p_value, effect_size.
- **ClaudeAnalysis**: insights, concerns, proposed refinements, branch recommendation.
- **SweepResult**: If a parameter sweep was run.

Refinement types: FILTER, FEATURE, MODEL, PARAMETER, HYPOTHESIS, ENSEMBLE.

The tree persists to disk and renders as Mermaid diagrams. The exploration CLI (`pm-explore`) drives interactive sessions.

---

## 7. CLI Commands

```bash
# Strategy execution (paper-dev mode)
uv run pm-strategy run --config strategies.toml
uv run pm-strategy run --config strategies.toml --only consensus_copy --log-dir /tmp/logs

# Strategy exploration
uv run pm-explore --help

# Data pipeline
uv run python scripts/build_data.py                # all steps
uv run python scripts/build_data.py --step derived  # Polars derived tables only

# Backfill
uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/

# Metadata sync
uv run python -m polymarket_pipeline.cli.market_sync
```

---

## 8. Testing Architecture

```bash
# Unit tests (no Docker)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Integration tests (requires Docker)
uv run pytest tests/test_sink_clickhouse.py -x -q

# Type checking
uv run mypy --strict src/

# Lint
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

Test organization:
- `test_strategy_protocol.py` — Protocol satisfaction tests (duck typing verification).
- `test_strategy_context_memory.py` — InMemoryContext behavior.
- `test_strategy_config.py` — TOML config loading, StrategyRegistry.
- `test_feature_backend_polars.py` / `test_feature_backend_clickhouse.py` — Backend implementations.
- `test_skilled_traders_provider.py` — SkilledTradersProvider logic.
- `test_paper_executor.py` — PaperExecutor pricing and fills.
- `test_runner_live.py` — LiveRunner dispatch and lifecycle.
- `test_cli_strategy.py` — CLI runner assembly from TOML.
- `test_paper_dev_integration.py` — Full end-to-end: provider + strategy + LiveRunner.
- `test_consensus_copy_full.py` — Backtest + parity gate integration.

---

## 9. Key Design Decisions

| Decision | Rationale |
|---|---|
| **Deterministic trade IDs** | SHA-256 from (chain, tx_hash, log_index) or (ws, timestamp, maker, taker, price, size). Enables dedup across sources. |
| **fastparquet only** | pyarrow fails on DECIMAL(100,18) precision > 76. DuckDB casts to lossy DOUBLE. |
| **USDC 1e6** | 6 decimals, not 18. Parquet amounts use this scaling. |
| **Protocol-first** | No base classes. Duck typing via `@runtime_checkable`. Easy to test, easy to mock. |
| **Parity gate** | Every strategy must implement both vectorized and event-driven paths. The gate catches logic bugs. |
| **Features as dict** | `get_features(key)` returns `Any`. Simple, extensible, no schema coupling between providers and strategies. |
| **Frozen configs** | `@dataclass(frozen=True)` everywhere. Prevents accidental mutation in async code. |
| **PostgreSQL = metadata truth** | ClickHouse reads metadata via PostgreSQL table engine. No duplicate writes. |
| **Execution delay is beneficial** | Consensus-copy signals improve at 60-300s delay. No latency race needed. Simplifies live architecture. |
| **Private operator submission** | Polymarket operators bypass public mempool entirely. Pending block RPC is the only pre-chain access. |
