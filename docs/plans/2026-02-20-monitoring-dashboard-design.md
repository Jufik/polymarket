# Monitoring Dashboard Design

## Goal

Browser-based monitoring dashboard embedded in the live FastStream pipeline app.
Shows producer/consumer health and data quality gap metrics via a single HTML endpoint.

## Approach

Mount a Starlette route into the existing FastStream app via `app.as_asgi(asgi_routes=...)`.
No new processes, no JS frameworks. Auto-refreshes every 5s via `<meta http-equiv="refresh">`.

## Data Flow

```
Browser --GET /dashboard--> Starlette route (dashboard.py)
                              |
                              +-- reads QualityChecker in-memory state
                              |   (heartbeats, check results, pipeline state)
                              +-- queries ClickHouse for gap metrics
                              |   (latency gap, coverage gap)
                              +-- renders server-side HTML
```

## Dashboard Sections

### 1. Pipeline State Banner

Colored banner: green (READY), yellow (CHECKING), red (DEGRADED).
Shows time since last quality check.

### 2. Producer Health Table

| Source | Last Heartbeat | Status |
|--------|---------------|--------|
| RTDS | 2s ago | OK |
| Alchemy | 5s ago | OK |
| Subgraph | (recovery only) | idle |

### 3. Quality Checks Table

Shows all 5 existing checks with status and detail string.

### 4. Data Quality Gap Metrics

**Latency gap** (new query): Median/max delay between RTDS (version=1) and Alchemy (version=2)
for the same trade_id over the last hour.

```sql
SELECT
    median(t2.timestamp - t1.timestamp) AS median_latency_s,
    max(t2.timestamp - t1.timestamp) AS max_latency_s
FROM trades_raw t1
JOIN trades_raw t2 USING (trade_id)
WHERE t1._version = 1 AND t2._version = 2
  AND t1.timestamp > now() - INTERVAL 1 HOUR
```

**Coverage gap** (new query): Trades seen by only one source in the last hour.

```sql
SELECT
    countIf(_version = 1 AND trade_id NOT IN (
        SELECT trade_id FROM trades_raw WHERE _version = 2 AND timestamp > now() - INTERVAL 1 HOUR
    )) AS rtds_only,
    countIf(_version = 2 AND trade_id NOT IN (
        SELECT trade_id FROM trades_raw WHERE _version = 1 AND timestamp > now() - INTERVAL 1 HOUR
    )) AS alchemy_only,
    count() AS total
FROM trades_raw
WHERE timestamp > now() - INTERVAL 1 HOUR
```

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `live/dashboard.py` | CREATE | Starlette route + HTML rendering + gap queries |
| `live/app.py` | MODIFY | Use `app.as_asgi()` with dashboard route |
| `cli/live.py` | MODIFY | Run via uvicorn (as_asgi returns ASGI app) |
| `live/settings.py` | MODIFY | Add `dashboard_port` and `dashboard_refresh_s` |

## Scope Exclusions

- No persistent metrics (no Prometheus/Grafana)
- No alerting
- No authentication (local/VPN use only)
- No WebSocket/SSE (meta refresh only)
