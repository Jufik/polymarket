"""Insider strategy monitoring endpoints.

Provides two views:
    GET /insiders/pool    — full insider pool with scoring breakdown from CH
    GET /insiders/signals — active consensus signals + matched intents from PG
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Request

router = APIRouter(tags=["insiders"])
log = structlog.get_logger()


async def _ch_query(request: Request, query: str) -> list[dict]:
    """Execute a ClickHouse query and return rows as dicts."""
    ch = request.app.state.ch_client
    db = request.app.state.ch_database
    resp = await ch.post(
        "/",
        content=f"{query} FORMAT JSONEachRow",
        params={"database": db},
        headers={"Content-Type": "text/plain"},
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return []
    return [json.loads(line) for line in text.split("\n") if line.strip()]


# ---------------------------------------------------------------------------
# SQL: insider pool scored (same as InsiderCopyProvider._INSIDER_POOL_SQL
# but returns richer columns for the UI)
# ---------------------------------------------------------------------------

_INSIDER_POOL_SQL = """\
WITH resolved_susceptible AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        p.avg_yes_price,
        p.resolved_at,
        ms.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN market_susceptibility AS ms ON p.condition_id = ms.condition_id
    WHERE ms.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback} MONTH
),
trader_stats AS (
    SELECT
        trader,
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct,
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
    FROM resolved_susceptible
    GROUP BY trader
    HAVING count(*) >= {min_positions}
),
scored AS (
    SELECT
        *,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total)
                >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM trader_stats
)
SELECT
    lower(trader) AS trader,
    effective_hr,
    best_direction,
    hr_excess,
    high_pct,
    total_positions,
    yes_wins, yes_total,
    no_wins, no_total,
    total_pnl,
    avg_volume
FROM scored
WHERE effective_hr >= {min_hr}
  AND effective_hr < {max_hr}
  AND high_pct >= {min_high_pct}
ORDER BY hr_excess DESC
"""

# Recent insider trades in the last N hours (for live signals)
_RECENT_INSIDER_TRADES_SQL = """\
SELECT
    t.condition_id,
    lower(t.maker) AS maker,
    t.asset_id,
    t.price,
    t.amount_usd,
    t.timestamp,
    m.question,
    e.slug AS event_slug
FROM trades_raw FINAL AS t
LEFT JOIN markets AS m ON t.condition_id = m.condition_id
LEFT JOIN events AS e ON m.event_id = e.id
WHERE lower(t.maker) IN ({addr_list})
  AND t.side = 'BUY'
  AND t.timestamp >= now() - INTERVAL {hours} HOUR
ORDER BY t.timestamp DESC
"""


@router.get("/insiders/pool")
async def insider_pool(
    request: Request,
    lookback_months: int = 12,
    min_positions: int = 3,
    min_hr: float = 0.75,
    max_hr: float = 0.99,
    min_high_pct: float = 0.20,
) -> dict:
    """Return the full insider pool with scoring breakdown from ClickHouse.

    Mirrors the InsiderCopyProvider query but returns richer data for the UI.
    """
    sql = _INSIDER_POOL_SQL.format(
        lookback=lookback_months,
        min_positions=min_positions,
        min_hr=min_hr,
        max_hr=max_hr,
        min_high_pct=min_high_pct,
    )

    try:
        rows = await _ch_query(request, sql)
    except Exception:
        log.exception("insiders.pool_query_failed")
        return {"traders": [], "total": 0, "error": "ClickHouse query failed"}

    traders = []
    for r in rows:
        traders.append({
            "trader": r["trader"],
            "effective_hr": round(float(r["effective_hr"]), 4),
            "best_direction": r["best_direction"],
            "hr_excess": round(float(r["hr_excess"]), 4),
            "high_pct": round(float(r["high_pct"]), 3),
            "total_positions": int(r["total_positions"]),
            "yes_wins": int(r["yes_wins"]),
            "yes_total": int(r["yes_total"]),
            "no_wins": int(r["no_wins"]),
            "no_total": int(r["no_total"]),
            "total_pnl": round(float(r["total_pnl"]), 2),
            "avg_volume": round(float(r["avg_volume"]), 2),
        })

    # Also check if any of these traders are in the live strategy_pool
    live_addresses: set[str] = set()
    try:
        pool = request.app.state.pool
        async with pool.acquire() as conn:
            pg_rows = await conn.fetch(
                """SELECT lower(trader_address) AS addr
                   FROM strategy_pool
                   WHERE strategy = 'insider_copy_provider'"""
            )
            live_addresses = {r["addr"] for r in pg_rows}
    except Exception:
        log.debug("insiders.pg_pool_check_failed")

    for t in traders:
        t["in_live_pool"] = t["trader"] in live_addresses

    return {
        "traders": traders,
        "total": len(traders),
        "params": {
            "lookback_months": lookback_months,
            "min_positions": min_positions,
            "min_hr": min_hr,
            "max_hr": max_hr,
            "min_high_pct": min_high_pct,
        },
    }


@router.get("/insiders/signals")
async def insider_signals(
    request: Request,
    hours: int = 48,
) -> dict:
    """Return active consensus signals — markets where insiders are trading.

    Cross-references with strategy_intents to show which signals triggered.
    """
    # Step 1: Get live pool addresses from PG
    pool = request.app.state.pool
    try:
        async with pool.acquire() as conn:
            pg_rows = await conn.fetch(
                """SELECT lower(trader_address) AS addr
                   FROM strategy_pool
                   WHERE strategy = 'insider_copy_provider'"""
            )
    except Exception:
        log.exception("insiders.signals_pg_failed")
        return {"signals": [], "total": 0, "error": "PG query failed"}

    addresses = [r["addr"] for r in pg_rows]
    if not addresses:
        return {"signals": [], "total": 0, "message": "No insiders in live pool"}

    # Step 2: Query recent insider BUY trades from CH
    addr_list = ",".join(f"'{a}'" for a in addresses)
    sql = _RECENT_INSIDER_TRADES_SQL.format(addr_list=addr_list, hours=hours)

    try:
        trade_rows = await _ch_query(request, sql)
    except Exception:
        log.exception("insiders.signals_ch_failed")
        return {"signals": [], "total": 0, "error": "ClickHouse query failed"}

    # Step 3: Build per-market consensus
    markets: dict[str, dict] = {}
    for r in trade_rows:
        cid = r["condition_id"]
        maker = r["maker"]
        if cid not in markets:
            markets[cid] = {
                "condition_id": cid,
                "question": r.get("question"),
                "event_slug": r.get("event_slug"),
                "insiders": set(),
                "trades": [],
                "first_trade": r["timestamp"],
                "last_trade": r["timestamp"],
                "max_price": float(r["price"]),
                "total_usd": 0.0,
            }
        m = markets[cid]
        m["insiders"].add(maker)
        m["trades"].append({
            "maker": maker[:8] + "..." + maker[-6:] if len(maker) > 16 else maker,
            "price": round(float(r["price"]), 4),
            "amount_usd": round(float(r["amount_usd"]), 2),
            "timestamp": r["timestamp"],
        })
        m["last_trade"] = r["timestamp"]
        m["max_price"] = max(m["max_price"], float(r["price"]))
        m["total_usd"] += float(r["amount_usd"])

    # Step 4: Get matching intents from PG
    intent_map: dict[str, list[dict]] = {}
    if markets:
        cid_list = list(markets.keys())
        try:
            async with pool.acquire() as conn:
                intent_rows = await conn.fetch(
                    """SELECT si.condition_id, si.side, si.outcome,
                              si.size_usd, si.disposition, si.filled_price,
                              si.reason, si.captured_at, si.strategy
                       FROM strategy_intents si
                       WHERE si.condition_id = ANY($1::text[])
                         AND si.strategy LIKE 's2_insider%'
                       ORDER BY si.captured_at DESC""",
                    cid_list,
                )
                for r in intent_rows:
                    cid = r["condition_id"]
                    if cid not in intent_map:
                        intent_map[cid] = []
                    intent_map[cid].append({
                        "strategy": r["strategy"],
                        "side": r["side"],
                        "outcome": r["outcome"],
                        "size_usd": round(float(r["size_usd"]), 2),
                        "disposition": r["disposition"],
                        "filled_price": (
                            round(float(r["filled_price"]), 4)
                            if r["filled_price"]
                            else None
                        ),
                        "reason": r["reason"],
                        "captured_at": r["captured_at"].isoformat()
                        if r["captured_at"]
                        else None,
                    })
        except Exception:
            log.debug("insiders.signals_intents_failed")

    # Step 5: Assemble output
    signals = []
    for cid, m in sorted(
        markets.items(), key=lambda x: len(x[1]["insiders"]), reverse=True
    ):
        signals.append({
            "condition_id": cid,
            "question": m["question"],
            "event_slug": m["event_slug"],
            "polymarket_url": (
                f"https://polymarket.com/event/{m['event_slug']}"
                if m["event_slug"]
                else None
            ),
            "consensus_count": len(m["insiders"]),
            "insider_addresses": [
                a[:8] + "..." + a[-6:] for a in sorted(m["insiders"])
            ],
            "trade_count": len(m["trades"]),
            "total_usd": round(m["total_usd"], 2),
            "max_price": round(m["max_price"], 4),
            "first_trade": m["first_trade"],
            "last_trade": m["last_trade"],
            "intents": intent_map.get(cid, []),
            "triggered": len(intent_map.get(cid, [])) > 0,
        })

    return {
        "signals": signals,
        "total": len(signals),
        "pool_size": len(addresses),
        "lookback_hours": hours,
    }


@router.get("/insiders/overview")
async def insider_overview(request: Request) -> dict:
    """Quick overview stats for the insiders dashboard header."""
    pg = request.app.state.pool

    overview: dict = {
        "pool_size": 0,
        "pool_refreshed_at": None,
        "total_intents": 0,
        "filled_intents": 0,
        "active_signals": 0,
    }

    try:
        async with pg.acquire() as conn:
            # Pool size + last refresh
            row = await conn.fetchrow(
                """SELECT count(*) AS pool_size,
                          max(refreshed_at) AS refreshed_at
                   FROM strategy_pool
                   WHERE strategy = 'insider_copy_provider'"""
            )
            if row:
                overview["pool_size"] = row["pool_size"]
                overview["pool_refreshed_at"] = (
                    row["refreshed_at"].isoformat() if row["refreshed_at"] else None
                )

            # Intent counts
            row = await conn.fetchrow(
                """SELECT count(*) AS total,
                          countIf(disposition = 'filled') AS filled
                   FROM (
                       SELECT disposition FROM strategy_intents
                       WHERE strategy LIKE 's2_insider%'
                   ) sub"""
            )
            if row:
                overview["total_intents"] = row["total"]
                overview["filled_intents"] = row["filled"]

            # Active signals (unique markets with insider intents in last 48h)
            val = await conn.fetchval(
                """SELECT count(DISTINCT condition_id)
                   FROM strategy_intents
                   WHERE strategy LIKE 's2_insider%'
                     AND captured_at >= now() - INTERVAL '48 hours'"""
            )
            overview["active_signals"] = val or 0

    except Exception:
        log.exception("insiders.overview_failed")

    return overview
