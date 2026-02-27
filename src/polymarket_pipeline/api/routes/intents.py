"""Strategy intent endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["intents"])


@router.get("/intents")
async def list_intents(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    strategy: str | None = None,
    disposition: str | None = None,
) -> dict:
    """List recent strategy intents, newest first."""
    pool = request.app.state.pool

    where_clauses: list[str] = []
    params: list[object] = []
    idx = 1

    if strategy:
        where_clauses.append(f"si.strategy = ${idx}")
        params.append(strategy)
        idx += 1
    if disposition:
        where_clauses.append(f"si.disposition = ${idx}")
        params.append(disposition)
        idx += 1

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT si.*, m.question, m.slug AS market_slug, e.slug AS event_slug,
                       e.end_date, m.resolved_at, m.winner_outcome
                FROM strategy_intents si
                LEFT JOIN markets m ON si.condition_id = m.condition_id
                LEFT JOIN events e ON m.event_id = e.id
                {where_sql}
                ORDER BY si.captured_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",  # noqa: S608
            *params,
            limit,
            offset,
        )
        total = await conn.fetchval(
            f"SELECT count(*) FROM strategy_intents si{where_sql}",  # noqa: S608
            *params,
        )

    return {
        "intents": [
            {
                "id": r["id"],
                "strategy": r["strategy"],
                "condition_id": r["condition_id"],
                "side": r["side"],
                "outcome": r["outcome"],
                "size_usd": r["size_usd"],
                "max_price": r["max_price"],
                "reason": r["reason"],
                "disposition": r["disposition"],
                "rejection_reason": r["rejection_reason"],
                "filled_price": r["filled_price"],
                "filled_size_usd": r["filled_size_usd"],
                "fee_usd": r["fee_usd"],
                "captured_at": r["captured_at"].isoformat() if r["captured_at"] else None,
                "question": r["question"],
                "event_slug": r["event_slug"],
                "end_date": r["end_date"].isoformat() if r["end_date"] else None,
                "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
                "winner_outcome": r["winner_outcome"] or None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/intents/stats")
async def intent_stats(request: Request) -> dict:
    """Summary stats for strategy intents."""
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT strategy, disposition, count(*) as cnt,
                      coalesce(sum(size_usd), 0) as total_size_usd
               FROM strategy_intents
               GROUP BY strategy, disposition
               ORDER BY strategy, disposition"""
        )
    stats: dict[str, dict] = {}
    for r in rows:
        s = r["strategy"]
        if s not in stats:
            stats[s] = {}
        stats[s][r["disposition"]] = {
            "count": r["cnt"],
            "total_size_usd": round(r["total_size_usd"], 2),
        }
    return {"stats": stats}
