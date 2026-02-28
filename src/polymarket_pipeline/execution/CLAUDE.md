# execution/ — Trade Execution Layer

Real-money trade execution via Polymarket CLOB REST API with position tracking and emergency controls.

## ClobClient (clob_client.py)

Async httpx client for the Polymarket CLOB REST API.

**Auth**: `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE` headers.
**Settings**: `PM_CLOB_API_URL`, `PM_CLOB_API_KEY`, `PM_CLOB_API_SECRET`, `PM_CLOB_API_PASSPHRASE`

| Method | Endpoint | Returns | Notes |
|--------|----------|---------|-------|
| `submit_order()` | `POST /order` | `OrderResult` | Retry logic: 3x backoff for transient errors, respect 429 Retry-After |
| `cancel_order(id)` | `DELETE /order/{id}` | `bool` | |
| `get_open_orders(cid?)` | `GET /orders` | `list[OpenOrder]` | |
| `get_balances()` | `GET /balances` | `dict[asset_id, balance]` | |
| `get_orderbook(token_id)` | `GET /book?token_id=X` | `ClobOrderbook \| None` | **5s TTL cache**, race-safe via asyncio.Lock |

### Retry Logic

- **Transient errors** (timeout, connect, remote protocol): exponential backoff (1s, 2s, 4s), max 3 retries
- **HTTP 429**: respect `Retry-After` header (max 10s wait)
- **HTTP 4xx**: do NOT retry (business logic errors)

### Orderbook Cache

`get_orderbook()` uses a 5-second TTL cache:
- Fast path: check cache without lock
- Slow path: acquire `_ob_lock`, double-check, fetch from API
- Returns `ClobOrderbook(best_bid, best_ask, spread, fetched_at)`

## PositionTracker (position_tracker.py)

PostgreSQL-backed position tracking with **atomic recomputation from fills**.

### Record Fill Atomicity

```
1. INSERT fill with ON CONFLICT (intent_id) DO NOTHING  (dedup)
2. Recompute position from ALL fills for that condition_id  (not memory + delta)
3. UPSERT positions table
4. Update in-memory cache AFTER transaction commits
5. All under asyncio.Lock
```

**Key**: Position is recomputed from full fill history inside a single transaction. This is the single source of truth — never derives from memory + delta.

### Position Math (from SQL)

- `net_tokens = sum(BUY tokens) - sum(SELL tokens)`
- `size = max(net_tokens, 0)` (no shorts)
- `avg_entry = total_bought_usd / total_bought_tokens`
- `cost_basis = max(total_bought_usd - total_sold_usd, 0)`
- `realized_pnl = total_sold_usd - (avg_entry * total_sold_tokens) - total_fees`

### PostgreSQL Tables

```sql
positions (condition_id PK, asset_id, side, size, avg_entry, cost_basis, last_price, realized_pnl, updated_at)
fills (id SERIAL, intent_id UNIQUE, strategy, condition_id, asset_id, side, outcome, price, size_usd, fee_usd, filled_at, created_at)
```

## Panic (panic.py)

Emergency position close. CLI: `pm-panic`

```python
async def panic_close_all(clob, tracker, timeout_s=60.0) -> list[OrderResult]
```

1. Get all open orders
2. Cancel in parallel (`asyncio.gather`, 15s per-call timeout)
3. Close all positions in parallel (15s per-call timeout)
4. Global timeout: 60s

**Parallel execution** minimizes time window for new fills during close.

## LiveExecutor (used by LiveRunner)

Defined in strategy framework but uses execution layer components:
- Checks position limits (MTM when possible, 30s orderbook staleness guard)
- Resolves asset_id from intent or token_market_map
- Submits via ClobClient
- **Requires confirmed fill price** — if CLOB returns success but no fill price, order is resting (not filled) → reject

## Hardening (completed 2026-02-27)

- PositionTracker race fix (asyncio.Lock around record_fill)
- Fill dedup via intent_id UNIQUE constraint
- Gateway exception safety (executor errors never crash pipeline)
- CLOB retry (3x exponential backoff)
- Parallel panic close
- Realized PnL tracking
- MTM staleness guard (30s orderbook age)
- Budget gate race fix (atomic check-and-reserve)
- Panic timeout (60s global + 15s per-call)
- 429 rate limit handling (Retry-After)
- Intent validation (size_usd <= 0 rejected)
