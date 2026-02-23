# Phase 2: Paper-Dev Mode — LiveRunner + FeatureProvider + PaperExecutor

Date: 2026-02-23

## Problem

Phase 1 delivered the strategy framework (protocols, backtest runners, parity gate, ConsensusCopy
port). But strategies can only run on historical data. We need to connect them to the live Kafka
pipeline so they can paper-trade against real-time data — without touching the existing ingestor
code.

Separately, strategies like ConsensusCopy depend on pre-computed features (e.g. "skilled traders
list") that are expensive to derive and independent of per-trade logic. There's no abstraction for
this today — the backtester hardcodes a frozen set, and there's no way to refresh it live.

## What This Phase Builds

1. **FeatureProvider protocol** — Independent computation units (skilled traders, MVF bands) with
   a backend abstraction (Polars for backtest, ClickHouse for live).
2. **LiveRunner** — Kafka consumer that dispatches trades to providers then strategies, with hot
   path timing enforcement.
3. **PaperExecutor** — Executor that simulates fills from context (orderbook when available,
   fallback to mid-price), logs paper trades.
4. **CLI entry point** — `pm-strategy run` command to start strategies against live Kafka.
5. **TOML config extensions** — Feature provider dependencies and provider-specific params.

## Architecture

```
Kafka trades.raw ──> LiveRunner ──> FeatureProvider_1.on_trade()  ← providers first
                         │          FeatureProvider_N.on_trade()
                         │
                         ├───────> Strategy_1.on_trade(ctx)       ← strategies second
                         │         Strategy_N.on_trade(ctx)
                         │
                         └───────> ExecutionGateway
                                       │
                                  PaperExecutor (paper-dev)
                                       │
                                  JSONL intent + fill log
```

**Dispatch order matters:** Providers update context *before* strategies read it. This ensures
strategies always see fresh features (e.g. updated skilled trader set) for the current trade.

## FeatureProvider Protocol

```python
@runtime_checkable
class FeatureProvider(Protocol):
    """Independent computation unit that feeds features into StrategyContext.

    Providers run their own lifecycle: batch compute at startup, O(1) streaming
    updates on each trade, periodic refresh for expensive recomputation.
    """

    name: str

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute features at startup (or after refresh trigger).

        Called once at runner startup. For backtest, this runs before the first
        trade. For live, this runs during LiveRunner initialization.
        """
        ...

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """O(1) streaming update — maintain running state from trade feed.

        HOT PATH. Must be purely in-memory, no I/O. LiveRunner enforces timing.
        """
        ...

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic expensive recomputation (e.g. every 15 min).

        Runs in background, swaps result atomically. Strategies never see
        partial state.
        """
        ...

    def get_features(self) -> dict[str, Any]:
        """Return current feature values for injection into StrategyContext.

        Called by the runner after on_trade() to update context. Must be pure
        read — no computation, no I/O.
        """
        ...
```

### FeatureBackend

Abstracts Polars (backtest) vs ClickHouse (live) for batch queries:

```python
@runtime_checkable
class FeatureBackend(Protocol):
    """Data access layer for FeatureProvider.compute() and .refresh()."""

    async def query_trades(
        self, condition_ids: list[str] | None = None
    ) -> pl.DataFrame:
        """Return trades (optionally filtered). Polars scans parquet or CH queries."""
        ...

    async def query_markets(self) -> pl.DataFrame:
        """Return market metadata."""
        ...

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary query (SQL for CH, Polars expression for in-memory)."""
        ...
```

**Backend implementations (this phase):**
- `PolarsBackend` — Scans parquet files, returns LazyFrame.collect(). For backtest + replay.
- `ClickHouseBackend` — Runs SQL, returns as Polars DataFrame. For paper-dev/prod + live.
  - ClickHouse materialized views are infrastructure (DDL). Providers read from them — they don't
    manage the views. `query_custom("SELECT ... FROM mv_skilled_traders WHERE ...")`.

### Example: SkilledTradersProvider

```python
class SkilledTradersProvider:
    """Computes and maintains the skilled traders set.

    compute(): Queries trader PnL from backend, selects top N by win rate.
    on_trade(): No-op (skilled set changes slowly, updated via refresh()).
    refresh(): Re-queries and atomically swaps the skilled set.
    """

    name = "skilled_traders"

    def __init__(self, min_pnl: float = 100.0, min_trades: int = 50) -> None:
        self._min_pnl = min_pnl
        self._min_trades = min_trades
        self._skilled: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        df = await backend.query_trades()
        # ... PnL computation, filtering ...
        self._skilled = frozenset(skilled_addresses)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        pass  # Skilled set refreshed periodically, not per-trade

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)  # Atomic swap via assignment

    def get_features(self) -> dict[str, Any]:
        return {"skilled_traders": self._skilled}
```

## LiveRunner

The runner subscribes to `trades.raw` via FastStream/Kafka, deserializes `NormalizedTrade`,
and dispatches through providers then strategies.

```python
class LiveRunner:
    """Kafka consumer that runs strategies against live trade feed.

    Lifecycle:
    1. Load configs + create strategies via registry
    2. Initialize feature providers (compute())
    3. Subscribe to trades.raw
    4. For each trade: providers.on_trade() → strategies.on_trade()
    5. Submit intents to ExecutionGateway
    6. Periodic: timer callbacks, provider refresh()
    """

    def __init__(
        self,
        strategies: list[tuple[Strategy, StrategyConfig]],
        providers: list[FeatureProvider],
        gateway: ExecutionGateway,
        ctx: StrategyContext,
        backend: FeatureBackend,
        *,
        timer_interval_s: float = 60.0,
        refresh_interval_s: float = 900.0,
        hot_path_warn_ms: float = 5.0,
    ) -> None: ...

    async def start(self, broker: KafkaBroker) -> None:
        """Initialize providers and start consuming."""
        for p in self.providers:
            await p.compute(self.backend)
        # Register Kafka subscriber, start timer loop, start refresh loop

    async def _handle_trade(self, trade: NormalizedTrade) -> None:
        """Hot path: dispatch trade to providers then strategies."""
        # 1. Providers first (update features)
        for provider in self.providers:
            t0 = time.monotonic()
            await provider.on_trade(trade)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                log.warning("provider.slow_on_trade",
                            provider=provider.name, elapsed_ms=elapsed_ms)

        # 2. Inject features into context
        for provider in self.providers:
            self.ctx.update_features(provider.get_features())

        # 3. Strategies (read updated context)
        for strategy, config in self.strategies:
            t0 = time.monotonic()
            intents = await strategy.on_trade(trade, self.ctx)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > self.hot_path_warn_ms:
                log.warning("strategy.slow_on_trade",
                            strategy=strategy.name, elapsed_ms=elapsed_ms)
            if intents:
                for intent in intents:
                    await self.gateway.submit(intent)

    async def _timer_loop(self) -> None:
        """Periodic timer callbacks for strategies."""
        while True:
            await asyncio.sleep(self.timer_interval_s)
            now = time.time()
            for strategy, config in self.strategies:
                intents = await strategy.on_timer(now, self.ctx)
                if intents:
                    for intent in intents:
                        await self.gateway.submit(intent)

    async def _refresh_loop(self) -> None:
        """Periodic provider refresh (expensive recomputation)."""
        while True:
            await asyncio.sleep(self.refresh_interval_s)
            for provider in self.providers:
                await provider.refresh(self.backend)
                self.ctx.update_features(provider.get_features())
```

### Hot Path Enforcement

All `on_trade()` calls (providers and strategies) are timed. If any exceeds `hot_path_warn_ms`
(default 5ms), a structlog warning fires with the offending component name and elapsed time.
This catches accidental I/O or heavy computation in the hot path during development.

### InMemoryContext Extension

`InMemoryContext` gains an `update_features(features: dict[str, Any])` method that merges
provider features into a `_features` dict. Strategies access features via the context:

```python
class InMemoryContext:
    # Existing methods unchanged...

    def update_features(self, features: dict[str, Any]) -> None:
        """Merge provider features into context."""
        self._features.update(features)

    async def get_feature(self, key: str) -> Any:
        """Return a feature value by key, or None."""
        return self._features.get(key)
```

`StrategyContext` protocol gains:
```python
async def get_feature(self, key: str) -> Any: ...
```

## PaperExecutor

Extends `SimulatedExecutor` with better price simulation. Uses context to check orderbook
availability; falls back to mid-price estimation.

```python
class PaperExecutor:
    """Paper-trading executor that simulates fills with market-aware pricing.

    In paper-dev (InMemoryContext), orderbook is typically unavailable — fills
    at intent's max_price or a configurable default (same as SimulatedExecutor).

    In paper-prod (RedisContext), checks orderbook for realistic fill price
    and logs slippage estimation.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        fee_pct: float = 0.02,
        default_price: float = 0.50,
    ) -> None: ...

    async def execute(self, intent: TradeIntent) -> Fill:
        ob = await self.ctx.get_orderbook(intent.condition_id)
        if ob is not None:
            price = ob.best_ask if intent.side == "BUY" else ob.best_bid
        elif intent.max_price is not None:
            price = intent.max_price
        else:
            price = self.default_price
        # ... fee calc, return Fill
```

For paper-dev mode (InMemoryContext, no Redis), `get_orderbook()` returns `None` and the executor
falls back to `max_price` — functionally identical to `SimulatedExecutor` but with the orderbook
check ready for paper-prod promotion.

## CLI Entry Point

New Typer command `pm-strategy` with a `run` subcommand:

```bash
# Start strategies in paper-dev mode
uv run pm-strategy run --config strategies.toml

# Override mode for all strategies
uv run pm-strategy run --config strategies.toml --mode paper_dev

# Run specific strategy only
uv run pm-strategy run --config strategies.toml --only consensus_copy
```

The CLI:
1. Loads TOML config
2. Creates strategies via registry
3. Creates providers based on config dependencies
4. Assembles LiveRunner with appropriate context + executor for the mode
5. Connects to Kafka broker and starts consuming

Registered as `[project.scripts]` in `pyproject.toml`:
```toml
pm-strategy = "polymarket_pipeline.cli.strategy:app"
```

## TOML Config Extensions

```toml
# Feature providers
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
params.min_pnl = 100.0
params.min_trades = 50

# Strategy with provider dependencies
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["skilled_traders"]  # declares dependency on provider

[strategy.consensus_copy.params]
min_traders = 5
agreement_pct = 0.80
direction = "NO"
mvf_band = "pure_taker"
delay_s = 60
```

Config loader extended with `ProviderConfig`:
```python
@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool
    refresh_interval_s: float
    params: dict[str, Any]
```

`StrategyConfig` gains `features: list[str]` field listing required provider names.
LiveRunner validates that all declared feature dependencies are satisfied at startup.

## Integration with Existing Pipeline

LiveRunner does NOT modify `app.py`. It runs as a separate process that connects to the same
Kafka broker:

```
app.py (ingestors) ──> Kafka trades.raw ──> LiveRunner (strategies)
```

This keeps concerns separated:
- `app.py` owns data ingestion (RTDS, Alchemy, subgraph recovery, quality checks)
- `pm-strategy run` owns strategy execution (providers, strategies, paper trading)

Both connect to `PM_REDPANDA_URL`. The strategy CLI reuses `Settings` for Kafka/CH connection
params and adds strategy-specific settings.

## Module Structure (new files)

```
src/polymarket_pipeline/
├── strategies/
│   ├── protocol.py              # Add FeatureProvider, FeatureBackend protocols
│   │                            # Add get_feature() to StrategyContext
│   ├── context/
│   │   └── memory.py            # Add update_features(), get_feature()
│   ├── execution/
│   │   └── paper.py             # NEW: PaperExecutor
│   ├── features/
│   │   ├── __init__.py          # NEW
│   │   ├── backend_polars.py    # NEW: PolarsBackend
│   │   └── backend_clickhouse.py # NEW: ClickHouseBackend
│   ├── runners/
│   │   └── live.py              # NEW: LiveRunner
│   └── config.py                # Extend with ProviderConfig, features field
│
├── strategies_impl/
│   └── consensus_copy/
│       └── providers.py         # NEW: SkilledTradersProvider
│
├── cli/
│   └── strategy.py              # NEW: pm-strategy CLI
```

## What's Deferred from This Phase

### Deferred: RedisContext + paper-prod mode

**What:** StrategyContext backed by Redis for shared state with orderbook data.
**Why deferred:** Paper-dev uses InMemoryContext, which is sufficient for validating strategy logic.
**When:** When we want realistic orderbook-based fill simulation.

### Deferred: market.updates Kafka topic consumption

**What:** LiveRunner subscribing to `market.updates` for orderbook/price ticks.
**Why deferred:** Paper-dev mode doesn't need live orderbook (PaperExecutor falls back to max_price).
**When:** When RedisContext + CLOB WS ingestor are built.

### Deferred: Provider registry (dynamic discovery)

**What:** Auto-discovery of FeatureProvider classes (like strategy registry).
**Why deferred:** Only one provider (SkilledTradersProvider) exists. Manual wiring is fine.
**When:** When we have 3+ providers and want TOML-driven instantiation.

### Deferred: BacktestRunner integration with providers

**What:** BacktestRunner calling providers (compute + on_trade) during replay.
**Why deferred:** Backtest currently uses hardcoded skilled_traders in ConsensusCopyConfig. Works fine.
**When:** When we want provider-computed features in backtest (after provider abstraction is proven live).

### Deferred: ClickHouse materialized view DDL management

**What:** Scripts/migrations to create/update CH materialized views that providers read from.
**Why deferred:** SkilledTradersProvider can query raw trades directly. MVs are an optimization.
**When:** When provider queries become too slow on raw tables.
