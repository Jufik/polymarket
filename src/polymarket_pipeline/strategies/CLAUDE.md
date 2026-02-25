# strategies/ — Strategy Framework

Protocol-based framework for event-driven and vectorized trading strategies.

## Protocols (protocol.py)

All `@runtime_checkable`. No base classes — pure structural typing.

| Protocol | Key Methods | Purpose |
|----------|-------------|---------|
| `Strategy` | `on_trade()`, `on_market_update()`, `on_timer()` | Event-driven (live/replay) |
| `VectorizedStrategy` | `compute_signals(trades, markets)` | Batch (backtest) |
| `FeatureProvider` | `compute()`, `on_trade()`, `refresh()`, `get_features()` | Independent feature units |
| `FeatureBackend` | `query_trades()`, `query_markets()`, `query_custom()` | Data access abstraction |
| `Executor` | `execute(intent)` | Intent-to-fill bridge |
| `StrategyContext` | `get_position()`, `get_market()`, `get_orderbook()`, `get_features()` | Read-only world view |

## Types (types.py)

All frozen dataclasses.

| Type | Fields | Flow |
|------|--------|------|
| `TradeIntent` | strategy, condition_id, side, outcome, size_usd, max_price, asset_id | Strategy → Gateway |
| `Fill` | intent_id, filled_price, filled_size_usd, fee_usd, status, error | Executor → Runner |
| `Position` | qty_yes, qty_no, avg_entry_yes/no, cost_basis, realized_pnl | Context state |
| `OrderbookSnapshot` | best_bid, best_ask, bid_depth, ask_depth, spread | Context state |
| `ExecutionMode` | vectorized, replay, paper_dev, paper_prod, live | TOML config enum |
| `FillStatus` | filled, partial, rejected | Fill outcome |

## Config (config.py)

TOML-based. Two loaders:

```python
load_strategy_configs(path, enabled_only=False) -> dict[str, StrategyConfig]
load_provider_configs(path, enabled_only=False) -> dict[str, ProviderConfig]
```

TOML structure: `[strategy.<name>]` with nested `[strategy.<name>.params]`; `[provider.<name>]` with nested `[provider.<name>.params]`.

## Execution (execution/)

- **ExecutionGateway** — routes intents: quality gate → budget gate → executor → spending tracker
  - `strategy_budgets: dict[str, float]` — cumulative per-strategy USD limits
  - Strategies without budget entry are uncapped
  - Only tracks spending on FILLED status
- **PaperExecutor** — simulates fills using orderbook or `max_price` fallback

## Features (features/)

Two backends, same protocol:
- **PolarsBackend** — in-memory DataFrames for backtesting
- **ClickHouseBackend** — HTTP queries against CH for live mode
  - `consistency_pnl_query()` — JOINs trader_trade_agg + markets_resolved + token_market_map for YES-side breakdown
  - `query_mvf()`, `query_trader_pnl()`, `query_consistency_pnl()`, `query_resolved_markets()` — async convenience

## Runners (runners/)

- **LiveRunner** — event-driven: `_handle_trade()` dispatches to strategies, `_refresh_loop()` re-queries providers
  - `request_refresh()` — sets `asyncio.Event` for on-demand out-of-cycle refresh
  - `_refresh_loop` uses `asyncio.timeout()` wrapping event wait — wakes on timer OR explicit signal
- **BacktestRunner** — vectorized: runs `compute_signals()` on full DataFrames
- **helpers.py** — `check_risk_gate()`: 4 gates (capital, position limit, max open, cooldown)

## Adding a new strategy

1. Create `strategies_impl/<name>/` with `config.py`, `strategy.py`, `providers.py`
2. Strategy config: frozen dataclass with strategy-specific params
3. Strategy: implement `Strategy` protocol (`on_trade()` returns `list[TradeIntent] | None`)
4. Optional: implement `VectorizedStrategy` for backtesting
5. Provider: implement `FeatureProvider` protocol if strategy needs custom features
6. Register in `cli/strategy.py`: add factory to `_STRATEGY_FACTORIES`, provider to `_PROVIDER_REGISTRY`
7. Add TOML section to `configs/strategies_example.toml`
