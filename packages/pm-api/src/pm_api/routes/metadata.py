"""Market and token metadata from ClickHouse (PG engine tables)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from pm_api.deps import ch_query

router = APIRouter(prefix="/api/v1", tags=["metadata"])


@router.get("/pipeline/health")
async def pipeline_health() -> dict[str, Any]:
    """Market and token counts from ClickHouse (PG engine tables)."""
    market_counts = await ch_query("""
        SELECT
            count() AS total_markets,
            countIf(resolved_at IS NOT NULL) AS resolved,
            countIf(resolved_at IS NULL AND closed_at IS NULL) AS open
        FROM markets
    """)

    recent_markets = await ch_query("""
        SELECT condition_id, question, created_at, category
        FROM markets
        WHERE created_at IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 5
    """)

    token_count = await ch_query("SELECT count() AS cnt FROM token_market_map")

    freshness = await ch_query("""
        SELECT max(updated_at) AS last_update FROM markets
    """)

    return {
        "markets": market_counts[0] if market_counts else {},
        "tokens": token_count[0].get("cnt", 0) if token_count else 0,
        "last_update": freshness[0].get("last_update") if freshness else None,
        "recent_markets": recent_markets,
    }
