"""FastAPI application factory for the Polymarket trading API.

Serves:
  /                       -- HTML dashboard (auto-refresh)
  /api/*                  -- backward-compatible routes (delegates to /api/v1/*)
  /api/v1/health          -- liveness
  /api/v1/metrics         -- request rate (sliding 60s window)
  /api/v1/ingestion       -- per-source trade counts + TPS
  /api/v1/pipeline/health -- metadata counts
  /api/v1/markets         -- market list + detail
  /api/v1/trades          -- trade list with filters
  /api/v1/strategies      -- strategy list
  /api/v1/intents         -- intent list + detail
  /api/v1/fills           -- fill list
  /api/v1/roundtrips      -- round trip list + detail
  /api/v1/prices/{cid}    -- zoomable price history
  /api/v1/introspect      -- strategy introspect proxy
"""

from __future__ import annotations

import time

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pm_api.deps import request_times
from pm_api.errors import APIError, api_error_handler
from pm_api.routes.compat import compat
from pm_api.routes.fills import router as fills_router
from pm_api.routes.health import router as health_router
from pm_api.routes.ingestion import router as ingestion_router
from pm_api.routes.intents import router as intents_router
from pm_api.routes.introspect import router as introspect_router
from pm_api.routes.markets import router as markets_router
from pm_api.routes.metadata import router as metadata_router
from pm_api.routes.prices import router as prices_router
from pm_api.routes.roundtrips import router as roundtrips_router
from pm_api.routes.strategies import router as strategies_router
from pm_api.routes.trades import router as trades_router


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    application = FastAPI(title="Polymarket Trading API", version="1.0.0")

    # -- CORS --
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Error handler --
    application.add_exception_handler(APIError, api_error_handler)  # type: ignore[arg-type]

    # -- Request metrics middleware --
    @application.middleware("http")
    async def track_requests(request: Request, call_next: object) -> Response:  # type: ignore[override]
        request_times.append(time.time())
        return await call_next(request)  # type: ignore[misc]

    # -- v1 routers --
    application.include_router(health_router)
    application.include_router(ingestion_router)
    application.include_router(metadata_router)
    application.include_router(markets_router)
    application.include_router(trades_router)
    application.include_router(strategies_router)
    application.include_router(intents_router)
    application.include_router(fills_router)
    application.include_router(roundtrips_router)
    application.include_router(prices_router)
    application.include_router(introspect_router)

    # -- Backward-compat /api/* routes --
    application.include_router(compat)

    # -- HTML dashboard --
    @application.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    return application


# ---------------------------------------------------------------------------
# Module-level app instance (for uvicorn import)
# ---------------------------------------------------------------------------

app = create_app()


def main() -> None:
    """Entry point for ``pm-api``."""
    uvicorn.run(
        "pm_api.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )


# ---------------------------------------------------------------------------
# Dashboard HTML -- single-page, fetches /api/* endpoints via JS
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Polymarket Dashboard</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #22c55e; --red: #ef4444; --amber: #eab308; --purple: #a78bfa;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; }
  h2 { font-size: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin: 1.5rem 0 .75rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border-radius: 8px; padding: 1rem 1.25rem; }
  .card .value { font-size: 1.6rem; font-weight: 700; }
  .card .label { font-size: .75rem; color: var(--muted); margin-top: .25rem; }
  .card.green .value { color: var(--green); }
  .card.red .value { color: var(--red); }
  .card.amber .value { color: var(--amber); }
  .card.accent .value { color: var(--accent); }
  .card.purple .value { color: var(--purple); }
  table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 8px; overflow: hidden; margin-bottom: 1.5rem; }
  th { text-align: left; padding: .6rem 1rem; color: var(--muted); font-size: .75rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }
  td { padding: .5rem 1rem; border-bottom: 1px solid var(--border); font-size: .85rem; font-variant-numeric: tabular-nums; }
  tr:last-child td { border-bottom: none; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.green { background: var(--green); }
  .dot.amber { background: var(--amber); }
  .dot.red { background: var(--red); }
  .mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: .8rem; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem; font-weight: 600; }
  .tag.buy { background: #22c55e22; color: var(--green); }
  .tag.sell { background: #ef444422; color: var(--red); }
  .tag.filled { background: #22c55e22; color: var(--green); }
  .tag.rejected { background: #ef444422; color: var(--red); }
  .tag.partial { background: #eab30822; color: var(--amber); }
  .reason { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
  .chart-wrap { background: var(--surface); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; height: 280px; }
  .refresh-bar { position: fixed; top: 0; left: 0; height: 2px; background: var(--accent); transition: width .3s linear; z-index: 100; }
  .tabs { display: flex; gap: .5rem; margin-bottom: 1rem; }
  .tabs button { background: var(--surface); color: var(--muted); border: 1px solid var(--border); border-radius: 6px; padding: .4rem 1rem; cursor: pointer; font-size: .8rem; }
  .tabs button.active { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  footer { color: var(--muted); font-size: .75rem; margin-top: 2rem; }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="refresh-bar" id="refreshBar"></div>
<h1>Polymarket Pipeline</h1>

<!-- Ingestion -->
<h2>Ingestion</h2>
<div class="grid" id="ingestionCards"></div>
<div class="chart-wrap"><canvas id="tpsChart"></canvas></div>

<!-- Metadata -->
<h2>Metadata</h2>
<div class="grid" id="metadataCards"></div>

<!-- Strategy Activity -->
<h2>Strategy Activity</h2>
<div class="grid" id="strategyCards"></div>

<!-- Intents & Fills -->
<h2>Recent Intents &amp; Fills</h2>
<div class="tabs" id="intentTabs"></div>
<table id="intentTable"><thead><tr>
  <th>Time</th><th>Strategy</th><th>Side</th><th>Outcome</th>
  <th>Size</th><th>Max Price</th><th>Fill Price</th><th>Status</th><th>Reason</th>
</tr></thead><tbody id="intentBody"></tbody></table>

<footer id="footer"></footer>

<script>
const API = '/api';
const REFRESH_MS = 10000;
let tpsChart = null;
let currentStrategy = null;

function fmt(ts) {
  if (!ts) return '\\u2014';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function fmtDt(s) {
  if (!s) return '\\u2014';
  try { return new Date(s).toLocaleString('en-GB', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
  catch { return s; }
}
function shortCid(cid) { return cid ? cid.slice(0,10) + '...' : '\\u2014'; }
function card(value, label, cls='') { return `<div class="card ${cls}"><div class="value">${value}</div><div class="label">${label}</div></div>`; }

async function fetchJson(url) {
  try { const r = await fetch(url); return await r.json(); }
  catch { return null; }
}

async function refreshIngestion() {
  const d = await fetchJson(API + '/ingestion?hours=1');
  if (!d) return;
  const el = document.getElementById('ingestionCards');
  let html = '';
  const t = d.total || {};
  html += card(Number(t.trades || 0).toLocaleString(), 'Trades (1h)', 'accent');
  html += card(t.tps || '0', 'Avg TPS', 'green');
  html += card(fmtDt(t.latest), 'Latest Trade');
  for (const s of (d.by_source || [])) {
    html += card(`${Number(s.trades).toLocaleString()} <span style="font-size:.8rem;color:var(--muted)">(${s.tps}/s)</span>`, s.source, 'purple');
  }
  el.innerHTML = html;
  updateTpsChart(d.tps_series || []);
}

function updateTpsChart(series) {
  const byMinute = {};
  const sources = new Set();
  for (const row of series) {
    const m = row.minute;
    sources.add(row.source);
    if (!byMinute[m]) byMinute[m] = {};
    byMinute[m][row.source] = Number(row.cnt);
  }
  const minutes = Object.keys(byMinute).sort();
  const labels = minutes.map(m => m.slice(11, 16));
  const srcArr = [...sources].sort();
  const colors = { alchemy: '#a78bfa', rtds: '#38bdf8', goldsky_subgraph: '#fbbf24', pending_block: '#34d399' };
  const datasets = srcArr.map(src => ({
    label: src,
    data: minutes.map(m => (byMinute[m][src] || 0) / 60),
    borderColor: colors[src] || '#94a3b8',
    backgroundColor: (colors[src] || '#94a3b8') + '33',
    fill: true, tension: 0.3, pointRadius: 0,
  }));

  const ctx = document.getElementById('tpsChart');
  if (tpsChart) { tpsChart.data.labels = labels; tpsChart.data.datasets = datasets; tpsChart.update('none'); }
  else {
    tpsChart = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { ticks: { color: '#94a3b8', maxRotation: 0, maxTicksLimit: 20 }, grid: { color: '#1e293b' } },
          y: { title: { display: true, text: 'TPS', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });
  }
}

async function refreshMetadata() {
  const d = await fetchJson(API + '/metadata');
  if (!d) return;
  const el = document.getElementById('metadataCards');
  const m = d.markets || {};
  let html = '';
  html += card(Number(m.total_markets || 0).toLocaleString(), 'Total Markets');
  html += card(Number(m.open || 0).toLocaleString(), 'Open', 'green');
  html += card(Number(m.resolved || 0).toLocaleString(), 'Resolved', 'purple');
  html += card(Number(d.tokens || 0).toLocaleString(), 'Token Mappings', 'accent');
  html += card(fmtDt(d.last_update), 'Last Metadata Update');
  el.innerHTML = html;
}

async function refreshActivity() {
  const d = await fetchJson(API + '/activity');
  if (!d) return;
  const el = document.getElementById('strategyCards');
  const tabs = document.getElementById('intentTabs');
  let html = '';
  let tabHtml = '<button class="' + (!currentStrategy ? 'active' : '') + '" onclick="setStrategy(null)">All</button>';

  for (const [name, info] of Object.entries(d)) {
    const fillRate = info.total_intents > 0 ? Math.round(info.total_fills / info.total_intents * 100) : 0;
    html += card(
      `${info.total_fills}/${info.total_intents} <span style="font-size:.85rem;color:var(--muted)">(${fillRate}%)</span>`,
      `${name} &middot; $${info.total_filled_usd.toLocaleString()}`,
      fillRate > 50 ? 'green' : fillRate > 0 ? 'amber' : ''
    );
    tabHtml += `<button class="${currentStrategy===name?'active':''}" onclick="setStrategy('${name}')">${name}</button>`;
  }
  el.innerHTML = html;
  tabs.innerHTML = tabHtml;
}

async function refreshIntents() {
  const q = currentStrategy ? '?strategy=' + currentStrategy + '&n=100' : '?n=100';
  const [intD, fillD] = await Promise.all([fetchJson(API+'/intents'+q), fetchJson(API+'/fills'+q)]);
  const intents = (intD?.intents || []).map(i => ({...i, _type: 'intent'}));
  const fills = (fillD?.fills || []).map(f => ({...f, _type: 'fill'}));

  const fillMap = {};
  for (const f of fills) {
    const key = f.condition_id + '|' + f.strategy;
    if (!fillMap[key]) fillMap[key] = [];
    fillMap[key].push(f);
  }

  const rows = [];
  const usedFills = new Set();
  for (const intent of intents) {
    const key = intent.condition_id + '|' + intent.strategy;
    const matchedFills = fillMap[key] || [];
    const fill = matchedFills.find(f => !usedFills.has(f.intent_id) && Math.abs((f.filled_at||0) - (intent.signal_time||0)) < 60);
    if (fill) usedFills.add(fill.intent_id);
    rows.push({
      time: intent.signal_time,
      strategy: intent._source || intent.strategy,
      side: intent.side,
      outcome: intent.outcome,
      size: intent.size_usd,
      maxPrice: intent.max_price,
      fillPrice: fill ? fill.filled_price : null,
      status: fill ? fill.status : 'pending',
      reason: intent.reason || '',
    });
  }
  rows.sort((a,b) => (b.time||0) - (a.time||0));

  const tbody = document.getElementById('intentBody');
  tbody.innerHTML = rows.slice(0, 80).map(r => `<tr>
    <td class="mono">${fmt(r.time)}</td>
    <td>${r.strategy}</td>
    <td><span class="tag ${(r.side||'').toLowerCase()}">${r.side||'\\u2014'}</span></td>
    <td>${r.outcome||'\\u2014'}</td>
    <td>$${(r.size||0).toFixed(0)}</td>
    <td class="mono">${r.maxPrice != null ? r.maxPrice.toFixed(3) : '\\u2014'}</td>
    <td class="mono">${r.fillPrice != null ? r.fillPrice.toFixed(3) : '\\u2014'}</td>
    <td><span class="tag ${r.status}">${r.status}</span></td>
    <td class="reason" title="${r.reason}">${r.reason}</td>
  </tr>`).join('');
}

function setStrategy(name) {
  currentStrategy = name;
  refreshActivity();
  refreshIntents();
}

async function refresh() {
  const bar = document.getElementById('refreshBar');
  bar.style.width = '0%';
  await Promise.all([refreshIngestion(), refreshMetadata(), refreshActivity(), refreshIntents()]);
  document.getElementById('footer').textContent = 'Last refresh: ' + new Date().toLocaleTimeString() + ' \\u00b7 Auto-refresh every ' + (REFRESH_MS/1000) + 's';
  let pct = 0;
  const step = 100 / (REFRESH_MS / 100);
  const iv = setInterval(() => { pct += step; bar.style.width = Math.min(pct, 100) + '%'; if (pct >= 100) clearInterval(iv); }, 100);
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
"""
