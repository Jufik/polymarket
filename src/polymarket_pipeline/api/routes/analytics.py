"""Data health and source reconciliation analytics endpoint."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Request

router = APIRouter(tags=["analytics"])
log = structlog.get_logger()


async def _ch_query(request: Request, query: str) -> list[dict]:
    """Execute a ClickHouse query and return rows as dicts."""
    ch: object = request.app.state.ch_client
    db: str = request.app.state.ch_database
    full_query = f"{query} FORMAT JSONEachRow"

    resp = await ch.post(  # type: ignore[union-attr]
        "/",
        content=full_query,
        params={"database": db},
        headers={"Content-Type": "text/plain"},
    )
    resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        return []
    return [json.loads(line) for line in text.split("\n") if line.strip()]


@router.get("/analytics")
async def analytics(request: Request) -> dict:
    """Return data health metrics and 24h source reconciliation."""
    # --- Data health ---
    broken_count = 0
    broken_assets = 0
    total_trades = 0

    try:
        rows = await _ch_query(
            request,
            "SELECT count(*) AS broken_count,"
            " countDistinct(asset_id) AS broken_assets"
            " FROM trades_raw FINAL"
            " WHERE condition_id NOT LIKE '0x%'",
        )
        if rows:
            broken_count = int(rows[0]["broken_count"])
            broken_assets = int(rows[0]["broken_assets"])
    except Exception:
        log.warning("analytics.broken_query_failed")

    try:
        rows = await _ch_query(request, "SELECT count(*) AS total FROM trades_raw")
        if rows:
            total_trades = int(rows[0]["total"])
    except Exception:
        log.warning("analytics.total_query_failed")

    # --- Reconciliation: 24h hourly by source ---
    reconciliation: list[dict] = []
    try:
        reconciliation = await _ch_query(
            request,
            "SELECT toStartOfHour(timestamp) AS hour,"
            " source, count(*) AS trades"
            " FROM trades_raw"
            " WHERE timestamp >= now() - INTERVAL 24 HOUR"
            " GROUP BY hour, source"
            " ORDER BY hour",
        )
    except Exception:
        log.warning("analytics.reconciliation_query_failed")

    return {
        "data_health": {
            "broken_count": broken_count,
            "broken_assets": broken_assets,
            "total_trades": total_trades,
        },
        "reconciliation": [
            {"hour": r["hour"], "source": r["source"], "trades": int(r["trades"])}
            for r in reconciliation
        ],
    }
