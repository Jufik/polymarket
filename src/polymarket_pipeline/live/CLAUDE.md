# live/ — Live Sync Pipeline

FastStream + Redpanda-based live ingestion pipeline.

## Entry Points

- `pm-live` → `app.py` — Main pipeline (ingestors + CH storage + market events)
- `pm-strategy run --config X.toml` → `cli/strategy.py` — Strategy runner (consumes from Kafka)

## Kafka Topics

| Topic | Producer | Consumer | Schema |
|-------|----------|----------|--------|
| `trades.raw` | Ingestors (rpc, rtds, pending_block) | ClickHouse Kafka engine, strategy runner | NormalizedTrade JSON |
| `pending.signal` | PendingBlockIngestor | Strategies with `subscribe_pending=true` | NormalizedTrade JSON |
| `orderbooks.raw` | CLOBOrderbookIngestor | ClickHouse Kafka engine, strategy runner | `{condition_id, asset_id, best_bid, best_ask, timestamp}` |
| `markets.events` | CLOBOrderbookIngestor, RPCIngestor (resolution) | MarketEventsConsumer (both app.py + strategy CLI) | `{type, condition_id, payload, timestamp}` |
| `pipeline.status` | All ingestors (heartbeat) | QualityChecker | `{source, status, timestamp}` |

## Ingestors (ingestors/)

All extend `BaseIngestor` ABC (heartbeat, counters, circuit breaker).

| Ingestor | Source | Latency | Identity |
|----------|--------|---------|----------|
| `RPCIngestor` | Polygon RPC logs + on-chain resolution | T+3.7s (trades), T+3s (resolution) | maker + taker |
| `RTDSIngestor` | RTDS WS pool | T+4.2s | proxyWallet (taker/maker) |
| `PendingBlockIngestor` | RPC pending block poll | T+0.2s | from/to only |
| `CLOBOrderbookIngestor` | CLOB WS (120s stale timeout) | T+0.0s | none (prices only) |
| `SubgraphIngestor` | Goldsky subgraph | recovery only | maker + taker |

Note: `AlchemyIngestor` in `alchemy.py` is a backward-compat shim that re-exports `RPCIngestor`.
The `source_name` remains `"alchemy"` for backward compatibility with stored data in CH/Kafka.

### CLOBOrderbookIngestor

Routes messages by type:
- `price_change` → `orderbooks.raw` topic
- `market_resolved` / `new_market` → `markets.events` topic (requires `custom_feature_enabled: true` in WS subscription)

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

ClickHouse DDL for Kafka engine integration. Key tables:

| Table/View | Engine | Purpose |
|------------|--------|---------|
| `trades_kafka` | Kafka | Consumes from `trades.raw` topic |
| `trades_raw` | ReplacingMergeTree(_version) | Deduplicated trade storage |
| `trades_kafka_mv` | MV | trades_kafka → trades_raw |
| `orderbook_snapshots` | ReplacingMergeTree | Best bid/ask, 7-day TTL |
| `trader_volumes` | SummingMergeTree | Per-trader maker/taker volumes |
| `trader_trade_agg` | SummingMergeTree | Per-(trader, condition_id, asset_id) aggregates |
| `markets_resolved` | VIEW | JOIN markets (PG engine) + token_market_map |

## Quality State Machine

```
CHECKING → READY → DEGRADED → RED
    ↑         ↓        ↓
    └─────────┴────────┘ (recovery)
```

- `QualityChecker` runs periodic health checks
- `DEGRADED` + `RED` trigger `ExecutionGateway` to reject intents
- `protection.py` auto-closes positions on RED state
