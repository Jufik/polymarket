"""Round-trip (paired BUY+SELL) listing and detail endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import LOG_DIR, ch_query, read_all_jsonl, strategy_dirs

router = APIRouter(prefix="/api/v1", tags=["roundtrips"])


@router.get("/roundtrips")
async def list_roundtrips(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Pair BUY+SELL intents into round trips for scalp strategies."""
    dirs = [strategy] if strategy else strategy_dirs()
    trips: list[dict[str, Any]] = []

    for name in dirs:
        all_intents = read_all_jsonl(LOG_DIR / name / "intents.jsonl")
        all_fills = read_all_jsonl(LOG_DIR / name / "fills.jsonl")

        fill_idx: dict[str, list[dict[str, Any]]] = {}
        for f in all_fills:
            key = f"{f.get('condition_id')}|{f.get('strategy')}|{f.get('side')}"
            fill_idx.setdefault(key, []).append(f)

        groups: dict[str, list[dict[str, Any]]] = {}
        for intent in all_intents:
            key = f"{intent.get('condition_id')}|{intent.get('strategy')}"
            groups.setdefault(key, []).append(intent)

        for _key, intent_group in groups.items():
            buy = next((i for i in intent_group if i.get("side") == "BUY"), None)
            sell = next((i for i in intent_group if i.get("side") == "SELL"), None)
            if buy is None:
                continue

            cid = buy.get("condition_id", "")
            strat = buy.get("strategy", "")

            buy_fills = fill_idx.get(f"{cid}|{strat}|BUY", [])
            sell_fills = fill_idx.get(f"{cid}|{strat}|SELL", [])
            buy_fill = next((f for f in buy_fills if f.get("status") == "filled"), None)
            sell_fill = next((f for f in sell_fills if f.get("status") == "filled"), None)

            entry_price = buy_fill["filled_price"] if buy_fill else buy.get("max_price")
            entry_size = buy_fill["filled_size_usd"] if buy_fill else buy.get("size_usd", 0)
            entry_time = buy.get("signal_time", 0)

            exit_price = sell_fill["filled_price"] if sell_fill else None
            exit_time = sell.get("signal_time") if sell else None

            pnl = None
            hold_s = None
            if buy_fill and entry_price and entry_price > 0:
                tokens = entry_size / entry_price
                if sell_fill and exit_price:
                    pnl = round((exit_price - entry_price) * tokens, 2)
                    hold_s = round(sell_fill.get("filled_at", 0) - buy_fill.get("filled_at", 0), 1)

            reason = buy.get("reason", "")

            status = "open"
            if sell_fill:
                status = "closed"
            elif sell and not sell_fill:
                sell_fill_rej = next((f for f in sell_fills if f.get("status") == "rejected"), None)
                status = "exit_rejected" if sell_fill_rej else "exit_pending"

            trips.append(
                {
                    "config": name,
                    "strategy": strat,
                    "condition_id": cid,
                    "outcome": buy.get("outcome"),
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "entry_size": entry_size,
                    "exit_time": exit_time,
                    "exit_price": exit_price,
                    "hold_s": hold_s,
                    "pnl": pnl,
                    "status": status,
                    "reason": reason,
                    "exit_reason": sell.get("reason", "") if sell else "",
                }
            )

    trips.sort(key=lambda r: r.get("entry_time", 0), reverse=True)

    closed = [t for t in trips if t["status"] == "closed"]
    total_pnl = sum(t["pnl"] or 0 for t in closed)
    wins = sum(1 for t in closed if (t["pnl"] or 0) > 0)
    hit_rate = round(wins / len(closed) * 100, 1) if closed else 0
    avg_hold = round(sum(t["hold_s"] or 0 for t in closed) / len(closed), 1) if closed else 0

    return {
        "count": len(trips[:n]),
        "roundtrips": trips[:n],
        "summary": {
            "total_trips": len(trips),
            "closed": len(closed),
            "open": len(trips) - len(closed),
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": len(closed) - wins,
            "hit_rate": hit_rate,
            "avg_hold_s": avg_hold,
        },
    }


@router.get("/roundtrip/{config}/{condition_id}")
async def roundtrip_detail(config: str, condition_id: str) -> dict[str, Any]:
    """Full detail for a single round trip (paired BUY+SELL)."""
    cid = condition_id
    all_intents = read_all_jsonl(LOG_DIR / config / "intents.jsonl")
    all_fills = read_all_jsonl(LOG_DIR / config / "fills.jsonl")

    buy_intent = next(
        (i for i in all_intents if i.get("condition_id") == cid and i.get("side") == "BUY"),
        None,
    )
    sell_intent = next(
        (i for i in all_intents if i.get("condition_id") == cid and i.get("side") == "SELL"),
        None,
    )
    buy_fill = next(
        (
            f
            for f in all_fills
            if f.get("condition_id") == cid
            and f.get("side") == "BUY"
            and f.get("status") == "filled"
        ),
        None,
    )
    sell_fill = next(
        (
            f
            for f in all_fills
            if f.get("condition_id") == cid
            and f.get("side") == "SELL"
            and f.get("status") == "filled"
        ),
        None,
    )

    if buy_intent is None:
        return {"error": "No BUY intent found for this condition_id"}

    signal_time = buy_intent.get("signal_time", 0)
    outcome = buy_intent.get("outcome", "")
    strat = buy_intent.get("strategy", "")

    # PnL computation
    pnl = None
    hold_s = None
    if buy_fill and sell_fill:
        entry_p = buy_fill["filled_price"]
        exit_p = sell_fill["filled_price"]
        entry_size = buy_fill["filled_size_usd"]
        if entry_p > 0:
            tokens = entry_size / entry_p
            pnl = round((exit_p - entry_p) * tokens, 4)
            hold_s = round(sell_fill.get("filled_at", 0) - buy_fill.get("filled_at", 0), 1)

    signal_dt = (
        datetime.fromtimestamp(signal_time, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        if signal_time
        else "2020-01-01 00:00:00"
    )
    exit_time = sell_intent.get("signal_time", 0) if sell_intent else 0
    exit_dt = (
        datetime.fromtimestamp(exit_time, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        if exit_time
        else signal_dt
    )

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

    price_hist_q = ch_query(
        """
        SELECT
            toString(t.timestamp) AS ts,
            coalesce(tm.outcome, 'UNKNOWN') AS outcome,
            t.price AS price,
            t.amount_usd AS size_usd,
            t.side AS side
        FROM trades_raw t
        LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
        WHERE t.condition_id = {cid:String}
          AND t.timestamp >= {sig_dt:String} - INTERVAL 5 MINUTE
          AND t.timestamp <= {exit_dt:String} + INTERVAL 5 MINUTE
        ORDER BY t.timestamp
        LIMIT 5000
        """,
        {"cid": cid, "sig_dt": signal_dt, "exit_dt": exit_dt},
    )

    ob_entry_q = ch_query(
        """
        SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
        FROM orderbook_l2
        WHERE condition_id = {cid:String}
          AND timestamp >= {sig_dt:String} - INTERVAL 1 MINUTE
          AND timestamp <= {sig_dt:String} + INTERVAL 1 MINUTE
        ORDER BY abs(toUnixTimestamp(timestamp) - {sig_ts:Int64})
        LIMIT 1
        """,
        {"cid": cid, "sig_dt": signal_dt, "sig_ts": int(signal_time)},
    )

    async def _empty_list() -> list[dict[str, Any]]:
        return []

    ob_exit_q = (
        ch_query(
            """
            SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
            FROM orderbook_l2
            WHERE condition_id = {cid:String}
              AND timestamp >= {exit_dt:String} - INTERVAL 1 MINUTE
              AND timestamp <= {exit_dt:String} + INTERVAL 1 MINUTE
            ORDER BY abs(toUnixTimestamp(timestamp) - {exit_ts:Int64})
            LIMIT 1
            """,
            {"cid": cid, "exit_dt": exit_dt, "exit_ts": int(exit_time)},
        )
        if exit_time
        else _empty_list()
    )

    ob_series_q = ch_query(
        """
        SELECT
            toString(timestamp) AS ts,
            best_bid AS bid,
            best_ask AS ask
        FROM orderbook_l2
        WHERE condition_id = {cid:String}
          AND timestamp >= {sig_dt:String} - INTERVAL 5 MINUTE
          AND timestamp <= {exit_dt:String} + INTERVAL 5 MINUTE
          AND best_bid > 0 AND best_ask > 0
        ORDER BY timestamp
        LIMIT 5000
        """,
        {"cid": cid, "sig_dt": signal_dt, "exit_dt": exit_dt},
    )

    market, price_hist, ob_entry, ob_exit, ob_series = await asyncio.gather(
        market_q, price_hist_q, ob_entry_q, ob_exit_q, ob_series_q
    )

    return {
        "config": config,
        "strategy": strat,
        "condition_id": cid,
        "outcome": outcome,
        "buy_intent": buy_intent,
        "sell_intent": sell_intent,
        "buy_fill": buy_fill,
        "sell_fill": sell_fill,
        "market": market[0] if market else None,
        "price_history": price_hist,
        "ob_at_entry": ob_entry[0] if ob_entry else None,
        "ob_at_exit": ob_exit[0] if ob_exit else None,
        "ob_series": ob_series,
        "pnl": pnl,
        "hold_s": hold_s,
    }
