# Live Sync Architecture Design

**Date**: 2026-02-20
**Status**: Approved
**Machine**: Local Ryzen 5990X / 128GB RAM

## Overview

A streaming data pipeline that ingests Polymarket trades from multiple sources, normalizes them into a canonical schema, deduplicates via ClickHouse ReplacingMergeTree, and fans out to independent consumers (live execution, dashboards, data refresh) through Redpanda.

## Architecture: Hub and Spoke (Redpanda-Centric)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    INGEST LAYER                                          │
│                                                                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────┐  │
│  │ RTDS Ingestor    │  │ Alchemy Ingestor │  │ Subgraph Poller      │  │
│  │ (< 1s latency)   │  │ (~2s latency)    │  │ (recovery only)      │  │
│  │ _version=1       │  │ _version=2       │  │ _version=2           │  │
│  └──────┬───────────┘  └──────┬───────────┘  └────────┬─────────────┘  │
│         │                      │                       │                 │
│         └──────────┬───────────┴───────────────────────┘                 │
│                    ▼                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │              Redpanda — Topic: trades.raw                       │    │
│  │         key=condition_id, 8 partitions, 7-day retention        │    │
│  └──────┬──────────┬──────────┬──────────┬────────────────────────┘    │
│         │          │          │          │                              │
│  ┌──────┴──────────┴──────────┴──────────┴────────────────────────┐    │
│  │              Redpanda — Topic: pipeline.status                  │    │
│  │         heartbeats, caught_up, ready/degraded signals          │    │
│  └──────┬──────────┬──────────────────────────────────────────────┘    │
└─────────┼──────────┼────────────────────────────────────────────────────┘
          │          │
┌─────────┼──────────┼────────────────────────────────────────────────────┐
│         ▼          ▼         FastStream App (Consumers)                  │
│                                                                          │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │ClickHouse │ │ Signal   │ │Dashboard │ │ Derived  │ │  Quality    │  │
│  │Kafka Eng. │ │Evaluator │ │Consumer  │ │Refresher │ │  Checker    │  │
│  │(MV, auto) │ │(copy     │ │(metrics, │ │(PnL,MVF  │ │(readiness   │  │
│  │→trades_raw│ │ trading) │ │ alerts)  │ │ refresh) │ │  gate)      │  │
│  └───────────┘ └──────────┘ └──────────┘ └──────────┘ └─────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘

SEPARATE PATH (one-time):
  Goldsky Parquet ──> backfill CLI ──> ClickHouse (direct insert)
```

## Data Sources

| Source | Role | Latency | Data Quality | When Active |
|--------|------|---------|--------------|-------------|
| **Goldsky Parquet** | Initial historical backfill | N/A (batch) | Full on-chain (_v=2) | Once at bootstrap |
| **RTDS WebSocket** | Fast path, immediate signal detection | < 1s | `proxyWallet` only (_v=1) | Always on |
| **Alchemy eth_subscribe** | Enrichment, full on-chain data | ~2s (block time) | Full maker/taker (_v=2) | Always on |
| **Goldsky Subgraph** | Gap recovery after outages | Seconds (polling) | Full on-chain (_v=2) | On-demand |

All four write to the same `trades.raw` Redpanda topic. ClickHouse `ReplacingMergeTree(_version)` ensures on-chain events (v=2) overwrite WS events (v=1) for the same `trade_id`.

## Ingestor Design

### RTDS Ingestor

- **Endpoint**: `wss://ws-live-data.polymarket.com`
- **Subscribe**: `{topic: "activity", type: "trades"}` (global firehose, no filter)
- **Heartbeat**: PING every 5s (required or connection drops)
- **Normalizer**: Existing `RTDSNormalizer` (reused from current codebase)
- **Output**: `NormalizedTrade` with `_version=1`, `source=rtds`
- **Reconnect**: Exponential backoff (1s, 2s, 4s, ..., capped at 60s)
- **Checkpoint**: `last_trade_timestamp` (Unix seconds)

### Alchemy eth_subscribe Ingestor

- **Endpoint**: `wss://polygon-mainnet.g.alchemy.com/v2/{KEY}`
- **Subscribe**: `eth_subscribe("logs", {address: [CTF_EXCHANGE, NEGRISK_EXCHANGE]})`
- **Decoding**: ABI-decode OrderFilled event from `topics` + `data` fields (~30 lines using `eth_abi`)
- **Dedup**: Drop taker-perspective duplicates (taker address == exchange contract)
- **Scaling**: Amounts use 1e6 (USDC 6 decimals), same as existing Parquet normalizer
- **Normalizer**: New `PolygonRPCNormalizer`
- **Output**: `NormalizedTrade` with `_version=2`, `source=alchemy`
- **Checkpoint**: `last_block_number` (hex)
- **Volume**: Current peak 543 trades/sec, well within Alchemy free tier capacity

### Goldsky Subgraph Recovery Poller

- **Endpoint**: `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn`
- **Query**: `orderFilledEvents(orderBy: timestamp, orderDirection: asc, first: 1000, where: {timestamp_gt: ...})`
- **Pagination**: Timestamp-based cursor with sticky mode for same-second batches (timestamp + id_gt)
- **Normalizer**: New `SubgraphNormalizer`
- **Output**: `NormalizedTrade` with `_version=2`, `source=goldsky_sink`
- **Lifecycle**: Runs until caught up to real-time, then stops
- **Trigger**: Automatic when gap > 10 minutes detected at startup; manual via CLI

### Connection Lifecycle & Checkpoints

Each ingestor tracks a checkpoint persisted to disk:

```
State per ingestor:
  rtds:       last_trade_timestamp (Unix seconds)
  alchemy:    last_block_number (hex)
  subgraph:   last_timestamp + last_id (cursor pair)
```

On startup:
1. Read checkpoints
2. If gap > 10 minutes → trigger subgraph recovery before going live
3. Start Alchemy eth_subscribe (from last block number)
4. Start RTDS WebSocket (global firehose)
5. Trigger quality check

## Data Quality & Readiness Gate

The quality checker is the **only** component that can authorize live trading. Ingestors saying "caught_up" is necessary but not sufficient.

### Checks

| Check | What | WARN Threshold | RED Threshold |
|-------|------|----------------|---------------|
| **A. Resolved Market Completeness** | Closed markets in metadata have trades in CH | N/A | Any closed market with 0 trades |
| **B. Volume Reconciliation** | Current hour vs trailing 24h average rate | < 50% of average | < 10% of average |
| **C. Source Liveness** | Last heartbeat from each ingestor | Any source > 30s silent | Both sources > 60s silent |
| **D. Metadata Freshness** | Gamma API last sync age; orphan asset_ids | Sync > 1h old | Orphan asset_ids in recent trades |
| **E. Dedup Sanity** | Ratio of _version=2 (enriched) to _version=1 (WS-only) | Enrichment < 80% | Enrichment < 50% |

### Triggers

- **Startup**: Full check (A through E)
- **On "caught_up" message**: Re-run checks
- **Periodic**: Every 15 minutes

### Readiness State Machine

```
                    ┌─────────┐
     startup ──────>│CHECKING │
                    └────┬────┘
                         │
              all pass?  │  any fail?
              ┌──────────┴──────────┐
              ▼                     ▼
         ┌────────┐          ┌──────────┐
         │ READY  │◄────────│ DEGRADED  │
         │(GREEN) │ recovery │  (RED)    │
         └────┬───┘ succeeds └─────┬────┘
              │                    │
              │  check fails       │  trigger:
              └───────────────────>│  - subgraph catch-up
                                   │  - metadata refresh
                                   │  - alert (structlog + webhook)
```

### Output

- **GREEN**: Publish `{event: "ready", checks: {...}, ts: ...}` to `pipeline.status`. Strategies may execute.
- **RED**: Publish `{event: "degraded", failures: [...]}` to `pipeline.status`. Strategies hold. Remediation triggered automatically.

## Redpanda Topics

| Topic | Key | Partitions | Retention | Producers | Consumers |
|-------|-----|------------|-----------|-----------|-----------|
| `trades.raw` | `condition_id` | 8 | 7 days | RTDS, Alchemy, Subgraph | ClickHouse (Kafka engine), Signal Evaluator, Dashboard, Derived Refresher |
| `pipeline.status` | `source` | 1 | 1 day | All ingestors, Quality Checker | Quality Checker, Signal Evaluator |

## ClickHouse Schema

### Kafka Engine Table (reads from Redpanda)

```sql
CREATE TABLE trades_kafka (
    trade_id        String,
    condition_id    String,
    asset_id        String,
    side            Enum8('BUY'=1, 'SELL'=2),
    price           Decimal64(4),
    size            Decimal64(6),
    amount_usd      Decimal64(2),
    fee_usd         Decimal64(2),
    maker           Nullable(String),
    taker           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    source          Enum8('goldsky_sink'=1, 'websocket'=2, 'rtds'=3, 'alchemy'=4),
    tx_hash         Nullable(String),
    order_hash      Nullable(String),
    block_number    Nullable(UInt64),
    is_backfill     UInt8,
    _version        UInt16
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'localhost:19092',
    kafka_topic_list = 'trades.raw',
    kafka_group_name = 'clickhouse',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 4;
```

### Storage Table (ReplacingMergeTree)

```sql
CREATE TABLE trades_raw (
    -- same columns as trades_kafka
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
PARTITION BY toYYYYMM(timestamp);
```

### Materialized View (auto-pipes Kafka → storage)

```sql
CREATE MATERIALIZED VIEW trades_kafka_mv TO trades_raw AS
SELECT * FROM trades_kafka;
```

## FastStream Application Structure

### Module Layout

```
src/polymarket_pipeline/
├── live/                          # All live pipeline code
│   ├── __init__.py
│   ├── app.py                     # FastStream app + lifespan hooks
│   ├── settings.py                # Pydantic Settings (env-based, PM_ prefix)
│   │
│   ├── ingestors/                 # Producers (publish to Redpanda)
│   │   ├── __init__.py
│   │   ├── rtds.py               # RTDS WS → RTDSNormalizer → publish
│   │   ├── alchemy.py            # eth_subscribe → PolygonRPCNormalizer → publish
│   │   └── subgraph.py           # GraphQL recovery → SubgraphNormalizer → publish
│   │
│   ├── consumers/                 # Subscribers (read from Redpanda)
│   │   ├── __init__.py
│   │   ├── signal_evaluator.py   # Copy trading signal detection
│   │   ├── dashboard.py          # Metrics + alerts
│   │   └── derived_refresher.py  # Periodic PnL/MVF recompute
│   │
│   ├── quality/                   # Data quality gate
│   │   ├── __init__.py
│   │   ├── checker.py            # Health checks A-E
│   │   └── state.py              # Readiness state machine
│   │
│   └── normalizers/
│       ├── __init__.py
│       ├── polygon_rpc.py        # ABI decode OrderFilled → NormalizedTrade
│       └── subgraph.py           # GraphQL JSON → NormalizedTrade
│
├── normalizers/                   # EXISTING (unchanged, backfill only)
│   ├── sink.py
│   ├── rtds.py                   # Reused by live/ingestors/rtds.py
│   └── market_ws.py
```

### Settings

```python
class Settings(BaseSettings):
    # Redpanda
    redpanda_url: str = "localhost:19092"

    # Alchemy
    alchemy_ws_url: str  # Required, no default (contains API key)

    # ClickHouse
    ch_host: str = "192.168.0.148"
    ch_port: int = 18123

    # PostgreSQL
    pg_dsn: str = "postgresql://polymarket:polymarket@192.168.0.148:15432/polymarket"

    # Quality thresholds
    quality_check_interval_s: int = 900
    source_liveness_timeout_s: int = 30
    volume_drop_warn_pct: float = 0.50
    volume_drop_red_pct: float = 0.10
    enrichment_ratio_min: float = 0.80

    # Recovery
    gap_threshold_s: int = 600

    model_config = SettingsConfigDict(env_prefix="PM_")
```

### Running

```bash
# Start infrastructure
docker compose up -d  # ClickHouse, PostgreSQL, Redpanda

# Backfill (one-time, existing CLI)
uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/

# Start live pipeline
uv run faststream run polymarket_pipeline.live.app:app

# Or via CLI wrapper
uv run python -m polymarket_pipeline.cli.live
```

## Startup & Recovery Flows

### Normal Startup (gap < 10 min)

1. Load settings, connect CH/PG
2. Read checkpoints → gap small
3. Start Alchemy eth_subscribe (from last block)
4. Start RTDS WebSocket
5. Quality checker: full check → GREEN → strategies active

### Startup After Short Outage (10 min < gap < 24h)

1. Load settings, connect CH/PG
2. Read checkpoints → gap detected
3. Start subgraph recovery (from last checkpoint)
4. Subgraph runs until caught up, then stops
5. Start Alchemy + RTDS
6. Quality checker: full check → GREEN → strategies active

### Startup After Long Outage (gap > 24h)

Same as short outage — subgraph handles any gap size. For multi-week gaps, subgraph will take longer but the cursor-based pagination handles it.

### Runtime Recovery (source drops while running)

- **One source drops**: Other source continues. Quality checker warns but stays GREEN (data still flowing via the surviving source).
- **Both drop**: Quality checker goes RED. Strategies hold. On reconnect, if gap > 10 min, subgraph recovery triggers. Re-check → GREEN → resume.

## Volume & Capacity

| Metric | Current (Feb 2026) | Alchemy Free Tier Limit |
|--------|-------------------|------------------------|
| Average rate | 57.6 trades/sec | N/A (pushed events) |
| Peak second | 543 trades/sec | ~1000 events/sec |
| Peak day | 5.9M trades | N/A |
| Monthly growth | +111% MoM | 30M compute units/month |
| Headroom | 1.84x | Plan migration by Q2 2026 if growth sustains |

## Key Design Decisions

1. **Backfill stays direct-insert** — No reason to route 438M historical rows through Redpanda.
2. **JSON serialization** — NormalizedTrade serializes via Pydantic `.model_dump_json()`. At 50 msg/s average, Avro/Protobuf adds complexity for negligible gain.
3. **Single trades.raw topic** — All sources converge. Partition by `condition_id` for locality.
4. **Quality checker as gate** — Strategies never execute without explicit GREEN from quality checker.
5. **Subgraph as recovery-only** — Not in the hot path. Only activates for gap fill.
6. **FastStream framework** — Decorator-based Kafka subscribers, Pydantic validation, dependency injection, lifespan hooks. Matches existing Pydantic v2 + async patterns in the codebase.

## Docker Compose Additions

Redpanda added to existing stack:

```yaml
services:
  redpanda:
    image: redpandadata/redpanda:v24.3.1
    command:
      - redpanda start
      - --smp 2
      - --memory 4G
      - --overprovisioned
      - --kafka-addr 0.0.0.0:19092
      - --advertise-kafka-addr localhost:19092
    ports:
      - "19092:19092"    # Kafka API
      - "18082:8082"     # Admin API
    volumes:
      - ./docker-drives/redpanda:/var/lib/redpanda/data
```

## Dependencies (new)

```
faststream[kafka]     # Framework + Kafka broker
eth-abi               # ABI decoding for Polygon RPC logs
websockets            # Already in use for RTDS
gql[aiohttp]          # GraphQL client for Goldsky Subgraph
```
