# Ingestion Layer

Complete documentation of the live pipeline's layered ingestion architecture, including all 6 ingestors, normalizers, deduplication strategies, and operational gotchas.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                    INGESTORS (BaseIngestor)                  │
                    │                                                             │
                    │  RPCIngestor ─────── Polygon WS ──── T+3.7s ── trades.raw  │
                    │  RTDSIngestor ────── RTDS WS ─────── T+4.2s ── trades.raw  │
                    │  PendingBlock ────── Free RPC poll ── T+0.2s ── pending.signal│
                    │  CLOBOrderbook ───── CLOB WS ─────── T+0.0s ── orderbooks.raw│
                    │  MempoolIngestor ─── Rust PyO3 ────── T-1s ─── mempool.raw │
                    │  SubgraphPoller ──── Goldsky GQL ──── Minutes ─ trades.raw  │
                    │                                                             │
                    │  All → BaseIngestor (heartbeat, circuit breaker, metrics)   │
                    └─────────────────────────────────────────────────────────────┘
                                              │
                              ┌───────────────┴──────────────┐
                              ▼                              ▼
                      ┌──────────────┐            ┌───────────────────┐
                      │   Redpanda   │            │    ClickHouse     │
                      │  (Kafka)     │            │  (Kafka engine →  │
                      │              │            │   trades_raw)     │
                      └──────────────┘            └───────────────────┘
```

### Timing Hierarchy (Empirically Verified)

| Source | Latency | Coverage | Identity |
|---|---|---|---|
| **Pending Block poll** | T+0.2s (pre-chain) | ~81.4% (2 endpoints) | Full calldata |
| **CLOB WS** | T+0.0s (match time) | ~100% | None |
| **RPC (Alchemy)** | T+3.7s (on-chain) | ~100% | maker + taker |
| **RTDS** | T+4.2s (post-chain) | ~100% | proxyWallet |
| **Subgraph** | Minutes | Recovery only | Full |

---

## BaseIngestor (Abstract Base)

**File**: `src/polymarket_pipeline/live/ingestors/base.py`

All ingestors inherit from `BaseIngestor`, which provides:

- **Heartbeat loop**: Publishes to `pipeline.status` topic every 10s
- **Circuit breaker**: Shared `CircuitBreaker` for publish operations (trips after 5 failures, 30s cooldown)
- **Metrics**: `_trade_count`, `_drops_queue_full`, `_circuit_breaker`
- **Abstract method**: `run()` — must be implemented by each ingestor

### Circuit Breaker States

```
CLOSED (normal) ──[5 consecutive failures]──> OPEN (tripped)
     ▲                                            │
     │                                     [30s cooldown]
     │                                            ▼
     └────────[1 successful test publish]──── HALF_OPEN
```

### Heartbeat Format

```json
{
  "source": "rtds",
  "event": "heartbeat",
  "trade_count": 12345,
  "drops_queue_full": 0,
  "ts": 1709136000.0,
  "...source-specific fields..."
}
```

---

## 1. RPCIngestor (On-Chain Logs)

**File**: `src/polymarket_pipeline/live/ingestors/rpc.py`
**Source Name**: `"alchemy"` (backward compatibility)
**Settings**: `PM_RPC_WS_URL` (accepts legacy `PM_ALCHEMY_WS_URL` via AliasChoices)

### What It Does

Subscribes to Polygon RPC WebSocket for two event types:
1. **OrderFilled events** → normalized trades → `trades.raw`
2. **QuestionResolved events** (optional) → resolution signals → `markets.events`

### Connection Architecture

```
WebSocket (wss://polygon RPC)
    │
    ├── eth_subscribe(logs, OrderFilled)  ──> _handle_message() ──> queue
    │                                                                  │
    ├── eth_subscribe(logs, QuestionResolved) ──> _handle_resolution() │
    │                                                                  │
    └── _publish_loop() <──────────────────────────────────────────────┘
              │
              └──> Redpanda trades.raw / markets.events
```

### Key Constants

```python
CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
UMA_CTF_ADAPTER_V3 = "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49"
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
QUESTION_RESOLVED_SIG = "0x566c3fbd0982e206be981f8d7a42e3e436525258ecc0adc044023b81ab281d0e"
_STALE_TIMEOUT = 120.0  # Force reconnect if silent for 2 min
_QUEUE_MAXSIZE = 1000
```

### Normalizer: `PolygonRPCNormalizer`

- Decodes indexed params from `topics`, non-indexed from `data` (using `eth_abi`)
- Side determination: if `taker_asset_id == 0`, it's BUY (maker provides USDC)
- Drops taker duplicates: `taker.lower() in EXCHANGE_ADDRS`
- Trade ID: `make_trade_id_chain(tx_hash, order_hash)`
- Version: **2** (on-chain, highest priority)

### Resolution Detection

The `_resolution_loop()` subscribes to `QuestionResolved` events from UMA CTF Adapter:
- `topics[1]` = condition_id (bytes32)
- `topics[2]` = settledPrice (int256)
- Winner: `settledPrice == 1e18` → YES, `0` → NO
- Publishes `{type: "market_resolved", condition_id, payload: {winner, settled_price}}` to `markets.events`

### Gotchas

1. **`blockTimestamp` is hex-encoded** — must `int(..., 16)` before using. Falls back to `time.time()` if missing.
2. **Topics are NOT stripped of `0x` prefix** — extract address from last 40 chars.
3. **Stale timeout (120s)** — forces WS reconnect if no messages arrive.
4. **`_last_block`** tracks highest block seen (reported in heartbeat for gap detection).

---

## 2. RTDSIngestor (RTDS WebSocket)

**File**: `src/polymarket_pipeline/live/ingestors/rtds.py`
**Source Name**: `"rtds"`
**Settings**: `PM_RTDS_POOL_SIZE` (default 2), `PM_RTDS_ROTATION_INTERVAL_S` (default 300)

### What It Does

Maintains a pool of redundant WebSocket connections to RTDS for global trade feed.

### Connection Architecture

```
Connection 0 ─── wss://ws-live-data.polymarket.com ───┐
                                                       ├──> TradeDedup ──> queue ──> Redpanda
Connection 1 ─── wss://ws-live-data.polymarket.com ───┘         (5min TTL)
                     (staggered rotation)
```

### Key Constants

```python
RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
_DEDUP_TTL_S = 300.0  # 5min TTL for cross-connection dedup
_QUEUE_MAXSIZE = 1000
```

### Subscription

```json
{"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]}
```

### Normalizer: `RTDSNormalizer`

- Rounds price/size to 2 decimals with `ROUND_HALF_UP` (fixes float imprecision)
- Filters dust trades (size rounds to 0)
- Trade ID: `make_trade_id_ws(asset_id, timestamp_ms, price, size)`
- Maker: `proxyWallet` (on-chain proxy wallet address)
- Taker: `None`
- Fee: `Decimal("0")` (RTDS doesn't provide fees)
- Version: **1** (off-chain)

### Dedup Strategy

`TradeDedup` with `OrderedDict` and 5-minute TTL eviction. **Shared across all pool connections** to prevent duplicates when multiple connections see the same trade.

### Rotation

Connections rotate on staggered intervals:
- Connection 0: first rotation at `rotation_interval * 1/pool_size`
- Connection 1: first rotation at `rotation_interval * 2/pool_size`

This ensures at least one connection is always alive during rotation.

### Gotchas

1. **Two timestamps**: `payload["timestamp"]` (trade time, seconds) vs top-level `msg["timestamp"]` (delivery time, milliseconds, ~500ms later). **Always use `payload["timestamp"]`.**
2. **Float imprecision**: Prices like `0.3996666666666667` are common. Always round.
3. **PING/PONG**: Send "PING" every 5s, expect "PONG". Not JSON — handle separately.
4. **Pool size must be >= 1** (enforced in constructor).

---

## 3. PendingBlockIngestor (Pre-Chain Signals)

**File**: `src/polymarket_pipeline/live/ingestors/pending_block.py`
**Source Name**: `"pending_block"`
**Settings**: `PM_PENDING_BLOCK_ENABLED`, `PM_PENDING_BLOCK_RPC_WS_URLS`, `PM_PENDING_BLOCK_POLL_INTERVAL_S`

### What It Does

Polls multiple free RPC endpoints for `eth_getBlockByNumber("pending", true)` to extract Polymarket trades before block finalization (~0.2-1s early).

### Connection Architecture

```
Endpoint 0 (publicnode) ──> poll every 0.5s ──┐
                                               ├──> _LRUSet dedup ──> queue ──> Redpanda pending.signal
Endpoint 1 (drpc) ─────────> poll every 0.5s ──┘      (10K max)
```

### Key Constants

```python
DEFAULT_RPC_ENDPOINTS = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon.drpc.org",
]
_LRUSet_MAXSIZE = 10000
```

### Normalizer: `PendingBlockNormalizer`

- Decodes `matchOrders` calldata (selector `0x2287e350`)
- Extracts Order struct per maker in the call
- Side: `taker_order[side]` (0=BUY, 1=SELL)
- Computes price/size from fill amounts, **clamps to [0, 1]**
- Trade ID: `make_trade_id_pending(tx_hash, index)` (per-fill index within tx)
- Version: **0** (lowest priority)

### Critical Design Decision

Pending block trades publish to **`pending.signal` topic** (NOT `trades.raw`). They are:
- **NOT written to ClickHouse** `trades_raw`
- **NOT part of version-based dedup** (different trade ID scheme)
- Consumed directly by strategies as early signals

### Why Race Multiple Endpoints

Single endpoint catches ~55% of pending block txs. Racing publicnode + drpc catches ~81.4%. Different validators build different candidate blocks, so multiple endpoints provide better coverage.

### Gotchas

1. **Price can exceed [0, 1]** after arithmetic — always clamp.
2. **Trade ID !== on-chain ID** — `pending:` prefix won't match `chain:` on confirmation.
3. **10MB max WS message size** — large pending blocks with many txs.
4. **`FEE_MODULE_ADDRS` filter** — only processes txs targeting Polymarket fee module contracts.

---

## 4. CLOBOrderbookIngestor (CLOB WebSocket)

**File**: `src/polymarket_pipeline/live/ingestors/clob_orderbook.py`
**Source Name**: `"clob_orderbook"`
**Settings**: `PM_CLOB_ORDERBOOK_ENABLED`, `PM_CLOB_ORDERBOOK_WS_URL`, `PM_CLOB_MAX_OB_CONNECTIONS`

### What It Does

Maintains two types of CLOB WebSocket connections:
1. **Firehose** (1 connection): Empty subscription → broadcasts `new_market`, `market_resolved`
2. **Targeted** (N connections): Subscribe to specific asset IDs → `price_change` events + snapshots

### Connection Architecture

```
Firehose ──── wss://...clob.../ws/market (empty arrays) ──> markets.events
    │
    ├── market_resolved events
    └── new_market events

Targeted 0 ── wss://...clob.../ws/market (500 assets) ──> orderbooks.raw
Targeted 1 ── wss://...clob.../ws/market (500 assets) ──> orderbooks.raw
    ...
```

### Message Routing

```
Received message:
    │
    ├── List (array) → if any entry has "bids"/"asks" → orderbook snapshot
    │
    └── Dict:
        ├── has "price_changes" → extract best_bid/best_ask → orderbooks.raw
        ├── has "question" + "market" → new_market broadcast
        └── has "event_type" in (market_resolved, new_market) → markets.events
```

### Key Constants

```python
_MAX_ASSETS_PER_WS = 500    # Max assets per targeted connection
_FIREHOSE_STALE_TIMEOUT = 120.0  # Force reconnect if silent
```

### Output Schema (orderbooks.raw)

```json
{
  "condition_id": "0x...",
  "asset_id": "12345...",
  "best_bid": 0.65,
  "best_ask": 0.67,
  "timestamp": 1709136000.123
}
```

### Gotchas

1. **Firehose requires `custom_feature_enabled: true`** in subscription payload to receive broadcast events.
2. **No timestamp in snapshots** — must use `time.time()`.
3. **`_safe_float()` returns None on invalid prices** — silently skips broken snapshots.
4. **Asset scaling**: connections cap at `max_ob_connections * 500` assets total.
5. **CLOB WS firehose `market_resolved` is broken** for some markets — RPC resolution detection is the reliable path.

---

## 5. MempoolIngestor (Rust PyO3 Sidecar)

**File**: `src/polymarket_pipeline/live/ingestors/mempool.py`
**Source Name**: `"mempool"`
**Settings**: `PM_MEMPOOL_ENABLED` (default: False), `PM_MEMPOOL_LISTEN_PORT` (default: 30304)

### What It Does

Wraps a Rust PyO3 module (`polymarket_mempool`) that monitors devp2p gossip network for pending Polymarket transactions.

### Build Requirements

```bash
cd crates/polymarket-mempool
maturin build --release
uv pip install --force-reinstall target/wheels/*.whl
```

**Gotcha**: `maturin develop` can produce stale `.so` files. Always use `build --release` + manual install.

### Normalizer: `MempoolNormalizer`

- Trade ID: `f"mempool:{sha256(tx_hash)[:16]}"` (NOT `make_trade_id_chain`)
- Fee: Always 0 (pre-confirmation)
- Version: **0** (lowest priority)
- Source: `Source.MEMPOOL`

### Critical Finding

Polymarket operators bypass the public mempool entirely via Flashbots/MEV relay private submission. The mempool sidecar sees very few operator transactions. Pending block polling is more effective.

### Gotchas

1. **Optional module** — `ImportError` is caught gracefully; ingestor just returns without crashing.
2. **Trade ID is mempool-specific** — won't match on-chain or pending block IDs.
3. **`_peers_active`** metadata key is popped before normalization (not a trade field).

---

## 6. SubgraphPoller (Gap Recovery)

**File**: `src/polymarket_pipeline/live/ingestors/subgraph.py`
**NOT an ingestor** — standalone recovery tool, not part of the live feed.

### What It Does

Fills gaps in trade data after outages by querying Goldsky Subgraph's GraphQL API.

### Query Strategy

```graphql
query($timestamp_gt: Int!) {
  orderFilledEvents(
    first: 500
    orderBy: timestamp
    orderDirection: asc
    where: { timestamp_gt: $timestamp_gt, taker_not_in: [...EXCHANGE_ADDRS] }
  ) {
    ...fields
  }
}
```

**Pagination**: Cursor-based. When timestamp repeats (multiple events same second), uses `id_gt` for next query.

### Sink Modes

1. **Redpanda** (`--redpanda`): Publishes to `trades.raw` topic
2. **ClickHouse Direct** (default): Synchronous insert via `ClickHouseDirectSink`

### Resumable Recovery

- PostgreSQL tracks cursor: `(from_ts, cursor_ts, cursor_id, total_published, status)`
- Checkpoints every 50 batches
- If killed mid-run, next `pm-recover` resumes from cursor
- ETA calculation: rolling 600s window of (wall_time, data_seconds_covered)

### Normalizer: `SubgraphNormalizer`

- Trade ID: `make_trade_id_chain(tx_hash, order_hash)` (matches on-chain ID)
- Version: **2** (on-chain, highest priority)
- Fallback: if unknown asset_id, uses `asset_id` as `condition_id` (instead of returning None)

---

## Deduplication Strategies

### Per-Source Dedup

| Ingestor | Strategy | Scope | TTL/Eviction |
|---|---|---|---|
| **RPC** | Taker filter in normalizer | Per-message | N/A |
| **RTDS** | `TradeDedup` (OrderedDict) | Across pool connections | 5 min TTL |
| **PendingBlock** | `_LRUSet` (OrderedDict) | Across endpoints | 10K max LRU |
| **CLOB Orderbook** | Overwrite (latest timestamp wins) | Per snapshot | N/A |
| **Mempool** | Taker filter in normalizer | Per-message | N/A |
| **Subgraph** | Taker filter at query level (`taker_not_in`) | Per query | N/A |

### Cross-Source Dedup

ClickHouse `ReplacingMergeTree(_version)` keeps the highest version for each `(condition_id, timestamp, trade_id)`:
- Version 2 (on-chain) overwrites version 1 (off-chain)
- Use `FINAL` in queries to get deduplicated results

### Taker-Focused Duplicate Filtering

~40.5% of on-chain `OrderFilled` events are taker-perspective duplicates. The exchange emits two events per trade (one from each party). Rows where `taker.lower() in EXCHANGE_ADDRS` are dropped.

---

## Error Handling & Recovery

### Reconnection

All WS-based ingestors use exponential backoff:
- Base: 1.0s
- Max: 60.0s
- Formula: `min(base * 2^attempt, max)`

### Stale Timeout

RPC and CLOB firehose force reconnect after 120s of silence. This catches silent WebSocket deaths (observed after ~20min, GitHub #26).

### Backpressure Queue

All ingestors use an `asyncio.Queue(maxsize=1000)`:
- Producer (WS handler): enqueues normalized trades
- Consumer (`_publish_loop`): drains queue, publishes to Kafka with circuit breaker
- Queue full → trade dropped, `_drops_queue_full` counter incremented

### Task Supervision

`supervise_tasks()` in orchestrator watches for ingestor task crashes:
- Crashed tasks are logged immediately
- **No automatic restart** — quality checker detects stale heartbeats
- Pipeline transitions to DEGRADED → RED → auto-protect (close positions)

---

## Configuration Reference

All settings use `PM_` prefix (Pydantic Settings):

| Setting | Default | Purpose |
|---|---|---|
| `PM_RPC_WS_URL` | `wss://polygon-bor-rpc.publicnode.com` | RPC endpoint for OrderFilled + resolution logs |
| `PM_RESOLUTION_RPC_ENABLED` | `true` | Enable QuestionResolved detection loop |
| `PM_RTDS_POOL_SIZE` | `2` | Number of redundant RTDS connections |
| `PM_RTDS_ROTATION_INTERVAL_S` | `300` | RTDS connection rotation cycle |
| `PM_PENDING_BLOCK_ENABLED` | `false` | Enable pending block polling |
| `PM_PENDING_BLOCK_RPC_WS_URLS` | `publicnode,drpc` | Comma-separated RPC endpoints |
| `PM_PENDING_BLOCK_POLL_INTERVAL_S` | `0.5` | Poll frequency |
| `PM_CLOB_ORDERBOOK_ENABLED` | `false` | Enable CLOB WS orderbook capture |
| `PM_CLOB_ORDERBOOK_WS_URL` | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | CLOB WS endpoint |
| `PM_CLOB_MAX_OB_CONNECTIONS` | `30` | Max targeted WS connections |
| `PM_MEMPOOL_ENABLED` | `false` | Enable Rust mempool sidecar |
| `PM_MEMPOOL_LISTEN_PORT` | `30304` | Mempool UDP listen port |
| `PM_SOURCE_LIVENESS_TIMEOUT_S` | `30` | Heartbeat timeout for quality check |
