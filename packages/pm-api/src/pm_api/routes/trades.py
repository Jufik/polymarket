"""Trade listing endpoint with parameterized filters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import ch_query
from pm_api.filters import EqFilter, FilterSet, GteFilter, LteFilter

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

TRADE_FILTERS = {
    "condition_id": EqFilter("condition_id"),
    "source": EqFilter("source"),
    "side": EqFilter("side"),
    "after": GteFilter("timestamp", "ts_after", "String"),
    "before": LteFilter("timestamp", "ts_before", "String"),
    "min_usd": GteFilter("amount_usd", "min_usd"),
}


@router.get("")
async def list_trades(
    condition_id: str | None = None,
    source: str | None = None,
    side: str | None = None,
    after: str | None = None,
    before: str | None = None,
    min_usd: float | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    fs = FilterSet(
        {
            "condition_id": condition_id,
            "source": source,
            "side": side,
            "after": after,
            "before": before,
            "min_usd": min_usd,
        },
        TRADE_FILTERS,
    )
    where, params = fs.to_sql()

    count_rows = await ch_query(
        f"SELECT count() AS total FROM trades_raw WHERE {where}",
        params,
    )
    total = int(count_rows[0]["total"]) if count_rows else 0

    rows = await ch_query(
        f"""
        SELECT trade_id, condition_id, asset_id, price, side,
               amount_usd, source, timestamp
        FROM trades_raw
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {{lim:UInt32}} OFFSET {{off:UInt32}}
        """,
        {**params, "lim": limit, "off": offset},
    )

    return {
        "data": rows,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }
