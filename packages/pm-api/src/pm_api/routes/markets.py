"""Market list and detail endpoints (placeholder for future expansion)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import ch_query
from pm_api.filters import EqFilter, FilterSet

router = APIRouter(prefix="/api/v1/markets", tags=["markets"])

MARKET_FILTERS = {
    "category": EqFilter("category"),
    "status": EqFilter("status"),
}


@router.get("")
async def list_markets(
    category: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    fs = FilterSet(
        {"category": category, "status": status},
        MARKET_FILTERS,
    )
    where, params = fs.to_sql()

    count_rows = await ch_query(
        f"SELECT count() AS total FROM markets WHERE {where}",
        params,
    )
    total = int(count_rows[0]["total"]) if count_rows else 0

    rows = await ch_query(
        f"""
        SELECT condition_id, question, category, status,
               resolution_value, winner_outcome,
               created_at, closed_at, resolved_at
        FROM markets
        WHERE {where}
        ORDER BY created_at DESC
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


@router.get("/{condition_id}")
async def market_detail(condition_id: str) -> dict[str, Any]:
    rows = await ch_query(
        """
        SELECT m.condition_id, m.question, m.category, m.status,
               m.resolution_value, m.winner_outcome,
               m.created_at, m.closed_at, m.resolved_at,
               e.end_date
        FROM markets m
        LEFT JOIN events e ON m.event_id = e.id
        WHERE m.condition_id = {cid:String}
        LIMIT 1
        """,
        {"cid": condition_id},
    )
    if not rows:
        return {"error": "market not found"}
    return {"market": rows[0]}
