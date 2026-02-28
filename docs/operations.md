# Operations & CLI Reference

Pipeline lifecycle management, quality monitoring, auto-protection, and all CLI entry points.

---

## Live Pipeline Lifecycle

### Startup Sequence (`pm-live`)

```
1. Load token_map from PostgreSQL
2. Spawn check_and_recover (non-blocking gap detection)
3. Apply ClickHouse schema (Kafka engine tables, idempotent)
4. Create QualityChecker
5. Create MarketEventsConsumer (debounced pool refresh)
6. Create ResolutionPoller (CLOB API periodic resolution check)
7. Spawn periodic_quality_check
8. Spawn all ingestors as background tasks:
   ├── RTDSIngestor → trades.raw
   ├── RPCIngestor → trades.raw + markets.events
   ├── MempoolIngestor (optional) → mempool.raw
   ├── PendingBlockIngestor (optional) → pending.signal
   └── CLOBOrderbookIngestor (optional) → orderbooks.raw + markets.events
9. Spawn periodic_token_map_refresh (every token_map_refresh_interval_s)
10. Spawn supervise_tasks (watches for crashes)
```

### Shutdown Sequence

```
1. Call auto_protect (close all positions if not already SAFE_STOP)
2. Cancel all ingestor tasks
3. Wait up to 10s for in-flight work (graceful drain)
4. Force-cancel remaining tasks
```

### Health Endpoints (ASGI)

| Path | Response |
|---|---|
| `/health/live` | 200 always (liveness probe) |
| `/health/ready` | 200 if READY, 503 otherwise (readiness probe) |
| `/dashboard` | HTML dashboard (auto-refresh every 5s) |

---

## Quality Gate

### State Machine

```
CHECKING ──[all checks pass]──> READY
    │                              │
    │                       [some checks fail]
    │                              │
    │                              ▼
    │                         DEGRADED
    │                              │
    │                   [shortest grace elapsed]
    │                              │
    │                              ▼
    └──────────────────────────── RED
                                   │
                           [auto_protect()]
                                   │
                                   ▼
                               CLOSING
                                   │
                           [all positions closed]
                                   │
                                   ▼
                              SAFE_STOP (terminal)
```

**Terminal states** (CLOSING, SAFE_STOP) are **sticky** — once entered, they never transition back.

### Health Checks

| Check | Grace Period | What It Tests |
|---|---|---|
| `source_liveness` | 120s | Required sources (rtds, alchemy) have recent heartbeats |
| `volume_reconciliation` | 600s | Current hour trades vs 24h hourly average (>= 10%) |
| `dedup_sanity` | 300s | Version=2 / Version=1 ratio in last hour (>= 80%) |
| `metadata_freshness` | 900s | token_market_map updated in last 2h |
| `resolved_completeness` | 600s | Resolved markets in PG match trades in CH (>= 90%) |

The **shortest grace period** among failing checks determines the RED transition time.

### Auto-Protection

Triggered when pipeline transitions to RED state:
1. Module-level `asyncio.Lock` prevents re-entrancy
2. Sets state to CLOSING
3. Creates ClobClient + asyncpg pool + PositionTracker
4. Calls `panic_close_all(clob, tracker)`
5. Sets state to SAFE_STOP if all positions closed successfully

Called from:
- `periodic_quality_check` loop (every check interval)
- `handle_status` on caught_up event (if RED)
- `on_shutdown`

---

## Token Map Refresh

**Purpose**: Detect new markets without restarting the pipeline.

- First iteration: runs immediately at startup (non-blocking background)
- Subsequent: every `token_map_refresh_interval_s` (default 300s)
- Queries Gamma API for **open markets only** (~8K events vs 450K+ total — much cheaper)
- Upserts to PostgreSQL in FK order (events → tags → event_tags → markets → token_map)
- Reloads full token_map into shared dict (all ingestors see new markets immediately)
- No pipeline restart required

---

## Gap Detection & Recovery

During startup, `check_and_recover()`:
1. Checks PostgreSQL for active recovery job (don't interfere with `pm-recover`)
2. Queries ClickHouse for `max(timestamp)` in trades_raw
3. If gap > `gap_threshold_s` (default 600s = 10 min), spawns SubgraphPoller
4. Recovery runs with `recovery_timeout_s` (default 300s = 5 min)
5. Publishes `caught_up` event to `pipeline.status` when complete

---

## CLI Commands

### `pm-live` — Start Live Pipeline

```bash
uv run pm-live
```

Starts FastStream (Kafka consumers) + ASGI (health/dashboard) via uvicorn. Port from `PM_DASHBOARD_PORT` (default 8099).

### `pm-strategy run` — Run Strategies

```bash
uv run pm-strategy run --config configs/my_strategy.toml
uv run pm-strategy run --config configs/my_strategy.toml --only my_strategy
uv run pm-strategy run --config configs/my_strategy.toml --log-dir logs/paper/ --verbose
```

Subscribes to Kafka topics, dispatches trades to strategies, logs intents to PostgreSQL + JSONL.

**SIGUSR1**: Resets paper state (positions, budgets, counters) without restart.

### `pm-strategy reset` — Clear Paper State

```bash
uv run pm-strategy reset --log-dir logs/paper/
uv run pm-strategy reset --log-dir logs/paper/ --yes  # skip confirmation
```

Truncates PostgreSQL tables (strategy_intents, fills, positions), deletes JSONL files, recreates Kafka `strategy.intents` topic.

### `pm-backfill` — Historical Parquet → ClickHouse

```bash
uv run pm-backfill --parquet-dir order_filled/
uv run pm-backfill --compact --compact-dir data/compact/   # pre-normalized mode
uv run pm-backfill --no-market-sync                         # skip PG metadata sync
```

**Standard mode**: ProcessPoolExecutor (fork on Linux, spawn on macOS) + CH semaphore.
**Compact mode**: Constant-memory streaming via `iter_row_groups_arrow()`.

### `pm-sync` — Metadata Sync

```bash
uv run pm-sync
uv run pm-sync --force   # re-fetch even if < 24h old
```

Fetches CLOB API + Gamma API → PostgreSQL + Parquet. 24h freshness gate prevents API hammering.

### `pm-recover` — Subgraph Gap Recovery

```bash
uv run pm-recover
uv run pm-recover --from-timestamp 1709136000
uv run pm-recover --from-parquet order_filled/
uv run pm-recover --redpanda                    # write to Kafka instead of direct CH
uv run pm-recover --fresh                       # ignore active recovery job
```

**Resumable**: PostgreSQL tracks cursor. If killed mid-run, next `pm-recover` picks up where it left off.

### `pm-compact` — Recompress Raw Parquet

```bash
uv run pm-compact
uv run pm-compact --global-sort   # Pass 2: globally sort for range queries
```

Two-pass: recompress (parallel), then optional global sort (single-threaded).

### `pm-load` — Compact Parquet → ClickHouse

```bash
uv run pm-load
uv run pm-load --compact-dir data/compact/
```

Streaming mode, constant memory. No token_map needed (data pre-normalized).

### `pm-build` — Full Data Build Pipeline

```bash
uv run pm-build                    # all steps
uv run pm-build --step sync        # metadata only
uv run pm-build --step derived     # Polars derived tables only
uv run pm-build --step prices      # market price timeseries
uv run pm-build --force-metadata   # re-fetch even if fresh
```

Orchestrates: sync → compact → load → derived → prices.

### `pm-migrate` — ClickHouse Migrations

```bash
uv run pm-migrate
```

Thin wrapper around Alembic `upgrade head`.

### `pm-panic` — Emergency Close All Positions

```bash
uv run pm-panic
```

Creates ClobClient + PositionTracker, calls `panic_close_all()`. Exits with code 1 if any close failed.

### `pm-api` — FastAPI REST API

```bash
uv run pm-api
```

Serves on port 8001. Routers: health, positions, intents, analytics, panic, quality, markets, pnl, metrics.

### `pm-bridge` — JSON Bridge (TypeScript → Python)

```bash
python -m polymarket_pipeline.cli.bridge \
  --module polymarket_pipeline.cli.bridge \
  --func read_parquet \
  --args '{"path": "data/derived/trader_market_pnl.parquet", "n_rows": 10}'
```

JSON-in/JSON-out dispatcher for subprocess calls.

---

## Monitoring Dashboard

**File**: `src/polymarket_pipeline/live/dashboard.py`

HTML dashboard served at `/dashboard` with auto-refresh. Displays:

- **Producer table**: Per-source heartbeat age + status indicator
- **Check table**: QualityChecker results per check
- **TPS chart**: Trades/sec by source (last 1h) + delivery lag
- **Waterfall chart**: Trade lifecycle timing (first_seen → consolidated)
- **Race metrics**: RTDS vs RPC who publishes first, median/max delta
- **Coverage gaps**: RTDS-only vs RPC-only trades

Dashboard queries ClickHouse via thread pool (sync client). Uses Chart.js + Tailwind dark theme.

---

## Docker Services

| Service | Image | Port(s) | Purpose |
|---|---|---|---|
| clickhouse | clickhouse/clickhouse-server:24.8 | 18123 (HTTP), 19000 (Native) | Trade OLAP |
| ch-ui | ghcr.io/caioricciuti/ch-ui | 15521 | ClickHouse web UI |
| postgres | postgres:16-alpine | 15432 | Metadata + position tracking |
| redpanda | redpandadata/redpanda:v24.3.1 | 19092 (Kafka), 18082 (Proxy) | Event streaming |
| redpanda-console | redpandadata/console:v2.8.0 | 18080 | Redpanda web UI |
| mlflow | ghcr.io/mlflow/mlflow:v2.19.0 | 5050 | ML experiment tracking |
| loki | grafana/loki:3.4.2 | 3100 | Log aggregation |
| promtail | grafana/promtail:3.4.2 | — | Log shipper |
| prometheus | prom/prometheus:latest | 9090 | Metrics scraping |
| grafana | grafana/grafana:latest | 3001 | Dashboards |
| metabase | metabase/metabase:latest | 3030 | BI tool |

### Environment Variables

All settings use `PM_` prefix. Key variables:

```bash
PM_RPC_WS_URL=wss://polygon-bor-rpc.publicnode.com
PM_PG_DSN=postgresql://polymarket:polymarket@localhost:15432/polymarket
PM_REDPANDA_URL=localhost:19092
PM_CH_HOST=localhost
PM_CH_PORT=18123
PM_CH_DATABASE=polymarket
PM_DASHBOARD_PORT=8099
PM_CLOB_API_KEY=...
PM_CLOB_API_SECRET=...
PM_CLOB_API_PASSPHRASE=...
```

---

## Supervision & No-Restart Design

Ingestor crashes are **logged but NOT restarted**:
- `supervise_tasks()` detects task exceptions immediately
- QualityChecker detects stale heartbeats (> `source_liveness_timeout_s`)
- Pipeline transitions CHECKING → DEGRADED → RED
- Auto-protect closes all positions on RED
- This prevents cascade failures from restart loops

**Recovery path**: fix the root cause, then restart the entire `pm-live` process.
