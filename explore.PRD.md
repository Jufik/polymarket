# Polymarket Unified Data Pipeline — Product Requirements Document

**Version:** 2.0
**Date:** February 8, 2026
**Author:** Julien
**Status:** Draft → Review

---

## 1. Executive Summary

### 1.1 Vision

Build a unified, event-driven data pipeline that ingests Polymarket trade data from five heterogeneous sources, normalizes it into a single canonical model, deduplicates across sources, and delivers clean, fast-queryable data to ClickHouse — serving as the foundation for all downstream strategy research, backtesting, and live trading.

### 1.2 Why This Matters

Every strategy in the Polymarket research platform — skilled trader detection, weather market pricing, regime classification, BTC binary options, news arbitrage — depends on the same thing: clean, complete, consistent trade data with sub-second query performance. Without a reliable data foundation, strategy research produces unreliable results, and live trading operates on incomplete information.

### 1.3 Core Value Proposition

A user (the researcher/trader) can:

- Query any market's complete trade history in under 1 second
- See every trade attributed to the correct maker and taker wallet
- Trust that volume figures are accurate (no double-counting)
- Transition seamlessly from historical analysis to live monitoring
- Receive real-time trade events with maker identity for copy trading signals

### 1.4 Key Outcomes

| Outcome | Target |
|---------|--------|
| Historical coverage | All OrderFilled events from contract deployment to present |
| Query latency | `GROUP BY wallet` on single market < 1s |
| Data freshness (live) | < 1s from CLOB match to ClickHouse |
| Data freshness (enriched) | < 10 min from match to address-enriched record |
| Volume accuracy | 0% double-counting (validated hourly) |
| Storage footprint | < 25 GB compressed (down from ~150 GB raw) |
| Gap tolerance | Zero gaps > 30 seconds in steady state |

---

## 2. Problem Statement

### 2.1 The Current Situation

Polymarket exposes trade data through five different taps, each with different schemas, latencies, completeness guarantees, and failure modes. No single source provides everything needed: real-time speed AND wallet addresses AND complete history AND no double-counting.

A naive approach — picking one source — forces an unacceptable tradeoff:

- **Goldsky Sink alone**: Complete history with addresses, but minutes-to-hours stale. Trades are double-counted unless you know the Paradigm trap.
- **WebSocket alone**: Sub-second latency, but no wallet addresses and only covers the period you're connected.
- **Subgraph alone**: Addresses and cursor-resumable, but ~5 min behind and still has the double-counting trap.

### 2.2 The Double-Counting Trap

Every Polymarket trade emits `OrderFilled` events **twice** on-chain:

1. **Maker-focused event(s)**: one per maker in the match. `maker` = actual maker, `taker` = actual taker.
2. **Taker-focused event**: `maker` = the taker(!), `taker` = an Exchange contract address.

If you sum all `OrderFilled` events without filtering, you double-count every trade. This was documented by Paradigm in December 2025 and affects both the Goldsky Sink and Subgraph.

**Detection rule**: Drop any event where `taker` is one of:
- `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` (CTF Exchange)
- `0xC5d563A36AE78145C45a50134d48A1215220f80a` (NegRisk CTF Exchange)

### 2.3 What Users Need

Depending on the downstream strategy, users need different properties from the same underlying data:

| Strategy | Needs Speed | Needs Addresses | Needs History | Needs Orderbook |
|----------|:-----------:|:---------------:|:-------------:|:---------------:|
| Skilled trader detection | | ✅ | ✅ | |
| Copy trading (live) | ✅ | ✅ | ✅ (calibration) | |
| Weather market pricing | | | ✅ | ✅ |
| Market regime classification | | | ✅ | ✅ |
| BTC 15-min market making | ✅ | | | ✅ |
| News arbitrage | ✅ | | | |

The data pipeline must serve all of these from a single, consistent store.

---

## 3. Goals & Non-Goals

### 3.1 Goals

1. **Unified canonical model**: All five data taps normalize into one `NormalizedTrade` shape. Downstream consumers never think about source differences.
2. **Zero double-counting**: Taker-focused `OrderFilled` events are filtered at ingestion. Hourly validation confirms accuracy.
3. **Seamless backfill-to-live transition**: Historical data (Sink), catch-up data (Subgraph), and live data (WebSocket/RTDS) merge into the same ClickHouse table with automatic deduplication.
4. **Address enrichment**: Live WebSocket trades (no addresses) are enriched with maker/taker addresses from on-chain sources within ~10 minutes.
5. **Sub-second query performance**: ClickHouse schema optimized for the two primary access patterns — by-wallet and by-market — with bloom filters, materialized views, and tuned compression.
6. **Market lifecycle management**: Automatic discovery of new markets, subscription management for WebSocket feeds, and cleanup of resolved markets.
7. **Observability**: Per-source health metrics, gap detection, cross-source reconciliation, and alerting.

### 3.2 Non-Goals (for this phase)

- **Orderbook storage**: Live orderbook snapshots and L2 data are consumed by downstream strategies directly, not persisted in the pipeline. (Future: orderbook replay for backtest fidelity.)
- **Strategy execution**: The pipeline delivers data; it does not generate signals or execute trades.
- **Multi-chain support**: Polygon only. No Ethereum L1, no other L2s.
- **User-facing API**: The pipeline writes to ClickHouse and PostgreSQL. No REST/GraphQL API layer in this phase.
- **Real-time dashboards**: Monitoring is CLI/logs-based. Grafana dashboards are a fast-follow.

---

## 4. Architecture Overview

### 4.1 The Two Fundamental Paths

Every Polymarket trade traverses two systems in sequence:

```
STEP 1 (off-chain)                STEP 2 (on-chain)
─────────────────                 ─────────────────
Polymarket CLOB                   Polygon Blockchain
matching engine                   smart contract
matches orders            →       settles the trade

Latency: instant                  Latency: ~2s after match
Where: Polymarket servers         Where: Polygon L2 chain
```

The five data taps are windows into these two steps.

### 4.2 The Five Taps

```
               ┌─────────────────────────────────┐
               │   Polymarket CLOB Engine         │
               │   (matches orders off-chain)     │
               └──────┬──────┬──────┬─────────────┘
                      │      │      │
           ┌──────────┘      │      └──────────┐
           ▼                 ▼                  ▼
     ┌──────────┐    ┌────────────┐    ┌────────────┐
     │ Market WS│    │ User WS    │    │ RTDS       │
     │ (public) │    │ (auth'd)   │    │ activity   │
     │ TAP 1    │    │ TAP 2      │    │ TAP 3      │
     └──────────┘    └────────────┘    └────────────┘

               ┌─────────────────────────────────┐
               │   Polygon Blockchain             │
               │   (settles trades on-chain)      │
               └──────┬─────────────┬─────────────┘
                      │             │
           ┌──────────┘             └──────────┐
           ▼                                   ▼
     ┌──────────────┐                 ┌──────────────┐
     │ Goldsky Sink │                 │ Goldsky      │
     │ (S3 Parquet) │                 │ Subgraph     │
     │ TAP 4        │                 │ (GraphQL)    │
     │              │                 │ TAP 5        │
     └──────────────┘                 └──────────────┘
```

### 4.3 Tap Comparison

| Property | TAP 1: Market WS | TAP 2: User WS | TAP 3: RTDS Activity | TAP 4: Goldsky Sink | TAP 5: Goldsky Subgraph |
|---|---|---|---|---|---|
| **Source** | CLOB (off-chain) | CLOB (off-chain) | CLOB (off-chain) | Blockchain (on-chain) | Blockchain (on-chain) |
| **Latency** | <1s | <1s | <1s (hypothesis) | Minutes to hours | ~5 min |
| **Maker address** | ✗ | Only yours | ✓ `maker_address` | ✓ `maker` | ✓ `maker` |
| **Taker address** | ✗ | Only yours | ✗ (UUID `owner`) | ✓ `taker` | ✓ `taker` |
| **Price/Size** | ✓ | ✓ | ✓ | Must compute | ✓ (computed) |
| **Tx hash** | ✗ | ✗ | ✓ (unconfirmed) | ✓ | ✓ |
| **Auth required** | No | Yes | Unclear | No | No |
| **Coverage** | While connected | While connected | While connected | Full history | Full history |
| **Double-count risk** | None | None | None | ⚠️ YES | ⚠️ YES |
| **Pipeline role** | Live price/book feed | Execution monitoring | Copy trading signal | Bulk backfill | Catch-up + enrichment |

### 4.4 What Each Tap Is Used For

**TAP 1 (Market WS)**: Real-time price feed and orderbook snapshots. Primary source for live trade events (without addresses). Consumed by: regime classification, BTC market making, general price monitoring.

**TAP 2 (User WS)**: Execution feedback for our own orders. Not used in the data pipeline — used by the execution engine.

**TAP 3 (RTDS Activity)**: The critical unknown. If it provides `maker_address` globally without authentication at sub-second latency, it becomes the primary live source for copy trading. Status: **requires empirical validation** (test script created, pending execution).

**TAP 4 (Goldsky Sink)**: Bulk historical backfill. The complete record from contract deployment. Loaded once into ClickHouse, then superseded by Subgraph for incremental catch-up.

**TAP 5 (Goldsky Subgraph)**: Incremental catch-up from where the Sink left off. Runs continuously in steady state to enrich WebSocket trades with on-chain addresses (~5 min lag). The completeness guarantee.

---

## 5. Data Model

### 5.1 Canonical Trade Model

All five sources normalize into one shape. This is the single source of truth for all downstream consumers.

```python
@dataclass
class NormalizedTrade:
    # Identity (dedup key)
    trade_id: str              # Deterministic hash — see §5.2

    # Market context
    condition_id: str          # Market identifier (from Gamma API)
    asset_id: str              # Token ID (YES or NO outcome token)

    # Trade data
    side: Literal["BUY", "SELL"]   # From maker's perspective
    price: Decimal                  # 0.00–1.00
    size: Decimal                   # Outcome tokens
    amount_usd: Decimal             # USDC notional
    fee_usd: Decimal                # Fee in USDC

    # Participants (nullable for WS/RTDS sources)
    maker: str | None          # Maker wallet address
    taker: str | None          # Taker wallet address

    # Timing
    timestamp: datetime        # UTC, millisecond precision

    # Provenance
    source: Literal["goldsky_sink", "goldsky_subgraph", "websocket", "rtds"]
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None

    # Flags
    is_backfill: bool
```

### 5.2 Trade ID Generation (Dedup Key)

The most critical design decision. Different sources identify the same trade differently.

```python
def make_trade_id(source: str, **kwargs) -> str:
    if source in ("goldsky_sink", "goldsky_subgraph"):
        # On-chain: tx_hash + order_hash uniquely identifies a maker fill
        raw = f"{kwargs['tx_hash']}:{kwargs['order_hash']}"
        return f"chain:{sha256(raw)[:16]}"

    elif source in ("websocket", "rtds"):
        # Off-chain: no tx_hash. Use composite key.
        raw = f"{kwargs['asset_id']}:{kwargs['timestamp_ms']}:{kwargs['price']}:{kwargs['size']}"
        return f"ws:{sha256(raw)[:16]}"
```

**Implications**:
- Sink + Subgraph produce **identical** `trade_id` for the same fill → ClickHouse auto-deduplicates.
- WebSocket + RTDS produce a **different** `trade_id` than on-chain sources → cannot directly dedup. Resolved via the enrichment merge (§7.3).
- RTDS + Market WS for the same trade will produce the **same** `trade_id` (same composite key) → auto-dedup between off-chain sources.

### 5.3 Source-Specific Normalization

**On-chain (Sink/Subgraph)**:
1. Drop taker-focused events (taker is Exchange contract).
2. Determine side: `makerAssetId == "0"` → BUY, else SELL.
3. Extract amounts: USDC uses 1e6 scaling, tokens use 1e6 scaling. Compute `price = usdc_amount / token_amount`.
4. Map `asset_id → condition_id` via the token-market lookup table (§9.2).
5. Generate `trade_id` from `tx_hash:order_hash`.

**WebSocket (`last_trade_price`)**:
1. No double-counting (one event per trade).
2. Side, price, size provided directly.
3. No maker/taker addresses — fields are `None`.
4. Generate `trade_id` from `asset_id:timestamp_ms:price:size`.

**RTDS Activity** (if validated):
1. Provides `maker_address`, `price`, `size`, `side`, `match_time`, `transaction_hash`, `taker_order_id`.
2. Does NOT provide taker wallet (uses UUID `owner`).
3. Generate same-style `trade_id` as WebSocket for off-chain dedup.
4. `maker` field populated; `taker` remains `None`.

---

## 6. Pipeline Architecture

### 6.1 Three-Phase Handoff

```
TIME ────────────────────────────────────────────────────────────────►

Goldsky Sink (S3 Parquet)
[████████████████████████████]
                              ↑ Sink stops here (stale by hours)

Goldsky Subgraph (GraphQL)
                    [█████████████████████████████]
                    ↑ Start 10min BEFORE sink end  ↑ Within ~5min of now
                    (overlap for safety)

WebSocket / RTDS (Live)
                                          [█████████████████████►
                                          ↑ Connect BEFORE subgraph
                                          catches up

|---- Phase A: Bulk ----|-- Phase B: Catchup --|--- Phase C: Live ---|
```

All three phases write to the **same ClickHouse table**. Deduplication is automatic.

### 6.2 Phase A → B: Sink → Subgraph

```python
# Find last reliable timestamp in the sink
last_sink_ts = clickhouse.query("""
    SELECT max(timestamp) FROM trades_raw
    WHERE source = 'goldsky_sink'
""")

# Start subgraph 10 minutes before sink's edge (overlap window)
overlap_start = last_sink_ts - timedelta(minutes=10)

# Subgraph cursor-based catch-up from overlap_start forward
# Dedup via ReplacingMergeTree on trade_id (identical for same fill)
```

Both produce the same `trade_id` → ClickHouse silently deduplicates overlapping records.

### 6.3 Phase B → C: Subgraph → Live

```python
SUBGRAPH_LIVE_THRESHOLD = timedelta(minutes=7)

async def catch_up_loop():
    while True:
        gap = now() - get_latest_subgraph_timestamp()
        if gap < SUBGRAPH_LIVE_THRESHOLD:
            break  # Close enough — transition to live
        batch = fetch_subgraph_batch(cursor=last_cursor)
        write_to_clickhouse(normalize_batch(batch))
```

During transition, both WebSocket and Subgraph write concurrently. The overlap window handles the gap.

### 6.4 Phase C: Steady State

In production, **both** WebSocket and Subgraph run concurrently:

```
WebSocket/RTDS ──► trades_raw (immediate, _version=1, no addresses)
                         │
                         ▼
                   [ClickHouse FINAL]  ──► trades (deduplicated view)
                         ▲
                         │
Subgraph poll ──► trades_raw (delayed ~5min, _version=2, with addresses)
(60s interval)
```

The `_version` field in `ReplacingMergeTree` ensures on-chain data (version 2) overwrites WebSocket data (version 1) when both exist for the same trade.

### 6.5 Address Enrichment

For trades where WebSocket arrived first (version 1, no addresses), the Subgraph poll enriches them within ~5-10 minutes. Since on-chain and off-chain sources produce different `trade_id` formats, enrichment uses a fuzzy match:

```sql
-- Match by: same market + similar timestamp (±30s) + same price + same size
SELECT
    COALESCE(chain.trade_id, ws.trade_id) as trade_id,
    COALESCE(chain.maker, ws.maker) as maker,
    COALESCE(chain.taker, ws.taker) as taker,
    COALESCE(chain.tx_hash, ws.tx_hash) as tx_hash,
    COALESCE(ws.timestamp, chain.timestamp) as timestamp  -- WS has ms precision
FROM trades_ws ws
LEFT JOIN trades_chain chain ON
    ws.condition_id = chain.condition_id
    AND ws.asset_id = chain.asset_id
    AND abs(toUnixTimestamp(ws.timestamp) - toUnixTimestamp(chain.timestamp)) < 30
    AND abs(ws.price - chain.price) < 0.001
    AND abs(ws.size - chain.size) < 0.01
```

---

## 7. Storage Schema

### 7.1 ClickHouse: Trade Storage

```sql
CREATE TABLE trades_raw (
    trade_id String,

    -- Market
    condition_id LowCardinality(String),
    asset_id String,

    -- Trade
    side Enum8('BUY' = 1, 'SELL' = 2),
    price Float32 CODEC(Gorilla, LZ4),
    size Float32,
    amount_usd Float32,
    fee_usd Float32,

    -- Participants
    maker Nullable(String),
    taker Nullable(String),

    -- Timing
    timestamp DateTime64(3) CODEC(DoubleDelta, LZ4),

    -- Provenance
    source LowCardinality(String),
    tx_hash Nullable(String),
    order_hash Nullable(String),
    block_number Nullable(UInt64),
    is_backfill Bool,

    -- ReplacingMergeTree version: on-chain (2) > off-chain (1)
    _version UInt8 DEFAULT if(source IN ('goldsky_sink','goldsky_subgraph'), 2, 1),

    -- Ingestion
    ingested_at DateTime64(3) DEFAULT now64()
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(timestamp)
ORDER BY (condition_id, timestamp, trade_id)
SETTINGS index_granularity = 8192;

-- Bloom filters for point lookups
ALTER TABLE trades_raw ADD INDEX idx_maker maker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE trades_raw ADD INDEX idx_taker taker TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE trades_raw ADD INDEX idx_trade_id trade_id TYPE bloom_filter(0.01) GRANULARITY 1;
```

**Deduplicated view** (for all downstream queries):
```sql
CREATE VIEW trades AS SELECT * FROM trades_raw FINAL;
```

### 7.2 ClickHouse: Materialized Views

```sql
-- Trades by wallet (for skilled trader analysis)
CREATE MATERIALIZED VIEW trades_by_wallet
ENGINE = ReplacingMergeTree(_version)
ORDER BY (maker, condition_id, timestamp)
AS SELECT * FROM trades_raw WHERE maker IS NOT NULL;

-- Trades by market (for market analysis)
CREATE MATERIALIZED VIEW trades_by_market
ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
AS SELECT * FROM trades_raw;

-- 5-minute activity aggregation (for burst detection)
CREATE MATERIALIZED VIEW market_activity_5m
ENGINE = SummingMergeTree()
ORDER BY (condition_id, bucket)
AS SELECT
    condition_id,
    toStartOfFiveMinutes(timestamp) as bucket,
    count() as trade_count,
    sum(amount_usd) as volume_usd,
    uniq(maker) as unique_makers
FROM trades_raw
GROUP BY condition_id, bucket;
```

### 7.3 ClickHouse: Compression & Tuning

Target: 150 GB raw → ~20 GB compressed.

| Column | Codec | Rationale |
|--------|-------|-----------|
| `timestamp` | DoubleDelta + LZ4 | Sequential timestamps compress ~95% |
| `price` | Gorilla + LZ4 | Float compression for 0-1 range |
| `condition_id` | LowCardinality | ~tens of thousands distinct values |
| `source` | LowCardinality | 4 distinct values |
| `side` | Enum8 | 1 byte per row |
| `maker`, `taker` | Default (LZ4) | Hex addresses, moderate cardinality |

**Server tuning** (for Ryzen 5990X, 128 GB RAM):
```xml
<max_threads>64</max_threads>
<uncompressed_cache_size>32212254720</uncompressed_cache_size>  <!-- 30 GB -->
<mark_cache_size>10737418240</mark_cache_size>                  <!-- 10 GB -->
```

### 7.4 PostgreSQL: Operational State

```sql
-- Market registry
CREATE TABLE markets (
    condition_id VARCHAR(66) PRIMARY KEY,
    question TEXT,
    slug VARCHAR(200),
    category VARCHAR(100),
    token_yes VARCHAR(80),
    token_no VARCHAR(80),
    neg_risk BOOLEAN DEFAULT false,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    resolution_outcome VARCHAR(10),
    ws_subscribed BOOLEAN DEFAULT false,
    ws_subscribed_at TIMESTAMPTZ,
    ws_last_event_at TIMESTAMPTZ,
    backfill_status VARCHAR(20) DEFAULT 'pending',
    backfill_last_ts TIMESTAMPTZ,
    gamma_last_sync TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Token → Market lookup (critical for normalizers)
CREATE TABLE token_market_map (
    asset_id VARCHAR(80) PRIMARY KEY,
    condition_id VARCHAR(66) NOT NULL REFERENCES markets(condition_id),
    outcome VARCHAR(10) NOT NULL  -- 'YES' or 'NO'
);

-- Pipeline health
CREATE TABLE pipeline_health (
    source VARCHAR(30),
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    last_event_at TIMESTAMPTZ,
    events_last_hour INT,
    gap_seconds INT,
    status VARCHAR(20)  -- 'healthy', 'degraded', 'stale'
);
```

---

## 8. Market Lifecycle Management

### 8.1 Market Discovery

The `MarketSyncer` polls the Gamma API periodically (every 5 minutes) to discover new markets and detect status changes (active → closed → resolved).

**Discovery flow**:
1. Fetch all markets from Gamma API (`GET /markets?limit=100&offset=...`).
2. For each market, upsert into `markets` table.
3. For new markets: populate `token_market_map` with YES/NO token IDs.
4. For status changes: emit events (`market.discovered`, `market.closed`, `market.resolved`).

### 8.2 Subscription Management

The `SubscriptionManager` controls which markets have active WebSocket subscriptions.

**Preconditions for subscribing**:
- Market exists in registry
- Market status is `active`
- Both `token_yes` and `token_no` are populated
- Not already subscribed
- Below max subscription limit (default: 500)

**Health checks** (every 5 minutes):
- Detect stale subscriptions (no events in >1 hour)
- Detect active markets not subscribed
- Detect zombie subscriptions (resolved markets still subscribed)
- Verify WebSocket connection health

### 8.3 Cleanup

When a market resolves:
1. Unsubscribe from WebSocket
2. Ensure subgraph catch-up completes (all trades captured)
3. Mark `backfill_status = 'complete'`
4. Retain data in ClickHouse (no deletion — needed for historical analysis)

---

## 9. RTDS Activity Stream — The Critical Unknown

### 9.1 Discovery

Polymarket's undocumented RTDS (Real-Time Data Stream) Activity topic was discovered in their TypeScript client code (`real-time-data-client`). The official RTDS documentation only lists `crypto_prices` and `comments` topics, but the client code implements an `activity` topic with `type: "trades"` subscription.

### 9.2 Hypothesized Schema

Based on the TypeScript client code:
```json
{
  "topic": "activity",
  "type": "trades",
  "data": {
    "id": "...",
    "maker_address": "0x...",       // ← THE KEY FIELD
    "market": "...",
    "asset_id": "...",
    "side": "BUY",
    "price": "0.65",
    "size": "100",
    "match_time": "2026-...",
    "transaction_hash": "0x...",
    "taker_order_id": "...",
    "maker_orders": [
      { "order_id": "...", "matched_amount": "...", "maker_address": "0x..." }
    ]
  }
}
```

### 9.3 Four Questions (Pending Empirical Test)

| # | Question | Best Case | Worst Case | Impact |
|---|----------|-----------|------------|--------|
| 1 | Does it provide `maker_address`? | ✓ Real-time addresses | ✗ Must use subgraph (~5 min) | Copy trading latency: <1s vs ~5 min |
| 2 | Is it a global firehose? | ✓ All trades, all markets | ✗ Needs per-market filters | Complexity of subscription management |
| 3 | Does it work without auth? | ✓ Public, no API key | ✗ Needs `gamma_auth` token | Operational complexity |
| 4 | Is it complete? | ✓ Same trade count as WS | ✗ Misses 5-10% of trades | Reliability for signal generation |

### 9.4 Test Script

A comprehensive test script (`test_polymarket_ws.py`) has been created that connects to three WebSocket endpoints simultaneously — RTDS global, RTDS filtered, and Market WS — and compares trade counts, field availability, and latency over a configurable duration. The script answers all four questions with quantitative data.

### 9.5 Architecture Implications

**If RTDS provides `maker_address` globally without auth (best case)**:
- RTDS becomes the primary live source for copy trading
- Copy trading latency drops from ~5 min to <1 second
- Subgraph remains the completeness guarantee / enrichment source
- Architecture: `RTDS → filter by tracked wallets → signal.copy`

**If RTDS lacks `maker_address` or requires auth (worst case)**:
- Fall back to subgraph polling (reliable, ~5 min lag)
- Evaluate Polygon RPC WebSocket as alternative (~2s lag, Alchemy free tier)
- Copy trading profitability model must assume ~5 min detection latency
- Architecture: `Subgraph poll (60s) → filter by tracked wallets → signal.copy`

**Recommended approach**: Layer three sources by reliability:
```
Primary:     RTDS Activity (fastest, test first)
Fallback:    Subgraph polling (reliable, complete)
Nuclear:     Polygon RPC WebSocket (if both fail)
Validation:  Cross-check RTDS vs Subgraph counts hourly
```

---

## 10. Sanity Checks & Monitoring

### 10.1 Per-Event Validation (At Ingestion)

Every trade is validated before insertion:

| Check | Rule | Action on Failure |
|-------|------|-------------------|
| Price bounds | `0 ≤ price ≤ 1.0` | Reject + log |
| Positive size | `size > 0` | Reject + log |
| USD consistency | `abs(amount_usd - price × size) / amount_usd < 0.01` | Warn + accept |
| Timestamp sanity | `timestamp ≤ now() + 60s` | Reject + log |
| Market exists | `condition_id` in registry | Queue for retry (market may not be synced yet) |
| Not self-trade | `maker ≠ taker` (for on-chain sources) | Reject + log |
| Not taker-focused | `taker NOT IN Exchange contracts` (on-chain) | Drop silently |

### 10.2 Hourly Cross-Source Reconciliation

Every hour, compare per-source trade counts and volumes:

```sql
SELECT
    source,
    count() as trade_count,
    sum(amount_usd) as total_volume,
    min(timestamp) as earliest,
    max(timestamp) as latest
FROM trades_raw
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY source
```

**Alerts**:
- If any source has zero events for >30 minutes → `gap_detected`
- If on-chain volume > 1.8× off-chain volume → `double_count_suspected`
- If off-chain volume > 1.2× on-chain volume → `on_chain_lag`

### 10.3 Pipeline Health Dashboard

Track per-source:
- Messages/second (rate)
- Last event timestamp (freshness)
- Error rate (failed normalizations)
- Dedup rate (how many records collapsed by ReplacingMergeTree)
- Enrichment rate (% of WebSocket trades that got address-enriched)

---

## 11. Implementation Phases

### Phase 0: Prerequisites (Week 1)

| Task | Description |
|------|-------------|
| Market metadata sync | Gamma API → PostgreSQL `markets` + `token_market_map` |
| Token-market lookup | Build and cache `asset_id → condition_id` mapping |
| ClickHouse schema | Create `trades_raw`, views, materialized views |
| PostgreSQL schema | Create operational tables |
| RTDS test | Run `test_polymarket_ws.py`, document findings |

**Gate**: Can insert a manually-constructed `NormalizedTrade` into ClickHouse and query it back.

### Phase 1: Backfill (Weeks 2-3)

| Task | Description |
|------|-------------|
| Sink loader | S3 Parquet → staging table → dedup filter → `trades_raw` |
| Double-count filter | Drop taker-focused events at ingestion |
| Amount normalization | Convert raw wei/1e6 amounts to Float32 USDC |
| Side derivation | `makerAssetId == "0"` → BUY, else → SELL |
| Validation | Spot-check known markets against Polymarket UI totals |

**Gate**: `SELECT count(), sum(amount_usd) FROM trades` matches expected totals (within 1%) for 10 sample markets.

### Phase 2: Catch-Up (Week 3)

| Task | Description |
|------|-------------|
| Subgraph poller | Cursor-based GraphQL polling with 60s interval |
| Overlap management | Start subgraph 10 min before sink's last timestamp |
| Incremental loading | Write normalized trades to `trades_raw` continuously |
| Resume logic | Persist cursor in PostgreSQL, resume after restart |

**Gate**: Subgraph catches up to within 7 minutes of now. Trade counts for recent hours match between Sink and Subgraph.

### Phase 3: Live Streaming (Weeks 4-5)

| Task | Description |
|------|-------------|
| Market WS consumer | Connect, subscribe to active markets, normalize `last_trade_price` |
| Subscription manager | Auto-subscribe new markets, unsubscribe resolved |
| Dual-write | Both WS and Subgraph write to `trades_raw` concurrently |
| Reconnection logic | Exponential backoff, gap detection on reconnect |
| RTDS integration | If test results are positive, add RTDS as primary live source |

**Gate**: In steady state, every trade appears in ClickHouse within 2 seconds (via WS) and gets enriched with addresses within 10 minutes (via Subgraph).

### Phase 4: Observability & Hardening (Week 5-6)

| Task | Description |
|------|-------------|
| Health checks | Per-source freshness, gap detection, rate monitoring |
| Cross-source reconciliation | Hourly comparison job with alerting |
| Enrichment merge | Fuzzy-match WS trades to Subgraph trades for address backfill |
| Compression audit | Verify storage < 25 GB, tune codecs if needed |
| Load test | Simulate peak volume (2x normal) and verify no data loss |

**Gate**: System runs 72 hours with zero gaps > 30s, zero double-counts, and all health checks passing.

---

## 12. Success Criteria

| Criterion | Measurement | Target |
|-----------|-------------|--------|
| **Completeness** | Cross-reference 10 random markets with Polymarket UI trade counts | Within 1% |
| **No double-counting** | On-chain source volume ≈ 1.0× off-chain source volume (hourly) | Ratio 0.95–1.05 |
| **Freshness (live)** | Median time from CLOB match to ClickHouse insert | < 2 seconds |
| **Freshness (enriched)** | Median time from match to address-enriched record | < 10 minutes |
| **Query performance** | `GROUP BY maker WHERE condition_id = X` | < 1 second |
| **Query performance** | `GROUP BY condition_id WHERE maker = X` | < 3 seconds |
| **Storage** | Total ClickHouse disk usage | < 25 GB |
| **Uptime** | No gaps > 30 seconds in live stream over 72-hour window | 100% |

---

## 13. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **RTDS doesn't provide `maker_address`** | Medium | High (copy trading latency 300x worse) | Subgraph fallback already architected. Polygon RPC WS as nuclear option (~2s latency). |
| **RTDS requires authentication** | Medium | Medium | Polymarket API keys are free. Will need key management. |
| **RTDS is incomplete (misses trades)** | Medium | Low | Subgraph provides completeness guarantee within ~5 min. RTDS completeness only matters for latency-critical signals. |
| **Goldsky Sink has undetected gaps** | Low | High | Subgraph covers the same data. Cross-validate totals per month. |
| **Double-count detection misses edge cases** | Low | High | Additional heuristic: check if `maker` appears as `taker` in another event with same `tx_hash` and matching amounts. |
| **ClickHouse FINAL performance degrades at scale** | Medium | Medium | Pre-aggregate into materialized views. Use `OPTIMIZE TABLE` on schedule. Monitor query latency weekly. |
| **WebSocket disconnections during high volume** | Medium | Medium | Exponential backoff reconnection. Subgraph fills any gaps. Gap detection alerts. |
| **Gamma API rate limits during metadata sync** | Low | Low | Cache aggressively. Sync only changed markets (track `updated_at`). |
| **Token-market mapping is stale** | Low | Medium | Refresh mapping every 5 minutes. Queue unknown `asset_id`s for retry. |

---

## 14. Open Questions

1. **RTDS empirical results**: Does the RTDS activity/trades stream provide `maker_address`, work as a global firehose, function without auth, and match Market WS completeness? (Test script ready, awaiting execution.)

2. **Polygon RPC WebSocket viability**: If both RTDS and Subgraph are insufficient for copy trading latency, can Alchemy's free-tier Polygon WS handle subscribing to `OrderFilled` logs from the CTF Exchange and NegRisk contracts? What's the realistic throughput?

3. **OrdersMatched events**: Should we also ingest `OrdersMatched` events (one per tx, taker-side aggregate) as a cross-check? They're redundant with the sum of maker-focused `OrderFilled` events but could serve as a validation signal.

4. **Historical orderbook data**: For backtest fidelity, we eventually need orderbook depth at trade time. Can we reconstruct approximate orderbook state from the `book` events in Market WS? Worth persisting? Deferred to future phase.

5. **USDC vs token scaling**: The 1e18 float values in the raw Goldsky data — are these universally 1e6 (USDC decimals) for amounts, or do some fields use 1e18 (ERC-1155 token precision)? Needs empirical verification against a few known trades.

---

## 15. Dependencies

### External Services

| Service | Purpose | Failure Mode |
|---------|---------|-------------|
| Polymarket CLOB WS | Live trade feed | Reconnect with backoff. Subgraph covers gap. |
| Polymarket RTDS WS | Live trade feed with addresses | Graceful degradation to Subgraph. |
| Polymarket Gamma API | Market metadata | Cache + stale-serve for up to 1 hour. |
| Goldsky Mirror (S3) | Historical backfill | One-time load. No ongoing dependency. |
| Goldsky Subgraph (GraphQL) | Catch-up + enrichment | Retry with exponential backoff. Data is immutable. |

### Internal Infrastructure

| Component | Spec | Purpose |
|-----------|------|---------|
| ClickHouse | Single node, 4 TB NVMe | Analytics storage |
| PostgreSQL | Single node | Operational state, market registry |
| RedPanda/Kafka | Cluster (TuringPi) | Event bus between pipeline stages |
| Python 3.12+ | Workstation | Pipeline processes |

### Libraries

| Library | Purpose |
|---------|---------|
| `websockets` / `aiohttp` | WebSocket connections |
| `clickhouse-connect` | ClickHouse client |
| `asyncpg` / `SQLAlchemy` | PostgreSQL async client |
| `pyarrow` | Parquet file reading (Sink loader) |
| `faststream` | Event streaming consumers |
| `pydantic` | Data validation (NormalizedTrade) |

---

## Appendix A: API Reference

### Goldsky Subgraph Endpoint
```
POST https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn
```

### Market WebSocket
```
wss://ws-subscriptions-clob.polymarket.com/ws/market
Subscribe: {"type": "market", "assets_ids": ["<token_yes>", "<token_no>"]}
```

### RTDS WebSocket
```
wss://ws-live-data.polymarket.com
Subscribe: {"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]}
Heartbeat: PING every 5 seconds, must respond with PONG
```

### Gamma API
```
GET https://gamma-api.polymarket.com/markets?limit=100&offset=0
GET https://gamma-api.polymarket.com/events?limit=100&offset=0
```

### Exchange Contract Addresses (Taker-Focused Event Detection)
```
CTF Exchange:         0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
NegRisk CTF Exchange: 0xC5d563A36AE78145C45a50134d48A1215220f80a
```

## Appendix B: Related Documents

| Document | Description |
|----------|-------------|
| `polymarket-data-sources-explained.md` | Visual guide to the five taps and their tradeoffs |
| `polymarket-data-consistency.md` | Detailed consistency architecture with full code examples |
| `polymarket-roadmap.md` | Consolidated roadmap across all project phases |
| `test_polymarket_ws.py` | RTDS empirical validation test script |
| `polymarket-backfill-prd.md` | Historical data backfill PRD (superseded by §11 Phase 1) |
| `polymarket-copytrade-prd.md` | Copy trading strategy PRD (depends on this pipeline) |