# Industrialization Design

**Date:** 2026-02-24
**Status:** Approved
**Approach:** Vertical Slices (incremental value delivery)
**Target:** Single VPS / bare metal (home network), Docker Compose

## Context

Full codebase audit identified the pipeline as ~70% production-ready. Strong domain modeling and dedup architecture, but critical gaps in: hardcoded network addresses, missing connection lifecycle management, no CI/CD, live pipeline resilience issues, and no execution safety mechanisms.

Key requirements from brainstorming:
- **Self-protecting:** automatically close positions when quality degrades (not just alert)
- **Incremental:** fix critical bugs first, go live small, add safety in parallel
- **Execution exists but is mocked:** need real CLOB submission + panic close
- **UI/API as primary control plane:** React/Next.js SPA + FastAPI backend (separate process from pipeline)

## Architecture After Industrialization

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Pipeline    │────>│  Redpanda    │────>│  ClickHouse  │
│  (FastStream)│     │              │     └──────────────┘
│              │────>│ pipeline.*   │
└─────────────┘     │  (metrics,   │     ┌──────────────┐
                    │   quality)   │────>│  FastAPI      │──> /metrics (Prometheus)
                    └──────────────┘     │  (port 8000)  │──> /api/* (REST)
                                        │              │──> /api/panic
┌─────────────┐                         │              │
│  Next.js UI  │<───────────────────────>│  CLOB API    │───> Polymarket
│  (port 3000) │                         └──────┬───────┘
└─────────────┘                                 │
                                         ┌──────┴───────┐
┌─────────────┐                         │  PostgreSQL  │
│  pm-panic    │───> CLOB API directly   │  (positions, │
│  (CLI)       │                         │   fills,     │
└─────────────┘                         │   metadata)  │
                                        └──────────────┘
┌──────────────┐
│   Grafana    │<── Prometheus ──< FastAPI /metrics
│  (port 3001) │
└──────────────┘
```

**Key separation:** Pipeline (ingest + publish) and API (read state + execute orders) are independent processes. Pipeline crash doesn't kill execution. API crash doesn't kill ingestion. `pm-panic` CLI bypasses both.

## Slice 1: "Safe to Run"

**Goal:** Fix all critical bugs. Containerize the app. Add minimal health checks and CI. After this, `docker compose up -d` starts everything reliably.

### Changes

| # | What | Where |
|---|------|-------|
| 1.1 | Replace hardcoded `192.168.0.148` with `localhost` defaults | `sinks/clickhouse.py`, `cli/backfill.py`, `exploration/data.py`, `exploration/tracking.py` |
| 1.2 | Reconcile ClickHouse schema: add `published_at Float64 DEFAULT 0` to init.sql | `docker/clickhouse/init.sql` |
| 1.3 | Fix `ch.execute()` → `ch.query()` bug | `live/app.py:76-79` |
| 1.4 | Add ClickHouseSink lifecycle: `close()`, `__aenter__/__aexit__`, reconnect-on-stale | `sinks/clickhouse.py` |
| 1.5 | Add `asyncio.timeout(5.0)` on all `broker.publish()` calls + log on timeout | All 6 ingestors |
| 1.6 | Add `maxsize=1000` to PendingBlockIngestor queue + drop-oldest on full | `live/ingestors/pending_block.py` |
| 1.7 | Wrap pending block decode loop in try/except, skip bad tx | `live/ingestors/pending_block.py:177-192` |
| 1.8 | Add `asyncio.timeout(30)` on Subgraph GQL queries | `live/ingestors/subgraph.py:162` |
| 1.9 | Make startup recovery non-blocking: `asyncio.timeout(300)` + background task | `live/app.py:100-110` |
| 1.10 | Create `Dockerfile` (multi-stage: uv sync → slim runtime) | New file |
| 1.11 | Add `polymarket-app` service to docker-compose with health check | `docker-compose.yml` |
| 1.12 | Add `/health/live` and `/health/ready` HTTP endpoints | `live/app.py` or `live/dashboard.py` |
| 1.13 | GitHub Actions CI: ruff check, mypy --strict, pytest (unit only) | `.github/workflows/ci.yml` |

## Slice 2: "Safe to Trade Small"

**Goal:** Real order execution. Position tracking. Web UI as primary control plane. CLI kill switch as fallback. After this, can run with real small positions under manual oversight.

### Changes

| # | What | Where |
|---|------|-------|
| 2.1 | CLOB API order client — submit limit/market orders, cancel, query balances | New: `execution/clob_client.py` |
| 2.2 | Position tracker — fill-based, per-market (side, size, avg entry, unrealized PnL) | New: `execution/position_tracker.py` |
| 2.3 | Position + fills persistence in PostgreSQL | New tables: `positions`, `fills` |
| 2.4 | Wire strategy signals → real execution (replace mock executor) | `strategies/execution/live.py` |
| 2.5 | FastAPI service — REST API for positions, strategies, order history, panic | New: `api/` module, own process |
| 2.6 | Next.js UI — positions table, strategy status, order history, panic button | New: `ui/` directory |
| 2.7 | `pm-panic` CLI — standalone kill switch, works if API/pipeline are dead | New: `cli/panic.py` |
| 2.8 | Position size limits — max per market, max total exposure | `PM_MAX_POSITION_USD`, `PM_MAX_TOTAL_EXPOSURE_USD` |
| 2.9 | Drop `live/dashboard.py` — replaced by API + UI | Delete old dashboard |
| 2.10 | Add `polymarket-api` and `polymarket-ui` to docker-compose | `docker-compose.yml` |

### Design decisions
- Position tracker is **fill-based** (reconstructed from fills table), not balance-based.
- `pm-panic` connects to CLOB API directly — no dependency on FastAPI being up.
- Size limits enforced at executor level, not strategy level.
- Panic logic in shared `execution/panic.py` — used by CLI, API, and auto-trigger (Slice 3).

## Slice 3: "Self-Protecting"

**Goal:** System automatically protects capital when quality degrades. After this, can walk away from the screen.

### Changes

| # | What | Where |
|---|------|-------|
| 3.1 | Extend ReadinessState with configurable thresholds | `live/quality/state.py` |
| 3.2 | On DEGRADED: stop opening new positions, log warning | `execution/live.py` + API |
| 3.3 | On RED: trigger automatic position close (reuses panic logic) | `execution/panic.py` |
| 3.4 | Fix state machine: emit `caught_up` after recovery or first heartbeat | `live/app.py` |
| 3.5 | Anti-flap: grace period before escalating (DEGRADED 60s → RED) | `PM_DEGRADED_GRACE_S`, `PM_RED_GRACE_S` |
| 3.6 | Position-aware shutdown: close positions before cancelling ingestors | `live/app.py` |
| 3.7 | Pipeline → API quality bridge via Redpanda topic | New topic: `pipeline.quality` |
| 3.8 | UI: real-time quality state + auto-panic event log | `ui/` components |

### State machine

```
INITIALIZING ──> CHECKING ──> READY ──> DEGRADED ──(grace)──> RED
                                 ^          │                    │
                                 └──────────┘                    │
                              (checks pass)        (auto-panic)  │
                                                                 v
                                                            CLOSING
                                                                 │
                                                            (positions closed)
                                                                 v
                                                            SAFE_STOP
```

## Slice 4: "Observable"

**Goal:** Full visibility without reading logs. Metrics, dashboards, real-time charts in the UI.

### Changes

| # | What | Where |
|---|------|-------|
| 4.1 | Prometheus client on FastAPI `/metrics` endpoint | `api/metrics.py` |
| 4.2 | Pipeline metrics via Redpanda topic `pipeline.metrics` | Ingestors + quality |
| 4.3 | Key metrics: trades/sec, publish latency, queue depth, enrichment ratio, dedup hit rate | All ingestors |
| 4.4 | Execution metrics: position count, exposure, unrealized PnL, fill rate, order latency | `execution/` |
| 4.5 | Normalization drop counters | All normalizers |
| 4.6 | Grafana + provisioned dashboard in docker-compose | `docker/grafana/` |
| 4.7 | ClickHouse Kafka engine lag monitoring | `live/quality/checker.py` |
| 4.8 | Structured log sink to file with rotation | Structlog config |
| 4.9 | UI: real-time charts (trades/sec, PnL, liveness, latency) | `ui/` components |

### Metrics flow

Pipeline publishes to `pipeline.metrics` topic. API consumes and exposes via `/metrics` (Prometheus) and `/api/metrics` (UI). Single Prometheus scrape target.

## Slice 5: "Resilient"

**Goal:** Long-term unattended operation. Edge cases handled, schema changes safe, codebase cleaned up.

### Changes

| # | What | Where |
|---|------|-------|
| 5.1 | Circuit breaker on broker publish — shared state, auto-reset | New: `live/circuit_breaker.py` |
| 5.2 | Graceful shutdown with drain — gather with 10s timeout | `live/app.py` |
| 5.3 | Alembic for PostgreSQL migrations + `pm-migrate` CLI | New: `alembic/`, `cli/migrate.py` |
| 5.4 | ClickHouse migration table + sequential `.sql` files | `docker/clickhouse/migrations/` |
| 5.5 | Backpressure: bounded async queue between WS read and Kafka publish (all ingestors) | All ingestors |
| 5.6 | Time-based dedup eviction (TTL) instead of size-based | `live/ingestors/rtds.py` |
| 5.7 | Fix `RedpandaSink.write()` async issue | `live/ingestors/subgraph.py` |
| 5.8 | Fix trade ID divergence — fast path calls `make_trade_id_chain()` | `loaders/parquet.py` |
| 5.9 | Fix `load_strategy_configs` `.pop()` → `.get()` | `strategies/config.py` |
| 5.10 | Implement or remove placeholder quality checks | `live/quality/checker.py` |

## Out of Scope

- Kubernetes / multi-VPS / horizontal scaling (revisit if profitable)
- External secrets provider (Vault, AWS SM) — `.env` files sufficient for home network
- Multi-region deployment
- Mobile app

## Go-Live Gates

| Slice | Gate |
|-------|------|
| 1 | Pipeline runs reliably via Docker Compose, CI passes |
| 2 | **Can trade with manual oversight** |
| 3 | **Can walk away from screen** |
| 4 | Full visibility into system state |
| 5 | Long-term unattended operation |
