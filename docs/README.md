# Polymarket Unified Trade Pipeline

A data pipeline that ingests trade data from multiple Polymarket sources, normalizes it into a canonical schema, deduplicates across sources, and stores it in ClickHouse for analysis.

## Architecture

```
                        +------------------+
                        | Gamma Markets API|
                        |  /events         |
                        |  (embeds markets |
                        |   and tags)      |
                        +--------+---------+
                                 |
                     events, markets, tags,
                     token_market_map
                                 |
    +----------+    +------------v-----------+    +-------------------+
    | Goldsky  |    |                        |    |                   |
    | Parquet  +--->+                        +--->+   ClickHouse      |
    | (backfill)|   |   Normalizers          |    |   trades_raw      |
    +----------+    |                        |    |   (ReplacingMerge  |
                    |   - GoldskySink        |    |    Tree)           |
    +----------+    |   - RTDS               |    |   trades (view)   |
    | RTDS WS  +--->+   - MarketWS           |    |                   |
    | (live)   |    |                        |    |   events ─────────+──┐
    +----------+    |       |                |    |   markets ────────+──│
                    |       v                |    |   tags ────────────+──│
    +----------+    |   NormalizedTrade       |    |   event_tags ─────+──│
    | Market WS+--->+   (canonical model)    |    |   token_market_map+──│
    | (live)   |    |                        |    |   (PG engine)     |  │
    +----------+    +------------------------+    +-------------------+  │
                                                                        │
                                                  +-------------------+  │
                                                  |   PostgreSQL      |<─┘
                                                  |   - events         |
                                                  |   - markets        |
                                                  |   - tags           |
                                                  |   - event_tags     |
                                                  |   - token_map      |
                                                  |   - backfill_log   |
                                                  +-------------------+
```

### Data Flow

1. **Event & market metadata sync** -- the Gamma API `/events` endpoint is queried to fetch events with embedded markets and tags. Events, markets, tags, event-tag associations, and token-market mappings are persisted to PostgreSQL (the single source of truth). ClickHouse reads all metadata tables directly from PostgreSQL via the [PostgreSQL table engine](https://clickhouse.com/docs/engines/table-engines/integrations/postgresql). The token map (`asset_id` -> `(condition_id, outcome)`) is required by the Goldsky Sink and Market WS normalizers.

2. **Ingestion** -- data enters the pipeline from three sources (a fourth, Goldsky Subgraph, is reserved for future use):
   - **Goldsky Sink Parquet** (historical backfill): ~438M rows across 2,033 files, on-chain `OrderFilled` events.
   - **RTDS WebSocket** (live): real-time global trade feed with user metadata.
   - **Market WebSocket** (live): per-market trade feed with fee information.

3. **Normalization** -- each source has a dedicated normalizer that converts raw data into the canonical `NormalizedTrade` model.

4. **Deduplication** -- happens at two levels:
   - **Same-source**: the Sink normalizer drops taker-focused duplicates (~40.5% of rows) by filtering rows where the taker address matches known exchange contracts.
   - **Cross-source**: ClickHouse's `ReplacingMergeTree` engine uses `trade_id` as the dedup key. On-chain records (version 2) automatically overwrite off-chain records (version 1) for the same trade.

5. **Storage** -- trades land in `trades_raw` (ClickHouse). The `trades` view applies `FINAL` to return deduplicated results. Event/market/tag metadata is written to PostgreSQL (single source of truth) and exposed in ClickHouse via the PostgreSQL table engine — no duplicate writes needed. PostgreSQL also stores pipeline operational state.

## Data Sources

### Goldsky Sink Parquet (on-chain, backfill)

| Property | Value |
|----------|-------|
| Location | `order_filled/` directory |
| Files | ~2,033 files, ~100 MB each |
| Total rows | ~438M |
| Duplicates | ~40.5% (taker-focused) |
| Reader | **fastparquet only** (pyarrow fails on `DECIMAL(100,18)` precision > 76; DuckDB casts to lossy `DOUBLE`) |
| Amount scaling | 1e6 (USDC 6 decimals) |
| Hash fields | Raw bytes, converted to hex with `0x` prefix |

### RTDS WebSocket (off-chain, live)

| Property | Value |
|----------|-------|
| Endpoint | `wss://ws-live-data.polymarket.com` |
| Throughput | ~50 trades/sec globally |
| Heartbeat | `PING` / `PONG` text frames |
| Maker field | `proxyWallet` (user's on-chain proxy wallet) |
| Provides | `conditionId` directly (no lookup needed), `transactionHash` |
| Caveat | Prices may have float imprecision (e.g. `0.3996666666666667`), rounded to 2dp |

### Market WebSocket (off-chain, live)

| Property | Value |
|----------|-------|
| Endpoint | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Event types | `book` (snapshot), `price_change`, `last_trade_price` |
| Trades | Only `last_trade_price` events are normalized |
| Provides | `transaction_hash`, `fee_rate_bps` |

## Canonical Model

All sources normalize into `NormalizedTrade`:

```python
class NormalizedTrade(BaseModel):
    trade_id: str            # Deterministic SHA-256 hash (dedup key)
    condition_id: str        # Market identifier
    asset_id: str            # Token identifier
    side: Side               # BUY or SELL
    price: Decimal           # 0-1, rounded to 4dp
    size: Decimal            # Token quantity (> 0)
    amount_usd: Decimal      # Notional in USD
    fee_usd: Decimal         # Fee in USD
    maker: str | None        # Maker address (nullable for some WS sources)
    taker: str | None        # Taker address (nullable for WS sources)
    timestamp: datetime      # UTC
    source: Source           # goldsky_sink | goldsky_subgraph | websocket | rtds
    tx_hash: str | None      # Transaction hash (hex)
    order_hash: str | None   # Order hash (hex, on-chain only)
    block_number: int | None # Block number (on-chain only)
    is_backfill: bool        # True for historical data
    version: int             # 1 = off-chain, 2 = on-chain
```

### Metadata Models

Event and market metadata from the Gamma API `/events` endpoint is captured in structured Pydantic models:

```python
class Event(BaseModel):
    id: int                  # Gamma API event id
    slug: str                # URL slug
    title: str               # Event title
    category: str            # Category (e.g. "Politics")
    neg_risk: bool           # Negative risk event
    active: bool
    closed: bool
    archived: bool
    liquidity: float         # Current liquidity
    volume: float            # Total volume traded
    start_date: datetime | None
    end_date: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

class MarketStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"

class Market(BaseModel):
    condition_id: str        # Market identifier (from Gamma API conditionId)
    event_id: int | None     # FK to parent event
    question: str            # Market question text
    slug: str                # URL slug
    category: str            # Market category
    token_yes: str           # YES token asset_id
    token_no: str            # NO token asset_id
    neg_risk: bool           # True if marketType != "normal"
    status: MarketStatus     # Derived from active/closed/resolved booleans
    created_at: datetime | None
    closed_at: datetime | None
    resolved_at: datetime | None
    updated_at: datetime | None

class Tag(BaseModel):
    id: int                  # Gamma API tag id
    label: str               # Display label
    slug: str                # URL slug

class TokenMarketEntry(BaseModel):
    asset_id: str            # Token asset_id
    condition_id: str        # Parent market condition_id
    outcome: str             # "YES" or "NO"
```

Each model has a `from_gamma(raw)` classmethod that parses Gamma API JSON. Returns `None` if essential fields are missing. `Market.from_gamma()` accepts an optional `event_id` parameter to link markets to their parent event.

The `SyncResult` container aggregates all data from a single sync: `events`, `markets`, `token_entries`, `tags`, and `event_tag_pairs`.

## Trade ID Generation

Trade IDs are deterministic SHA-256 hashes truncated to 16 hex characters, prefixed by source type:

- **On-chain** (`chain:`): `sha256(tx_hash + ":" + order_hash)` -- Sink and Subgraph produce identical IDs for the same trade.
- **Off-chain** (`ws:`): `sha256(asset_id + ":" + timestamp_ms + ":" + price + ":" + size)` -- RTDS and Market WS produce identical IDs for the same trade.

This enables ClickHouse `ReplacingMergeTree` to deduplicate across sources: when an on-chain record (version 2) arrives for a trade that was already seen via WebSocket (version 1), the on-chain record wins.

## Project Structure

```
polymarket-pipeline/
  pyproject.toml
  docker-compose.yml
  docker/
    clickhouse/init.sql          # trades_raw table + PG engine for metadata
    postgres/init.sql            # events, markets, tags, event_tags, token_map
  src/polymarket_pipeline/
    models.py                    # NormalizedTrade, Event, Market, Tag, TokenMarketEntry
    trade_id.py                  # Deterministic trade_id generation
    market_sync.py               # Gamma API event/market/tag syncer (SyncResult)
    normalizers/
      sink.py                    # Goldsky Sink Parquet normalizer
      rtds.py                    # RTDS WebSocket normalizer
      market_ws.py               # Market WebSocket normalizer
    loaders/
      parquet.py                 # Parquet file loader (fastparquet)
    sinks/
      clickhouse.py              # ClickHouse sink (trades only)
      postgres.py                # PostgreSQL sink (events, markets, tags, token_map)
    consumers/
      rtds.py                    # RTDS WebSocket consumer (PING/PONG + callback)
    cli/
      backfill.py                # CLI runner for historical backfill
      market_sync.py             # CLI runner for event/market/tag sync (PG only, CH reads via PG engine)
  tests/
    fixtures/sink_rows.py        # Real sample Parquet rows
    test_models.py               # Model validation tests
    test_trade_id.py             # Trade ID determinism tests
    test_normalizer_sink.py      # Sink normalizer tests
    test_normalizer_rtds.py      # RTDS normalizer tests
    test_normalizer_market_ws.py # Market WS normalizer tests
    test_models_market.py        # Event, Market, Tag, TokenMarketEntry model tests
    test_sink_clickhouse.py      # ClickHouse trades integration tests
    test_sink_postgres.py        # PostgreSQL metadata integration tests
    test_loader_parquet.py       # Parquet loader integration tests
    test_market_sync.py          # Gamma API integration test
    test_e2e_backfill.py         # End-to-end backfill test
    test_consumer_rtds.py        # RTDS consumer tests
```

## Getting Started

### Prerequisites

- Python >= 3.11
- [uv](https://github.com/astral-sh/uv) (package manager)
- Docker (for ClickHouse + PostgreSQL)

### Setup

```bash
# Install all dependencies
uv sync --all-extras

# Start infrastructure
docker compose up -d

# Verify ClickHouse is ready
docker compose exec clickhouse clickhouse-client --query "SELECT 1"
```

### Run the Backfill

Load historical Goldsky Sink Parquet data into ClickHouse:

```bash
uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
```

Options:
- `--parquet-dir PATH` -- directory containing `.parquet` files (default: `order_filled/`)
- `--batch-size N` -- ClickHouse insert batch size (default: `10000`)
- `--no-market-sync` -- skip persisting event/market/tag metadata to PostgreSQL (only build in-memory token map)
- `--pg-dsn DSN` -- PostgreSQL connection string for market metadata (default: `postgresql://polymarket:polymarket@localhost:5432/polymarket`)

The backfill will:
1. Fetch events, markets, and tags from the Gamma API and persist to PostgreSQL (ClickHouse reads via PG engine)
2. Process each Parquet file sequentially
3. Drop taker-focused duplicates (~40.5%)
4. Insert normalized trades into ClickHouse in batches
5. Log progress per file

### Sync Event & Market Metadata

Standalone CLI to fetch events, markets, and tags from the Gamma API `/events` endpoint and persist to PostgreSQL. ClickHouse reads this data automatically via the PostgreSQL table engine.

```bash
# Sync all events + markets + tags
uv run python -m polymarket_pipeline.cli.market_sync

# Limit number of events fetched (for testing)
uv run python -m polymarket_pipeline.cli.market_sync --limit 500
```

Options:
- `--limit N` -- max events to fetch (default: `0` = all)
- `--pg-dsn DSN` -- PostgreSQL connection string (default: `postgresql://polymarket:polymarket@localhost:5432/polymarket`)

### Query Trades

```sql
-- Deduplicated trades (uses FINAL for ReplacingMergeTree dedup)
SELECT * FROM polymarket.trades
WHERE condition_id = '0x...'
ORDER BY timestamp DESC
LIMIT 100;

-- Raw table (may contain duplicates pre-merge)
SELECT count() FROM polymarket.trades_raw;

-- Trades by source
SELECT source, count() as trades
FROM polymarket.trades
GROUP BY source;
```

## Development

### Running Tests

```bash
# Unit tests only (fast, no Docker needed)
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py

# Integration tests (requires Docker + running ClickHouse/PostgreSQL)
uv run pytest tests/test_sink_clickhouse.py -x -q
uv run pytest tests/test_sink_postgres.py -x -q

# All tests
uv run pytest tests/ -x -q
```

### Linting and Type Checking

```bash
# Type checking (strict mode, Pydantic plugin)
uv run mypy --strict src/

# Linting
uv run ruff check src/ tests/

# Formatting
uv run ruff format src/ tests/
```

### ClickHouse Schema

**`trades_raw`** -- `ReplacingMergeTree(_version)`:
- **Engine**: `ReplacingMergeTree(_version)` -- keeps the row with the highest `_version` for each unique `(condition_id, timestamp, trade_id)`.
- **Partitioning**: `toYYYYMM(timestamp)` -- monthly partitions.
- **Indexes**: Bloom filter indexes on `maker`, `taker`, `trade_id`, and `tx_hash` for fast point lookups.
- **Dedup view**: `trades` view applies `FINAL` to return only deduplicated rows.

**`events`** -- `PostgreSQL` engine:
- Reads directly from PostgreSQL `events` table. Event metadata (title, category, liquidity, volume, etc.).

**`markets`** -- `PostgreSQL` engine:
- Reads directly from PostgreSQL `markets` table. Includes `event_id` FK to events.

**`tags`** -- `PostgreSQL` engine:
- Reads directly from PostgreSQL `tags` table. Category labels for events.

**`event_tags`** -- `PostgreSQL` engine:
- Reads directly from PostgreSQL `event_tags` table. Many-to-many event-tag associations.

**`token_market_map`** -- `PostgreSQL` engine:
- Reads directly from PostgreSQL `token_market_map` table. Maps each token to its `condition_id` and outcome (YES/NO).

### Duplicate Filtering

**Taker-focused duplicates** (~40.5% of Parquet rows): Polymarket's CTF Exchange emits two `OrderFilled` events per trade -- one from each party's perspective. The normalizer drops rows where the taker address matches known exchange contracts:

| Contract | Address |
|----------|---------|
| CTF Exchange | `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e` |
| NegRisk CTF Exchange | `0xc5d563a36ae78145c45a50134d48a1215220f80a` |

**Cross-source duplicates**: handled by `ReplacingMergeTree` -- on-chain records (version 2) overwrite off-chain WebSocket records (version 1) when they share the same `trade_id`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| ClickHouse host | `localhost` | ClickHouse HTTP interface host |
| ClickHouse port | `8123` | ClickHouse HTTP interface port |
| ClickHouse database | `polymarket` | Target database |
| PostgreSQL host | `localhost:5432` | PostgreSQL connection |
| PostgreSQL database | `polymarket` | Metadata database |
| Gamma API | `https://gamma-api.polymarket.com` | Market metadata API |
| RTDS WS | `wss://ws-live-data.polymarket.com` | Live trade feed |
| Market WS | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Per-market trade feed |
