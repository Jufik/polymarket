"""Intent detail endpoint — price history, PnL, resolution countdown."""

from __future__ import annotations

import json
import re

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["intents"])
log = structlog.get_logger()

_VALID_HEX = re.compile(r"^0x[0-9a-fA-F]+$")
_CLOB_BASE = "https://clob.polymarket.com"


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


@router.get("/intents/{intent_id}/detail")
async def intent_detail(request: Request, intent_id: int) -> dict:
    """Return enriched detail for a single intent (hover panel data)."""
    pool = request.app.state.pool

    # --- Intent + market metadata from PG ---
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT si.*, m.question, m.resolved_at, m.winner_outcome,
                      m.token_yes, m.token_no,
                      e.end_date
               FROM strategy_intents si
               LEFT JOIN markets m ON si.condition_id = m.condition_id
               LEFT JOIN events e ON m.event_id = e.id
               WHERE si.id = $1""",
            intent_id,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Intent not found")

    condition_id = row["condition_id"]
    asset_id = row["asset_id"]

    # If asset_id is missing on the intent, look up from token_market_map
    # Match the intent's outcome (YES/NO) to get the right token
    if not asset_id:
        async with pool.acquire() as conn:
            outcome = row["outcome"] or "YES"
            token_row = await conn.fetchrow(
                "SELECT asset_id FROM token_market_map"
                " WHERE condition_id = $1 AND outcome = $2",
                condition_id,
                outcome,
            )
            if token_row:
                asset_id = token_row["asset_id"]

    # --- Price history from ClickHouse (48h, 5-minute buckets) ---
    price_history: list[dict] = []
    current_price: float | None = None

    # trades_raw.price is always YES-side; flip for NO positions
    outcome = row["outcome"] or "YES"
    is_no = outcome == "NO"

    # Validate hex IDs before interpolating into CH queries
    cid_valid = bool(_VALID_HEX.match(condition_id)) if condition_id else False

    if cid_valid:
        try:
            # Use median to be robust against taker-side dups (which record 1-price)
            price_history = await _ch_query(
                request,
                f"""SELECT
                        toStartOfFiveMinutes(timestamp) AS ts,
                        medianExact(price) AS price
                    FROM trades_raw FINAL
                    WHERE condition_id = '{condition_id}'
                      AND timestamp >= now() - INTERVAL 48 HOUR
                    GROUP BY ts
                    ORDER BY ts""",
            )
            if is_no:
                for p in price_history:
                    p["price"] = 1.0 - float(p["price"])
        except Exception:
            log.warning("intent_detail.price_history_failed", intent_id=intent_id)

    # Current price: CLOB API midpoint (authoritative, token-specific)
    # trades_raw is unreliable here — taker-side dups record 1-price
    if asset_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{_CLOB_BASE}/midpoint",
                    params={"token_id": asset_id},
                )
                resp.raise_for_status()
                mid = resp.json().get("mid")
                if mid:
                    current_price = float(mid)
        except Exception:
            log.warning("intent_detail.clob_midpoint_failed", intent_id=intent_id)

    # Fallback: orderbook snapshot from CH (CLOB WS, YES-side)
    if current_price is None and cid_valid:
        try:
            ob_rows = await _ch_query(
                request,
                f"""SELECT best_bid, best_ask
                    FROM orderbook_snapshots
                    WHERE condition_id = '{condition_id}'
                    ORDER BY timestamp DESC
                    LIMIT 1""",
            )
            if ob_rows:
                bid = float(ob_rows[0]["best_bid"])
                ask = float(ob_rows[0]["best_ask"])
                yes_mid = (bid + ask) / 2
                current_price = (1.0 - yes_mid) if is_no else yes_mid
        except Exception:
            log.warning("intent_detail.orderbook_price_failed", intent_id=intent_id)

    # --- PnL calculation ---
    pnl: dict | None = None
    filled_price = row["filled_price"]
    filled_size_usd = row["filled_size_usd"]

    if filled_price and filled_size_usd and filled_price > 0 and current_price is not None:
        tokens = filled_size_usd / filled_price
        pnl_usd = tokens * current_price - filled_size_usd
        pnl_pct = (pnl_usd / filled_size_usd) * 100 if filled_size_usd else 0
        pnl = {
            "entry_price": round(filled_price, 4),
            "current_price": round(current_price, 4),
            "pnl_usd": round(pnl_usd, 4),
            "pnl_pct": round(pnl_pct, 2),
        }

    # --- Resolution info ---
    resolution = {
        "end_date": row["end_date"].isoformat() if row["end_date"] else None,
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        "winner_outcome": row["winner_outcome"] or None,
    }

    return {
        "id": row["id"],
        "strategy": row["strategy"],
        "condition_id": condition_id,
        "question": row["question"],
        "side": row["side"],
        "outcome": row["outcome"],
        "size_usd": row["size_usd"],
        "filled_price": filled_price,
        "filled_size_usd": filled_size_usd,
        "disposition": row["disposition"],
        "price_history": [
            {"ts": p["ts"], "price": float(p["price"])} for p in price_history
        ],
        "current_price": current_price,
        "pnl": pnl,
        "resolution": resolution,
    }
