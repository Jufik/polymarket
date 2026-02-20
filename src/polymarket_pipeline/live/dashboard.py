"""Monitoring dashboard for the live pipeline."""

from __future__ import annotations

import json
import math
import time
from typing import TYPE_CHECKING, Any

import structlog
from faststream.asgi import AsgiResponse

log = structlog.get_logger()

if TYPE_CHECKING:
    from polymarket_pipeline.live.quality.checker import QualityChecker

# ── Gap queries ──────────────────────────────────────────────────────────

LATENCY_GAP_SQL = """\
SELECT
    count() AS matched,
    medianIf(
        dateDiff('second', t1_ts, t2_ts),
        1 = 1
    ) AS median_latency_s,
    maxIf(
        dateDiff('second', t1_ts, t2_ts),
        1 = 1
    ) AS max_latency_s
FROM (
    SELECT trade_id, timestamp AS t1_ts
    FROM trades_raw
    WHERE _version = 1
      AND timestamp > now() - INTERVAL 1 HOUR
) t1
JOIN (
    SELECT trade_id, timestamp AS t2_ts
    FROM trades_raw
    WHERE _version = 2
      AND timestamp > now() - INTERVAL 1 HOUR
) t2 USING (trade_id)
"""

COVERAGE_GAP_SQL = """\
SELECT
    countIf(v = 1 AND v2_cnt = 0) AS rtds_only,
    countIf(v = 2 AND v1_cnt = 0) AS alchemy_only,
    count()                        AS total
FROM (
    SELECT
        trade_id,
        _version AS v,
        countIf(_version = 2) OVER (PARTITION BY trade_id) AS v2_cnt,
        countIf(_version = 1) OVER (PARTITION BY trade_id) AS v1_cnt
    FROM trades_raw
    WHERE timestamp > now() - INTERVAL 1 HOUR
)
"""

# Per-minute TPS by source over the last hour
TPS_BY_SOURCE_SQL = """\
SELECT
    toStartOfMinute(timestamp) AS minute,
    source,
    count() / 60.0 AS tps
FROM trades_raw
WHERE timestamp > now() - INTERVAL 1 HOUR
GROUP BY minute, source
ORDER BY minute
"""

# Per-minute median latency gap (v1 vs v2) over the last hour
LATENCY_TIMESERIES_SQL = """\
SELECT
    toStartOfMinute(t1_ts) AS minute,
    median(dateDiff('second', t1_ts, t2_ts)) AS median_lat_s
FROM (
    SELECT trade_id, timestamp AS t1_ts
    FROM trades_raw
    WHERE _version = 1
      AND timestamp > now() - INTERVAL 1 HOUR
) t1
JOIN (
    SELECT trade_id, timestamp AS t2_ts
    FROM trades_raw
    WHERE _version = 2
      AND timestamp > now() - INTERVAL 1 HOUR
) t2 USING (trade_id)
GROUP BY minute
ORDER BY minute
"""


def _query_gap_metrics(checker: QualityChecker) -> dict[str, Any]:
    """Run latency and coverage gap queries against ClickHouse."""
    metrics: dict[str, Any] = {
        "median_latency_s": None,
        "max_latency_s": None,
        "rtds_only": 0,
        "alchemy_only": 0,
        "total": 0,
    }
    ch = checker.clickhouse
    try:
        rows = ch.query(LATENCY_GAP_SQL)
        if rows and rows[0].get("matched", 0) > 0:
            metrics["median_latency_s"] = rows[0].get("median_latency_s")
            metrics["max_latency_s"] = rows[0].get("max_latency_s")
    except Exception as exc:
        log.warning("dashboard.latency_query_failed", error=str(exc))

    try:
        rows = ch.query(COVERAGE_GAP_SQL)
        if rows:
            metrics["rtds_only"] = rows[0].get("rtds_only", 0)
            metrics["alchemy_only"] = rows[0].get("alchemy_only", 0)
            metrics["total"] = rows[0].get("total", 0)
    except Exception as exc:
        log.warning("dashboard.coverage_query_failed", error=str(exc))

    return metrics


def _query_chart_data(checker: QualityChecker) -> dict[str, Any]:
    """Query TPS timeseries by source and latency timeseries."""
    chart: dict[str, Any] = {"tps": {}, "latency": {}}
    ch = checker.clickhouse

    try:
        rows = ch.query(TPS_BY_SOURCE_SQL)
        for row in rows:
            minute = str(row["minute"])
            source = row["source"]
            tps = float(row["tps"])
            if source not in chart["tps"]:
                chart["tps"][source] = {}
            chart["tps"][source][minute] = round(tps, 2)
    except Exception as exc:
        log.warning("dashboard.tps_query_failed", error=str(exc))

    try:
        rows = ch.query(LATENCY_TIMESERIES_SQL)
        for row in rows:
            minute = str(row["minute"])
            val = float(row["median_lat_s"])
            if not math.isnan(val):
                chart["latency"][minute] = round(val, 1)
    except Exception as exc:
        log.warning(
            "dashboard.latency_ts_query_failed", error=str(exc)
        )

    return chart


def _fmt_age(ts: float | None) -> str:
    """Format a timestamp as 'Ns ago' relative to now."""
    if ts is None:
        return "never"
    age = time.time() - ts
    if age < 0:
        return "0s ago"
    if age < 60:
        return f"{age:.0f}s ago"
    if age < 3600:
        return f"{age / 60:.0f}m ago"
    return f"{age / 3600:.1f}h ago"


def _fmt_val(v: Any) -> str:
    if v is None:
        return "&mdash;"
    if isinstance(v, float):
        if math.isnan(v):
            return "&mdash;"
        return f"{v:.1f}s"
    return str(v)


def _status_dot(ok: bool) -> str:
    color = "#22c55e" if ok else "#ef4444"
    return f'<span style="color:{color}; font-size:1.4em;">&#9679;</span>'


def _state_color(state_val: str) -> str:
    return {
        "ready": "#22c55e",
        "degraded": "#ef4444",
        "checking": "#eab308",
    }.get(state_val, "#6b7280")


def _build_chart_js(chart_data: dict[str, Any]) -> str:
    """Build Chart.js initialization script for TPS + latency."""
    # Collect all unique minutes across all sources + latency
    all_minutes: set[str] = set()
    for source_data in chart_data["tps"].values():
        all_minutes.update(source_data.keys())
    all_minutes.update(chart_data["latency"].keys())

    if not all_minutes:
        return "/* no chart data */"

    labels = sorted(all_minutes)
    # Short label: HH:MM
    short_labels = []
    for lbl in labels:
        try:
            short_labels.append(lbl[11:16])  # "2026-02-20 16:35:00" → "16:35"
        except (IndexError, TypeError):
            short_labels.append(lbl)

    source_colors = {
        "rtds": "#38bdf8",
        "alchemy": "#a78bfa",
        "goldsky_sink": "#fb923c",
        "goldsky_subgraph": "#fbbf24",
        "websocket": "#34d399",
    }

    datasets = []
    for source, minute_data in sorted(chart_data["tps"].items()):
        color = source_colors.get(source, "#94a3b8")
        values = [minute_data.get(m, 0) for m in labels]
        datasets.append({
            "label": f"{source} TPS",
            "data": values,
            "borderColor": color,
            "backgroundColor": color + "33",
            "yAxisID": "y",
            "tension": 0.3,
            "fill": True,
        })

    # Latency on right axis
    lat_values = [chart_data["latency"].get(m, None) for m in labels]
    if any(v is not None for v in lat_values):
        datasets.append({
            "label": "Median Latency (s)",
            "data": lat_values,
            "borderColor": "#f472b6",
            "backgroundColor": "#f472b600",
            "yAxisID": "y1",
            "tension": 0.3,
            "borderDash": [5, 3],
            "pointRadius": 2,
        })

    config = {
        "type": "line",
        "data": {
            "labels": short_labels,
            "datasets": datasets,
        },
        "options": {
            "responsive": True,
            "interaction": {
                "mode": "index",
                "intersect": False,
            },
            "scales": {
                "y": {
                    "type": "linear",
                    "position": "left",
                    "title": {
                        "display": True,
                        "text": "Trades / sec",
                        "color": "#94a3b8",
                    },
                    "ticks": {"color": "#94a3b8"},
                    "grid": {"color": "#1e293b"},
                },
                "y1": {
                    "type": "linear",
                    "position": "right",
                    "title": {
                        "display": True,
                        "text": "Latency (s)",
                        "color": "#f472b6",
                    },
                    "ticks": {"color": "#f472b6"},
                    "grid": {"drawOnChartArea": False},
                },
                "x": {
                    "ticks": {"color": "#94a3b8", "maxRotation": 45},
                    "grid": {"color": "#1e293b"},
                },
            },
            "plugins": {
                "legend": {
                    "labels": {"color": "#e2e8f0"},
                },
            },
        },
    }

    return (
        f"const ctx = document.getElementById('tpsChart');"
        f"new Chart(ctx, {json.dumps(config)});"
    )


def build_dashboard_html(
    checker: QualityChecker, refresh_s: int = 5
) -> str:
    """Build the full HTML dashboard page."""
    # Run checks on each render — no periodic timer exists yet
    checker.run_all_checks()

    now = time.time()
    state = checker.state
    state_val = state.current.value
    results = state.last_results
    heartbeats = checker.heartbeats
    gap = _query_gap_metrics(checker)
    chart_data = _query_chart_data(checker)
    chart_js = _build_chart_js(chart_data)

    # Producer rows
    producer_rows = ""
    for src in ["rtds", "alchemy"]:
        ts = heartbeats.get(src)
        age = _fmt_age(ts)
        ok = ts is not None and (now - ts) < checker.liveness_timeout_s
        producer_rows += (
            f"<tr><td>{src}</td><td>{age}</td>"
            f"<td>{_status_dot(ok)}</td></tr>\n"
        )

    # Check rows
    check_rows = ""
    for name, result in results.items():
        detail = result.reason if result.reason else (
            "OK" if result.ok else "FAIL"
        )
        check_rows += (
            f"<tr><td>{name}</td>"
            f"<td>{_status_dot(result.ok)}</td>"
            f"<td>{detail}</td></tr>\n"
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_s}">
<title>Pipeline Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    margin: 2rem; background: #0f172a; color: #e2e8f0;
  }}
  .banner {{
    padding: 1rem 2rem; border-radius: 8px;
    font-size: 1.5rem; font-weight: 700;
    background: {_state_color(state_val)};
    color: #fff; margin-bottom: 2rem;
  }}
  table {{
    border-collapse: collapse;
    width: 100%; margin-bottom: 2rem;
  }}
  th, td {{
    text-align: left; padding: 0.5rem 1rem;
    border-bottom: 1px solid #334155;
  }}
  th {{
    color: #94a3b8; font-weight: 600;
    font-size: 0.85rem; text-transform: uppercase;
  }}
  h2 {{
    color: #94a3b8; font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.05em; margin-top: 2rem;
  }}
  .metric {{
    display: inline-block; background: #1e293b;
    padding: 1rem 1.5rem; border-radius: 8px;
    margin: 0.5rem; text-align: center;
  }}
  .metric .value {{
    font-size: 1.8rem; font-weight: 700;
  }}
  .metric .label {{
    font-size: 0.8rem; color: #94a3b8;
  }}
  .chart-container {{
    background: #1e293b; border-radius: 8px;
    padding: 1rem; margin-bottom: 2rem;
    height: 300px;
  }}
</style>
</head>
<body>
<div class="banner">Pipeline: {state_val.upper()}</div>

<h2>Producers</h2>
<table>
<tr><th>Source</th><th>Last Heartbeat</th><th>Status</th></tr>
{producer_rows}
</table>

<h2>Throughput &amp; Latency (last hour)</h2>
<div class="chart-container">
  <canvas id="tpsChart"></canvas>
</div>

<h2>Quality Checks</h2>
<table>
<tr><th>Check</th><th>Status</th><th>Detail</th></tr>
{check_rows}
</table>

<h2>Data Quality Gaps (last hour)</h2>
<div>
  <div class="metric">
    <div class="label">Latency Gap (median)</div>
    <div class="value">{_fmt_val(gap["median_latency_s"])}</div>
  </div>
  <div class="metric">
    <div class="label">Latency Gap (max)</div>
    <div class="value">{_fmt_val(gap["max_latency_s"])}</div>
  </div>
  <div class="metric">
    <div class="label">Coverage Gap: RTDS-only</div>
    <div class="value">{gap["rtds_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Coverage Gap: Alchemy-only</div>
    <div class="value">{gap["alchemy_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Coverage Gap: Total</div>
    <div class="value">{gap["total"]}</div>
  </div>
</div>

<p style="color:#475569; margin-top:2rem; font-size:0.8rem;">
  Auto-refresh every {refresh_s}s &middot; {time.strftime("%H:%M:%S")}
</p>
<script>{chart_js}</script>
</body>
</html>"""


def make_dashboard_route(
    checker: QualityChecker, refresh_s: int = 5
) -> Any:
    """Create an ASGI app that serves the dashboard HTML."""

    async def _handle(
        scope: Any, receive: Any, send: Any
    ) -> None:
        html = build_dashboard_html(checker, refresh_s=refresh_s)
        response = AsgiResponse(
            body=html.encode(),
            status_code=200,
            headers={
                "content-type": "text/html; charset=utf-8"
            },
        )
        await response(scope, receive, send)

    return _handle
