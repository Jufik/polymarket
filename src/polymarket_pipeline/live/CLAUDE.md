# live/ — Live Sync Pipeline

FastStream + Redpanda-based live ingestion pipeline.

## Entry Points

- `pm-live` → `app.py` — Main pipeline (ingestors + CH storage + market events)
- `pm-strategy run --config X.toml` → `cli/strategy.py` — Strategy runner (consumes from Kafka)

## Kafka Topics

| Topic | Producer | Consumer | Schema |
|-------|----------|----------|--------|
| `trades.raw` | Ingestors (rpc, rtds, subgraph) | ClickHouse Kafka engine, strategy runner | NormalizedTrade JSON |
| `pending.signal` | PendingBlockIngestor | Strategies with `subscribe_pending=true` | NormalizedTrade JSON (NOT persisted to CH) |
| `orderbooks.raw` | CLOBOrderbookIngestor | ClickHouse Kafka engine, strategy runner | `{condition_id, asset_id, best_bid, best_ask, timestamp}` |
| `markets.events` | CLOBOrderbookIngestor, RPCIngestor (resolution) | MarketEventsConsumer (both app.py + strategy CLI) | `{type, condition_id, payload, timestamp}` |
| `pipeline.status` | All ingestors (heartbeat every 10s) | QualityChecker | `{source, event, trade_count, drops_queue_full, ts, ...}` |

## Ingestors (ingestors/)

All extend `BaseIngestor` ABC (heartbeat, counters, circuit breaker).

| Ingestor | Source | Latency | Identity | Topic | Version |
|----------|--------|---------|----------|-------|---------|
| `RPCIngestor` | Polygon RPC logs + resolution | T+3.7s | maker + taker | trades.raw + markets.events | 2 |
| `RTDSIngestor` | RTDS WS pool (redundant) | T+4.2s | proxyWallet | trades.raw | 1 |
| `PendingBlockIngestor` | Free RPC pending block poll | T+0.2s | full calldata | pending.signal | 0 |
| `CLOBOrderbookIngestor` | CLOB WS firehose + targeted | T+0.0s | none (prices) | orderbooks.raw + markets.events | — |
| `MempoolIngestor` | Rust PyO3 devp2p (optional) | T-1s | full calldata | mempool.raw | 0 |
| `SubgraphPoller` | Goldsky GraphQL (gap recovery) | minutes | maker + taker | trades.raw | 2 |

Note: `alchemy.py` is a backward-compat shim re-exporting `RPCIngestor`. Source name stays `"alchemy"` for CH/Kafka compatibility.

### Key Gotchas

- **RPC `blockTimestamp` is hex-encoded** — must `int(..., 16)` before use
- **RTDS has two timestamps** — `payload["timestamp"]` (trade time) vs top-level (delivery time ~500ms later). Use payload.
- **PendingBlock prices can exceed [0, 1]** — clamp after arithmetic
- **CLOB firehose needs `custom_feature_enabled: true`** in WS subscription
- **Stale timeouts**: RPC + CLOB firehose force reconnect after 120s silence
- **Mempool is optional** — ImportError gracefully handled, ingestor just returns
- **Taker dedup crucial** — ~40.5% of on-chain events are taker duplicates

### Dedup Strategies

| Ingestor | Dedup | Scope | Eviction |
|----------|-------|-------|----------|
| RPC | Taker filter (normalizer) | Per-message | — |
| RTDS | TradeDedup (OrderedDict) | Cross-connection | 5 min TTL |
| PendingBlock | _LRUSet (OrderedDict) | Cross-endpoint | 10K max LRU |
| CLOB Orderbook | Overwrite (latest wins) | Per-snapshot | — |
| Subgraph | `taker_not_in` query filter | Per-query | — |

## Consumers (consumers/)

### MarketEventsConsumer

Debounced pool refresh on market resolution events.

```
market_resolved event → PG upsert → schedule_refresh()
                                         │
                                    cancel existing timer
                                    start 5s debounce timer
                                         │
                                    runner.request_refresh()
```

- Two consumer groups: `market-events` (main pipeline, PG upserts), `strategy-market-events` (strategy CLI, pool refresh only)
- `pg_pool=None` in strategy CLI — PG updates handled by main pipeline

### ResolutionPoller

Background task polling CLOB API for resolution of markets with filled intents. Compensates for broken CLOB WS `market_resolved` firehose. Default interval: 60s.

## Normalizers (normalizers/)

| Normalizer | Input | Output Source | Trade ID | Version |
|---|---|---|---|---|
| `PolygonRPCNormalizer` | RPC log (topics + data) | `ALCHEMY` | `chain:` | 2 |
| `RTDSNormalizer` (in main normalizers/) | JSON msg payload | `RTDS` | `ws:` | 1 |
| `PendingBlockNormalizer` | Raw tx calldata | `PENDING_BLOCK` | `pending:` | 0 |
| `SubgraphNormalizer` | GraphQL response | `GOLDSKY_SUBGRAPH` | `chain:` | 2 |
| `MempoolNormalizer` | Rust dict | `MEMPOOL` | `mempool:` | 0 |

## Settings (settings.py)

Pydantic Settings with `PM_` env prefix. Key groups:

| Group | Settings |
|-------|----------|
| Redpanda | `redpanda_url` |
| RPC | `rpc_ws_url` (accepts legacy `PM_ALCHEMY_WS_URL`), `resolution_rpc_enabled` |
| ClickHouse | `ch_host`, `ch_port`, `ch_database`, `ch_batch_size`, `ch_flush_interval_s` |
| PostgreSQL | `pg_dsn` |
| CLOB WS | `clob_orderbook_enabled`, `clob_orderbook_ws_url`, `clob_markets_events_topic` |
| CLOB API | `clob_api_url`, `clob_api_key`, `clob_api_secret`, `clob_api_passphrase` |
| Pending Block | `pending_block_enabled`, `pending_block_rpc_ws_urls`, `pending_block_poll_interval_s` |
| Quality | `quality_initial_delay_s`, `quality_check_interval_s`, `source_liveness_timeout_s` |
| Protection | `max_position_usd`, `max_total_exposure_usd`, `degraded_grace_s` |

## Schema (schema.py)

ClickHouse DDL for Kafka engine integration:

| Table/View | Engine | Purpose |
|------------|--------|---------|
| `trades_kafka` | Kafka (4 consumers) | Consumes `trades.raw` topic |
| `trades_raw` | ReplacingMergeTree(_version) | Deduplicated trade storage |
| `trades_kafka_mv` | MV | trades_kafka → trades_raw |
| `orderbook_snapshots` | ReplacingMergeTree | Best bid/ask, 7-day TTL |
| `trader_volumes` | SummingMergeTree | Per-trader maker/taker volumes |
| `trader_trade_agg` | SummingMergeTree | Per-(trader, cid, asset) aggregates |
| `trader_market_positions` | SummingMergeTree (chained) | Per-(trader, cid) net_yes/net_no |
| `markets_resolved` | VIEW | JOIN markets (PG engine) + token_market_map |
| `trader_positions_resolved` | VIEW | Position classification + resolution correctness |

**ClickHouse gotcha**: `FROM table FINAL AS alias` is **invalid** in v24.8. Use `FROM (SELECT * FROM table FINAL) alias`.

## Quality State Machine

```
CHECKING → READY → DEGRADED → RED → CLOSING → SAFE_STOP
```

- Terminal states (CLOSING, SAFE_STOP) are sticky
- Shortest grace period among failing checks determines RED transition
- `QualityChecker` runs 5 health checks (source liveness, volume, dedup, metadata, resolution)
- `protection.py` auto-closes positions on RED via `panic_close_all()`

## Lifecycle

**No ingestor restart on crash.** Quality checker detects stale heartbeats → DEGRADED → RED → auto-protect. This prevents cascade failures. Fix root cause, then restart `pm-live`.
