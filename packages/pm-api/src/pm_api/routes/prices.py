"""Zoomable price history for a market."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import ch_query

router = APIRouter(prefix="/api/v1", tags=["prices"])

# Zoom level -> (interval for symmetric window, bucket function)
# `None` interval means "all data" (no time filter).
_ZOOM_WINDOWS: dict[str, tuple[str | None, str]] = {
    "1m": ("1 MINUTE", "toStartOfSecond"),
    "2m": ("2 MINUTE", "toStartOfSecond"),
    "5m": ("5 MINUTE", "toStartOfSecond"),
    "15m": ("15 MINUTE", "toStartOfSecond"),
    "30m": ("30 MINUTE", "toStartOfMinute"),
    "1h": ("1 HOUR", "toStartOfMinute"),
    "6h": ("6 HOUR", "toStartOfMinute"),
    "24h": ("24 HOUR", "toStartOfFiveMinutes"),
    "7d": ("7 DAY", "toStartOfHour"),
    "30d": ("30 DAY", "toStartOfHour"),
    "all": (None, "toStartOfHour"),
}

# The set of valid zoom levels (whitelist for the interval string).
_VALID_ZOOMS = set(_ZOOM_WINDOWS.keys())


@router.get("/prices/{condition_id}")
async def price_history(
    condition_id: str,
    signal_time: float = Query(...),
    zoom: str = Query("6h"),
) -> dict[str, Any]:
    """Price history for a market around a signal time, with zoom levels."""
    cid = condition_id
    signal_dt = datetime.fromtimestamp(signal_time, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

    # Validate zoom against whitelist (prevents any injection)
    if zoom not in _VALID_ZOOMS:
        zoom = "6h"

    window_spec = _ZOOM_WINDOWS[zoom]
    interval, _bucket_fn = window_spec

    if interval is not None:
        # Time-based window: symmetric around signal.
        # interval is from a fixed whitelist, safe to interpolate.
        trade_time_clause = (
            f"AND t.timestamp >= {{sig_dt:String}} - INTERVAL {interval} "
            f"AND t.timestamp <= {{sig_dt:String}} + INTERVAL {interval}"
        )
        ob_time_clause = (
            f"AND timestamp >= {{sig_dt:String}} - INTERVAL {interval} "
            f"AND timestamp <= {{sig_dt:String}} + INTERVAL {interval}"
        )
    else:
        trade_time_clause = ""
        ob_time_clause = ""

    trades_q = ch_query(
        f"""
        SELECT
            toString(t.timestamp) AS ts,
            coalesce(tm.outcome, 'UNKNOWN') AS outcome,
            t.price AS price,
            t.amount_usd AS size_usd,
            t.side AS side
        FROM trades_raw t
        LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
        WHERE t.condition_id = {{cid:String}}
          {trade_time_clause}
        ORDER BY t.timestamp
        LIMIT 5000
        """,
        {"cid": cid, "sig_dt": signal_dt},
    )

    ob_q = ch_query(
        f"""
        SELECT
            toString(timestamp) AS ts,
            best_bid AS bid,
            best_ask AS ask
        FROM orderbook_l2
        WHERE condition_id = {{cid:String}}
          {ob_time_clause}
          AND best_bid > 0 AND best_ask > 0
        ORDER BY timestamp
        LIMIT 5000
        """,
        {"cid": cid, "sig_dt": signal_dt},
    )

    trades, ob = await asyncio.gather(trades_q, ob_q)
    return {
        "zoom": zoom,
        "signal_time": signal_time,
        "points": trades,
        "ob_series": ob,
    }
