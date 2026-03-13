"""Intent listing and detail endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import LOG_DIR, ch_query, read_all_jsonl, strategy_dirs

router = APIRouter(prefix="/api/v1", tags=["intents"])


@router.get("/intents")
async def list_intents(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    dirs = [strategy] if strategy else strategy_dirs()
    results: list[dict[str, Any]] = []
    for name in dirs:
        all_recs = read_all_jsonl(LOG_DIR / name / "intents.jsonl")
        for idx, rec in enumerate(all_recs):
            rec["_source"] = name
            rec["_index"] = idx
        results.extend(all_recs[-n:])
    results.sort(key=lambda r: r.get("signal_time", 0), reverse=True)
    return {"count": len(results[:n]), "intents": results[:n]}


@router.get("/intent/{strategy}/{index}")
async def intent_detail(strategy: str, index: int) -> dict[str, Any]:
    """Full detail view for a single intent.

    Returns intent data, matched fill, market metadata, current price,
    orderbook snapshot near signal time, price history, and expected PnL.
    """
    all_intents = read_all_jsonl(LOG_DIR / strategy / "intents.jsonl")
    if index < 0 or index >= len(all_intents):
        return {"error": "intent not found", "total": len(all_intents)}

    intent = all_intents[index]
    cid = intent.get("condition_id", "")
    asset_id = intent.get("asset_id", "")
    signal_time = intent.get("signal_time", 0)

    # Match fill
    all_fills = read_all_jsonl(LOG_DIR / strategy / "fills.jsonl")
    fill = None
    for f in all_fills:
        if (
            f.get("condition_id") == cid
            and f.get("strategy") == intent.get("strategy")
            and abs((f.get("filled_at") or 0) - signal_time) < 120
        ):
            fill = f
            break

    signal_dt = (
        datetime.fromtimestamp(signal_time, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        if signal_time
        else "2020-01-01 00:00:00"
    )

    # Build asset_id filter clause (parameterized)
    asset_clause = "AND asset_id = {aid:String}" if asset_id else ""
    asset_params: dict[str, Any] = {"aid": asset_id} if asset_id else {}

    # Parallel CH queries -- all parameterized
    market_q = ch_query(
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
        {"cid": cid},
    )

    price_q = ch_query(
        f"""
        SELECT price, side, amount_usd, timestamp
        FROM trades_raw
        WHERE condition_id = {{cid:String}} {asset_clause}
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        {"cid": cid, **asset_params},
    )

    signal_price_q = ch_query(
        f"""
        SELECT price, timestamp
        FROM trades_raw
        WHERE condition_id = {{cid:String}} {asset_clause}
          AND timestamp <= {{sig_dt:String}}
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        {"cid": cid, "sig_dt": signal_dt, **asset_params},
    )

    ob_q = ch_query(
        """
        SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
        FROM orderbook_l2
        WHERE condition_id = {cid:String}
          AND timestamp >= {sig_dt:String} - INTERVAL 2 MINUTE
          AND timestamp <= {sig_dt:String} + INTERVAL 2 MINUTE
        ORDER BY abs(toUnixTimestamp(timestamp) - {sig_ts:Int64})
        LIMIT 1
        """,
        {"cid": cid, "sig_dt": signal_dt, "sig_ts": int(signal_time)},
    )

    price_hist_q = ch_query(
        """
        SELECT
            toStartOfMinute(t.timestamp) AS minute,
            coalesce(tm.outcome, 'UNKNOWN') AS outcome,
            avg(t.price) AS avg_price,
            count() AS trades
        FROM trades_raw t
        LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
        WHERE t.condition_id = {cid:String}
          AND t.timestamp >= {sig_dt:String} - INTERVAL 6 HOUR
          AND t.timestamp <= {sig_dt:String} + INTERVAL 6 HOUR
        GROUP BY minute, outcome
        ORDER BY minute
        """,
        {"cid": cid, "sig_dt": signal_dt},
    )

    recent_trades_q = ch_query(
        """
        SELECT t.price, t.side, t.amount_usd, t.timestamp, t.source,
               coalesce(tm.outcome, '') AS outcome
        FROM trades_raw t
        LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
        WHERE t.condition_id = {cid:String}
        ORDER BY t.timestamp DESC
        LIMIT 20
        """,
        {"cid": cid},
    )

    market, current_price, signal_price, ob, price_hist, recent_trades = await asyncio.gather(
        market_q, price_q, signal_price_q, ob_q, price_hist_q, recent_trades_q
    )

    # Compute expected PnL
    pnl = None
    if fill and fill.get("status") == "filled" and current_price:
        fp = fill.get("filled_price", 0)
        cp = current_price[0].get("price", 0)
        qty = fill.get("filled_size_usd", 0)
        if fp > 0 and qty > 0:
            shares = qty / fp
            current_value = shares * float(cp)
            pnl = {
                "entry_price": fp,
                "current_price": float(cp),
                "shares": round(shares, 2),
                "cost": round(qty, 2),
                "current_value": round(current_value, 2),
                "unrealized_pnl": round(current_value - qty, 2),
                "pnl_pct": round((current_value - qty) / qty * 100, 2) if qty else 0,
            }

    # If market resolved, compute realized PnL
    resolved_pnl = None
    if fill and fill.get("status") == "filled" and market:
        m = market[0]
        winner = m.get("winner_outcome")
        if winner:
            fp = fill.get("filled_price", 0)
            qty = fill.get("filled_size_usd", 0)
            outcome = intent.get("outcome", "")
            if fp > 0 and qty > 0:
                shares = qty / fp
                won = outcome.upper() == winner.upper()
                payout = shares if won else 0
                resolved_pnl = {
                    "winner": winner,
                    "won": won,
                    "payout": round(payout, 2),
                    "cost": round(qty, 2),
                    "realized_pnl": round(payout - qty, 2),
                }

    return {
        "intent": intent,
        "intent_index": index,
        "total_intents": len(all_intents),
        "fill": fill,
        "market": market[0] if market else None,
        "current_price": current_price[0] if current_price else None,
        "signal_price": signal_price[0] if signal_price else None,
        "orderbook_at_signal": ob[0] if ob else None,
        "price_history": price_hist,
        "recent_trades": recent_trades,
        "pnl": pnl,
        "resolved_pnl": resolved_pnl,
    }
