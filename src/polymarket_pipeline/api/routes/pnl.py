"""PnL summary endpoint — live vs resolved breakdown."""

from __future__ import annotations

import asyncio

import httpx
import structlog
from fastapi import APIRouter, Request

router = APIRouter(tags=["pnl"])
log = structlog.get_logger()

_CLOB_BASE = "https://clob.polymarket.com"
_MAX_CONCURRENT = 20


async def _fetch_midpoint(
    client: httpx.AsyncClient, asset_id: str
) -> tuple[str, float | None]:
    """Fetch CLOB midpoint for a single asset_id. Returns (asset_id, price)."""
    try:
        resp = await client.get(
            f"{_CLOB_BASE}/midpoint", params={"token_id": asset_id}
        )
        resp.raise_for_status()
        mid = resp.json().get("mid")
        return (asset_id, float(mid)) if mid else (asset_id, None)
    except Exception:
        log.warning("pnl.midpoint_failed", asset_id=asset_id[:16])
        return (asset_id, None)


@router.get("/pnl")
async def pnl_summary(request: Request) -> dict:
    """Aggregate PnL with live/resolved segregation and win/loss breakdown."""
    pool = request.app.state.pool

    # Single query: all filled intents with market metadata + NO asset_id
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT si.id, si.strategy, si.condition_id, si.asset_id,
                      si.side, si.outcome, si.size_usd,
                      si.filled_price, si.filled_size_usd,
                      m.question, m.resolved_at, m.winner_outcome,
                      m.token_yes, m.token_no,
                      e.end_date,
                      tmm.asset_id AS tmm_asset_id
               FROM strategy_intents si
               LEFT JOIN markets m ON si.condition_id = m.condition_id
               LEFT JOIN events e ON m.event_id = e.id
               LEFT JOIN token_market_map tmm
                   ON si.condition_id = tmm.condition_id
                   AND tmm.outcome = si.outcome
               WHERE si.disposition = 'filled'
                 AND si.filled_price IS NOT NULL
                 AND si.filled_price > 0
                 AND si.filled_size_usd IS NOT NULL
               ORDER BY si.captured_at DESC"""
        )

    live_positions: list[dict] = []
    resolved_positions: list[dict] = []

    # Collect unresolved condition_ids to batch-check CLOB API
    unresolved_cids: set[str] = set()
    all_entries: list[dict] = []
    for r in rows:
        entry = dict(r)
        all_entries.append(entry)
        if r["resolved_at"] is None:
            unresolved_cids.add(r["condition_id"])

    # Batch-check CLOB API for resolution of markets PG doesn't know about
    clob_resolutions: dict[str, str] = {}  # condition_id -> winner_outcome
    if unresolved_cids:
        sem = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _check_resolution(
            client: httpx.AsyncClient, cid: str
        ) -> tuple[str, str]:
            async with sem:
                try:
                    resp = await client.get(f"{_CLOB_BASE}/markets/{cid}")
                    resp.raise_for_status()
                    mkt = resp.json()
                    tokens = mkt.get("tokens") or []
                    closed = mkt.get("closed", False)
                    winners = [t for t in tokens if t.get("winner") is True]
                    if closed and len(winners) == 1:
                        wo = winners[0].get("outcome", "")
                        return (cid, wo)
                except Exception:
                    pass
                return (cid, "")

        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = [_check_resolution(client, cid) for cid in unresolved_cids]
            results = await asyncio.gather(*tasks)
            clob_resolutions = {cid: wo for cid, wo in results if wo}

        # Backfill PG for discovered resolutions
        if clob_resolutions:
            try:
                async with pool.acquire() as conn:
                    for cid, wo in clob_resolutions.items():
                        await conn.execute(
                            """UPDATE markets
                               SET resolution_value = 1,
                                   winner_outcome = $1,
                                   resolved_at = NOW()
                               WHERE condition_id = $2
                                 AND resolved_at IS NULL""",
                            wo, cid,
                        )
            except Exception:
                log.debug("pnl.pg_backfill_failed")

    # Separate live vs resolved (using PG + CLOB data)
    for entry in all_entries:
        cid = entry["condition_id"]
        if entry["resolved_at"] is not None:
            resolved_positions.append(entry)
        elif cid in clob_resolutions:
            entry["winner_outcome"] = clob_resolutions[cid]
            entry["resolved_at"] = True  # sentinel
            resolved_positions.append(entry)
        else:
            live_positions.append(entry)

    # --- Resolved PnL (no API calls needed) ---
    resolved_out: list[dict] = []
    resolved_invested = 0.0
    resolved_pnl = 0.0
    wins = 0
    losses = 0

    for r in resolved_positions:
        fp = float(r["filled_price"])
        fs = float(r["filled_size_usd"])
        outcome = r["outcome"] or "YES"
        won = outcome == r["winner_outcome"]
        tokens = fs / fp
        pnl_usd = (tokens * 1.0 - fs) if won else -fs
        pnl_pct = (pnl_usd / fs) * 100 if fs else 0

        resolved_invested += fs
        resolved_pnl += pnl_usd
        if won:
            wins += 1
        else:
            losses += 1

        resolved_out.append({
            "id": r["id"],
            "strategy": r["strategy"],
            "question": r["question"],
            "outcome": outcome,
            "entry_price": round(fp, 4),
            "size_usd": round(fs, 2),
            "won": won,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
            "winner_outcome": r["winner_outcome"],
        })

    # --- Live PnL (batch CLOB midpoints) ---
    # Resolve correct outcome-specific asset_id for each position
    def _resolve_aid(r: dict) -> str | None:  # type: ignore[type-arg]
        """Pick the right asset_id: intent > token_market_map > markets table."""
        if r["asset_id"]:
            return r["asset_id"]
        if r["tmm_asset_id"]:
            return r["tmm_asset_id"]
        outcome = r["outcome"] or "YES"
        if outcome == "NO" and r.get("token_no"):
            return r["token_no"]
        if r.get("token_yes"):
            return r["token_yes"]
        return None

    # Collect unique asset_ids for live positions
    asset_ids_needed: set[str] = set()
    for r in live_positions:
        aid = _resolve_aid(r)
        if aid:
            asset_ids_needed.add(aid)

    # Fetch midpoints concurrently (max 20)
    midpoints: dict[str, float | None] = {}
    if asset_ids_needed:
        sem = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _bounded_fetch(
            client: httpx.AsyncClient, aid: str
        ) -> tuple[str, float | None]:
            async with sem:
                return await _fetch_midpoint(client, aid)

        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = [_bounded_fetch(client, aid) for aid in asset_ids_needed]
            results = await asyncio.gather(*tasks)
            midpoints = dict(results)

    live_out: list[dict] = []
    live_invested = 0.0
    live_pnl = 0.0

    for r in live_positions:
        fp = float(r["filled_price"])
        fs = float(r["filled_size_usd"])
        outcome = r["outcome"] or "YES"
        aid = _resolve_aid(r)
        current = midpoints.get(aid) if aid else None

        tokens = fs / fp
        pos_pnl: float | None = None
        pos_pct: float | None = None
        if current is not None:
            pos_pnl = round(tokens * current - fs, 2)
            pos_pct = round((pos_pnl / fs) * 100, 2) if fs else 0

        live_invested += fs
        if pos_pnl is not None:
            live_pnl += pos_pnl

        live_out.append({
            "id": r["id"],
            "strategy": r["strategy"],
            "question": r["question"],
            "outcome": outcome,
            "entry_price": round(fp, 4),
            "current_price": round(current, 4) if current is not None else None,
            "size_usd": round(fs, 2),
            "pnl_usd": pos_pnl,
            "pnl_pct": pos_pct,
            "end_date": r["end_date"].isoformat() if r["end_date"] else None,
        })

    total_pnl = resolved_pnl + live_pnl
    total_invested = resolved_invested + live_invested
    n_fills = len(resolved_positions) + len(live_positions)
    n_resolved = wins + losses
    win_rate = wins / n_resolved if n_resolved > 0 else None

    return {
        "summary": {
            "total_pnl_usd": round(total_pnl, 2),
            "total_invested": round(total_invested, 2),
            "n_fills": n_fills,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        },
        "live": {
            "count": len(live_positions),
            "invested": round(live_invested, 2),
            "pnl_usd": round(live_pnl, 2),
            "positions": live_out,
        },
        "resolved": {
            "count": n_resolved,
            "invested": round(resolved_invested, 2),
            "pnl_usd": round(resolved_pnl, 2),
            "wins": wins,
            "losses": losses,
            "positions": resolved_out,
        },
    }
