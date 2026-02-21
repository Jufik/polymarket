# Monitoring Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a browser-based monitoring dashboard at `/dashboard` embedded in the live FastStream app, showing producer health, quality check results, and data quality gap metrics.

**Architecture:** Mount a Starlette ASGI route into the existing FastStream app via `app.as_asgi(asgi_routes=...)`. The dashboard reads in-memory state from `QualityChecker` and runs ClickHouse queries for latency/coverage gap metrics. HTML rendered server-side with auto-refresh.

**Tech Stack:** FastStream `as_asgi`, `AsgiResponse`, ClickHouse queries, plain HTML/CSS, `<meta http-equiv="refresh">`.

---

### Task 1: Expose `last_results` on ReadinessState

The `ReadinessState._last_results` dict is private. The dashboard needs to read individual check results. Add a public property.

**Files:**
- Modify: `src/polymarket_pipeline/live/quality/state.py:23-44`
- Test: `tests/test_quality_state.py` (new)

**Step 1: Write the failing test**

Create `tests/test_quality_state.py`:

```python
"""Tests for ReadinessState and CheckResult."""

from polymarket_pipeline.live.quality.state import CheckResult, PipelineState, ReadinessState


def test_last_results_empty_by_default():
    state = ReadinessState()
    assert state.last_results == {}


def test_last_results_populated_after_update():
    state = ReadinessState()
    results = {
        "liveness": CheckResult(ok=True),
        "volume": CheckResult(ok=False, reason="low"),
    }
    state.update(results)
    assert state.last_results == results
    assert state.current == PipelineState.DEGRADED
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_quality_state.py -x -q`
Expected: FAIL — `AttributeError: 'ReadinessState' object has no attribute 'last_results'`

**Step 3: Add the property**

In `src/polymarket_pipeline/live/quality/state.py`, add after the `failures` property:

```python
    @property
    def last_results(self) -> dict[str, CheckResult]:
        return dict(self._last_results)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_quality_state.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_quality_state.py src/polymarket_pipeline/live/quality/state.py
git commit -m "feat(quality): expose last_results property on ReadinessState"
```

---

### Task 2: Add dashboard settings

**Files:**
- Modify: `src/polymarket_pipeline/live/settings.py:39` (add two fields)
- Test: `tests/test_live_settings.py` (add test)

**Step 1: Write the failing test**

Add to `tests/test_live_settings.py`:

```python
def test_dashboard_settings_defaults(monkeypatch):
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")
    from polymarket_pipeline.live.settings import Settings
    s = Settings()
    assert s.dashboard_refresh_s == 5
    assert s.dashboard_port == 8099
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_live_settings.py::test_dashboard_settings_defaults -x -q`
Expected: FAIL — `AttributeError`

**Step 3: Add settings fields**

In `src/polymarket_pipeline/live/settings.py`, add after the `gap_threshold_s` line:

```python
    # Dashboard
    dashboard_refresh_s: int = 5
    dashboard_port: int = 8099
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_live_settings.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/settings.py tests/test_live_settings.py
git commit -m "feat(settings): add dashboard_refresh_s and dashboard_port"
```

---

### Task 3: Create the dashboard module

This is the core module. It builds the HTML dashboard by reading QualityChecker state and querying ClickHouse for gap metrics.

**Files:**
- Create: `src/polymarket_pipeline/live/dashboard.py`
- Test: `tests/test_dashboard.py` (new)

**Step 1: Write the failing tests**

Create `tests/test_dashboard.py`:

```python
"""Tests for the monitoring dashboard."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.quality.state import CheckResult, PipelineState


@pytest.fixture
def mock_checker(monkeypatch: pytest.MonkeyPatch) -> QualityChecker:
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test")
    from polymarket_pipeline.live.settings import Settings

    ch = MagicMock()
    ch.query.return_value = []
    checker = QualityChecker(settings=Settings(), clickhouse=ch)
    checker.record_heartbeat("rtds", time.time())
    checker.record_heartbeat("alchemy", time.time() - 5)
    checker.run_all_checks()
    return checker


def test_build_html_contains_pipeline_state(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert '<meta http-equiv="refresh"' in html
    assert "DEGRADED" in html or "READY" in html or "CHECKING" in html


def test_build_html_contains_producer_table(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "rtds" in html
    assert "alchemy" in html


def test_build_html_contains_check_results(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "source_liveness" in html
    assert "volume_reconciliation" in html
    assert "dedup_sanity" in html


def test_build_html_contains_gap_section(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import build_dashboard_html

    html = build_dashboard_html(mock_checker, refresh_s=5)
    assert "Latency Gap" in html or "latency_gap" in html
    assert "Coverage Gap" in html or "coverage_gap" in html


def test_make_asgi_app_callable(mock_checker: QualityChecker):
    from polymarket_pipeline.live.dashboard import make_dashboard_route

    asgi_app = make_dashboard_route(mock_checker, refresh_s=5)
    assert callable(asgi_app)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_pipeline.live.dashboard'`

**Step 3: Implement dashboard module**

Create `src/polymarket_pipeline/live/dashboard.py`:

```python
"""Monitoring dashboard for the live pipeline."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from faststream.asgi import AsgiResponse

if TYPE_CHECKING:
    from polymarket_pipeline.live.quality.checker import QualityChecker

# ── Gap queries ──────────────────────────────────────────────────────────

LATENCY_GAP_SQL = """\
SELECT
    median(t2_ts - t1_ts) AS median_latency_s,
    max(t2_ts - t1_ts)    AS max_latency_s
FROM (
    SELECT trade_id, timestamp AS t1_ts
    FROM trades_raw
    WHERE _version = 1 AND timestamp > now() - INTERVAL 1 HOUR
) t1
JOIN (
    SELECT trade_id, timestamp AS t2_ts
    FROM trades_raw
    WHERE _version = 2 AND timestamp > now() - INTERVAL 1 HOUR
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


def _query_gap_metrics(checker: QualityChecker) -> dict[str, Any]:
    """Run latency and coverage gap queries against ClickHouse."""
    metrics: dict[str, Any] = {
        "median_latency_s": None,
        "max_latency_s": None,
        "rtds_only": 0,
        "alchemy_only": 0,
        "total": 0,
    }
    try:
        rows = checker._ch.query(LATENCY_GAP_SQL)
        if rows:
            metrics["median_latency_s"] = rows[0].get("median_latency_s")
            metrics["max_latency_s"] = rows[0].get("max_latency_s")
    except Exception:
        pass

    try:
        rows = checker._ch.query(COVERAGE_GAP_SQL)
        if rows:
            metrics["rtds_only"] = rows[0].get("rtds_only", 0)
            metrics["alchemy_only"] = rows[0].get("alchemy_only", 0)
            metrics["total"] = rows[0].get("total", 0)
    except Exception:
        pass

    return metrics


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
        return f"{v:.1f}s"
    return str(v)


def _status_dot(ok: bool) -> str:
    color = "#22c55e" if ok else "#ef4444"
    return f'<span style="color:{color}; font-size:1.4em;">&#9679;</span>'


def _state_color(state_val: str) -> str:
    return {"ready": "#22c55e", "degraded": "#ef4444", "checking": "#eab308"}.get(
        state_val, "#6b7280"
    )


def build_dashboard_html(checker: QualityChecker, refresh_s: int = 5) -> str:
    """Build the full HTML dashboard page."""
    now = time.time()
    state = checker.state
    state_val = state.current.value
    results = state.last_results
    heartbeats = checker._heartbeats
    gap = _query_gap_metrics(checker)

    # Producer rows
    producer_rows = ""
    for src in ["rtds", "alchemy"]:
        ts = heartbeats.get(src)
        age = _fmt_age(ts)
        ok = ts is not None and (now - ts) < checker._settings.source_liveness_timeout_s
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
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; background: #0f172a; color: #e2e8f0; }}
  .banner {{ padding: 1rem 2rem; border-radius: 8px; font-size: 1.5rem; font-weight: 700;
             background: {_state_color(state_val)}; color: #fff; margin-bottom: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
  th, td {{ text-align: left; padding: 0.5rem 1rem; border-bottom: 1px solid #334155; }}
  th {{ color: #94a3b8; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }}
  h2 {{ color: #94a3b8; font-size: 1rem; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2rem; }}
  .metric {{ display: inline-block; background: #1e293b; padding: 1rem 1.5rem; border-radius: 8px; margin: 0.5rem; text-align: center; }}
  .metric .value {{ font-size: 1.8rem; font-weight: 700; }}
  .metric .label {{ font-size: 0.8rem; color: #94a3b8; }}
</style>
</head>
<body>
<div class="banner">Pipeline: {state_val.upper()}</div>

<h2>Producers</h2>
<table>
<tr><th>Source</th><th>Last Heartbeat</th><th>Status</th></tr>
{producer_rows}
</table>

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
    <div class="label">RTDS-only trades</div>
    <div class="value">{gap["rtds_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Alchemy-only trades</div>
    <div class="value">{gap["alchemy_only"]}</div>
  </div>
  <div class="metric">
    <div class="label">Total trades</div>
    <div class="value">{gap["total"]}</div>
  </div>
</div>

<p style="color:#475569; margin-top:2rem; font-size:0.8rem;">
  Auto-refresh every {refresh_s}s &middot; {time.strftime("%H:%M:%S")}
</p>
</body>
</html>"""


def make_dashboard_route(
    checker: QualityChecker, refresh_s: int = 5
) -> Any:
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dashboard.py -x -q`
Expected: PASS (all 5 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): add monitoring dashboard module with gap metrics"
```

---

### Task 4: Wire dashboard into the FastStream app

Mount the dashboard route into the app and switch the CLI to use `uvicorn` since `as_asgi` produces a Uvicorn-compatible ASGI app.

**Files:**
- Modify: `src/polymarket_pipeline/live/app.py`
- Modify: `src/polymarket_pipeline/cli/live.py`

**Step 1: Write the failing test**

Add to `tests/test_live_app.py`:

```python
def test_asgi_app_has_dashboard_route(monkeypatch: pytest.MonkeyPatch):
    """ASGI app should expose /dashboard route."""
    monkeypatch.setenv("PM_ALCHEMY_WS_URL", "wss://test.example.com")

    import importlib
    import polymarket_pipeline.live.app as app_mod

    importlib.reload(app_mod)

    assert app_mod.asgi_app is not None
    # Check that /dashboard route is registered
    route_paths = [path for path, _ in app_mod.asgi_app.routes]
    assert "/dashboard" in route_paths
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_live_app.py::test_asgi_app_has_dashboard_route -x -q`
Expected: FAIL — `AttributeError: module has no attribute 'asgi_app'`

**Step 3: Modify app.py**

At the end of `on_startup`, after `_quality_checker` is set, create the dashboard route. At module level, create the ASGI app with a placeholder that gets the real route after startup.

Replace the bottom of `src/polymarket_pipeline/live/app.py`. After the existing `on_startup` function, add the dashboard route creation. The key change: instead of exporting `app` directly, export `asgi_app` which wraps `app` with the dashboard route.

Add these changes to `app.py`:

1. After `_quality_checker` is assigned in `on_startup`, build the route and patch it into `asgi_app.routes`.

2. At module level after `app = FastStream(broker)`, add:

```python
from polymarket_pipeline.live.dashboard import make_dashboard_route

# Placeholder dashboard that returns 503 until startup completes
async def _dashboard_placeholder(scope, receive, send):
    from faststream.asgi import AsgiResponse
    resp = AsgiResponse(body=b"Starting...", status_code=503)
    await resp(scope, receive, send)

asgi_app = app.as_asgi(asgi_routes=[("/dashboard", _dashboard_placeholder)])
```

3. At the end of `on_startup`, add:

```python
    # Mount live dashboard route (replaces placeholder)
    from polymarket_pipeline.live.dashboard import make_dashboard_route
    route = make_dashboard_route(_quality_checker, refresh_s=settings.dashboard_refresh_s)
    asgi_app.routes = [
        (path, route if path == "/dashboard" else handler)
        for path, handler in asgi_app.routes
    ]
    log.info("dashboard.mounted", path="/dashboard")
```

**Step 4: Modify cli/live.py to use uvicorn**

Replace `src/polymarket_pipeline/cli/live.py`:

```python
"""CLI entry point for the live sync pipeline."""

from __future__ import annotations


def main() -> None:
    """Run the live pipeline with monitoring dashboard.

    Uses uvicorn to serve the ASGI app (FastStream + dashboard routes).
    """
    import uvicorn

    from polymarket_pipeline.live.app import asgi_app, settings

    uvicorn.run(
        asgi_app,
        host="0.0.0.0",
        port=settings.dashboard_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_live_app.py -x -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/app.py src/polymarket_pipeline/cli/live.py tests/test_live_app.py
git commit -m "feat(live): wire dashboard into FastStream ASGI app with uvicorn"
```

---

### Task 5: Add uvicorn dependency and update .env.example

**Files:**
- Modify: `pyproject.toml` (add `uvicorn` to `live` extras)
- Modify: `.env.example` (add dashboard settings)

**Step 1: Add uvicorn to live extras**

In `pyproject.toml`, the `live` extras list. Add `"uvicorn>=0.30"` to it. `uvicorn` is already installed (confirmed version 0.40.0) but should be declared.

**Step 2: Update .env.example**

Add:
```
# Dashboard
PM_DASHBOARD_REFRESH_S=5
PM_DASHBOARD_PORT=8099
```

**Step 3: Run `uv sync --all-extras` to verify**

Run: `uv sync --all-extras`
Expected: resolves without error

**Step 4: Commit**

```bash
git add pyproject.toml .env.example
git commit -m "chore: add uvicorn dep and dashboard env vars"
```

---

### Task 6: Run full lint and type check

**Step 1: Run ruff**

Run: `uv run ruff check src/polymarket_pipeline/live/dashboard.py tests/test_dashboard.py tests/test_quality_state.py`
Fix any issues.

**Step 2: Run mypy**

Run: `uv run mypy --strict src/polymarket_pipeline/live/dashboard.py`
Fix any type errors (likely need type annotations on the ASGI handler).

**Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: all pass

**Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: lint and type check fixes for dashboard"
```
