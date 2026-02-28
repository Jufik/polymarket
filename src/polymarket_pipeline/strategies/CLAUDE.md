# strategies/ — Strategy Framework

Protocol-based, async, backend-agnostic framework for event-driven and vectorized trading strategies.

## Design Principles

1. **Protocol-first**: All interfaces are `@runtime_checkable`. No base classes — pure structural typing.
2. **Frozen types**: All configs and domain objects are immutable (`frozen=True`).
3. **Async reads, sync writes**: StrategyContext reads are async (protocol); runner writes are sync.
4. **Backend-agnostic**: Strategies never know if data comes from Polars or ClickHouse.

## Protocols (protocol.py)

| Protocol | Key Methods | Hot Path? | Purpose |
|----------|-------------|-----------|---------|
| `StrategyContext` | `get_position()`, `get_market()`, `get_orderbook()`, `get_features()`, `now()` | Yes | Read-only world view |
| `Strategy` | `on_trade()`, `on_market_update()`, `on_timer()` → `list[TradeIntent] \| None` | Yes (<5ms) | Event-driven (live/replay) |
| `VectorizedStrategy` | `compute_signals(trades, markets)` → `pl.DataFrame` | No | Batch (backtest) |
| `FeatureProvider` | `compute()` (startup), `on_trade()` (O(1)), `refresh()` (periodic), `get_features()` | on_trade is hot | Independent feature computation |
| `FeatureBackend` | `query_trades()`, `query_markets()`, `query_custom()` | No | Data access abstraction |
| `Executor` | `execute(intent)` → `Fill` | No | Intent-to-fill bridge |

## Types (types.py)

All frozen dataclasses:

| Type | Key Fields | Flow |
|------|------------|------|
| `TradeIntent` | strategy, condition_id, side, outcome, size_usd, urgency, max_price, reason, asset_id | Strategy → Gateway |
| `Fill` | intent_id, filled_price, filled_size_usd, fee_usd, status, error | Executor → Runner |
| `Position` | condition_id, strategy, qty_yes, qty_no, avg_entry_yes/no, cost_basis, realized_pnl | Context state |
| `OrderbookSnapshot` | best_bid, best_ask, bid_depth, ask_depth, timestamp, `spread` (property) | Context state |
| `MarketInfo` | condition_id, question, active, yes_price, no_price | Context state |
| `ExecutionMode` | vectorized, replay, paper_dev, paper_prod, live | TOML config enum |
| `FillStatus` | filled, partial, rejected | Fill outcome |

**Note**: Position is **per-strategy per-market**. Different strategies can hold different positions in the same market.

## Config (config.py)

TOML-based:

```toml
[strategy.my_strategy]
enabled = true
mode = "paper_prod"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 5
cooldown_s = 30
features = ["my_provider"]    # declares provider dependency
subscribe_pending = false     # subscribe to pending.signal topic

[strategy.my_strategy.params]
custom_param = "value"        # forwarded to strategy __init__

[provider.my_provider]
enabled = true
refresh_interval_s = 900

[provider.my_provider.params]
min_trades = 50               # forwarded to provider __init__
```

```python
configs = load_strategy_configs(path, enabled_only=True)
providers = load_provider_configs(path, enabled_only=True)
```

CLI validates that every `features` entry has a matching provider configured.

## Execution (execution/)

### ExecutionGateway (gateway.py)

Pipeline: validation → quality gate → budget gate (locked) → executor → budget reconciliation (locked) → logging.

- `strategy_budgets: dict[str, float]` — cumulative per-strategy USD limits
- Budget check-and-reserve are **atomic** (asyncio.Lock) to prevent concurrent overrun
- Only counts FILLED status toward spending
- Intent/fill logging to JSONL (disk errors never crash)

### PaperExecutor (paper.py)

Price resolution order (outcome-specific):
1. WS orderbook by `asset_id` (fastest, in context)
2. CLOB REST API `GET /price?token_id=X&side=Y` (5s cache)
3. Reject if neither available

**Never falls back to wrong outcome token.** Fee: `fee_pct * min(price, 1-price) * size_usd`.

### SimulatedExecutor (simulated.py)

Instant fills at `intent.max_price` or `default_price`. Zero friction. For quick vectorized backtest.

### RealisticFillSimulator (realistic.py)

Fills at `max_price` (same as SimulatedExecutor) but adds calibrated **slippage cost** to `fee_usd`:
- `slippage_cost = (half_spread + impact) * size_usd`
- `half_spread` = per-market from `calibrate_spreads()` or fallback config
- `impact` = `size_usd / estimated_liquidity * impact_scale`
- Optional rejection probability (liquidity miss)
- Deterministic via `rng_seed` for reproducible backtests

Use `calibrate.py` to estimate spreads from historical trades:
```python
from polymarket_pipeline.strategies.execution.calibrate import calibrate_spreads, calibrate_volumes
spreads = calibrate_spreads(trades, method="median_abs_change")  # or "roll"
volumes = calibrate_volumes(trades)
```

## Features (features/)

| Backend | I/O | SQL | Use Case |
|---------|-----|-----|----------|
| `PolarsBackend` | None (in-memory) | No | Backtest, replay |
| `ClickHouseBackend` | HTTP (httpx, 120s timeout) | Yes | Paper-prod, live |

ClickHouseBackend includes SQL builders: `mvf_query()`, `trader_pnl_query()`, `consistency_pnl_query()`, `resolved_markets_query()`.

**Gotcha**: `FROM (SELECT * FROM table FINAL) alias` NOT `FROM table FINAL AS alias`.

## Runners (runners/)

| Runner | Mode | Input | Executor |
|--------|------|-------|----------|
| `LiveRunner` | PAPER_DEV/PROD/LIVE | Kafka stream | Paper/LiveExecutor |
| `BacktestRunner` | REPLAY | Trade list | Simulated or Realistic |
| `VectorizedRunner` | VECTORIZED | LazyFrame | N/A (signals) |
| `CombinedBacktestRunner` | VECTORIZED | LazyFrame | N/A (multiple strategies) |
| `ParityRunner` | Both | Trade list + LazyFrame | Both (validates agreement) |

### LiveRunner Hot Path

```
Trade → age filter (120s) → dedup (600s TTL) → providers.on_trade()
  → features sync → strategy.on_trade() → risk gate → gateway.submit()
  → fill → position update → intent callback (PG + Kafka)
```

Background loops: timer (60s for `on_timer()`), refresh (900s for `provider.refresh()`, or on-demand via `request_refresh()`).

### Risk Gates (helpers.py)

1. Capital: `sum(cost_basis) + size_usd <= capital_usd`
2. Position: `pos.cost_basis + size_usd <= max_position_usd`
3. Max open: `open_count + 1 <= max_open_positions` (new market only)
4. Cooldown: `now - last_trade >= cooldown_s`

### Position Math (helpers.py)

- BUY: weighted average entry, increment qty, add to cost_basis
- SELL: `pnl_delta = (fill_price - avg_entry) * sold_qty - fee`, update realized_pnl

## Ledger (ledger/)

Unified outcome tracking across backtest and paper modes.

| Module | Purpose |
|--------|---------|
| `types.py` | `LedgerRecord` — frozen dataclass: signal→fill→resolution→PnL |
| `base.py` | `LedgerBackend` protocol, `make_ledger_record()`, `compute_pnl()` |
| `parquet.py` | `ParquetLedger` — in-memory buffer → Polars → parquet |
| `analytics.py` | `LedgerSummary` + `compute_summary()` |

**LedgerRecord fields**: record_id, signal_time, strategy, condition_id, side, outcome, intent_size_usd, max_price, reason, asset_id, fill_status, fill_price, fill_size_usd, fill_fee_usd, fill_latency_ms, resolution, pnl_gross, pnl_net, hold_duration_s, resolved_at.

**PnL model** (compute_pnl):
- BUY + outcome matches winner: `pnl_gross = (1.0 - fill_price) * qty_tokens`
- BUY + outcome loses: `pnl_gross = -fill_size_usd`
- SELL is the inverse
- `pnl_net = pnl_gross - fill_fee_usd`

**Analytics** (compute_summary):
- hit_rate, avg_edge, annualized Sharpe, max drawdown (absolute $), profit_factor
- Only resolved records contribute to performance metrics

**BacktestRunner integration**: pass `ledger=ParquetLedger(path)` to `BacktestRunner.__init__`. Records auto-appended after each fill. Call `ledger.enrich_resolutions(...)` post-run.

## Promotion (promotion.py)

Gate checker enforcing ExecutionMode transitions:

```
vectorized/replay → paper_dev → paper_prod → live
```

```python
checker = PromotionChecker(ledger, PromotionThresholds(min_trades=1000, min_sharpe=0.5))
report = await checker.check("my_strat", ExecutionMode.VECTORIZED, ExecutionMode.PAPER_DEV)
```

| Transition | Gates |
|------------|-------|
| → paper_dev | min_trades, positive PnL, Sharpe threshold |
| → paper_prod | min_paper_fills, min_runtime_hours |
| → live | min_live_days, positive PnL, max_drawdown, manual_signoff (--force) |

Thresholds configurable via `[promotion]` TOML section. CLI: `pm-strategy promote <name> --to <mode> --config <toml>`.

## Context (context/memory.py)

`InMemoryContext` — dict-backed StrategyContext. Stores orderbooks by both `condition_id` (backward compat) and `asset_id` (outcome-specific for PaperExecutor).

## Adding a New Strategy

### Research Phase (research/)

1. Create `research/strategies/my_strat.py` — implement `Strategy` protocol
2. Use `research/harness.py` to backtest:
   ```python
   from research.harness import load_compact_trades, run_backtest, print_summary
   from research.strategies.my_strat import MyStrategy
   from polymarket_pipeline.strategies.execution.realistic import FillModelConfig

   trades = load_compact_trades(max_files=10)
   config = StrategyConfig(enabled=True, mode=ExecutionMode.REPLAY,
       capital_usd=1000, max_position_usd=100, max_open_positions=20, cooldown_s=0)
   result, summary = asyncio.run(run_backtest(
       MyStrategy(), trades, config, fill_config=FillModelConfig(),
   ))
   print_summary(summary, "my_strat")
   ```
3. Check promotion readiness: `pm-strategy promote my_strat --to paper_dev --config configs/my_strat.toml`

### Production Phase (strategies_impl/)

4. Move to `strategies_impl/<name>/strategy.py`
5. Register in `cli/strategy.py`:
   - `_STRATEGY_FACTORIES["name"] = lambda **params: MyStrategy(**params)`
   - `_PROVIDER_REGISTRY["name"] = MyProvider`
6. Create TOML config under `configs/` with `[strategy.*]` and `[provider.*]` sections
7. Run: `pm-strategy run --config configs/my_strat.toml`

**Import boundary**: `research/` imports from `polymarket_pipeline`, never the reverse.
