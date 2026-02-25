# Pool Refresh Automation via CLOB WS Resolution Events

**Date:** 2026-02-25
**Status:** Approved

## Problem

The live pipeline has zero automatic metadata refresh. `pm-sync` is manual only. When markets resolve, the skilled trader pool is stale until the next manual sync or restart. The consistency filter needs fresh resolution data to accurately compute trader PnL and update the pool.

## Solution: Approach C — Extend CLOBOrderbookIngestor + Dedicated Kafka Consumer

### Data Flow

```
CLOB WS (market channel)
  ├── price_change → orderbooks.raw (existing)
  ├── market_resolved ──┐
  └── new_market ───────┼──→ markets.events (NEW topic)
                        │
FastStream subscriber ◄─┘
  ├── UPDATE markets SET resolution_value=... (PG)
  ├── debounce 5s
  └── signal LiveRunner.request_refresh()
                        │
LiveRunner._refresh_loop ◄──┘
  └── provider.refresh(backend)
       └── SkilledTradersProvider
            ├── backend.query_trader_pnl()  (CH derived views)
            ├── backend.query_mvf()         (CH derived views)
            └── filter_consistent_traders() → update ctx
```

### Component Changes

#### 1. CLOBOrderbookIngestor (extend)

- Add `custom_feature_enabled: true` to WS subscription payload (enables `market_resolved` and `new_market` events).
- Route `market_resolved` / `new_market` messages to `markets.events` Kafka topic.
- Continue routing `price_change` to `orderbooks.raw` as before.

#### 2. New Kafka Topic: `markets.events`

- Schema: `{"type": "market_resolved"|"new_market", "condition_id": str, "payload": dict, "timestamp": float}`
- Low throughput (~100/day for resolutions), no partitioning needed.

#### 3. New FastStream Subscriber in `app.py`

- `@broker.subscriber("markets.events", group_id="market-events")`
- On `market_resolved`: UPDATE PG `markets` table with resolution data, then signal refresh.
- On `new_market`: INSERT into PG `markets` table.
- **Debounce**: Collect events for 5s before triggering a single refresh (handles election-night bursts).

#### 4. LiveRunner — `request_refresh()`

- New method that sets an `asyncio.Event` to trigger an immediate iteration of `_refresh_loop`.
- Hot path (`_handle_trade`) is never blocked — reads from a snapshot that gets swapped atomically.

#### 5. SkilledTradersProvider.refresh(backend)

- Re-query CH derived views via `backend.query_trader_pnl()` and `backend.query_mvf()`.
- Also query `markets_resolved` view for fresh resolution data.
- Run `filter_consistent_traders()` with the fresh DataFrames.
- Swap `_skilled_traders` atomically.

#### 6. Settings

- Add `PM_MARKETS_EVENTS_TOPIC` (default: `"markets.events"`).

### Error Handling

- **CLOB WS disconnects**: Existing reconnect logic in `CLOBOrderbookIngestor`. Timer fallback (15min `_refresh_loop`) catches missed events.
- **Duplicate resolutions**: PG upsert is idempotent. Pool refresh is idempotent. No dedup needed.
- **Stale CH views**: `FINAL` keyword forces merge at query time. `markets_resolved` VIEW reads from PG engine (always fresh after PG update).
- **Hot path safety**: `request_refresh()` sets a flag; actual refresh runs async. `_handle_trade` reads from snapshot, never blocked.
- **Backpressure**: 5s debounce batches rapid resolution events into a single refresh.

### Testing Strategy

1. Unit test: CLOBOrderbookIngestor message routing — mock WS, verify `market_resolved` published to `markets.events`
2. Unit test: debounce logic — multiple rapid events produce single refresh
3. Unit test: `SkilledTradersProvider.refresh(backend)` — mock CH backend, verify queries + filter
4. Integration test: `LiveRunner.request_refresh()` — verify out-of-cycle refresh updates context
5. Existing tests unaffected (pure function + provider tests don't depend on Kafka)

### Out of Scope

- Historical backfill of missed resolutions (timer fallback is sufficient).
- `new_market` metadata enrichment beyond basic PG insert (can add later).
- Changing the vectorized/backtest path (remains parquet-based for now).
