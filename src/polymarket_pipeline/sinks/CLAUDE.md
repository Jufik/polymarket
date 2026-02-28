# sinks/ — Storage Backends

## ClickHouse Sink (clickhouse.py)

Synchronous insert operations via `clickhouse_connect`.

| Method | Purpose |
|--------|---------|
| `insert_trades(trades)` | Batch insert NormalizedTrade list → `trades_raw` |
| `insert_arrow(table)` | Native PyArrow insert (no pandas conversion) |
| `insert_dataframe(df, batch_size)` | Pandas DataFrame batch insert |
| `query(sql, params)` | Execute query → list[dict] |
| `execute(sql)` | Execute statement (no return) |
| `ping()` | SELECT 1 health check |

Supports context manager (`__enter__`/`__exit__`).

### Column Mapping

Decimal fields → float, enum fields → `.value` string. Columns: trade_id, condition_id, asset_id, side, price, size, amount_usd, fee_usd, maker, taker, timestamp, source, tx_hash, order_hash, block_number, is_backfill, _version.

## PostgreSQL Sink (postgres.py)

Async metadata persistence via `asyncpg`.

### Upsert Methods (FK order matters!)

```
upsert_events()       → must be first (no FK deps)
upsert_tags()         → must be before event_tags
upsert_event_tags()   → depends on events + tags
upsert_markets()      → depends on events (event_id FK)
upsert_token_map()    → depends on markets (condition_id FK)
```

All use `INSERT ON CONFLICT DO UPDATE` with `_executemany_chunked(batch_size=5000)`.

### Other Methods

| Method | Purpose |
|--------|---------|
| `fetch_token_market_map()` | Load asset_id → (condition_id, outcome) dict |
| `create_recovery_job()` | Track Subgraph recovery cursor |
| `update_recovery_cursor()` | Checkpoint progress |
| `complete_recovery_job()` | Mark done |
| `get_active_recovery_job()` | Resume after crash |
| `query(sql, *args)` | Generic query |

### Key Gotchas

- **Use TEXT everywhere** — VARCHAR(n) causes truncation with real Gamma API data (471K+ markets)
- **FK order is critical** — upsert events before markets, markets before token_map
- `updated_at` set to `NOW()` on both INSERT and UPDATE
- Pool: min_size=1, max_size=5
