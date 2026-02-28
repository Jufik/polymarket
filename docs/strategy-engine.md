# Strategy Engine

Complete documentation of the protocol-based strategy framework, execution layer, and runtime behavior.

---

## Design Principles

1. **Protocol-first**: All interfaces are `@runtime_checkable` structural protocols (duck typing, no inheritance).
2. **Frozen types**: All configs and domain objects are immutable (`frozen=True`).
3. **Async throughout**: All I/O is non-blocking.
4. **Backend-agnostic**: Strategies never know if data comes from Polars or ClickHouse.
5. **Event-driven + vectorized**: Same strategy can implement both paths; parity gate validates agreement.

---

## Protocols

All defined in `src/polymarket_pipeline/strategies/protocol.py`.

### StrategyContext (Read-Only View)

Provided to all strategy callbacks. Strategies can read but never mutate.

```python
async def get_position(condition_id: str) -> Position | None
async def get_market(condition_id: str) -> MarketInfo | None
async def get_orderbook(condition_id: str) -> OrderbookSnapshot | None
async def get_price(condition_id: str, outcome: str) -> float | None
async def now() -> float
async def get_features(key: str) -> Any    # bridge to FeatureProvider outputs
```

### Strategy (Event-Driven)

Used for live trading, replay, and paper trading.

```python
name: str
async def on_trade(trade: NormalizedTrade, ctx: StrategyContext) -> list[TradeIntent] | None
async def on_market_update(update: Any, ctx: StrategyContext) -> list[TradeIntent] | None
async def on_timer(now: float, ctx: StrategyContext) -> list[TradeIntent] | None
```

- Returns `None` = no action; returns `[TradeIntent, ...]` = execute those intents
- **Hot path**: `on_trade()` must stay < 5ms to avoid Kafka lag
- `on_timer()` runs periodically (default 60s) for cleanup/rebalance

### VectorizedStrategy (Batch)

Used for fast backtesting and parameter sweeps.

```python
def compute_signals(trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame
```

Returns DataFrame with at minimum a `signal` column. Pure Polars lazy evaluation.

### Executor

```python
async def execute(intent: TradeIntent) -> Fill
```

Three implementations: `PaperExecutor` (simulated), `LiveExecutor` (real), `SimulatedExecutor` (backtest).

### FeatureBackend (Data Access)

```python
async def query_trades(condition_ids: list[str] | None = None) -> pl.DataFrame
async def query_markets() -> pl.DataFrame
async def query_custom(query: str, **params: Any) -> pl.DataFrame
```

Two implementations:
- `PolarsBackend`: In-memory DataFrames, zero I/O. For backtest/replay.
- `ClickHouseBackend`: HTTP queries via httpx. Has pre-built SQL builders for common patterns.

### FeatureProvider (Independent Computation)

```python
name: str
async def compute(backend: FeatureBackend) -> None    # startup (expensive)
async def on_trade(trade: NormalizedTrade) -> None     # hot path, O(1)
async def refresh(backend: FeatureBackend) -> None     # periodic (expensive)
def get_features() -> dict[str, Any]                   # current values
```

**Lifecycle**:
1. `compute()` — once at startup, can run expensive queries
2. `on_trade()` — per event, must stay O(1), in-memory only
3. `refresh()` — every N seconds (default 900s), can re-query backend
4. `get_features()` — injected into StrategyContext between events

---

## Types

All defined in `src/polymarket_pipeline/strategies/types.py`. All frozen (immutable).

### TradeIntent (Strategy → Executor)

```python
strategy: str           # strategy name
condition_id: str       # target market
side: "BUY" | "SELL"
outcome: "YES" | "NO"
size_usd: float         # notional size
urgency: "immediate" | "patient"
max_price: float | None # for limit orders
reason: str             # human-readable rationale
signal_time: float      # when signal was generated
asset_id: str | None    # optional (executor resolves from token_map if missing)
```

### Position (Context State)

```python
condition_id: str
strategy: str           # positions are PER-STRATEGY per-market
qty_yes: float = 0.0
qty_no: float = 0.0
avg_entry_yes: float = 0.0
avg_entry_no: float = 0.0
cost_basis: float = 0.0
realized_pnl: float = 0.0
```

**Note**: Different strategies can hold different positions in the same market.

### Fill (Executor → Runner)

```python
intent_id: str
strategy: str
condition_id: str
side: "BUY" | "SELL"
outcome: "YES" | "NO"
filled_price: float
filled_size_usd: float
fee_usd: float
status: FillStatus      # FILLED, PARTIAL, REJECTED
filled_at: float
error: str | None
```

### ExecutionMode

```python
VECTORIZED = "vectorized"   # batch backtest (VectorizedRunner)
REPLAY = "replay"           # trade-by-trade replay (BacktestRunner)
PAPER_DEV = "paper_dev"     # live feed, simulated fills, dev testing
PAPER_PROD = "paper_prod"   # live feed, simulated fills, prod validation
LIVE = "live"               # real money execution (LiveExecutor)
```

### OrderbookSnapshot (Context State)

```python
condition_id: str
best_bid: float
best_ask: float
bid_depth: float
ask_depth: float
timestamp: float

@property
def spread() -> float: return best_ask - best_bid
```

---

## Configuration (TOML)

Defined in `src/polymarket_pipeline/strategies/config.py`.

### StrategyConfig

```toml
[strategy.my_strategy]
enabled = true
mode = "paper_prod"
capital_usd = 1000          # total capital budget
max_position_usd = 100      # per-market position limit
max_open_positions = 5       # concurrent open positions
cooldown_s = 30              # minimum seconds between trades
features = ["pool_traders"]  # declares provider dependencies
subscribe_pending = false    # subscribe to pending.signal topic

[strategy.my_strategy.params]
custom_param = "value"       # forwarded to strategy __init__
```

### ProviderConfig

```toml
[provider.pool_traders]
enabled = true
refresh_interval_s = 900    # how often to call refresh()

[provider.pool_traders.params]
min_trades = 50              # forwarded to provider __init__
```

### Loading

```python
configs = load_strategy_configs(Path("config.toml"), enabled_only=True)
providers = load_provider_configs(Path("config.toml"), enabled_only=True)
```

The CLI validates that every entry in `features` has a matching provider configured.

---

## Runners

### LiveRunner (Kafka → Strategy → Execution)

**File**: `src/polymarket_pipeline/strategies/runners/live.py`

The primary runtime for paper and live trading.

#### Hot Path (`_handle_trade`)

```
Trade arrives from Kafka
    │
    ├── 1. Age filter: drop if trade.published_at > max_trade_age_s old (default 120s)
    ├── 2. Dedup: drop if trade_id seen in TTL window (default 600s)
    ├── 3. Providers: on_trade(trade)           # O(1) streaming update
    ├── 4. Features: merge provider outputs into context
    ├── 5. Context: set_time(), update market price
    ├── 6. Strategy: on_trade(trade, ctx)       # read context, emit TradeIntent
    ├── 7. Metadata: capture orderbook snapshot + strategy rationale
    ├── 8. Risk gate: capital, position, max_open, cooldown
    ├── 9. Gateway: submit(intent)              # quality gate → budget gate → execute
    ├── 10. Callback: intent_cb(record)         # PostgreSQL + Kafka logging
    └── 11. Position: apply_fill_to_position()  # update context
```

**Hot path timing enforcement**: warns if `on_trade()` or `provider.on_trade()` exceeds `hot_path_warn_ms` (default 5ms).

#### Background Loops

```python
async def _timer_loop(self)    # every timer_interval_s (default 60s)
    # calls strategy.on_timer(now, ctx) for each strategy
    # used for stats, rebalancing, cleanup

async def _refresh_loop(self)  # every refresh_interval_s (default 900s) OR on-demand
    # calls provider.refresh(backend) for each provider
    # wakes early via _refresh_event.set() (from MarketEventsConsumer)
    # atomic context swap: old features → new features

async def start_background_loops(self)  # spawns both as asyncio.Tasks
async def stop(self)                    # cancels tasks + drain
```

#### Pool Refresh on Resolution

```
CLOB WS market_resolved → markets.events topic
    → MarketEventsConsumer.handle()
        → runner.settle_resolved_market(condition_id, winner)  # zero position, apply PnL
        → 5s debounce timer
        → runner.request_refresh()  # sets _refresh_event
            → _refresh_loop wakes early
                → provider.refresh(backend)  # re-query CH
                → atomic context swap
```

#### Position Settlement

```python
def settle_resolved_market(self, condition_id: str, winner: str) -> None
```

When a market resolves:
- YES tokens pay $1 if `winner == "YES"`, else $0
- NO tokens pay $1 if `winner == "NO"`, else $0
- Position qty zeroed, realized PnL updated
- Frees budget + max_open slots

#### Reset (SIGUSR1)

```python
def reset(self) -> None
```

Clears positions, orderbooks, budgets, dedup cache, market volumes, counters. Does NOT re-run providers.

### BacktestRunner (Event-Driven Replay)

**File**: `src/polymarket_pipeline/strategies/runners/backtest.py`

Replays `NormalizedTrade` list through a Strategy with optional execution delay.

```python
async def run(self, trades: list[NormalizedTrade]) -> BacktestResult
```

Supports delayed execution: intents queued with `signal_time + delay_s` ready time, drained at each subsequent trade.

### VectorizedRunner (Batch DataFrame)

**File**: `src/polymarket_pipeline/strategies/runners/vectorized.py`

Thin wrapper for `VectorizedStrategy.compute_signals()`.

### ParityRunner

**File**: `src/polymarket_pipeline/strategies/runners/parity.py`

Validates that vectorized and event-driven paths produce the same signals for the same strategy.

### CombinedBacktestRunner

**File**: `src/polymarket_pipeline/strategies/runners/combined.py`

Runs multiple vectorized strategies over shared data with per-strategy budget caps.

---

## Execution Layer

### ExecutionGateway

**File**: `src/polymarket_pipeline/strategies/execution/gateway.py`

Routes intents through a pipeline of gates:

```
Intent arrives
    │
    ├── 1. Validation: reject if size_usd <= 0
    ├── 2. Quality gate: reject if pipeline state not CHECKING or READY
    ├── 3. Budget gate (serialized via asyncio.Lock):
    │       check _strategy_spent[strategy] + intent.size_usd <= budget
    │       reserve amount
    ├── 4. Intent logging: append to JSONL (disk error never crashes)
    ├── 5. Delay: sleep if configured
    ├── 6. Execute: try executor, catch and log any exception
    ├── 7. Budget reconciliation (serialized via asyncio.Lock):
    │       FILLED → adjust to actual fill size
    │       REJECTED/PARTIAL → refund reservation
    └── 8. Fill logging: append to fills.jsonl
```

**Budget serialization**: Check-and-reserve are atomic (inside lock) to prevent concurrent intents from overrunning cap.

### PaperExecutor

**File**: `src/polymarket_pipeline/strategies/execution/paper.py`

Simulates fills using real orderbook prices.

**Price resolution order** (outcome-specific):
1. WS orderbook snapshot by `asset_id` (fastest, in context)
2. CLOB REST API `GET /price?token_id=X&side=Y` (authoritative fallback, 5s cache)
3. Reject if neither available

**Key behavior**: Never falls back to wrong outcome token. If intent is for NO, it resolves the NO `asset_id` and uses that price.

Fee: `fee_pct * min(price, 1-price) * size_usd` (accounts for price curvature).

### LiveExecutor

**File**: `src/polymarket_pipeline/execution/` (separate from strategy framework)

Real execution via CLOB API with position limit checks.

**Critical**: Requires confirmed fill price. If CLOB returns success but `fill_price is None`, the order is resting on book (not filled) → reject to prevent position avg_entry corruption.

### ClobClient

**File**: `src/polymarket_pipeline/execution/clob_client.py`

Async httpx client for Polymarket CLOB REST API.

```python
async def submit_order(...) -> OrderResult
async def cancel_order(order_id: str) -> bool
async def get_open_orders(condition_id: str | None) -> list[OpenOrder]
async def get_balances() -> dict[str, float]
async def get_orderbook(token_id: str) -> ClobOrderbook | None  # 5s TTL cache
```

**Retry logic**:
- Transient errors: exponential backoff (1s, 2s, 4s), max 3 retries
- HTTP 429: respect `Retry-After` header (max 10s)
- HTTP 4xx: do NOT retry (business logic errors)

**Orderbook cache**: 5s TTL, race-safe via `asyncio.Lock`.

### PositionTracker

**File**: `src/polymarket_pipeline/execution/position_tracker.py`

PostgreSQL-backed position tracking.

**Atomic fill recording**:
1. Insert fill with `ON CONFLICT (intent_id) DO NOTHING` (dedup)
2. Recompute position from **ALL** fills for that condition_id (not memory + delta)
3. Upsert position
4. Update in-memory cache after transaction commits
5. All under `asyncio.Lock`

### Panic Close

**File**: `src/polymarket_pipeline/execution/panic.py`

```python
async def panic_close_all(clob, tracker, timeout_s=60.0) -> list[OrderResult]
```

1. Cancel all open orders (parallel, 15s per-call timeout)
2. Close all positions (parallel, 15s per-call timeout)
3. Global timeout: 60s

---

## Risk Gates

Applied in `LiveRunner._handle_trade()` before gateway submission:

| Gate | Check | Rejection |
|---|---|---|
| **Capital** | `sum(pos.cost_basis) + intent.size_usd <= config.capital_usd` | "capital_exceeded" |
| **Position** | `pos.cost_basis + intent.size_usd <= config.max_position_usd` | "position_limit" |
| **Max Open** | `open_count + 1 <= config.max_open_positions` (new market only) | "max_positions" |
| **Cooldown** | `now - last_trade_times[strategy] >= config.cooldown_s` | "cooldown" |

---

## InMemoryContext

**File**: `src/polymarket_pipeline/strategies/context/memory.py`

Dict-backed implementation of `StrategyContext`. All reads are async (protocol), all writes are sync (runner-facing).

```python
# Async reads (StrategyContext protocol)
async def get_position(condition_id) -> Position | None
async def get_market(condition_id) -> MarketInfo | None
async def get_orderbook(condition_id) -> OrderbookSnapshot | None
async def get_price(condition_id, outcome) -> float | None
async def now() -> float
async def get_features(key) -> Any

# Sync writes (runner only)
def set_position(condition_id, position)
def set_market(condition_id, market)
def set_time(t)
def set_orderbook(condition_id, ob, asset_id=None)  # stores by both condition_id AND asset_id
def get_orderbook_by_asset(asset_id) -> OrderbookSnapshot | None  # for PaperExecutor
def update_features(features: dict)
def get_all_positions() -> dict[str, Position]
```

---

## Position Math

Defined in `src/polymarket_pipeline/strategies/runners/helpers.py`:

### BUY (adding to position)

```python
added_qty = fill.filled_size_usd / fill.filled_price
new_qty = old_qty + added_qty
new_avg = (old_avg * old_qty + fill_price * added_qty) / new_qty
cost_basis += fill.filled_size_usd + fill.fee_usd
```

### SELL (reducing position)

```python
sold_qty = fill.filled_size_usd / fill.filled_price
pnl_delta = (fill_price - old_avg) * sold_qty - fee_usd
realized_pnl += pnl_delta
cost_basis = max(cost_basis - fill.filled_size_usd, 0)
```

---

## Strategy CLI

**Command**: `pm-strategy run --config <toml> [--only name] [--log-dir path] [--verbose]`

### Assembly Flow

1. Load strategy configs + provider configs from TOML
2. Validate feature dependencies (each strategy's features must have a matching provider)
3. Create providers from registry + config params
4. Create strategies from registry (factory pattern)
5. Assemble: InMemoryContext → ClobClient → PaperExecutor → ExecutionGateway → LiveRunner

### Kafka Subscriptions

| Topic | Consumer Group | Handler |
|---|---|---|
| `trades.raw` | `strategy-{config_stem}` | `handle_trade()` |
| `markets.events` | `strategy-{config_stem}-events` | `MarketEventsConsumer.handle()` |
| `orderbooks.raw` | `strategy-{config_stem}` | `handle_orderbook()` |
| `pending.signal` | `strategy-{config_stem}` | Optional (if `subscribe_pending=true`) |

### Intent Persistence

Intents are logged to both Kafka (`strategy.intents`) and PostgreSQL (`strategy_intents` table):

```sql
strategy, condition_id, side, outcome, size_usd, urgency, max_price, reason,
signal_time, asset_id, disposition, rejection_reason,
filled_price, filled_size_usd, fee_usd,
metadata (JSONB: orderbook snapshot + strategy rationale),
captured_at
```

### Pool Publish

After provider refresh, the current trader pool is atomically updated in PostgreSQL `strategy_pool` table.

### Registries

Currently empty (all strategy implementations deleted in cleanup). Ready for new implementations:

```python
_STRATEGY_FACTORIES: dict[str, Callable] = {}
_PROVIDER_REGISTRY: dict[str, type] = {}
```

Register a new strategy:
1. Implement `Strategy` protocol (and optionally `VectorizedStrategy`)
2. Add factory function to `_STRATEGY_FACTORIES`
3. Implement `FeatureProvider` protocol for any data needs
4. Add provider class to `_PROVIDER_REGISTRY`
5. Create TOML config with strategy + provider sections

---

## Implementing a New Strategy

### Step 1: Create Strategy Class

```python
# src/polymarket_pipeline/strategies_impl/my_strategy/strategy.py

class MyStrategy:
    name = "my_strategy"

    def __init__(self, *, my_param: str = "default"):
        self.my_param = my_param

    async def on_trade(self, trade: NormalizedTrade, ctx: StrategyContext) -> list[TradeIntent] | None:
        features = await ctx.get_features("my_provider")
        if features and some_condition(trade, features):
            return [TradeIntent(
                strategy=self.name,
                condition_id=trade.condition_id,
                side="BUY",
                outcome="YES",
                size_usd=10.0,
                urgency="patient",
                max_price=0.50,
                reason="Signal detected",
                signal_time=time.time(),
            )]
        return None

    async def on_market_update(self, update, ctx): return None
    async def on_timer(self, now, ctx): return None
```

### Step 2: Create Feature Provider

```python
# src/polymarket_pipeline/strategies_impl/my_strategy/providers.py

class MyProvider:
    name = "my_provider"

    async def compute(self, backend: FeatureBackend) -> None:
        df = await backend.query_trades()
        self._cache = compute_expensive_features(df)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        self._cache = update_incrementally(self._cache, trade)  # O(1)

    async def refresh(self, backend: FeatureBackend) -> None:
        df = await backend.query_trades()
        self._cache = compute_expensive_features(df)

    def get_features(self) -> dict[str, Any]:
        return {"my_provider": self._cache}
```

### Step 3: Register in CLI

```python
# src/polymarket_pipeline/cli/strategy.py

_STRATEGY_FACTORIES["my_strategy"] = lambda **params: MyStrategy(**params)
_PROVIDER_REGISTRY["my_provider"] = MyProvider
```

### Step 4: TOML Config

```toml
[provider.my_provider]
enabled = true
refresh_interval_s = 900

[strategy.my_strategy]
enabled = true
mode = "paper_dev"
capital_usd = 500
max_position_usd = 50
max_open_positions = 10
cooldown_s = 60
features = ["my_provider"]

[strategy.my_strategy.params]
my_param = "custom_value"
```
