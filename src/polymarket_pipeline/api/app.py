"""FastAPI service for the Polymarket trading platform.

Serves:
  /api/health           — liveness
  /api/metrics          — request rate (sliding 60s window)
  /api/ingestion        — per-source trade counts + TPS from ClickHouse
  /api/metadata         — token map refresh status from PG
  /api/strategies       — list strategy log dirs
  /api/intents          — recent trade intents (JSONL)
  /api/fills            — recent fills (JSONL)
  /api/activity         — per-strategy summary
  /                     — HTML dashboard (auto-refresh)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
import structlog
import uvicorn
from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

log = structlog.get_logger()

LOG_DIR = Path("logs/paper")
CH_HOST = os.environ.get("PM_CH_HOST", "localhost")
CH_PORT = os.environ.get("PM_CH_PORT", "18123")
CH_DB = os.environ.get("PM_CH_DATABASE", "polymarket")
CH_URL = f"http://{CH_HOST}:{CH_PORT}/"

# ---------------------------------------------------------------------------
# Request metrics — sliding window
# ---------------------------------------------------------------------------

_request_times: deque[float] = deque()
_WINDOW_S = 60.0


def _prune(now: float) -> None:
    cutoff = now - _WINDOW_S
    while _request_times and _request_times[0] < cutoff:
        _request_times.popleft()


# ---------------------------------------------------------------------------
# ClickHouse helpers
# ---------------------------------------------------------------------------

_ch_client: httpx.AsyncClient | None = None
_generic_client: httpx.AsyncClient | None = None


def _get_ch() -> httpx.AsyncClient:
    global _ch_client
    if _ch_client is None:
        _ch_client = httpx.AsyncClient(base_url=CH_URL, timeout=30.0)
    return _ch_client


def _get_ch_generic() -> httpx.AsyncClient:
    global _generic_client
    if _generic_client is None:
        _generic_client = httpx.AsyncClient(timeout=5.0)
    return _generic_client


async def _ch_query(sql: str) -> list[dict]:
    """Run a ClickHouse query, return rows as dicts."""
    try:
        resp = await _get_ch().post(
            "/",
            content=sql + " FORMAT JSONEachRow",
            params={"database": CH_DB},
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()
        rows = []
        for line in resp.text.strip().split("\n"):
            if line.strip():
                rows.append(json.loads(line))
        return rows
    except Exception as e:
        log.warning("ch_query_error", error=str(e))
        return []


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    """Read the last *n* lines from a JSONL file (reads from end)."""
    if not path.exists():
        return []
    try:
        # Read only the tail for large files
        data = path.read_bytes()
        lines = data.decode(errors="replace").splitlines()
        records = []
        for line in lines[-n:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    except Exception:
        return []


def _strategy_dirs() -> list[str]:
    if not LOG_DIR.is_dir():
        return []
    return sorted(
        d.name for d in LOG_DIR.iterdir()
        if d.is_dir() and (d / "intents.jsonl").exists() or (d / "fills.jsonl").exists()
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="Polymarket Trading API", version="0.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix="/api")

    @app.middleware("http")
    async def track_requests(request: Request, call_next) -> Response:
        _request_times.append(time.time())
        return await call_next(request)

    # -- Health & metrics --------------------------------------------------

    @api.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @api.get("/metrics")
    async def metrics() -> dict:
        now = time.time()
        _prune(now)
        count = len(_request_times)
        return {"window_s": _WINDOW_S, "requests": count, "req_per_s": round(count / _WINDOW_S, 2)}

    # -- Ingestion ---------------------------------------------------------

    @api.get("/ingestion")
    async def ingestion(hours: int = Query(1, ge=1, le=24)) -> dict:
        """Per-source trade counts, TPS, and latest trade timestamp."""
        summary = await _ch_query(f"""
            SELECT
                source,
                count() AS trades,
                round(count() / {hours * 3600}, 2) AS tps,
                max(timestamp) AS latest
            FROM trades_raw
            WHERE timestamp > now() - INTERVAL {hours} HOUR
            GROUP BY source
            ORDER BY trades DESC
        """)
        total = await _ch_query(f"""
            SELECT
                count() AS trades,
                round(count() / {hours * 3600}, 2) AS tps,
                max(timestamp) AS latest,
                min(timestamp) AS earliest
            FROM trades_raw
            WHERE timestamp > now() - INTERVAL {hours} HOUR
        """)
        # Per-minute TPS timeseries by source (last N hours)
        tps_series = await _ch_query(f"""
            SELECT
                toStartOfMinute(timestamp) AS minute,
                source,
                count() AS cnt
            FROM trades_raw
            WHERE timestamp > now() - INTERVAL {hours} HOUR
            GROUP BY minute, source
            ORDER BY minute
        """)
        return {
            "hours": hours,
            "by_source": summary,
            "total": total[0] if total else {},
            "tps_series": tps_series,
        }

    # -- Metadata ----------------------------------------------------------

    @api.get("/metadata")
    async def metadata() -> dict:
        """Market and token counts from ClickHouse (PG engine tables)."""
        market_counts = await _ch_query("""
            SELECT
                count() AS total_markets,
                countIf(resolved_at IS NOT NULL) AS resolved,
                countIf(resolved_at IS NULL AND closed_at IS NULL) AS open
            FROM markets
        """)
        recent_markets = await _ch_query("""
            SELECT condition_id, question, created_at, category
            FROM markets
            WHERE created_at IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 5
        """)
        token_count = await _ch_query("SELECT count() AS cnt FROM token_market_map")
        # Latest updated_at as proxy for metadata freshness
        freshness = await _ch_query("""
            SELECT max(updated_at) AS last_update FROM markets
        """)
        return {
            "markets": market_counts[0] if market_counts else {},
            "tokens": token_count[0].get("cnt", 0) if token_count else 0,
            "last_update": freshness[0].get("last_update") if freshness else None,
            "recent_markets": recent_markets,
        }

    # -- Strategy intents & fills ------------------------------------------

    @api.get("/strategies")
    async def strategies() -> dict:
        return {"strategies": _strategy_dirs()}

    @api.get("/intents")
    async def intents(
        strategy: str | None = Query(None),
        n: int = Query(50, ge=1, le=500),
    ) -> dict:
        dirs = [strategy] if strategy else _strategy_dirs()
        results: list[dict] = []
        for name in dirs:
            all_recs = _read_all_jsonl(LOG_DIR / name / "intents.jsonl")
            # Attach index (line number in file) and source
            for idx, rec in enumerate(all_recs):
                rec["_source"] = name
                rec["_index"] = idx
            results.extend(all_recs[-n:])
        results.sort(key=lambda r: r.get("signal_time", 0), reverse=True)
        return {"count": len(results[:n]), "intents": results[:n]}

    @api.get("/fills")
    async def fills(
        strategy: str | None = Query(None),
        n: int = Query(50, ge=1, le=500),
    ) -> dict:
        dirs = [strategy] if strategy else _strategy_dirs()
        results: list[dict] = []
        for name in dirs:
            for rec in _tail_jsonl(LOG_DIR / name / "fills.jsonl", n):
                rec["_source"] = name
                results.append(rec)
        results.sort(key=lambda r: r.get("filled_at", 0), reverse=True)
        return {"count": len(results[:n]), "fills": results[:n]}

    @api.get("/activity")
    async def activity(
        strategy: str | None = Query(None),
        n: int = Query(20, ge=1, le=200),
    ) -> dict:
        """Per-strategy-leg activity summary.

        Groups by the inner `strategy` field (e.g. politics_no_v3)
        rather than the log directory (e.g. portfolio_v3).
        """
        dirs = [strategy] if strategy else _strategy_dirs()
        summary: dict[str, dict] = {}
        for name in dirs:
            intents_data = _read_all_jsonl(LOG_DIR / name / "intents.jsonl")
            fills_data = _read_all_jsonl(LOG_DIR / name / "fills.jsonl")

            # Group by strategy leg name
            leg_intents: dict[str, list[dict]] = {}
            for rec in intents_data:
                leg = rec.get("strategy", name)
                leg_intents.setdefault(leg, []).append(rec)

            leg_fills: dict[str, list[dict]] = {}
            for rec in fills_data:
                leg = rec.get("strategy", name)
                leg_fills.setdefault(leg, []).append(rec)

            all_legs = set(leg_intents) | set(leg_fills)
            for leg in all_legs:
                li = leg_intents.get(leg, [])
                lf = leg_fills.get(leg, [])
                filled = [f for f in lf if f.get("status") == "filled"]
                rejected = [f for f in lf if f.get("status") == "rejected"]
                total_usd = sum(f.get("filled_size_usd", 0) for f in filled)
                summary[leg] = {
                    "total_intents": len(li),
                    "total_fills": len(filled),
                    "total_rejected": len(rejected),
                    "total_filled_usd": round(total_usd, 2),
                    "recent_intents": li[-n:],
                    "recent_fills": lf[-n:],
                    "config": name,
                }
        return summary

    # -- Intent detail -----------------------------------------------------

    def _read_all_jsonl(path: Path) -> list[dict]:
        """Read all lines from a JSONL file."""
        if not path.exists():
            return []
        try:
            records = []
            for line in path.read_text(errors="replace").splitlines():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return records
        except Exception:
            return []

    @api.get("/intent/{strategy}/{index}")
    async def intent_detail(strategy: str, index: int) -> dict:
        """Full detail view for a single intent.

        Returns intent data, matched fill, market metadata, current price,
        orderbook snapshot near signal time, price history, and expected PnL.
        """
        all_intents = _read_all_jsonl(LOG_DIR / strategy / "intents.jsonl")
        if index < 0 or index >= len(all_intents):
            return {"error": "intent not found", "total": len(all_intents)}

        intent = all_intents[index]
        cid = intent.get("condition_id", "")
        asset_id = intent.get("asset_id", "")
        signal_time = intent.get("signal_time", 0)

        # Match fill
        all_fills = _read_all_jsonl(LOG_DIR / strategy / "fills.jsonl")
        fill = None
        for f in all_fills:
            if (
                f.get("condition_id") == cid
                and f.get("strategy") == intent.get("strategy")
                and abs((f.get("filled_at") or 0) - signal_time) < 120
            ):
                fill = f
                break

        # Parallel CH queries
        signal_dt = datetime.fromtimestamp(signal_time, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if signal_time else "2020-01-01 00:00:00"

        # Market metadata (join events for end_date)
        market_q = _ch_query(f"""
            SELECT m.condition_id, m.question, m.category, m.status,
                   m.resolution_value, m.winner_outcome,
                   m.created_at, m.closed_at, m.resolved_at,
                   e.end_date
            FROM markets m
            LEFT JOIN events e ON m.event_id = e.id
            WHERE m.condition_id = '{cid}'
            LIMIT 1
        """)

        # Current price (last trade for this asset_id — outcome-specific)
        asset_filter = f"AND asset_id = '{asset_id}'" if asset_id else ""
        price_q = _ch_query(f"""
            SELECT price, side, amount_usd, timestamp
            FROM trades_raw
            WHERE condition_id = '{cid}' {asset_filter}
            ORDER BY timestamp DESC
            LIMIT 1
        """)

        # Price at signal time (trade closest before signal, same outcome)
        signal_price_q = _ch_query(f"""
            SELECT price, timestamp
            FROM trades_raw
            WHERE condition_id = '{cid}' {asset_filter}
              AND timestamp <= '{signal_dt}'
            ORDER BY timestamp DESC
            LIMIT 1
        """)

        # Orderbook snapshot nearest to signal time
        ob_q = _ch_query(f"""
            SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
            FROM orderbook_snapshots
            WHERE condition_id = '{cid}'
              AND timestamp >= '{signal_dt}' - INTERVAL 2 MINUTE
              AND timestamp <= '{signal_dt}' + INTERVAL 2 MINUTE
            ORDER BY abs(toUnixTimestamp(timestamp) - {int(signal_time)})
            LIMIT 1
        """)

        # Price history — default 6h symmetric window around signal
        price_hist_q = _ch_query(f"""
            SELECT
                toStartOfMinute(t.timestamp) AS minute,
                coalesce(tm.outcome, 'UNKNOWN') AS outcome,
                avg(t.price) AS avg_price,
                count() AS trades
            FROM trades_raw t
            LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
            WHERE t.condition_id = '{cid}'
              AND t.timestamp >= '{signal_dt}' - INTERVAL 6 HOUR
              AND t.timestamp <= '{signal_dt}' + INTERVAL 6 HOUR
            GROUP BY minute, outcome
            ORDER BY minute
        """)

        # Recent trades for this market (last 20)
        recent_trades_q = _ch_query(f"""
            SELECT t.price, t.side, t.amount_usd, t.timestamp, t.source,
                   coalesce(tm.outcome, '') AS outcome
            FROM trades_raw t
            LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
            WHERE t.condition_id = '{cid}'
            ORDER BY t.timestamp DESC
            LIMIT 20
        """)

        market, current_price, signal_price, ob, price_hist, recent_trades = (
            await asyncio.gather(
                market_q, price_q, signal_price_q, ob_q, price_hist_q, recent_trades_q
            )
        )

        # Compute expected PnL
        pnl = None
        if fill and fill.get("status") == "filled" and current_price:
            fp = fill.get("filled_price", 0)
            cp = current_price[0].get("price", 0)
            qty = fill.get("filled_size_usd", 0)
            if fp > 0 and qty > 0:
                # For BUY: pnl = (current - entry) * qty / entry
                # Simplified: shares = qty / entry_price, value = shares * current_price
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
                side = intent.get("side", "")
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

    # -- Round trips (paired BUY+SELL) -------------------------------------

    @api.get("/roundtrips")
    async def roundtrips(
        strategy: str | None = Query(None),
        n: int = Query(50, ge=1, le=500),
    ) -> dict:
        """Pair BUY+SELL intents into round trips for scalp strategies."""
        dirs = [strategy] if strategy else _strategy_dirs()
        trips: list[dict] = []

        for name in dirs:
            all_intents = _read_all_jsonl(LOG_DIR / name / "intents.jsonl")
            all_fills = _read_all_jsonl(LOG_DIR / name / "fills.jsonl")

            # Index fills by condition_id+strategy+side for fast lookup
            fill_idx: dict[str, list[dict]] = {}
            for f in all_fills:
                key = f"{f.get('condition_id')}|{f.get('strategy')}|{f.get('side')}"
                fill_idx.setdefault(key, []).append(f)

            # Group intents by condition_id+strategy
            groups: dict[str, list[dict]] = {}
            for intent in all_intents:
                key = f"{intent.get('condition_id')}|{intent.get('strategy')}"
                groups.setdefault(key, []).append(intent)

            for key, intent_group in groups.items():
                buy = next((i for i in intent_group if i.get("side") == "BUY"), None)
                sell = next((i for i in intent_group if i.get("side") == "SELL"), None)
                if buy is None:
                    continue

                cid = buy.get("condition_id", "")
                strat = buy.get("strategy", "")

                # Match fills
                buy_fills = fill_idx.get(f"{cid}|{strat}|BUY", [])
                sell_fills = fill_idx.get(f"{cid}|{strat}|SELL", [])
                buy_fill = next(
                    (f for f in buy_fills if f.get("status") == "filled"), None
                )
                sell_fill = next(
                    (f for f in sell_fills if f.get("status") == "filled"), None
                )

                entry_price = buy_fill["filled_price"] if buy_fill else buy.get("max_price")
                entry_size = buy_fill["filled_size_usd"] if buy_fill else buy.get("size_usd", 0)
                entry_time = buy.get("signal_time", 0)

                exit_price = sell_fill["filled_price"] if sell_fill else None
                exit_time = sell.get("signal_time") if sell else None

                # PnL: for BUY side, tokens = size / price, sell value = tokens * exit_price
                pnl = None
                hold_s = None
                if buy_fill and entry_price and entry_price > 0:
                    tokens = entry_size / entry_price
                    if sell_fill and exit_price:
                        pnl = round((exit_price - entry_price) * tokens, 2)
                        hold_s = round(
                            (sell_fill.get("filled_at", 0) - buy_fill.get("filled_at", 0)), 1
                        )

                # Parse key signal info from reason string
                reason = buy.get("reason", "")

                status = "open"
                if sell_fill:
                    status = "closed"
                elif sell and not sell_fill:
                    sell_fill_rej = next(
                        (f for f in sell_fills if f.get("status") == "rejected"), None
                    )
                    status = "exit_rejected" if sell_fill_rej else "exit_pending"

                trips.append({
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
                })

        trips.sort(key=lambda r: r.get("entry_time", 0), reverse=True)
        # Summary stats
        closed = [t for t in trips if t["status"] == "closed"]
        total_pnl = sum(t["pnl"] or 0 for t in closed)
        wins = sum(1 for t in closed if (t["pnl"] or 0) > 0)
        hit_rate = round(wins / len(closed) * 100, 1) if closed else 0
        avg_hold = round(
            sum(t["hold_s"] or 0 for t in closed) / len(closed), 1
        ) if closed else 0

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

    # -- Round trip detail ---------------------------------------------------

    @api.get("/roundtrip/{config}/{condition_id}")
    async def roundtrip_detail(config: str, condition_id: str) -> dict:
        """Full detail for a single round trip (paired BUY+SELL)."""
        cid = condition_id
        all_intents = _read_all_jsonl(LOG_DIR / config / "intents.jsonl")
        all_fills = _read_all_jsonl(LOG_DIR / config / "fills.jsonl")

        buy_intent = next(
            (i for i in all_intents if i.get("condition_id") == cid and i.get("side") == "BUY"),
            None,
        )
        sell_intent = next(
            (i for i in all_intents if i.get("condition_id") == cid and i.get("side") == "SELL"),
            None,
        )
        buy_fill = next(
            (f for f in all_fills
             if f.get("condition_id") == cid and f.get("side") == "BUY"
             and f.get("status") == "filled"),
            None,
        )
        sell_fill = next(
            (f for f in all_fills
             if f.get("condition_id") == cid and f.get("side") == "SELL"
             and f.get("status") == "filled"),
            None,
        )

        if buy_intent is None:
            return {"error": "No BUY intent found for this condition_id"}

        signal_time = buy_intent.get("signal_time", 0)
        asset_id = buy_intent.get("asset_id", "")
        outcome = buy_intent.get("outcome", "")
        strategy = buy_intent.get("strategy", "")

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

        signal_dt = datetime.fromtimestamp(signal_time, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if signal_time else "2020-01-01 00:00:00"
        exit_time = sell_intent.get("signal_time", 0) if sell_intent else 0
        exit_dt = datetime.fromtimestamp(exit_time, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if exit_time else signal_dt

        # Parallel CH queries
        asset_filter = f"AND asset_id = '{asset_id}'" if asset_id else ""

        market_q = _ch_query(f"""
            SELECT m.condition_id, m.question, m.category, m.status,
                   m.resolution_value, m.winner_outcome,
                   m.created_at, m.closed_at, m.resolved_at,
                   e.end_date
            FROM markets m
            LEFT JOIN events e ON m.event_id = e.id
            WHERE m.condition_id = '{cid}'
            LIMIT 1
        """)

        # Price history window: from 5min before entry to 5min after exit (or now)
        price_hist_q = _ch_query(f"""
            SELECT
                toStartOfMinute(t.timestamp) AS minute,
                coalesce(tm.outcome, 'UNKNOWN') AS outcome,
                avg(t.price) AS avg_price,
                count() AS trades
            FROM trades_raw t
            LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
            WHERE t.condition_id = '{cid}'
              AND t.timestamp >= '{signal_dt}' - INTERVAL 5 MINUTE
              AND t.timestamp <= '{exit_dt}' + INTERVAL 5 MINUTE
            GROUP BY minute, outcome
            ORDER BY minute
        """)

        # Orderbook at entry
        ob_entry_q = _ch_query(f"""
            SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
            FROM orderbook_snapshots
            WHERE condition_id = '{cid}'
              AND timestamp >= '{signal_dt}' - INTERVAL 1 MINUTE
              AND timestamp <= '{signal_dt}' + INTERVAL 1 MINUTE
            ORDER BY abs(toUnixTimestamp(timestamp) - {int(signal_time)})
            LIMIT 1
        """)

        # Orderbook at exit
        async def _empty_list() -> list:
            return []

        ob_exit_q = _ch_query(f"""
            SELECT best_bid, best_ask, bid_depth_usd, ask_depth_usd, timestamp
            FROM orderbook_snapshots
            WHERE condition_id = '{cid}'
              AND timestamp >= '{exit_dt}' - INTERVAL 1 MINUTE
              AND timestamp <= '{exit_dt}' + INTERVAL 1 MINUTE
            ORDER BY abs(toUnixTimestamp(timestamp) - {int(exit_time)})
            LIMIT 1
        """) if exit_time else _empty_list()

        market, price_hist, ob_entry, ob_exit = await asyncio.gather(
            market_q, price_hist_q, ob_entry_q, ob_exit_q
        )

        return {
            "config": config,
            "strategy": strategy,
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
            "pnl": pnl,
            "hold_s": hold_s,
        }

    # -- Price history (zoomable) ------------------------------------------

    _ZOOM_WINDOWS = {
        "1h": ("1 HOUR", "toStartOfMinute"),
        "6h": ("6 HOUR", "toStartOfMinute"),
        "24h": ("24 HOUR", "toStartOfFiveMinutes"),
        "7d": ("7 DAY", "toStartOfHour"),
        "30d": ("30 DAY", "toStartOfHour"),
        "all": (None, "toStartOfHour"),
    }

    @api.get("/prices/{condition_id}")
    async def price_history(
        condition_id: str,
        signal_time: float = Query(...),
        zoom: str = Query("6h"),
    ) -> dict:
        """Price history for a market around a signal time, with zoom levels."""
        cid = condition_id
        signal_dt = datetime.fromtimestamp(signal_time, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        window_spec = _ZOOM_WINDOWS.get(zoom, _ZOOM_WINDOWS["6h"])
        interval, bucket_fn = window_spec

        if interval is not None:
            # Time-based window: symmetric around signal
            where_time = (
                f"AND t.timestamp >= '{signal_dt}' - INTERVAL {interval} "
                f"AND t.timestamp <= '{signal_dt}' + INTERVAL {interval}"
            )
        else:
            # "all" — no time filter
            where_time = ""

        rows = await _ch_query(f"""
            SELECT
                {bucket_fn}(t.timestamp) AS minute,
                coalesce(tm.outcome, 'UNKNOWN') AS outcome,
                avg(t.price) AS avg_price,
                count() AS trades
            FROM trades_raw t
            LEFT JOIN token_market_map tm ON t.asset_id = tm.asset_id
            WHERE t.condition_id = '{cid}'
              {where_time}
            GROUP BY minute, outcome
            ORDER BY minute
        """)
        return {"zoom": zoom, "signal_time": signal_time, "points": rows}

    # -- Strategy introspection proxy --------------------------------------
    # Forward requests to strategy introspect servers running on 8010-8019.
    # Registry: config stem → port (matches supervisord.conf assignments).

    _INTROSPECT_PORTS: dict[str, int] = {
        "s2_insider_copy": 8010,
        "s3_no_sniper": 8011,
        "crypto_gbm": 8012,
        "portfolio_v3": 8013,
    }

    @api.get("/introspect")
    async def list_introspect() -> dict:
        """List all strategy introspect servers and their status."""
        results = []
        for config_name, port in _INTROSPECT_PORTS.items():
            try:
                resp = await _get_ch_generic().get(
                    f"http://localhost:{port}/status", timeout=2.0,
                )
                results.append({
                    "config": config_name,
                    "port": port,
                    "status": "up",
                    "data": resp.json(),
                })
            except Exception:
                results.append({
                    "config": config_name,
                    "port": port,
                    "status": "down",
                })
        return {"servers": results}

    @api.get("/introspect/{config_name}/{path:path}")
    async def proxy_introspect(config_name: str, path: str) -> Response:
        """Proxy to a strategy introspect server."""
        port = _INTROSPECT_PORTS.get(config_name)
        if port is None:
            return Response(
                content=json.dumps({"error": f"Unknown config: {config_name}"}),
                media_type="application/json",
                status_code=404,
            )
        try:
            resp = await _get_ch_generic().get(
                f"http://localhost:{port}/{path}", timeout=5.0,
            )
            return Response(
                content=resp.content,
                media_type="application/json",
                status_code=resp.status_code,
            )
        except Exception as e:
            return Response(
                content=json.dumps({"error": f"Introspect server down: {e}"}),
                media_type="application/json",
                status_code=502,
            )

    # -- HTML dashboard ----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    app.include_router(api)
    return app


# ---------------------------------------------------------------------------
# Dashboard HTML — single-page, fetches /api/* endpoints via JS
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Polymarket Dashboard</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #22c55e; --red: #ef4444; --amber: #eab308; --purple: #a78bfa;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; }
  h2 { font-size: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin: 1.5rem 0 .75rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border-radius: 8px; padding: 1rem 1.25rem; }
  .card .value { font-size: 1.6rem; font-weight: 700; }
  .card .label { font-size: .75rem; color: var(--muted); margin-top: .25rem; }
  .card.green .value { color: var(--green); }
  .card.red .value { color: var(--red); }
  .card.amber .value { color: var(--amber); }
  .card.accent .value { color: var(--accent); }
  .card.purple .value { color: var(--purple); }
  table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 8px; overflow: hidden; margin-bottom: 1.5rem; }
  th { text-align: left; padding: .6rem 1rem; color: var(--muted); font-size: .75rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }
  td { padding: .5rem 1rem; border-bottom: 1px solid var(--border); font-size: .85rem; font-variant-numeric: tabular-nums; }
  tr:last-child td { border-bottom: none; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.green { background: var(--green); }
  .dot.amber { background: var(--amber); }
  .dot.red { background: var(--red); }
  .mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: .8rem; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem; font-weight: 600; }
  .tag.buy { background: #22c55e22; color: var(--green); }
  .tag.sell { background: #ef444422; color: var(--red); }
  .tag.filled { background: #22c55e22; color: var(--green); }
  .tag.rejected { background: #ef444422; color: var(--red); }
  .tag.partial { background: #eab30822; color: var(--amber); }
  .reason { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
  .chart-wrap { background: var(--surface); border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; height: 280px; }
  .refresh-bar { position: fixed; top: 0; left: 0; height: 2px; background: var(--accent); transition: width .3s linear; z-index: 100; }
  .tabs { display: flex; gap: .5rem; margin-bottom: 1rem; }
  .tabs button { background: var(--surface); color: var(--muted); border: 1px solid var(--border); border-radius: 6px; padding: .4rem 1rem; cursor: pointer; font-size: .8rem; }
  .tabs button.active { background: var(--accent); color: var(--bg); border-color: var(--accent); }
  footer { color: var(--muted); font-size: .75rem; margin-top: 2rem; }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<div class="refresh-bar" id="refreshBar"></div>
<h1>Polymarket Pipeline</h1>

<!-- Ingestion -->
<h2>Ingestion</h2>
<div class="grid" id="ingestionCards"></div>
<div class="chart-wrap"><canvas id="tpsChart"></canvas></div>

<!-- Metadata -->
<h2>Metadata</h2>
<div class="grid" id="metadataCards"></div>

<!-- Strategy Activity -->
<h2>Strategy Activity</h2>
<div class="grid" id="strategyCards"></div>

<!-- Intents & Fills -->
<h2>Recent Intents &amp; Fills</h2>
<div class="tabs" id="intentTabs"></div>
<table id="intentTable"><thead><tr>
  <th>Time</th><th>Strategy</th><th>Side</th><th>Outcome</th>
  <th>Size</th><th>Max Price</th><th>Fill Price</th><th>Status</th><th>Reason</th>
</tr></thead><tbody id="intentBody"></tbody></table>

<footer id="footer"></footer>

<script>
const API = '/api';
const REFRESH_MS = 10000;
let tpsChart = null;
let currentStrategy = null;

function fmt(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function fmtDt(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleString('en-GB', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}); }
  catch { return s; }
}
function shortCid(cid) { return cid ? cid.slice(0,10) + '...' : '—'; }
function card(value, label, cls='') { return `<div class="card ${cls}"><div class="value">${value}</div><div class="label">${label}</div></div>`; }

async function fetchJson(url) {
  try { const r = await fetch(url); return await r.json(); }
  catch { return null; }
}

async function refreshIngestion() {
  const d = await fetchJson(API + '/ingestion?hours=1');
  if (!d) return;
  const el = document.getElementById('ingestionCards');
  let html = '';
  const t = d.total || {};
  html += card(Number(t.trades || 0).toLocaleString(), 'Trades (1h)', 'accent');
  html += card(t.tps || '0', 'Avg TPS', 'green');
  html += card(fmtDt(t.latest), 'Latest Trade');
  for (const s of (d.by_source || [])) {
    html += card(`${Number(s.trades).toLocaleString()} <span style="font-size:.8rem;color:var(--muted)">(${s.tps}/s)</span>`, s.source, 'purple');
  }
  el.innerHTML = html;
  updateTpsChart(d.tps_series || []);
}

function updateTpsChart(series) {
  // Group by minute, sum across sources for total, keep per-source
  const byMinute = {};
  const sources = new Set();
  for (const row of series) {
    const m = row.minute;
    sources.add(row.source);
    if (!byMinute[m]) byMinute[m] = {};
    byMinute[m][row.source] = Number(row.cnt);
  }
  const minutes = Object.keys(byMinute).sort();
  const labels = minutes.map(m => m.slice(11, 16));
  const srcArr = [...sources].sort();
  const colors = { alchemy: '#a78bfa', rtds: '#38bdf8', goldsky_subgraph: '#fbbf24', pending_block: '#34d399' };
  const datasets = srcArr.map(src => ({
    label: src,
    data: minutes.map(m => (byMinute[m][src] || 0) / 60),
    borderColor: colors[src] || '#94a3b8',
    backgroundColor: (colors[src] || '#94a3b8') + '33',
    fill: true, tension: 0.3, pointRadius: 0,
  }));

  const ctx = document.getElementById('tpsChart');
  if (tpsChart) { tpsChart.data.labels = labels; tpsChart.data.datasets = datasets; tpsChart.update('none'); }
  else {
    tpsChart = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { ticks: { color: '#94a3b8', maxRotation: 0, maxTicksLimit: 20 }, grid: { color: '#1e293b' } },
          y: { title: { display: true, text: 'TPS', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });
  }
}

async function refreshMetadata() {
  const d = await fetchJson(API + '/metadata');
  if (!d) return;
  const el = document.getElementById('metadataCards');
  const m = d.markets || {};
  let html = '';
  html += card(Number(m.total_markets || 0).toLocaleString(), 'Total Markets');
  html += card(Number(m.open || 0).toLocaleString(), 'Open', 'green');
  html += card(Number(m.resolved || 0).toLocaleString(), 'Resolved', 'purple');
  html += card(Number(d.tokens || 0).toLocaleString(), 'Token Mappings', 'accent');
  html += card(fmtDt(d.last_update), 'Last Metadata Update');
  el.innerHTML = html;
}

async function refreshActivity() {
  const d = await fetchJson(API + '/activity');
  if (!d) return;
  const el = document.getElementById('strategyCards');
  const tabs = document.getElementById('intentTabs');
  let html = '';
  let tabHtml = '<button class="' + (!currentStrategy ? 'active' : '') + '" onclick="setStrategy(null)">All</button>';

  for (const [name, info] of Object.entries(d)) {
    const fillRate = info.total_intents > 0 ? Math.round(info.total_fills / info.total_intents * 100) : 0;
    html += card(
      `${info.total_fills}/${info.total_intents} <span style="font-size:.85rem;color:var(--muted)">(${fillRate}%)</span>`,
      `${name} &middot; $${info.total_filled_usd.toLocaleString()}`,
      fillRate > 50 ? 'green' : fillRate > 0 ? 'amber' : ''
    );
    tabHtml += `<button class="${currentStrategy===name?'active':''}" onclick="setStrategy('${name}')">${name}</button>`;
  }
  el.innerHTML = html;
  tabs.innerHTML = tabHtml;
}

async function refreshIntents() {
  const q = currentStrategy ? '?strategy=' + currentStrategy + '&n=100' : '?n=100';
  const [intD, fillD] = await Promise.all([fetchJson(API+'/intents'+q), fetchJson(API+'/fills'+q)]);
  const intents = (intD?.intents || []).map(i => ({...i, _type: 'intent'}));
  const fills = (fillD?.fills || []).map(f => ({...f, _type: 'fill'}));

  // Merge: match fills to intents by condition_id+strategy, else show standalone
  const fillMap = {};
  for (const f of fills) {
    const key = f.condition_id + '|' + f.strategy;
    if (!fillMap[key]) fillMap[key] = [];
    fillMap[key].push(f);
  }

  const rows = [];
  const usedFills = new Set();
  for (const intent of intents) {
    const key = intent.condition_id + '|' + intent.strategy;
    const matchedFills = fillMap[key] || [];
    const fill = matchedFills.find(f => !usedFills.has(f.intent_id) && Math.abs((f.filled_at||0) - (intent.signal_time||0)) < 60);
    if (fill) usedFills.add(fill.intent_id);
    rows.push({
      time: intent.signal_time,
      strategy: intent._source || intent.strategy,
      side: intent.side,
      outcome: intent.outcome,
      size: intent.size_usd,
      maxPrice: intent.max_price,
      fillPrice: fill ? fill.filled_price : null,
      status: fill ? fill.status : 'pending',
      reason: intent.reason || '',
    });
  }
  rows.sort((a,b) => (b.time||0) - (a.time||0));

  const tbody = document.getElementById('intentBody');
  tbody.innerHTML = rows.slice(0, 80).map(r => `<tr>
    <td class="mono">${fmt(r.time)}</td>
    <td>${r.strategy}</td>
    <td><span class="tag ${(r.side||'').toLowerCase()}">${r.side||'—'}</span></td>
    <td>${r.outcome||'—'}</td>
    <td>$${(r.size||0).toFixed(0)}</td>
    <td class="mono">${r.maxPrice != null ? r.maxPrice.toFixed(3) : '—'}</td>
    <td class="mono">${r.fillPrice != null ? r.fillPrice.toFixed(3) : '—'}</td>
    <td><span class="tag ${r.status}">${r.status}</span></td>
    <td class="reason" title="${r.reason}">${r.reason}</td>
  </tr>`).join('');
}

function setStrategy(name) {
  currentStrategy = name;
  refreshActivity();
  refreshIntents();
}

async function refresh() {
  const bar = document.getElementById('refreshBar');
  bar.style.width = '0%';
  await Promise.all([refreshIngestion(), refreshMetadata(), refreshActivity(), refreshIntents()]);
  document.getElementById('footer').textContent = 'Last refresh: ' + new Date().toLocaleTimeString() + ' · Auto-refresh every ' + (REFRESH_MS/1000) + 's';
  // Animate progress bar
  let pct = 0;
  const step = 100 / (REFRESH_MS / 100);
  const iv = setInterval(() => { pct += step; bar.style.width = Math.min(pct, 100) + '%'; if (pct >= 100) clearInterval(iv); }, 100);
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
"""


app = create_app()


def main() -> None:
    """Entry point for pm-api."""
    uvicorn.run(
        "polymarket_pipeline.api.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )
