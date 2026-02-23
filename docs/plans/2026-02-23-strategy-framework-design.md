# Strategy Execution Framework Design

Date: 2026-02-23

## Problem

We have a backtester (consensus copy, in `research/`) and a live ingestor pipeline
(FastStream + Kafka), but no framework that lets strategies run identically across
backtest, paper trading, and live execution. As we scale to 10+ strategies, we need
a unified interface that eliminates code divergence between research and production.

## Architecture: Hybrid — Direct Kafka + Shared Services

Strategies consume trades directly from Kafka (no orchestrator hop on the hot path).
Shared services (execution gateway, position store, market data) sit on the cold path.

```
Kafka trades.raw ──┬──> Strategy_1 ──┐
                   ├──> Strategy_2 ──┼──> ExecutionGateway (shared, async)
                   └──> Strategy_N ──┘         │
                                          PositionStore (Redis/in-memory)
                                               │
                                        PortfolioObserver (reads, never blocks)
```

**Key insight:** Order execution is not latency-sensitive (backtester optimal delay = 60s+).
Signal computation is. So:
- **Hot path** (latency-sensitive): Kafka → Strategy (direct, no intermediary)
- **Cold path** (latency-tolerant): Strategy → ExecutionGateway → order placement

Shared state (orderbook, market data) lives in Redis for live modes, with Kafka as the
audit log for replay. Backtest modes use in-memory backends with zero external dependencies.

## Core Protocols

### Strategy (event-driven interface)

```python
@runtime_checkable
class Strategy(Protocol):
    name: str

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to incoming trade. HOT PATH — keep fast."""
        ...

    async def on_market_update(
        self, update: MarketUpdate, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to orderbook/price changes (CLOB WS via Redis)."""
        ...

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Periodic callback (e.g. every 60s). For delayed-entry strategies."""
        ...
```

### VectorizedStrategy (batch interface for research)

```python
class VectorizedStrategy(Protocol):
    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        """Return signal table: condition_id, signal_time, side, outcome, size_usd."""
        ...
```

Strategies that implement both get fast vectorized sweeps for research AND event-driven
replay for validation.

### TradeIntent (strategy output)

```python
@dataclass(frozen=True)
class TradeIntent:
    strategy: str           # which strategy emitted this
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    size_usd: float         # desired notional
    urgency: Literal["immediate", "patient"]  # market vs limit hint
    max_price: float | None # price ceiling (for limit-style)
    reason: str             # human-readable (for logging/audit)
    signal_time: float      # when the signal fired (for latency tracking)
```

### StrategyContext (mode-agnostic data access)

```python
class StrategyContext(Protocol):
    async def get_position(self, condition_id: str) -> Position | None: ...
    async def get_market(self, condition_id: str) -> MarketInfo | None: ...
    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None: ...
    async def get_price(self, condition_id: str, outcome: str) -> float | None: ...
    async def now(self) -> float: ...  # wall clock in live, simulated time in backtest
```

## Execution Modes

| Mode | Context | Trade source | Executor | Purpose |
|------|---------|-------------|----------|---------|
| Vectorized | N/A (Polars) | Parquet/CH batch | N/A | Fast research sweeps |
| Replay | InMemoryContext | Parquet/CH stream | SimulatedExecutor | Parity check vs vectorized |
| Paper-dev | InMemoryContext | Kafka live stream | PaperExecutor | Local dev, no Redis |
| Paper-prod | RedisContext | Kafka live stream | PaperExecutor | Pre-live validation |
| Live | RedisContext | Kafka live stream | LiveExecutor | Real money |

### Strategy promotion pipeline

Each step is a confidence gate — promote only when previous mode checks out:

```
Vectorized research (fast iteration, Polars)
  ↓ parity gate passes
Replay validation (event-driven, in-memory)
  ↓ intents match vectorized
Paper-dev (live Kafka, in-memory state, no Redis)
  ↓ looks good locally
Paper-prod (live Kafka, Redis state, full infra)
  ↓ confident in real conditions
Live (same code, LiveExecutor swapped in)
```

**Promotion is a config change, not a code change.**

### Parity gate

Validates that the event-driven path produces the same results as the vectorized path:

```python
async def validate_parity(
    strategy: Strategy & VectorizedStrategy,
    trades: pl.DataFrame,
    markets: pl.LazyFrame,
) -> ParityReport:
    vectorized_signals = strategy.compute_signals(trades.lazy(), markets)

    ctx = InMemoryContext(markets=markets)
    replay_intents = []
    for trade in trades.sort("published_at").iter_rows(named=True):
        intents = await strategy.on_trade(NormalizedTrade(**trade), ctx)
        if intents:
            replay_intents.extend(intents)

    return ParityReport(
        vectorized_count=len(vectorized_signals),
        replay_count=len(replay_intents),
        matched=...,
        divergences=...,
    )
```

## Data Flow

### Kafka topics

| Topic | Purpose | Key | Retention |
|-------|---------|-----|-----------|
| `trades.raw` | All NormalizedTrade from ingestors | condition_id | 7d (compact) |
| `market.updates` | CLOB WS price/orderbook ticks | condition_id | 1d |
| `intents.log` | Every TradeIntent emitted (all modes) | strategy+condition_id | forever (audit) |
| `fills.log` | Execution results (fill, reject, partial) | strategy+condition_id | forever (audit) |

### Redis keys (live modes only)

| Key pattern | Value | Updated by | TTL |
|-------------|-------|-----------|-----|
| `ob:{condition_id}` | Best bid/ask + depth | CLOB WS ingestor | 60s |
| `market:{condition_id}` | MarketInfo (status, resolution) | PG sync | 1h |
| `pos:{strategy}:{condition_id}` | Position (qty, avg_entry, pnl) | ExecutionGateway | none |
| `strat:state:{strategy}` | Strategy-private state blob | Strategy | none |

## Module Structure

```
src/polymarket_pipeline/
├── strategies/                    # Framework (strategy-agnostic)
│   ├── __init__.py
│   ├── protocol.py                # Strategy, VectorizedStrategy, StrategyContext protocols
│   ├── types.py                   # TradeIntent, Position, MarketInfo, OrderbookSnapshot, Fill
│   ├── context/
│   │   ├── __init__.py
│   │   ├── memory.py              # InMemoryContext (backtest + paper-dev)
│   │   └── redis.py               # RedisContext (paper-prod + live)
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── gateway.py             # ExecutionGateway — routes TradeIntent to executor
│   │   ├── simulated.py           # SimulatedExecutor (backtest fills at signal price)
│   │   ├── paper.py               # PaperExecutor (logs intent, simulates fill from orderbook)
│   │   └── live.py                # LiveExecutor (CLOB API)
│   ├── runners/
│   │   ├── __init__.py
│   │   ├── backtest.py            # Event-driven replay runner
│   │   ├── vectorized.py          # Polars batch runner
│   │   ├── parity.py              # Parity gate (vectorized vs replay comparison)
│   │   └── live.py                # Kafka consumer runner (paper + live modes)
│   ├── registry.py                # Strategy discovery + instantiation
│   └── config.py                  # StrategyConfig (mode, capital, risk params)
│
├── strategies_impl/               # Actual strategy implementations
│   ├── __init__.py
│   └── consensus_copy/
│       ├── __init__.py
│       ├── strategy.py            # ConsensusCopyStrategy(Strategy, VectorizedStrategy)
│       └── config.py              # Consensus copy specific params
│
├── live/                          # Existing ingestors (unchanged)
│   ├── app.py                     # Add strategy runner to lifespan
│   └── ingestors/                 # Still produce to trades.raw
```

## Configuration

Per-strategy TOML config:

```toml
[strategy.consensus_copy]
enabled = true
mode = "paper-dev"              # vectorized | replay | paper-dev | paper-prod | live
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300

# Strategy-specific params
min_traders = 5
agreement_pct = 0.80
direction = "NO"
mvf_band = "pure_taker"
delay_s = 60
```

## MVP Scope

### Build now

- Strategy + VectorizedStrategy + StrategyContext protocols (`protocol.py`, `types.py`)
- InMemoryContext backend (`context/memory.py`)
- SimulatedExecutor for backtest fills (`execution/simulated.py`)
- ExecutionGateway with intent logging to file (`execution/gateway.py`)
- BacktestRunner — event-driven replay (`runners/backtest.py`)
- VectorizedRunner — Polars batch runner (`runners/vectorized.py`)
- Parity gate (`runners/parity.py`)
- ConsensusCopyStrategy — port from `research/` to new protocols (`strategies_impl/consensus_copy/`)
- TOML-based strategy config (`config.py`)
- Strategy registry (`registry.py`)

### Deferred: RedisContext

**What:** `StrategyContext` backed by Redis for shared state (positions, orderbook, market data).
**Why deferred:** Only needed for paper-prod and live modes. InMemoryContext covers backtest + paper-dev.
**Dependency:** Requires Redis in docker-compose. Keys: `ob:{cid}`, `market:{cid}`, `pos:{strat}:{cid}`.
**When:** Before first paper-prod deployment.

### Deferred: PaperExecutor with live orderbook simulation

**What:** Executor that receives TradeIntents, looks up current orderbook from Redis, simulates realistic fills (slippage, partial fills based on available liquidity).
**Why deferred:** MVP uses SimulatedExecutor (instant fill at signal price). Realistic fill simulation requires orderbook data flowing into Redis.
**Dependency:** RedisContext + CLOB WS orderbook ingestor writing to `ob:{cid}`.
**When:** After RedisContext is built and orderbook data is flowing.

### Deferred: LiveExecutor (CLOB API integration)

**What:** Executor that places real orders via Polymarket's CLOB API. Handles order lifecycle: placement, fill confirmation, cancellation, partial fills.
**Why deferred:** Requires CLOB API credentials, order signing, and careful testing. Must work for both market and limit orders.
**Dependency:** PaperExecutor validated first. CLOB API authentication (EIP-712 signatures). Rate limiting.
**When:** After paper-prod demonstrates strategy viability with real market data.

### Deferred: LiveRunner (Kafka consumer)

**What:** Runner that subscribes to `trades.raw` and `market.updates` Kafka topics, dispatches to registered strategies, manages strategy lifecycle (start/stop/restart).
**Why deferred:** MVP focuses on backtest + replay. Live runners need graceful shutdown, rebalancing, consumer group management.
**Dependency:** Kafka topics populated by existing ingestors. Strategy registry for multi-strategy management.
**When:** After parity gate validates consensus copy strategy matches vectorized backtest.

### Deferred: PortfolioObserver

**What:** Read-only service that aggregates positions across all strategies. Provides portfolio-level metrics: total exposure, net position per market, cross-strategy conflicts, drawdown alerts.
**Why deferred:** Independent capital pools work fine for early strategies. Portfolio view becomes critical only when strategies might take opposing positions on the same market.
**Dependency:** RedisContext (reads `pos:{strat}:{cid}` keys). Dashboard integration.
**When:** When running 3+ strategies simultaneously on overlapping markets.

### Deferred: Kafka intents.log and fills.log topics

**What:** Write TradeIntents and execution fills to dedicated Kafka topics for audit trail and replay.
**Why deferred:** MVP logs intents to local files (sufficient for backtest and paper-dev). Kafka topics needed for production audit trail and cross-service consumption.
**Dependency:** Kafka cluster with topic creation rights. Schema registry for intent/fill serialization.
**When:** Before first paper-prod deployment (intents.log is the paper trading audit trail).

### Deferred: Strategy hot-reload

**What:** Ability to update strategy parameters or swap strategy implementations without restarting the runner process.
**Why deferred:** Config-driven restarts are fine for <10 strategies. Hot-reload adds complexity (state migration, in-flight intent handling).
**Dependency:** Strategy registry with versioning. Graceful strategy shutdown (drain pending intents).
**When:** When strategy iteration speed becomes a bottleneck in paper/live modes.

### Deferred: Portfolio-level risk limits

**What:** Cross-strategy risk constraints: max total exposure, max correlation between strategy positions, drawdown circuit breakers, daily loss limits.
**Why deferred:** Requires PortfolioObserver. Independent per-strategy limits (max_position_usd, max_open_positions) provide baseline safety.
**Dependency:** PortfolioObserver + risk rules engine.
**When:** Before live trading with significant capital.
