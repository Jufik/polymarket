# Data Model Reference

Complete reference for all data structures, database tables, and Kafka topics in the Polymarket pipeline.

---

## Canonical Trade Model

Every source normalizes into `NormalizedTrade` (defined in `src/polymarket_pipeline/models.py`):

| Field | Type | Notes |
|---|---|---|
| `trade_id` | `str` | Deterministic SHA-256 hash (dedup key). Prefixed: `chain:`, `ws:`, `pending:`, or `mempool:`. |
| `condition_id` | `str` | Market identifier (hex string from Polymarket). |
| `asset_id` | `str` | Token identifier (CLOB API token ID). |
| `side` | `Side` | BUY or SELL. BUY = buying YES/NO tokens. |
| `price` | `Decimal` | In [0, 1], quantized to 4 decimal places via Pydantic validator. |
| `size` | `Decimal` | Token quantity (must be > 0). |
| `amount_usd` | `Decimal` | USDC notional (`price * size`). Uses **1e6 scaling** (6 decimals, NOT 1e18). |
| `fee_usd` | `Decimal` | Fee in USDC. Zero for most markets. |
| `maker` | `str \| None` | Maker address. Nullable for WS sources (RTDS provides `proxyWallet`). |
| `taker` | `str \| None` | Taker address. Nullable for all WS sources. |
| `timestamp` | `datetime` | UTC. Serialized as `'YYYY-MM-DD HH:MM:SS.fff'` for ClickHouse JSONEachRow. |
| `source` | `Source` | Enum: `goldsky_sink`, `goldsky_subgraph`, `websocket`, `rtds`, `alchemy`, `mempool`, `pending_block`. |
| `tx_hash` | `str \| None` | Transaction hash (hex with `0x` prefix). On-chain sources only. |
| `order_hash` | `str \| None` | Order hash. On-chain sources only. |
| `block_number` | `int \| None` | Polygon block number. On-chain sources only. |
| `is_backfill` | `bool` | True for historical Parquet data. |
| `version` | `int` | Priority: 0=mempool/pending, 1=off-chain (WS), 2=on-chain. Higher wins in ClickHouse dedup. |
| `published_at` | `float` | Unix epoch seconds, set by ingestor via `time.time()`. |

**Pydantic Config**: `frozen=True` (immutable after creation).

### Source Enum

| Value | Origin | Version | Identity |
|---|---|---|---|
| `goldsky_sink` | Parquet backfill | 2 | maker + taker |
| `goldsky_subgraph` | GraphQL recovery | 2 | maker + taker |
| `websocket` | CLOB WS `last_trade_price` | 1 | none |
| `rtds` | RTDS WS `trades` | 1 | proxyWallet (maker) |
| `alchemy` | Polygon RPC `eth_subscribe` | 2 | maker + taker |
| `mempool` | Rust devp2p sidecar | 0 | maker + taker |
| `pending_block` | RPC `eth_getBlockByNumber("pending")` | 0 | maker + taker |

---

## Trade ID Generation

Defined in `src/polymarket_pipeline/trade_id.py`. All IDs are deterministic SHA-256 hashes truncated to 16 hex characters.

| Function | Input | Output | Used By |
|---|---|---|---|
| `make_trade_id_chain(tx_hash, order_hash)` | `"{tx}:{order}"` | `chain:abcdef0123456789` | Sink Parquet, Subgraph, RPC |
| `make_trade_ids_chain_batch(tx_hashes, order_hashes)` | Vectorized batch | Same format | Parquet fast loader |
| `make_trade_id_ws(asset_id, ts_ms, price, size)` | `"{asset}:{ts}:{price}:{size}"` | `ws:abcdef0123456789` | RTDS, Market WS |
| `make_trade_id_pending(tx_hash, index)` | `"{tx}:{index}"` | `pending:abcdef0123456789` | Pending Block |

**Cross-source dedup**: Sink and Subgraph produce identical `chain:` IDs for the same on-chain trade. RTDS and Market WS produce identical `ws:` IDs for the same off-chain trade. Pending block IDs do **NOT** match on-chain IDs (different scheme — they exist on `pending.signal` topic, not `trades.raw`).

---

## Metadata Models

### Event

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | Gamma API event ID (primary key). |
| `slug` | `str` | URL slug. |
| `title` | `str` | Event title. |
| `category` | `str` | Category (e.g., "Politics", "Crypto"). |
| `neg_risk` | `bool` | True for negative-risk events (multi-outcome markets). |
| `active`, `closed`, `archived` | `bool` | Lifecycle flags. |
| `liquidity`, `volume` | `float` | Current liquidity and total volume. |
| `start_date`, `end_date`, `created_at`, `updated_at` | `datetime \| None` | Timestamps. |

### Market

| Field | Type | Notes |
|---|---|---|
| `condition_id` | `str` | Market identifier (primary key). |
| `event_id` | `int \| None` | FK to Event. |
| `question` | `str` | Market question text. |
| `slug` | `str` | URL slug. |
| `category` | `str` | Market category. |
| `token_yes` | `str` | CLOB token ID for YES side (`token_index=0`). |
| `token_no` | `str` | CLOB token ID for NO side (`token_index=1`). |
| `neg_risk` | `bool` | True if `marketType != "normal"`. |
| `status` | `MarketStatus` | ACTIVE, CLOSED, RESOLVED, UNKNOWN. |
| `resolution_value` | `int` | 1=resolved, 0=unresolved, -1=voided. |
| `winner_outcome` | `str` | "Yes" or "No" (from CLOB `tokens[].winner`). |
| `created_at`, `closed_at`, `resolved_at`, `updated_at` | `datetime \| None` | Timestamps. |

**Gotcha**: Gamma API uses `closedTime` for both close and resolution timestamps. There is no separate `resolvedTime` field. The `resolved_at` is set to `closed_at` when `resolution_value == 1`.

**Gotcha**: Gamma's `resolved` field is broken for many markets. Always use CLOB API `tokens[].winner` as the ground truth for resolution.

### Tag

| Field | Type |
|---|---|
| `id` | `int` |
| `label` | `str` |
| `slug` | `str` |

### TokenMarketEntry

| Field | Type | Notes |
|---|---|---|
| `asset_id` | `str` | CLOB token ID. |
| `condition_id` | `str` | FK to Market. |
| `outcome` | `str` | "YES" or "NO". |
| `winner` | `bool` | Set from CLOB `tokens[].winner`. |

**Convention**: `token_index=0` = affirmative/"YES" side. CLOB API ordering matches Gamma's `token_yes`.

---

## PostgreSQL Schema

All columns use **TEXT** (not VARCHAR) to avoid truncation with real Gamma API data (471K+ markets).

### Tables

| Table | Primary Key | Purpose |
|---|---|---|
| `events` | `id` | Event metadata from Gamma API. |
| `markets` | `condition_id` | Market metadata (event_id FK to events). |
| `tags` | `id` | Category tags. |
| `event_tags` | `(event_id, tag_id)` | Many-to-many junction. |
| `token_market_map` | `asset_id` | Token → market mapping (condition_id FK to markets). |
| `positions` | `condition_id` | Live position tracking (execution layer). |
| `fills` | `id` (serial), unique `intent_id` | Fill records (deduped by intent_id). |
| `strategy_intents` | `id` (serial) | Strategy intent log with JSONB metadata. |
| `strategy_pool` | `(strategy, address)` | Current trader pool per strategy. |
| `recovery_jobs` | `id` (serial) | Subgraph recovery job tracking (cursor persistence). |

### FK Order (for upserts)

Upserts must follow this order to satisfy foreign key constraints:

```
events → tags → event_tags → markets → token_market_map
```

---

## ClickHouse Schema

Defined in `src/polymarket_pipeline/live/schema.py`. Applied idempotently by `apply_schema()`.

### Core Tables

**`trades_raw`** — Primary trade storage

```sql
ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
PARTITION BY toYYYYMM(timestamp)
```

Bloom filter indexes on `maker`, `taker`, `trade_id`, `tx_hash`. Dedup: highest `_version` survives.

**`trades`** — Deduplicated view

```sql
SELECT * FROM trades_raw FINAL
```

**`orderbook_snapshots`** — CLOB WS price data

```sql
ENGINE = ReplacingMergeTree(timestamp)
ORDER BY (condition_id, timestamp)
PARTITION BY toYYYYMMDD(timestamp)
TTL timestamp + INTERVAL 7 DAY
```

### Kafka Engine Tables

| Table | Topic | Consumers | Format |
|---|---|---|---|
| `trades_kafka` | `trades.raw` | 4 | JSONEachRow |
| `orderbook_kafka` | `orderbooks.raw` | 2 | JSONEachRow |

Each has a materialized view that transforms and inserts into the target ReplacingMergeTree table.

### Derived Feature Tables (Chained Materialized Views)

```
trades_raw
    ├── trader_volumes (SummingMergeTree)         # per-trader maker_vol + taker_vol
    │       via trader_volumes_maker_mv, trader_volumes_taker_mv
    │
    ├── trader_trade_agg (SummingMergeTree)        # per (trader, condition_id, asset_id)
    │   │   Metrics: net_tokens, net_usd, total_fees, volume, trade_count, price_x_vol
    │   │   SimpleAggregateFunction: first_trade (min), last_trade (max)
    │   │       via trader_trade_agg_maker_mv, trader_trade_agg_taker_mv
    │   │
    │   └── trader_market_positions (SummingMergeTree)   # per (trader, condition_id)
    │           Metrics: net_yes, net_no, volume, trade_count, yes_px_vol
    │           Derived: wavg_yes = yes_px_vol / volume (query-time)
    │               via trader_market_positions_mv (INNER JOIN token_market_map)
    │
    └── (PostgreSQL engine tables for JOINs)
            markets, token_market_map → markets_resolved VIEW
            trader_market_positions + markets_resolved → trader_positions_resolved VIEW
```

**`trader_positions_resolved`** — VIEW classifying positions:

| position | Condition |
|---|---|
| `YES` | `net_yes > 0.01 AND net_no <= 0.01` |
| `NO` | `net_no > 0.01 AND net_yes <= 0.01` |
| `HEDGED` | `net_yes > 0.01 AND net_no > 0.01` |
| `CLOSED` | both <= 0.01 |

**Gotcha**: ClickHouse v24.8 does not support `FROM table FINAL AS alias`. Use `FROM (SELECT * FROM table FINAL) alias` instead.

---

## Kafka / Redpanda Topics

| Topic | Producers | Consumers | Schema | Purpose |
|---|---|---|---|---|
| `trades.raw` | RPC, RTDS, Subgraph | ClickHouse (Kafka engine), Strategy CLI | NormalizedTrade JSON | Main trade stream |
| `pending.signal` | PendingBlock | Strategy CLI (early signals) | NormalizedTrade JSON (version=0) | Pre-confirmation signals. NOT persisted to CH. |
| `orderbooks.raw` | CLOBOrderbook | ClickHouse (Kafka engine), Strategy CLI | `{condition_id, asset_id, best_bid, best_ask, timestamp}` | Price snapshots |
| `markets.events` | CLOBOrderbook (firehose), RPC (resolution) | MarketEventsConsumer, Strategy CLI | `{type, condition_id, payload, timestamp}` | Resolution + new market events |
| `pipeline.status` | All ingestors (heartbeat, every 10s) | QualityChecker | `{source, event, trade_count, drops_queue_full, ts, ...}` | Health monitoring |
| `strategy.intents` | Strategy CLI | (logged only) | TradeIntent + Fill JSON | Intent/fill audit trail |

### Topic: `markets.events` Message Types

| type | Trigger | Payload |
|---|---|---|
| `market_resolved` | RPC QuestionResolved event, CLOB WS firehose | `{condition_id, winner, settled_price}` |
| `new_market` | CLOB WS firehose | `{question, market, tokens}` |
| `caught_up` | Subgraph recovery completion | `{total_published}` |

---

## Constants

Defined in `src/polymarket_pipeline/constants.py`:

| Constant | Value | Purpose |
|---|---|---|
| `EXCHANGE_ADDRS` | `{CTF_EXCHANGE, NEGRISK_EXCHANGE}` | Filter taker-focused duplicates (~40.5% of trades). |
| `FEE_MODULE_ADDRS` | 3 addresses | Polymarket fee module contracts (pending block tx filter). |
| `USDC_SCALE` | `Decimal("1e6")` | USDC uses 6 decimals. All raw amounts divided by this. |
| `CTF_EXCHANGE` | `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e` | Polymarket CTF Exchange. |
| `NEGRISK_EXCHANGE` | `0xc5d563a36ae78145c45a50134d48a1215220f80a` | NegRisk CTF Exchange. |

---

## Polymarket Fee Structure

Most markets have **zero trading fees**. Fees only apply to:
- Crypto 5/15-minute markets (max 1.56%)
- NCAAB, Serie A

Formula: `fee = C * feeRate * (p * (1-p))^exponent`

The `fee_rate_bps` field in CLOB WS `last_trade_price` events is `"0"` for fee-free markets.

---

## Market Base Rates

Across ~390K resolved markets:
- **38.1% YES-won**, 61.9% NO-won
- NO-only direction dominates in copy strategies
- YES-only consensus is anti-predictive (18.5% hit rate vs 38.1% base)
