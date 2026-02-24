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

# ── SQL queries ─────────────────────────────────────────────────────────

# Source race: for matched trades, who published first?
RACE_SQL = """\
SELECT
    count() AS matched,
    median(rtds_pub - alch_pub) AS median_delta_s,
    max(abs(rtds_pub - alch_pub)) AS max_delta_s,
    countIf(rtds_pub < alch_pub) AS rtds_first,
    countIf(alch_pub < rtds_pub) AS alchemy_first
FROM (
    SELECT trade_id, published_at AS rtds_pub
    FROM trades_raw
    WHERE source = 'rtds' AND published_at > 0
      AND timestamp > now() - INTERVAL 1 HOUR
) r
JOIN (
    SELECT trade_id, published_at AS alch_pub
    FROM trades_raw
    WHERE source = 'alchemy' AND published_at > 0
      AND timestamp > now() - INTERVAL 1 HOUR
) a USING (trade_id)
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

# Per-minute TPS by source
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

# Per-minute delivery lag per source: published_at - trade timestamp
DELIVERY_LAG_SQL = """\
SELECT
    toStartOfMinute(timestamp) AS minute,
    source,
    median(published_at - toFloat64(timestamp)) AS median_lag_s
FROM trades_raw
WHERE timestamp > now() - INTERVAL 1 HOUR
  AND published_at > 0
GROUP BY minute, source
ORDER BY minute
"""

# Trade lifecycle waterfall (matched trades only):
#   first_seen  = min(rtds_pub, alchemy_pub) - trade_ts
#   consolidated = max(rtds_pub, alchemy_pub) - trade_ts
WATERFALL_SQL = """\
SELECT
    toStartOfMinute(r.ts) AS minute,
    median(least(r.pub, a.pub) - toFloat64(r.ts)) AS p50_first_seen,
    median(greatest(r.pub, a.pub) - toFloat64(r.ts)) AS p50_consolidated,
    quantile(0.95)(greatest(r.pub, a.pub) - toFloat64(r.ts)) AS p95_consolidated
FROM (
    SELECT trade_id, timestamp AS ts, published_at AS pub
    FROM trades_raw
    WHERE source = 'rtds' AND published_at > 0
      AND timestamp > now() - INTERVAL 1 HOUR
) r
JOIN (
    SELECT trade_id, published_at AS pub
    FROM trades_raw
    WHERE source = 'alchemy' AND published_at > 0
      AND timestamp > now() - INTERVAL 1 HOUR
) a USING (trade_id)
GROUP BY minute
ORDER BY minute
"""


def _query_gap_metrics(checker: QualityChecker) -> dict[str, Any]:
    """Run race and coverage queries against ClickHouse."""
    metrics: dict[str, Any] = {
        "median_delta_s": None,
        "max_delta_s": None,
        "rtds_first": 0,
        "alchemy_first": 0,
        "matched": 0,
        "rtds_only": 0,
        "alchemy_only": 0,
        "total": 0,
    }
    ch = checker.clickhouse
    try:
        rows = ch.query(RACE_SQL)
        if rows and rows[0].get("matched", 0) > 0:
            metrics["median_delta_s"] = rows[0].get("median_delta_s")
            metrics["max_delta_s"] = rows[0].get("max_delta_s")
            metrics["rtds_first"] = rows[0].get("rtds_first", 0)
            metrics["alchemy_first"] = rows[0].get("alchemy_first", 0)
            metrics["matched"] = rows[0].get("matched", 0)
    except Exception as exc:
        log.warning("dashboard.race_query_failed", error=str(exc))

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
    """Query TPS, per-source delivery lag, and waterfall timeseries."""
    chart: dict[str, Any] = {
        "tps": {},
        "delivery_lag": {},
        "waterfall": {"first_seen": {}, "consolidated": {}, "p95_consolidated": {}},
    }
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
        rows = ch.query(DELIVERY_LAG_SQL)
        for row in rows:
            minute = str(row["minute"])
            source = row["source"]
            val = float(row["median_lag_s"])
            if not math.isnan(val):
                if source not in chart["delivery_lag"]:
                    chart["delivery_lag"][source] = {}
                chart["delivery_lag"][source][minute] = round(val, 2)
    except Exception as exc:
        log.warning("dashboard.delivery_lag_query_failed", error=str(exc))

    try:
        rows = ch.query(WATERFALL_SQL)
        for row in rows:
            minute = str(row["minute"])
            for key in ("first_seen", "consolidated", "p95_consolidated"):
                col = f"p50_{key}" if key != "p95_consolidated" else key
                val = float(row[col])
                if not math.isnan(val):
                    chart["waterfall"][key][minute] = round(val, 2)
    except Exception as exc:
        log.warning("dashboard.waterfall_query_failed", error=str(exc))

    return chart


# ── Helpers ──────────────────────────────────────────────────────────────


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


def _short_labels(labels: list[str]) -> list[str]:
    """Convert 'YYYY-MM-DD HH:MM:SS' to 'HH:MM'."""
    out = []
    for lbl in labels:
        try:
            out.append(lbl[11:16])
        except (IndexError, TypeError):
            out.append(lbl)
    return out


# ── Chart builders ───────────────────────────────────────────────────────


def _build_tps_chart_js(chart_data: dict[str, Any]) -> str:
    """TPS chart + per-source delivery lag on second axis."""
    all_minutes: set[str] = set()
    for source_data in chart_data["tps"].values():
        all_minutes.update(source_data.keys())
    for source_data in chart_data["delivery_lag"].values():
        all_minutes.update(source_data.keys())

    if not all_minutes:
        return "/* no TPS data */"

    labels = sorted(all_minutes)
    short = _short_labels(labels)

    source_colors = {
        "rtds": "#38bdf8",
        "alchemy": "#a78bfa",
        "goldsky_sink": "#fb923c",
        "goldsky_subgraph": "#fbbf24",
        "websocket": "#34d399",
    }
    lag_colors = {"rtds": "#22d3ee", "alchemy": "#c084fc"}

    datasets = []
    for source, minute_data in sorted(chart_data["tps"].items()):
        color = source_colors.get(source, "#94a3b8")
        datasets.append(
            {
                "label": f"{source} TPS",
                "data": [minute_data.get(m, 0) for m in labels],
                "borderColor": color,
                "backgroundColor": color + "33",
                "yAxisID": "y",
                "tension": 0.3,
                "fill": True,
            }
        )

    for source, minute_data in sorted(chart_data["delivery_lag"].items()):
        color = lag_colors.get(source, "#f472b6")
        values = [minute_data.get(m, None) for m in labels]
        if any(v is not None for v in values):
            datasets.append(
                {
                    "label": f"{source} lag (s)",
                    "data": values,
                    "borderColor": color,
                    "backgroundColor": color + "00",
                    "yAxisID": "y1",
                    "tension": 0.3,
                    "borderDash": [5, 3],
                    "pointRadius": 2,
                }
            )

    config = {
        "type": "line",
        "data": {"labels": short, "datasets": datasets},
        "options": {
            "responsive": True,
            "interaction": {"mode": "index", "intersect": False},
            "scales": {
                "y": {
                    "type": "linear",
                    "position": "left",
                    "title": {"display": True, "text": "Trades / sec", "color": "#94a3b8"},
                    "ticks": {"color": "#94a3b8"},
                    "grid": {"color": "#1e293b"},
                },
                "y1": {
                    "type": "linear",
                    "position": "right",
                    "title": {"display": True, "text": "Delivery Lag (s)", "color": "#c084fc"},
                    "ticks": {"color": "#c084fc"},
                    "grid": {"drawOnChartArea": False},
                },
                "x": {
                    "ticks": {"color": "#94a3b8", "maxRotation": 45},
                    "grid": {"color": "#1e293b"},
                },
            },
            "plugins": {"legend": {"labels": {"color": "#e2e8f0"}}},
        },
    }

    return (
        f"const ctx1 = document.getElementById('tpsChart');new Chart(ctx1, {json.dumps(config)});"
    )


def _build_waterfall_chart_js(chart_data: dict[str, Any]) -> str:
    """Trade lifecycle waterfall: Execution → First Seen → Consolidated."""
    wf = chart_data["waterfall"]
    all_minutes: set[str] = set()
    for series in wf.values():
        all_minutes.update(series.keys())

    if not all_minutes:
        return "/* no waterfall data — need matched trades from both sources */"

    labels = sorted(all_minutes)
    short = _short_labels(labels)

    # Stacked bar: segment 1 = first_seen, segment 2 = consolidated - first_seen
    first_seen = [wf["first_seen"].get(m, None) for m in labels]
    consol = [wf["consolidated"].get(m, None) for m in labels]
    p95 = [wf["p95_consolidated"].get(m, None) for m in labels]

    # Delta for stacked segment
    delta = []
    for fs, co in zip(first_seen, consol, strict=True):
        if fs is not None and co is not None:
            delta.append(round(co - fs, 2))
        else:
            delta.append(None)

    config = {
        "type": "bar",
        "data": {
            "labels": short,
            "datasets": [
                {
                    "label": "Execution → First Seen (s)",
                    "data": first_seen,
                    "backgroundColor": "#38bdf8cc",
                    "borderColor": "#38bdf8",
                    "borderWidth": 1,
                    "stack": "lifecycle",
                },
                {
                    "label": "First Seen → Consolidated (s)",
                    "data": delta,
                    "backgroundColor": "#a78bfacc",
                    "borderColor": "#a78bfa",
                    "borderWidth": 1,
                    "stack": "lifecycle",
                },
                {
                    "label": "p95 Consolidated (s)",
                    "data": p95,
                    "type": "line",
                    "borderColor": "#ef4444",
                    "backgroundColor": "#ef444400",
                    "borderDash": [4, 4],
                    "pointRadius": 2,
                    "tension": 0.3,
                },
            ],
        },
        "options": {
            "responsive": True,
            "interaction": {"mode": "index", "intersect": False},
            "scales": {
                "x": {
                    "stacked": True,
                    "ticks": {"color": "#94a3b8", "maxRotation": 45},
                    "grid": {"color": "#1e293b"},
                },
                "y": {
                    "stacked": True,
                    "title": {"display": True, "text": "Seconds", "color": "#94a3b8"},
                    "ticks": {"color": "#94a3b8"},
                    "grid": {"color": "#1e293b"},
                },
            },
            "plugins": {"legend": {"labels": {"color": "#e2e8f0"}}},
        },
    }

    return (
        f"const ctx2 = document.getElementById('waterfallChart');"
        f"new Chart(ctx2, {json.dumps(config)});"
    )


# ── HTML builder ─────────────────────────────────────────────────────────


def build_dashboard_html(checker: QualityChecker, refresh_s: int = 5) -> str:
    """Build the full HTML dashboard page."""
    checker.run_all_checks()

    now = time.time()
    state = checker.state
    state_val = state.current.value
    results = state.last_results
    heartbeats = checker.heartbeats
    gap = _query_gap_metrics(checker)
    chart_data = _query_chart_data(checker)
    tps_js = _build_tps_chart_js(chart_data)
    waterfall_js = _build_waterfall_chart_js(chart_data)

    # Producer rows
    producer_rows = ""
    for src in ["rtds", "alchemy"]:
        ts = heartbeats.get(src)
        age = _fmt_age(ts)
        ok = ts is not None and (now - ts) < checker.liveness_timeout_s
        producer_rows += f"<tr><td>{src}</td><td>{age}</td><td>{_status_dot(ok)}</td></tr>\n"

    # Check rows
    check_rows = ""
    for name, result in results.items():
        detail = result.reason if result.reason else ("OK" if result.ok else "FAIL")
        check_rows += (
            f"<tr><td>{name}</td><td>{_status_dot(result.ok)}</td><td>{detail}</td></tr>\n"
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

<h2>Throughput &amp; Delivery Lag (last hour)</h2>
<div class="chart-container">
  <canvas id="tpsChart"></canvas>
</div>

<h2>Trade Lifecycle (last hour, matched trades)</h2>
<div class="chart-container">
  <canvas id="waterfallChart"></canvas>
</div>

<h2>Quality Checks</h2>
<table>
<tr><th>Check</th><th>Status</th><th>Detail</th></tr>
{check_rows}
</table>

<h2>Source Race (last hour)</h2>
<div>
  <div class="metric">
    <div class="label">Matched Trades</div>
    <div class="value">{gap["matched"]}</div>
  </div>
  <div class="metric">
    <div class="label">RTDS First</div>
    <div class="value">{gap["rtds_first"]}</div>
  </div>
  <div class="metric">
    <div class="label">Alchemy First</div>
    <div class="value">{gap["alchemy_first"]}</div>
  </div>
  <div class="metric">
    <div class="label">Median Delta</div>
    <div class="value">{_fmt_val(gap["median_delta_s"])}</div>
  </div>
  <div class="metric">
    <div class="label">Max Delta</div>
    <div class="value">{_fmt_val(gap["max_delta_s"])}</div>
  </div>
</div>

<h2>Coverage Gaps (last hour)</h2>
<div>
  <div class="metric">
    <div class="label">RTDS-only</div>
    <div class="value">{gap["rtds_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Alchemy-only</div>
    <div class="value">{gap["alchemy_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Total</div>
    <div class="value">{gap["total"]}</div>
  </div>
</div>

<p style="color:#475569; margin-top:2rem; font-size:0.8rem;">
  Auto-refresh every {refresh_s}s &middot; {time.strftime("%H:%M:%S")}
</p>
<script>{tps_js}{waterfall_js}</script>
</body>
</html>"""


def make_dashboard_route(checker: QualityChecker, refresh_s: int = 5) -> Any:
    """Create an ASGI app that serves the dashboard HTML."""

    async def _handle(scope: Any, receive: Any, send: Any) -> None:
        html = build_dashboard_html(checker, refresh_s=refresh_s)
        response = AsgiResponse(
            body=html.encode(),
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
        )
        await response(scope, receive, send)

    return _handle
