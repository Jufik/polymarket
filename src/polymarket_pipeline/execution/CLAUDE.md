# execution/ — Trade Execution Layer

## ClobClient (clob_client.py)

Async httpx client for the Polymarket CLOB REST API.

**Auth:** `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE` headers.
**Settings:** `PM_CLOB_API_URL`, `PM_CLOB_API_KEY`, `PM_CLOB_API_SECRET`, `PM_CLOB_API_PASSPHRASE`

| Method | Endpoint | Returns |
|--------|----------|---------|
| `submit_order()` | `POST /order` | `OrderResult` (id, success, filled_price, error) |
| `cancel_order(id)` | `DELETE /order/{id}` | `bool` |
| `get_open_orders(cid?)` | `GET /orders` | `list[OpenOrder]` |
| `get_balances()` | `GET /balances` | `dict[asset_id, balance]` |

**Not yet implemented:** `get_orderbook()` — needed for price validation.

## Panic (panic.py)

Emergency position close: fetches all open orders → cancels → sells all balances at market.
CLI: `pm-panic`

## Position Tracker (position_tracker.py)

PostgreSQL-backed position tracking for live execution.
