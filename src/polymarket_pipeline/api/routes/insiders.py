"""Insider strategy monitoring endpoints.

Provides:
    GET /insiders/pool    — full insider pool with scoring breakdown from CH
    GET /insiders/signals — active consensus signals + matched intents from PG
    GET /insiders/health  — per-pool 7d rolling stats (fills, HR, PnL, candidates)
    GET /insiders/overview — quick stats for dashboard header
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog
from fastapi import APIRouter, Request

router = APIRouter(tags=["insiders"])
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Pool configuration: category → (pool_name, consensus_threshold, capital_usd)
# Must match configs/s2_insider_copy.toml
# ---------------------------------------------------------------------------
_POOL_CONFIG: dict[str, dict] = {
    "sports": {"pool": "s2_insider_sports", "consensus": 4, "capital_usd": 600},
    "politics": {"pool": "s2_insider_politics", "consensus": 3, "capital_usd": 250},
    "other": {"pool": "s2_insider_politics", "consensus": 3, "capital_usd": 250},
    "culture": {"pool": "s2_insider_misc", "consensus": 2, "capital_usd": 150},
    "weather": {"pool": "s2_insider_misc", "consensus": 2, "capital_usd": 150},
    "finance": {"pool": "s2_insider_misc", "consensus": 2, "capital_usd": 150},
}
# Categories explicitly excluded from strategy
_EXCLUDED_CATEGORIES = frozenset({"crypto", "esports"})

# Reverse map: pool_name → {categories, consensus, capital_usd}
_POOLS: dict[str, dict] = {}
for _cat, _cfg in _POOL_CONFIG.items():
    _pname = _cfg["pool"]
    if _pname not in _POOLS:
        _POOLS[_pname] = {
            "categories": [],
            "consensus": _cfg["consensus"],
            "capital_usd": _cfg["capital_usd"],
        }
    _POOLS[_pname]["categories"].append(_cat)


def _category_to_pool(category: str | None) -> tuple[str | None, int]:
    """Map a primary_category to (pool_name, consensus_threshold). Returns (None, 0) if excluded."""
    if not category or category in _EXCLUDED_CATEGORIES:
        return None, 0
    cfg = _POOL_CONFIG.get(category)
    if cfg:
        return cfg["pool"], cfg["consensus"]
    # Unmapped categories go to politics pool with default consensus
    return "s2_insider_politics", 3


# ---------------------------------------------------------------------------
# Cached pool: heavy query runs at most once per TTL, shared across endpoints
# ---------------------------------------------------------------------------
_POOL_CACHE_TTL = 3600  # 1 hour, matches provider refresh_interval_s
_pool_cache: list[dict] = []
_pool_cache_ts: float = 0.0
_pool_cache_lock = asyncio.Lock()


async def _get_cached_pool(request: Request) -> list[dict]:
    """Return the insider pool, refreshing from CH at most once per TTL."""
    global _pool_cache, _pool_cache_ts
    now = time.monotonic()
    if now - _pool_cache_ts < _POOL_CACHE_TTL and _pool_cache:
        return _pool_cache
    async with _pool_cache_lock:
        # Double-check after acquiring lock
        if now - _pool_cache_ts < _POOL_CACHE_TTL and _pool_cache:
            return _pool_cache
        sql = _INSIDER_POOL_SQL.format(
            lookback=12, min_positions=3, min_hr=0.75, max_hr=0.99,
            max_raw_hr=0.99, min_high_pct=0.20, max_entry_price=0.95,
        )
        rows = await _ch_query(request, sql)
        _pool_cache = rows
        _pool_cache_ts = time.monotonic()
        log.info("insiders.pool_cache_refreshed", size=len(rows))
        return rows


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
      AND (CASE WHEN p.position = 'YES'
                THEN p.avg_yes_price
                ELSE 1.0 - p.avg_yes_price
           END) < {max_entry_price}
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
        ) AS hr_excess,
        greatest(
            if(yes_total > 0, yes_wins * 1.0 / yes_total, 0),
            if(no_total > 0, no_wins * 1.0 / no_total, 0)
        ) AS raw_hr
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
  AND raw_hr < {max_raw_hr}
  AND high_pct >= {min_high_pct}
ORDER BY hr_excess DESC
"""

# Recent insider trades in the last N hours, pre-aggregated per market.
# Uses pre-computed pool addresses (passed as IN clause) instead of inline CTE.
_RECENT_INSIDER_TRADES_SQL = """\
SELECT
    t.condition_id AS condition_id,
    any(m.question) AS question,
    any(e.slug) AS event_slug,
    any(ms.primary_category) AS primary_category,
    groupUniqArray(lower(t.maker)) AS makers,
    count(*) AS trade_count,
    sum(t.amount_usd) AS total_usd,
    max(t.price) AS max_price,
    min(t.timestamp) AS first_trade,
    max(t.timestamp) AS last_trade
FROM trades_raw AS t
LEFT JOIN markets AS m ON t.condition_id = m.condition_id
LEFT JOIN events AS e ON m.event_id = e.id
LEFT JOIN market_susceptibility AS ms ON t.condition_id = ms.condition_id
WHERE t.side = 'BUY'
  AND t.timestamp >= now() - INTERVAL {hours} HOUR
  AND lower(t.maker) IN ({pool_addresses})
GROUP BY t.condition_id
ORDER BY last_trade DESC
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
    # Use cache when default params match the provider config
    is_default = (
        lookback_months == 12 and min_positions == 3
        and min_hr == 0.75 and max_hr == 0.99 and min_high_pct == 0.20
    )
    try:
        if is_default:
            rows = await _get_cached_pool(request)
        else:
            sql = _INSIDER_POOL_SQL.format(
                lookback=lookback_months, min_positions=min_positions,
                min_hr=min_hr, max_hr=max_hr, max_raw_hr=0.99,
                min_high_pct=min_high_pct, max_entry_price=0.95,
            )
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

    # Every trader returned by the pool query IS in the live pool
    for t in traders:
        t["in_live_pool"] = True

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


# Signals cache: refreshed at most every 2 minutes (CH query is heavy)
_SIGNALS_CACHE_TTL = 120
_signals_cache: dict[int, list[dict]] = {}  # hours -> rows
_signals_cache_ts: dict[int, float] = {}
_signals_cache_lock = asyncio.Lock()


async def _get_cached_signals(request: Request, hours: int) -> list[dict]:
    """Query recent insider trades using cached pool addresses."""
    global _signals_cache, _signals_cache_ts
    now = time.monotonic()
    if now - _signals_cache_ts.get(hours, 0) < _SIGNALS_CACHE_TTL and hours in _signals_cache:
        return _signals_cache[hours]
    async with _signals_cache_lock:
        if now - _signals_cache_ts.get(hours, 0) < _SIGNALS_CACHE_TTL and hours in _signals_cache:
            return _signals_cache[hours]
        # Reuse pool cache (1h TTL) to get trader addresses — avoids 15s pool CTE
        pool_rows = await _get_cached_pool(request)
        if not pool_rows:
            _signals_cache[hours] = []
            _signals_cache_ts[hours] = time.monotonic()
            return []
        addresses = [r["trader"] for r in pool_rows]
        pool_in = ", ".join(f"'{a}'" for a in addresses)
        sql = _RECENT_INSIDER_TRADES_SQL.format(hours=hours, pool_addresses=pool_in)
        rows = await _ch_query(request, sql)
        _signals_cache[hours] = rows
        _signals_cache_ts[hours] = time.monotonic()
        log.info("insiders.signals_cache_refreshed", hours=hours, rows=len(rows))
        return rows


@router.get("/insiders/signals")
async def insider_signals(
    request: Request,
    hours: int = 48,
) -> dict:
    """Return active signals by merging recent insider CH trades with PG intents.

    For each market where insiders have traded in the lookback window:
    - consensus_count: unique insider addresses that bought
    - consensus_met: count >= threshold for that pool
    - triggered: strategy actually placed a fill intent
    - intents: filled/rejected intents from strategy_intents
    """
    pg = request.app.state.pool

    # Step 1: CH — recent insider BUY trades in the window
    try:
        ch_rows = await _get_cached_signals(request, hours)
    except Exception:
        log.exception("insiders.signals_ch_failed")
        ch_rows = []

    # Step 2: CH rows are pre-aggregated per condition_id
    ch_by_cid: dict[str, dict] = {}
    for row in ch_rows:
        cid = row["condition_id"]
        makers = row.get("makers", [])
        if isinstance(makers, str):
            makers = json.loads(makers) if makers.startswith("[") else [makers]
        ch_by_cid[cid] = {
            "question": row.get("question"),
            "event_slug": row.get("event_slug"),
            "category": row.get("primary_category") or "unknown",
            "makers": set(makers),
            "trade_count": int(row.get("trade_count", 0)),
            "total_usd": float(row.get("total_usd", 0)),
            "max_price": float(row.get("max_price", 0)),
            "first_trade": row.get("first_trade", ""),
            "last_trade": row.get("last_trade", ""),
        }

    if not ch_by_cid:
        # No CH data — fallback to PG-only view
        pool_size = 0
        try:
            pool_rows = await _get_cached_pool(request)
            pool_size = len(pool_rows)
        except Exception:
            pass
        return {"signals": [], "total": 0, "pool_size": pool_size, "lookback_hours": hours}

    # Step 3: PG — intents for these condition_ids
    cids = list(ch_by_cid.keys())
    intents_by_cid: dict[str, list[dict]] = {}
    filled_cids: set[str] = set()
    try:
        async with pg.acquire() as conn:
            pg_rows = await conn.fetch(
                """SELECT si.condition_id, si.strategy, si.side, si.outcome,
                          si.size_usd, si.disposition, si.filled_price,
                          si.reason, si.captured_at
                   FROM strategy_intents si
                   WHERE si.strategy LIKE 's2_insider%'
                     AND si.condition_id = ANY($1)
                   ORDER BY si.captured_at DESC""",
                cids,
            )
        for r in pg_rows:
            cid = r["condition_id"]
            disp = r["disposition"] or "unknown"
            if disp == "filled":
                filled_cids.add(cid)
            intents_by_cid.setdefault(cid, []).append({
                "strategy": r["strategy"],
                "side": r["side"],
                "outcome": r["outcome"],
                "size_usd": round(float(r["size_usd"]), 2) if r["size_usd"] else 0,
                "disposition": disp,
                "filled_price": (
                    round(float(r["filled_price"]), 4) if r["filled_price"] else None
                ),
                "reason": r["reason"],
                "captured_at": r["captured_at"].isoformat() if r["captured_at"] else None,
            })
    except Exception:
        log.exception("insiders.signals_pg_failed")

    # Step 4: Pool size
    pool_size = 0
    try:
        pool_rows = await _get_cached_pool(request)
        pool_size = len(pool_rows)
    except Exception:
        pass

    # Step 5: Assemble signals
    signals = []
    for cid, data in ch_by_cid.items():
        category = data["category"]
        pool_name, consensus_threshold = _category_to_pool(category)
        insider_addresses = sorted(data["makers"])
        consensus_count = len(insider_addresses)
        consensus_met = (
            consensus_threshold > 0 and consensus_count >= consensus_threshold
        )
        triggered = cid in filled_cids
        first_trade = data["first_trade"]
        last_trade = data["last_trade"]

        signals.append({
            "condition_id": cid,
            "question": data["question"],
            "event_slug": data["event_slug"],
            "polymarket_url": (
                f"https://polymarket.com/event/{data['event_slug']}"
                if data["event_slug"]
                else None
            ),
            "category": category,
            "pool": pool_name,
            "consensus_count": consensus_count,
            "consensus_threshold": consensus_threshold,
            "consensus_met": consensus_met,
            "insider_addresses": insider_addresses,
            "trade_count": data["trade_count"],
            "total_usd": round(data["total_usd"], 2),
            "max_price": round(data["max_price"], 4),
            "first_trade": str(first_trade),
            "last_trade": str(last_trade),
            "intents": intents_by_cid.get(cid, [])[:10],
            "triggered": triggered,
        })

    # Sort: triggered first, then by consensus progress, then recency
    signals.sort(
        key=lambda s: (
            -(1 if s["triggered"] else 0),
            -(1 if s["consensus_met"] else 0),
            -(s["consensus_count"] / max(s["consensus_threshold"], 1)),
            s["last_trade"],
        )
    )

    return {
        "signals": signals,
        "total": len(signals),
        "pool_size": pool_size,
        "lookback_hours": hours,
    }


@router.get("/insiders/overview")
async def insider_overview(request: Request) -> dict:
    """Quick overview stats for the insiders dashboard header."""
    overview: dict = {
        "pool_size": 0,
        "pool_refreshed_at": None,
        "total_intents": 0,
        "filled_intents": 0,
        "active_signals": 0,
    }

    # Pool size from cached CH query
    try:
        rows = await _get_cached_pool(request)
        overview["pool_size"] = len(rows)
    except Exception:
        log.exception("insiders.overview_pool_failed")

    # Intent counts + active signals from PG (strategy_intents table)
    try:
        pg = request.app.state.pool
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT count(*) AS total,
                          count(*) FILTER (WHERE disposition = 'filled') AS filled
                   FROM strategy_intents
                   WHERE strategy LIKE 's2_insider%'"""
            )
            if row:
                overview["total_intents"] = row["total"]
                overview["filled_intents"] = row["filled"]

            val = await conn.fetchval(
                """SELECT count(DISTINCT condition_id)
                   FROM strategy_intents
                   WHERE strategy LIKE 's2_insider%'
                     AND captured_at >= now() - INTERVAL '48 hours'"""
            )
            overview["active_signals"] = val or 0
    except Exception:
        log.exception("insiders.overview_intents_failed")

    return overview


@router.get("/insiders/health")
async def insider_health(request: Request) -> dict:
    """Per-pool 7-day rolling health stats.

    Returns fills, resolved, wins, HR, PnL, capital utilization, fill rate,
    and candidate count for each strategy pool. Single PG query + reuses
    cached signals data for candidates.
    """
    pools_out: list[dict] = []

    # Step 1: Per-strategy 7d aggregates from PG (single query)
    strategy_stats: dict[str, dict] = {}
    try:
        pg = request.app.state.pool
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """SELECT
                       si.strategy,
                       count(*) FILTER (WHERE si.disposition = 'filled')
                           AS fills_7d,
                       count(*) FILTER (
                           WHERE si.disposition = 'filled'
                             AND m.winner_outcome IS NOT NULL
                       ) AS resolved_7d,
                       count(*) FILTER (
                           WHERE si.disposition = 'filled'
                             AND m.winner_outcome IS NOT NULL
                             AND m.winner_outcome = si.outcome
                       ) AS wins_7d,
                       count(*) FILTER (WHERE si.disposition = 'risk_rejected')
                           AS risk_rejected_7d,
                       count(*) FILTER (WHERE si.disposition = 'rejected')
                           AS rejected_7d,
                       coalesce(sum(si.filled_size_usd) FILTER (
                           WHERE si.disposition = 'filled'), 0)
                           AS invested_7d,
                       coalesce(sum(si.fee_usd) FILTER (
                           WHERE si.disposition = 'filled'), 0)
                           AS fees_7d,
                       count(DISTINCT si.condition_id) FILTER (
                           WHERE si.disposition = 'filled'
                             AND m.winner_outcome IS NULL
                       ) AS open_positions,
                       coalesce(sum(si.filled_size_usd) FILTER (
                           WHERE si.disposition = 'filled'
                             AND m.winner_outcome IS NULL
                       ), 0) AS open_invested_usd,
                       coalesce(sum(
                           CASE
                               WHEN si.disposition = 'filled'
                                 AND m.winner_outcome IS NOT NULL
                                 AND (si.filled_price IS NOT NULL AND si.filled_price > 0)
                               THEN
                                   CASE WHEN m.winner_outcome = si.outcome
                                       THEN si.filled_size_usd / si.filled_price
                                            - si.filled_size_usd
                                            - coalesce(si.fee_usd, 0)
                                       ELSE -si.filled_size_usd
                                            - coalesce(si.fee_usd, 0)
                                   END
                               ELSE 0
                           END
                       ), 0) AS pnl_resolved_7d
                   FROM strategy_intents si
                   LEFT JOIN markets m ON si.condition_id = m.condition_id
                   WHERE si.strategy LIKE 's2_insider%'
                     AND si.captured_at >= now() - INTERVAL '7 days'
                   GROUP BY si.strategy"""
            )
            for r in rows:
                strategy_stats[r["strategy"]] = dict(r)
    except Exception:
        log.exception("insiders.health_pg_failed")

    # Step 2: Candidates count — skip heavy CH query, use PG intent counts instead
    candidates_per_pool: dict[str, int] = {}
    try:
        async with pg.acquire() as conn:
            cand_rows = await conn.fetch(
                """SELECT strategy, count(DISTINCT condition_id) AS cnt
                   FROM strategy_intents
                   WHERE strategy LIKE 's2_insider%'
                     AND disposition = 'filled'
                     AND captured_at >= now() - INTERVAL '48 hours'
                   GROUP BY strategy"""
            )
            for r in cand_rows:
                candidates_per_pool[r["strategy"]] = r["cnt"]
    except Exception:
        log.exception("insiders.health_candidates_failed")

    # Step 3: Assemble per-pool output
    for pool_name, pool_cfg in _POOLS.items():
        stats = strategy_stats.get(pool_name, {})
        fills = int(stats.get("fills_7d", 0))
        resolved = int(stats.get("resolved_7d", 0))
        wins = int(stats.get("wins_7d", 0))
        risk_rej = int(stats.get("risk_rejected_7d", 0))
        rejected = int(stats.get("rejected_7d", 0))
        invested = float(stats.get("invested_7d", 0))
        open_pos = int(stats.get("open_positions", 0))
        open_invested = float(stats.get("open_invested_usd", 0))
        pnl_resolved = float(stats.get("pnl_resolved_7d", 0))
        total_intents = fills + risk_rej + rejected
        capital_usd = pool_cfg["capital_usd"]

        pools_out.append({
            "pool": pool_name,
            "categories": pool_cfg["categories"],
            "consensus_threshold": pool_cfg["consensus"],
            "capital_usd": capital_usd,
            "fills_7d": fills,
            "resolved_7d": resolved,
            "wins_7d": wins,
            "hr_7d": round(wins / resolved, 3) if resolved > 0 else None,
            # Realized PnL: only from resolved positions (won - lost - fees)
            "pnl_7d": round(pnl_resolved, 2),
            "invested_7d": round(invested, 2),
            "open_positions": open_pos,
            # Capital currently deployed in open (unresolved) positions
            "capital_pct": round(open_invested / capital_usd, 3) if capital_usd > 0 else 0,
            "fill_rate": round(
                fills / total_intents, 3
            ) if total_intents > 0 else None,
            "risk_rejected_7d": risk_rej,
            "rejected_7d": rejected,
            "candidates": candidates_per_pool.get(pool_name, 0),
        })

    return {"pools": pools_out}
