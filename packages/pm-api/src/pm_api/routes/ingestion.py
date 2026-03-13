"""Ingestion monitoring: per-source trade counts, TPS, timeseries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import ch_query

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


@router.get("/ingestion")
async def ingestion(hours: int = Query(1, ge=1, le=24)) -> dict[str, Any]:
    """Per-source trade counts, TPS, and latest trade timestamp."""
    # hours is an integer already validated by Query(ge=1, le=24), safe to
    # interpolate into SQL interval literals (CH doesn't parameterize intervals).
    summary = await ch_query(f"""
        SELECT
            source,
            count() AS trades,
            round(count() / {hours * 3600}, 2) AS tps,
            max(timestamp) AS latest
        FROM trades_raw
        WHERE timestamp > now() - INTERVAL {hours} HOUR
        GROUP BY source
        ORDER BY trades DESC
    """)

    total = await ch_query(f"""
        SELECT
            count() AS trades,
            round(count() / {hours * 3600}, 2) AS tps,
            max(timestamp) AS latest,
            min(timestamp) AS earliest
        FROM trades_raw
        WHERE timestamp > now() - INTERVAL {hours} HOUR
    """)

    tps_series = await ch_query(f"""
        SELECT
            toStartOfMinute(timestamp) AS minute,
            source,
            count() AS cnt
        FROM trades_raw
        WHERE timestamp > now() - INTERVAL {hours} HOUR
        GROUP BY minute, source
        ORDER BY minute
    """)

    return {
        "hours": hours,
        "by_source": summary,
        "total": total[0] if total else {},
        "tps_series": tps_series,
    }
