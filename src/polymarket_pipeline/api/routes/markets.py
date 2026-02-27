"""Market browser endpoints — will_no eligible markets + HR pool traders."""

from __future__ import annotations

import json
import re

import structlog
from fastapi import APIRouter, Request

router = APIRouter(tags=["markets"])
log = structlog.get_logger()

# Will-NO keyword defaults (same as WillNoConfig defaults)
_WILL_PATTERN = re.compile(r"^Will\b", re.IGNORECASE)
_DEFAULT_PREFER = {"between", "mlb", "prix", "grand", "league", "park", "traded", "fed"}
_DEFAULT_AVOID = {"reach", "hit"}


@router.get("/markets/will_no")
async def will_no_markets(request: Request, limit: int = 200) -> dict:
    """Return markets eligible for the will_no strategy.

    Applies the same keyword + pattern filter as the strategy to show
    which markets qualify and why.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.condition_id, m.question, m.category, m.slug,
                      e.end_date, e.slug AS event_slug
               FROM markets m
               LEFT JOIN events e ON m.event_id = e.id
               WHERE m.status = 'active'
                 AND m.resolved_at IS NULL
               ORDER BY e.end_date ASC NULLS LAST
               LIMIT $1""",
            limit,
        )

    markets = []
    for r in rows:
        question = r["question"] or ""
        if not _WILL_PATTERN.search(question):
            continue

        q_lower = question.lower()

        # Check avoid keywords
        if any(kw in q_lower for kw in _DEFAULT_AVOID):
            continue

        # Check prefer keywords
        matched_kw = [kw for kw in _DEFAULT_PREFER if kw in q_lower]
        if not matched_kw:
            continue

        markets.append({
            "condition_id": r["condition_id"],
            "question": question,
            "category": r["category"],
            "keywords_matched": matched_kw,
            "end_date": r["end_date"].isoformat() if r["end_date"] else None,
            "event_slug": r["event_slug"],
            "polymarket_url": (
                f"https://polymarket.com/event/{r['event_slug']}"
                if r["event_slug"]
                else None
            ),
        })

    return {"markets": markets, "total": len(markets)}


@router.get("/markets/pool")
async def pool_traders(
    request: Request, strategy: str | None = None
) -> dict:
    """Return copy pool traders with their trade stats from ClickHouse.

    Optionally filter by strategy name (e.g. ``?strategy=hr_pool``).
    """
    pool = request.app.state.pool
    ch = request.app.state.ch_client
    db = request.app.state.ch_database

    # Get pool from PG
    if strategy:
        async with pool.acquire() as conn:
            pg_rows = await conn.fetch(
                """SELECT strategy, trader_address, refreshed_at
                   FROM strategy_pool
                   WHERE strategy = $1
                   ORDER BY trader_address""",
                strategy,
            )
    else:
        async with pool.acquire() as conn:
            pg_rows = await conn.fetch(
                """SELECT strategy, trader_address, refreshed_at
                   FROM strategy_pool
                   ORDER BY strategy, trader_address"""
            )

    if not pg_rows:
        return {"traders": [], "total": 0}

    # Collect unique addresses for CH stats query
    addresses = list({r["trader_address"] for r in pg_rows})

    # Query trade stats from ClickHouse (best-effort)
    stats: dict[str, dict] = {}
    try:
        addr_list = ",".join(f"'{a}'" for a in addresses)
        query = f"""SELECT
                maker,
                count() AS n_trades,
                uniqExact(condition_id) AS n_markets,
                sum(amount_usd) AS volume_usd
            FROM trades_raw FINAL
            WHERE maker IN ({addr_list})
              AND timestamp >= now() - INTERVAL 30 DAY
            GROUP BY maker
            FORMAT JSONEachRow"""

        resp = await ch.post(
            "/",
            content=query,
            params={"database": db},
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if text:
            for line in text.split("\n"):
                if line.strip():
                    row = json.loads(line)
                    stats[row["maker"]] = row
    except Exception:
        log.warning("pool.ch_stats_failed")

    traders = []
    for r in pg_rows:
        addr = r["trader_address"]
        s = stats.get(addr, {})
        traders.append({
            "strategy": r["strategy"],
            "trader_address": addr,
            "n_trades": s.get("n_trades", 0),
            "n_markets": s.get("n_markets", 0),
            "volume_usd": round(float(s.get("volume_usd", 0)), 2),
            "refreshed_at": r["refreshed_at"].isoformat() if r["refreshed_at"] else None,
        })

    return {"traders": traders, "total": len(traders)}
