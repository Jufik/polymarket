"""Position and fill endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["positions"])


@router.get("/positions")
async def list_positions(request: Request) -> dict:
    """List all open positions with total exposure and market metadata."""
    tracker = request.app.state.tracker
    positions = tracker.get_all_positions()

    # Enrich with market question + strategy from PG strategy_intents
    cids = [p.condition_id for p in positions]
    meta: dict[str, dict] = {}
    if cids:
        try:
            pool = request.app.state.pool
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT DISTINCT ON (si.condition_id)
                              si.condition_id,
                              si.strategy,
                              si.outcome,
                              m.question,
                              e.slug AS event_slug,
                              m.resolved_at,
                              m.winner_outcome,
                              e.end_date
                       FROM strategy_intents si
                       LEFT JOIN markets m ON si.condition_id = m.condition_id
                       LEFT JOIN events e ON m.event_id = e.id
                       WHERE si.condition_id = ANY($1)
                         AND si.disposition = 'filled'
                       ORDER BY si.condition_id, si.captured_at DESC""",
                    cids,
                )
            for r in rows:
                meta[r["condition_id"]] = {
                    "strategy": r["strategy"],
                    "outcome": r["outcome"],
                    "question": r["question"],
                    "event_slug": r["event_slug"],
                    "resolved_at": (
                        r["resolved_at"].isoformat() if r["resolved_at"] else None
                    ),
                    "winner_outcome": r["winner_outcome"],
                    "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                }
        except Exception:
            pass

    return {
        "positions": [
            {
                "condition_id": p.condition_id,
                "asset_id": p.asset_id,
                "side": p.side,
                "size": p.size,
                "avg_entry": p.avg_entry,
                "cost_basis": p.cost_basis,
                "unrealized_pnl": p.unrealized_pnl,
                "last_price": p.last_price,
                # enriched fields
                "question": meta.get(p.condition_id, {}).get("question"),
                "strategy": meta.get(p.condition_id, {}).get("strategy"),
                "outcome": meta.get(p.condition_id, {}).get("outcome"),
                "event_slug": meta.get(p.condition_id, {}).get("event_slug"),
                "resolved_at": meta.get(p.condition_id, {}).get("resolved_at"),
                "winner_outcome": meta.get(p.condition_id, {}).get("winner_outcome"),
                "end_date": meta.get(p.condition_id, {}).get("end_date"),
            }
            for p in positions
        ],
        "total_exposure": tracker.get_total_exposure(),
        "count": len(positions),
    }


@router.get("/positions/{condition_id}")
async def get_position(condition_id: str, request: Request) -> dict:
    """Get position detail for a specific market."""
    tracker = request.app.state.tracker
    pos = tracker.get_position(condition_id)
    if pos is None:
        return {"position": None, "message": "No open position"}
    return {
        "position": {
            "condition_id": pos.condition_id,
            "asset_id": pos.asset_id,
            "side": pos.side,
            "size": pos.size,
            "avg_entry": pos.avg_entry,
            "cost_basis": pos.cost_basis,
            "unrealized_pnl": pos.unrealized_pnl,
            "last_price": pos.last_price,
        }
    }


@router.get("/fills")
async def list_fills(request: Request, limit: int = 50, offset: int = 0) -> dict:
    """List recent fills, paginated."""
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM fills ORDER BY filled_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        total = await conn.fetchval("SELECT count(*) FROM fills")
    return {
        "fills": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
