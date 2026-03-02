"""S2 Insider Copy: Per-tag tick-by-tick validation of vectorized per-category findings.

Runs SEPARATE tick-by-tick backtests per category with category-specific parameters.
The insider pool is GLOBAL (same pool selection across all categories), but trades
are filtered by the primary_category of each market.

Walk-forward: train 12mo, test 1mo.
Test months: 2025-07, 2025-10, 2026-01 (same as original validation).
Capital: $5,000 per pool, $50/position, max 50 open positions.

Category assignment uses the tag JOIN chain with priority:
  politics > esports > sports > crypto > culture > finance > weather > other

Usage:
    uv run python research/scripts/s2_tick_tag_validation.py
    uv run python research/scripts/s2_tick_tag_validation.py --categories sports politics
    uv run python research/scripts/s2_tick_tag_validation.py --months 2025-07
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

import clickhouse_connect
import polars as pl
from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
SQL_DIR = Path(__file__).parent.parent / "knowledge" / "queries"
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s2_tick_tag"

# Default parameters
DEFAULT_SIZE_USD = 50.0
DEFAULT_CAPITAL_USD = 5000.0
DEFAULT_MAX_OPEN = 50
DEFAULT_TRAIN_MONTHS = 12

# Test months (quarterly OOS samples)
DEFAULT_TEST_MONTHS = [
    date(2025, 7, 1),
    date(2025, 10, 1),
    date(2026, 1, 1),
]

# Per-category parameter grids (from vectorized findings)
# Format: category -> list of (consensus, max_entry_price) to sweep
CATEGORY_PARAMS: dict[str, list[tuple[int, float]]] = {
    "sports": [
        (3, 1.0), (3, 0.65),
        (4, 1.0), (4, 0.65),
        (5, 1.0), (5, 0.65),
    ],
    "politics": [
        (3, 1.0), (3, 0.65),
        (4, 1.0), (4, 0.65),
        (5, 1.0), (5, 0.65),
    ],
    "culture": [
        (2, 1.0), (2, 0.65),
        (3, 1.0), (3, 0.65),
        (4, 1.0), (4, 0.65),
    ],
    "other": [
        (2, 1.0), (2, 0.65),
        (3, 1.0), (3, 0.65),
        (5, 1.0), (5, 0.65),
    ],
    "weather": [
        (2, 1.0), (2, 0.65),
        (3, 1.0), (3, 0.65),
    ],
    "finance": [
        (2, 1.0), (2, 0.65),
        (3, 1.0), (3, 0.65),
    ],
    # EXCLUDED categories -- validate they should stay excluded
    "esports": [
        (3, 1.0), (3, 0.65),
        (5, 1.0), (5, 0.65),
    ],
    "crypto": [
        (3, 1.0), (3, 0.65),
        (5, 1.0), (5, 0.65),
    ],
}

# Vectorized reference values per category (from tag_tuning.md)
VECTORIZED_REF: dict[str, dict[str, float]] = {
    "sports":   {"hr": 0.784, "pnl_per_pos": 78.69, "comp": 1867},
    "politics": {"hr": 0.838, "pnl_per_pos": 46.86, "comp": 181},
    "culture":  {"hr": 0.808, "pnl_per_pos": 26.50, "comp": 40},
    "other":    {"hr": 0.800, "pnl_per_pos": 64.94, "comp": 121},
    "weather":  {"hr": 0.830, "pnl_per_pos": 8.59,  "comp": 35},
    "finance":  {"hr": 0.792, "pnl_per_pos": 1.84,  "comp": 6},
    "esports":  {"hr": 0.780, "pnl_per_pos": -185.0, "comp": -100},
    "crypto":   {"hr": 0.820, "pnl_per_pos": -28.0,  "comp": -50},
}

# Per-category base rates (from vectorized analysis, susceptible markets)
CATEGORY_BASE_RATES: dict[str, tuple[float, float]] = {
    "politics": (0.245, 0.755),
    "sports":   (0.387, 0.613),
    "esports":  (0.456, 0.544),
    "culture":  (0.114, 0.886),
    "finance":  (0.377, 0.623),
    "weather":  (0.141, 0.859),
    "crypto":   (0.234, 0.766),
    "other":    (0.296, 0.704),
}


# ---------------------------------------------------------------------------
# Lightweight trade record (avoid Pydantic overhead for millions of rows)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LightTrade:
    """Minimal trade record for tick-by-tick replay."""
    condition_id: str
    asset_id: str
    side: str
    price: float
    amount_usd: float
    maker: str
    timestamp_epoch: float
    published_at: float


@dataclass(slots=True)
class SimPosition:
    """Mutable position tracker for the simulation."""
    condition_id: str
    outcome: str
    qty: float = 0.0
    avg_entry: float = 0.0
    cost_basis: float = 0.0
    entry_time: float = 0.0
    slippage_fee: float = 0.0


@dataclass(frozen=True, slots=True)
class SimFill:
    """Record of a simulated fill."""
    condition_id: str
    asset_id: str
    outcome: str
    side: str
    entry_price: float
    size_usd: float
    slippage_fee: float
    signal_time: float
    consensus_count: int
    direction: str


@dataclass(frozen=True, slots=True)
class SimResult:
    """Resolved result of a position."""
    condition_id: str
    outcome: str
    entry_price: float
    size_usd: float
    slippage_fee: float
    signal_time: float
    resolved_at: float
    won: bool
    pnl_gross: float
    pnl_net: float
    hold_days: float
    consensus_count: int


@dataclass
class MonthCategoryResult:
    """Aggregated results for a single test month + category + parameter combo."""
    test_month: str
    category: str
    min_consensus: int = 0
    max_entry_price: float = 1.0
    pool_size: int = 0
    n_insider_trades: int = 0
    n_markets_touched: int = 0
    n_signals_triggered: int = 0
    n_fills: int = 0
    n_rejected_capital: int = 0
    n_rejected_duplicate: int = 0
    n_rejected_price: int = 0
    n_settled: int = 0
    n_pending: int = 0
    n_won: int = 0
    n_lost: int = 0
    total_pnl_gross: float = 0.0
    total_pnl_net: float = 0.0
    total_slippage: float = 0.0
    avg_entry_price: float = 0.0
    avg_hold_days: float = 0.0
    med_hold_days: float = 0.0
    hit_rate: float = 0.0
    base_rate_yes: float = 0.0
    base_rate_no: float = 0.0
    excess_hr: float = 0.0
    compounding_score: float = 0.0
    n_yes: int = 0
    n_no: int = 0
    yes_hr: float = 0.0
    no_hr: float = 0.0


# ---------------------------------------------------------------------------
# ClickHouse helpers
# ---------------------------------------------------------------------------


def get_client() -> Any:
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def ch_query(client: Any, sql: str) -> pl.DataFrame:
    r = client.query(sql)
    if not r.result_rows:
        return pl.DataFrame()
    return pl.DataFrame(
        {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
    )


def ch_query_raw(client: Any, sql: str) -> list[dict[str, Any]]:
    r = client.query(sql)
    if not r.result_rows:
        return []
    cols = r.column_names
    return [dict(zip(cols, row)) for row in r.result_rows]


# ---------------------------------------------------------------------------
# Stage 1: Build market-to-category mapping
# ---------------------------------------------------------------------------


def load_market_categories(client: Any) -> dict[str, str]:
    """Load primary_category for every market using the tag JOIN chain.

    Priority: politics > esports > sports > crypto > culture > finance > weather > other

    Returns: condition_id -> primary_category
    """
    sql = """
        SELECT
            m.condition_id,
            max(if(t.label = 'Politics', 1, 0)) AS is_politics,
            max(if(t.label = 'Esports', 1, 0)) AS is_esports,
            max(if(t.label = 'Sports', 1, 0)) AS is_sports,
            max(if(t.label = 'Crypto', 1, 0)) AS is_crypto,
            max(if(t.label = 'Culture', 1, 0)) AS is_culture,
            max(if(t.label = 'Finance', 1, 0)) AS is_finance,
            max(if(t.label = 'Weather', 1, 0)) AS is_weather,
            max(if(t.label IN (
                'Up or Down', 'Crypto Prices', '5M', '15M',
                'Hit Price', 'Multi Strikes', '4H', '1H'
            ), 1, 0)) AS has_gambling_tag,
            multiIf(
                is_politics = 1, 'politics',
                is_esports = 1, 'esports',
                is_sports = 1, 'sports',
                is_crypto = 1, 'crypto',
                is_culture = 1, 'culture',
                is_finance = 1, 'finance',
                is_weather = 1, 'weather',
                'other'
            ) AS primary_category
        FROM markets AS m
        INNER JOIN events AS e ON m.event_id = e.id
        INNER JOIN event_tags AS et ON e.id = et.event_id
        INNER JOIN tags AS t ON et.tag_id = t.id
        GROUP BY m.condition_id
    """
    rows = ch_query_raw(client, sql)
    result: dict[str, str] = {}
    for row in rows:
        cid = str(row["condition_id"])
        cat = str(row["primary_category"])
        gambling = int(row["has_gambling_tag"])
        # Exclude gambling markets entirely
        if gambling:
            result[cid] = "GAMBLING"
        else:
            result[cid] = cat
    return result


# ---------------------------------------------------------------------------
# Stage 2: Build insider pool (same as global script)
# ---------------------------------------------------------------------------


def build_insider_pool(
    client: Any,
    train_start: date,
    train_end: date,
) -> dict[str, dict[str, Any]]:
    """Build insider pool from ClickHouse training data.

    Returns dict: trader_address -> {direction, score, train_hr, ...}
    """
    sql_path = SQL_DIR / "s2_tick_insider_pool.sql"
    sql = sql_path.read_text()
    sql = sql.replace("{train_start}", train_start.isoformat())
    sql = sql.replace("{train_end}", train_end.isoformat())

    df = ch_query(client, sql)
    if df.is_empty():
        return {}

    pool: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        pool[row["trader"]] = {
            "direction": row["best_direction"],
            "score": float(row["effective_hr"]),
            "hr_excess": float(row["hr_excess"]),
            "train_hr": float(row["train_hr"]),
            "high_pct": float(row["high_pct"]),
            "train_n": int(row["train_n"]),
        }

    return pool


# ---------------------------------------------------------------------------
# Stage 3: Load insider BUY trades (filtered by category)
# ---------------------------------------------------------------------------


def load_insider_trades(
    client: Any,
    pool: dict[str, dict[str, Any]],
    test_start: date,
    test_end: date,
) -> list[LightTrade]:
    """Load BUY trades from insider pool members during the test window.

    Returns ALL insider BUY trades (not filtered by category yet).
    Category filtering happens later when we split trades per category.
    """
    # Create temp table with pool traders
    traders_list = list(pool.keys())
    client.command("DROP TABLE IF EXISTS _tmp_s2_tick_pool SYNC")
    client.command("""
        CREATE TABLE IF NOT EXISTS _tmp_s2_tick_pool (
            trader String
        ) ENGINE = Memory
    """)
    client.command("TRUNCATE TABLE _tmp_s2_tick_pool")

    batch_size = 10000
    for i in range(0, len(traders_list), batch_size):
        batch = traders_list[i : i + batch_size]
        client.insert(
            "_tmp_s2_tick_pool",
            [[t] for t in batch],
            column_names=["trader"],
        )

    sql = f"""
        SELECT
            tr.condition_id,
            tr.asset_id,
            tr.side,
            tr.price,
            tr.amount_usd,
            lower(tr.maker) AS maker,
            toUnixTimestamp(tr.timestamp) AS timestamp_epoch,
            tr.published_at
        FROM (SELECT * FROM trades_raw FINAL) AS tr
        WHERE tr.side = 'BUY'
          AND lower(tr.maker) IN (SELECT trader FROM _tmp_s2_tick_pool)
          AND tr.timestamp >= '{test_start.isoformat()}'
          AND tr.timestamp < '{test_end.isoformat()}'
        ORDER BY tr.timestamp
    """

    df = ch_query(client, sql)
    if df.is_empty():
        return []

    trades: list[LightTrade] = []
    for row in df.iter_rows(named=True):
        trades.append(
            LightTrade(
                condition_id=str(row["condition_id"]),
                asset_id=str(row["asset_id"]),
                side=str(row["side"]),
                price=float(row["price"]),
                amount_usd=float(row["amount_usd"]),
                maker=str(row["maker"]),
                timestamp_epoch=float(row["timestamp_epoch"]),
                published_at=float(row["published_at"]) if row["published_at"] else float(row["timestamp_epoch"]),
            )
        )

    return trades


# ---------------------------------------------------------------------------
# Stage 4: Load resolution data (same as global script)
# ---------------------------------------------------------------------------


def load_resolutions(
    client: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Load all market resolutions and token map from ClickHouse.

    Returns:
        resolutions: cid -> {resolved_epoch, winning_asset_ids: set[str]}
        token_map: cid -> {YES: asset_id, NO: asset_id}
    """
    sql = (SQL_DIR / "s2_tick_resolutions.sql").read_text()
    rows = ch_query_raw(client, sql)

    resolutions: dict[str, dict[str, Any]] = {}
    token_map: dict[str, dict[str, str]] = {}

    for row in rows:
        cid = str(row["condition_id"])
        asset_id = str(row["asset_id"])
        outcome = str(row["outcome"])
        token_won = int(row["token_won"]) if row["token_won"] is not None else 0
        epoch = float(row["resolved_epoch"]) if row["resolved_epoch"] else 0.0

        if cid not in token_map:
            token_map[cid] = {}
        token_map[cid][outcome] = asset_id

        if epoch > 0:
            if cid not in resolutions:
                resolutions[cid] = {"resolved_epoch": epoch, "winning_asset_ids": set()}
            if token_won == 1:
                resolutions[cid]["winning_asset_ids"].add(asset_id)

    return resolutions, token_map


# ---------------------------------------------------------------------------
# Stage 5: Per-category base rates
# ---------------------------------------------------------------------------


def load_per_category_base_rates(
    client: Any,
    test_start: date,
    test_end: date,
) -> dict[str, tuple[float, float]]:
    """Compute per-category YES/NO base rates for the test period.

    Uses tag-based gambling exclusion and primary_category assignment.
    Returns: category -> (yes_base_rate, no_base_rate)
    """
    sql = f"""
        WITH market_tags AS (
            SELECT
                m.condition_id,
                max(if(t.label IN (
                    'Up or Down', 'Crypto Prices', '5M', '15M',
                    'Hit Price', 'Multi Strikes', '4H', '1H'
                ), 1, 0)) AS has_gambling_tag,
                max(if(t.label = 'Politics', 1, 0)) AS is_politics,
                max(if(t.label = 'Esports', 1, 0)) AS is_esports,
                max(if(t.label = 'Sports', 1, 0)) AS is_sports,
                max(if(t.label = 'Crypto', 1, 0)) AS is_crypto,
                max(if(t.label = 'Culture', 1, 0)) AS is_culture,
                max(if(t.label = 'Finance', 1, 0)) AS is_finance,
                max(if(t.label = 'Weather', 1, 0)) AS is_weather
            FROM markets AS m
            INNER JOIN events AS e ON m.event_id = e.id
            INNER JOIN event_tags AS et ON e.id = et.event_id
            INNER JOIN tags AS t ON et.tag_id = t.id
            GROUP BY m.condition_id
        ),
        categorized AS (
            SELECT
                condition_id,
                has_gambling_tag,
                multiIf(
                    is_politics = 1, 'politics',
                    is_esports = 1, 'esports',
                    is_sports = 1, 'sports',
                    is_crypto = 1, 'crypto',
                    is_culture = 1, 'culture',
                    is_finance = 1, 'finance',
                    is_weather = 1, 'weather',
                    'other'
                ) AS primary_category
            FROM market_tags
            WHERE has_gambling_tag = 0
        )
        SELECT
            c.primary_category AS category,
            countIf(mr.token_won = 1 AND mr.outcome = 'YES') AS yes_won,
            countIf(mr.token_won = 1 AND mr.outcome = 'NO') AS no_won
        FROM markets_resolved AS mr
        INNER JOIN categorized AS c ON mr.condition_id = c.condition_id
        WHERE mr.resolved_at >= '{test_start.isoformat()}'
          AND mr.resolved_at < '{test_end.isoformat()}'
          AND mr.resolved_at > '1970-01-02'
        GROUP BY c.primary_category
    """
    df = ch_query(client, sql)
    result: dict[str, tuple[float, float]] = {}
    if df.is_empty():
        return result

    for row in df.iter_rows(named=True):
        cat = str(row["category"])
        yes_won = int(row["yes_won"])
        no_won = int(row["no_won"])
        total = yes_won + no_won
        if total > 0:
            result[cat] = (yes_won / total, no_won / total)
        else:
            result[cat] = (0.5, 0.5)

    return result


# ---------------------------------------------------------------------------
# Stage 6: Slippage model (simplified RealisticFillSimulator)
# ---------------------------------------------------------------------------


def compute_slippage(
    size_usd: float,
    *,
    fallback_half_spread: float = 0.005,
    default_liquidity_usd: float = 5000.0,
    impact_scale: float = 1.0,
) -> float:
    """Compute slippage cost (half_spread + linear impact) * size_usd."""
    impact = (size_usd / default_liquidity_usd) * impact_scale
    return (fallback_half_spread + impact) * size_usd


# ---------------------------------------------------------------------------
# Stage 7: Tick-by-tick simulation engine (per-category)
# ---------------------------------------------------------------------------


def run_tick_simulation(
    trades: list[LightTrade],
    pool: dict[str, dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    *,
    min_consensus: int = 3,
    max_entry_price: float = 1.0,
    size_usd: float = DEFAULT_SIZE_USD,
    capital_usd: float = DEFAULT_CAPITAL_USD,
    max_open: int = DEFAULT_MAX_OPEN,
) -> tuple[list[SimFill], list[SimResult], dict[str, int]]:
    """Run tick-by-tick simulation on (already category-filtered) trades.

    This is identical to the global simulation but operates on a subset of trades
    belonging to a single category.
    """
    consensus: dict[str, dict[str, Any]] = {}
    positions: dict[str, SimPosition] = {}
    fills: list[SimFill] = []
    results: list[SimResult] = []
    entered_markets: set[str] = set()

    # Pre-sort resolution timeline
    res_timeline: list[tuple[float, str]] = sorted(
        (r["resolved_epoch"], cid)
        for cid, r in resolutions.items()
        if r["resolved_epoch"] > 0
    )
    res_idx = 0
    n_res = len(res_timeline)

    stats: dict[str, int] = {
        "n_trades_processed": 0,
        "n_insider_trades": 0,
        "n_signals_triggered": 0,
        "n_fills": 0,
        "n_rejected_capital": 0,
        "n_rejected_duplicate": 0,
        "n_rejected_price": 0,
        "n_settled": 0,
    }

    used_capital = 0.0

    for trade in trades:
        now = trade.timestamp_epoch
        stats["n_trades_processed"] += 1

        # --- Settlement: settle markets that resolved before this tick ---
        while res_idx < n_res:
            res_time, res_cid = res_timeline[res_idx]
            if res_time > now:
                break
            _settle_position(
                res_cid, resolutions, token_map, positions, results, fills
            )
            if res_cid in positions:
                used_capital -= positions[res_cid].cost_basis
                del positions[res_cid]
                stats["n_settled"] += 1
            res_idx += 1

        # --- Only BUY trades from insiders ---
        if trade.side != "BUY":
            continue

        maker = trade.maker
        if maker not in pool:
            continue

        stats["n_insider_trades"] += 1
        cid = trade.condition_id
        insider_info = pool[maker]
        direction = insider_info["direction"]

        # Build consensus (unique traders per market)
        if cid not in consensus:
            consensus[cid] = {
                "direction": direction,
                "insiders": set(),
                "first_price": trade.price,
            }
        signal = consensus[cid]
        signal["insiders"].add(maker)
        consensus_count = len(signal["insiders"])

        # Check consensus threshold
        if consensus_count < min_consensus:
            continue

        stats["n_signals_triggered"] += 1

        # No existing/past position
        if cid in positions:
            stats["n_rejected_duplicate"] += 1
            continue
        if cid in entered_markets:
            stats["n_rejected_duplicate"] += 1
            continue

        # Entry price filter
        if trade.price >= max_entry_price:
            stats["n_rejected_price"] += 1
            continue

        # Capital limit
        open_count = sum(1 for p in positions.values() if p.qty > 0)
        if open_count >= max_open:
            stats["n_rejected_capital"] += 1
            continue
        if used_capital + size_usd > capital_usd:
            stats["n_rejected_capital"] += 1
            continue

        # --- Execute fill ---
        entry_price = trade.price
        slippage = compute_slippage(size_usd)
        qty = size_usd / entry_price if entry_price > 0 else 0.0

        outcome = signal["direction"]
        asset_id = ""
        if cid in token_map:
            asset_id = token_map[cid].get(outcome, "")

        fill = SimFill(
            condition_id=cid,
            asset_id=asset_id,
            outcome=outcome,
            side="BUY",
            entry_price=entry_price,
            size_usd=size_usd,
            slippage_fee=slippage,
            signal_time=now,
            consensus_count=consensus_count,
            direction=direction,
        )
        fills.append(fill)
        entered_markets.add(cid)

        positions[cid] = SimPosition(
            condition_id=cid,
            outcome=outcome,
            qty=qty,
            avg_entry=entry_price,
            cost_basis=size_usd + slippage,
            entry_time=now,
            slippage_fee=slippage,
        )
        used_capital += size_usd + slippage
        stats["n_fills"] += 1

    # --- Settle remaining resolved markets ---
    while res_idx < n_res:
        _, res_cid = res_timeline[res_idx]
        _settle_position(res_cid, resolutions, token_map, positions, results, fills)
        if res_cid in positions:
            stats["n_settled"] += 1
            del positions[res_cid]
        res_idx += 1

    return fills, results, stats


def _settle_position(
    condition_id: str,
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    positions: dict[str, SimPosition],
    results: list[SimResult],
    fills: list[SimFill],
) -> None:
    """Settle a position using asset_id-based resolution (CRITICAL: no string matching)."""
    pos = positions.get(condition_id)
    if pos is None or pos.qty <= 0:
        return

    resolution = resolutions.get(condition_id)
    if resolution is None:
        return

    winning_assets = resolution.get("winning_asset_ids", set())
    resolved_epoch = resolution["resolved_epoch"]

    matching_fill = None
    for f in fills:
        if f.condition_id == condition_id and f.side == "BUY":
            matching_fill = f
            break

    if matching_fill is None:
        return

    asset_id = matching_fill.asset_id
    won = asset_id in winning_assets

    if won:
        pnl_gross = (1.0 - pos.avg_entry) * pos.qty
    else:
        pnl_gross = -(pos.avg_entry * pos.qty)

    pnl_net = pnl_gross - pos.slippage_fee
    hold_s = max(resolved_epoch - pos.entry_time, 0.0)
    hold_days = hold_s / 86400.0

    result = SimResult(
        condition_id=condition_id,
        outcome=pos.outcome,
        entry_price=pos.avg_entry,
        size_usd=pos.cost_basis - pos.slippage_fee,
        slippage_fee=pos.slippage_fee,
        signal_time=pos.entry_time,
        resolved_at=resolved_epoch,
        won=won,
        pnl_gross=pnl_gross,
        pnl_net=pnl_net,
        hold_days=hold_days,
        consensus_count=matching_fill.consensus_count,
    )
    results.append(result)


# ---------------------------------------------------------------------------
# Stage 8: Run one category x month x parameter combo
# ---------------------------------------------------------------------------


def run_category_month(
    category: str,
    trades: list[LightTrade],
    pool: dict[str, dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    test_month: date,
    base_rates: dict[str, tuple[float, float]],
    *,
    min_consensus: int,
    max_entry_price: float,
) -> MonthCategoryResult:
    """Run tick simulation for a single category/month/param combo."""
    test_start = test_month
    test_end = test_month + relativedelta(months=1)

    fills, results, stats = run_tick_simulation(
        trades,
        pool,
        resolutions,
        token_map,
        min_consensus=min_consensus,
        max_entry_price=max_entry_price,
    )

    n_pending = stats["n_fills"] - len(results)
    n_won = sum(1 for r in results if r.won)
    n_lost = sum(1 for r in results if not r.won)
    n_resolved = n_won + n_lost
    hit_rate = n_won / n_resolved if n_resolved > 0 else 0.0

    total_pnl_gross = sum(r.pnl_gross for r in results)
    total_pnl_net = sum(r.pnl_net for r in results)
    total_slippage = sum(r.slippage_fee for r in results)

    avg_entry = sum(r.entry_price for r in results) / n_resolved if n_resolved > 0 else 0.0
    avg_hold = sum(r.hold_days for r in results) / n_resolved if n_resolved > 0 else 0.0
    hold_days_list = sorted(r.hold_days for r in results)
    med_hold = hold_days_list[len(hold_days_list) // 2] if hold_days_list else 0.0

    yes_results = [r for r in results if r.outcome == "YES"]
    no_results = [r for r in results if r.outcome == "NO"]
    yes_hr = sum(1 for r in yes_results if r.won) / len(yes_results) if yes_results else 0.0
    no_hr = sum(1 for r in no_results if r.won) / len(no_results) if no_results else 0.0

    # Period-specific base rates
    br = base_rates.get(category, CATEGORY_BASE_RATES.get(category, (0.5, 0.5)))
    yes_br, no_br = br

    # Weighted excess HR
    if n_resolved > 0:
        yes_frac = len(yes_results) / n_resolved
        no_frac = len(no_results) / n_resolved
        excess_hr = yes_frac * (yes_hr - yes_br) + no_frac * (no_hr - no_br)
    else:
        excess_hr = 0.0

    # Compounding score
    avg_pnl_per_fill = total_pnl_net / n_resolved if n_resolved > 0 else 0.0
    med_hold_safe = max(med_hold, 0.1)  # floor to avoid division by zero
    compounding = excess_hr * avg_pnl_per_fill / med_hold_safe if n_resolved > 0 else 0.0

    n_markets = len(set(t.condition_id for t in trades))

    return MonthCategoryResult(
        test_month=test_start.isoformat(),
        category=category,
        min_consensus=min_consensus,
        max_entry_price=max_entry_price,
        pool_size=len(pool),
        n_insider_trades=stats["n_insider_trades"],
        n_markets_touched=n_markets,
        n_signals_triggered=stats["n_signals_triggered"],
        n_fills=stats["n_fills"],
        n_rejected_capital=stats["n_rejected_capital"],
        n_rejected_duplicate=stats["n_rejected_duplicate"],
        n_rejected_price=stats["n_rejected_price"],
        n_settled=stats["n_settled"],
        n_pending=n_pending,
        n_won=n_won,
        n_lost=n_lost,
        total_pnl_gross=total_pnl_gross,
        total_pnl_net=total_pnl_net,
        total_slippage=total_slippage,
        avg_entry_price=avg_entry,
        avg_hold_days=avg_hold,
        med_hold_days=med_hold,
        hit_rate=hit_rate,
        base_rate_yes=yes_br,
        base_rate_no=no_br,
        excess_hr=excess_hr,
        compounding_score=compounding,
        n_yes=len(yes_results),
        n_no=len(no_results),
        yes_hr=yes_hr,
        no_hr=no_hr,
    )


# ---------------------------------------------------------------------------
# Stage 9: Main orchestration
# ---------------------------------------------------------------------------


def run_all(
    client: Any,
    test_months: list[date],
    categories: list[str],
    train_months: int,
) -> list[MonthCategoryResult]:
    """Run tick-by-tick validation per category per month per parameter combo."""

    # Load resolutions once (global, used across all months)
    print("Loading resolution data...")
    t0 = time.time()
    resolutions, token_map = load_resolutions(client)
    print(f"  {len(resolutions):,} resolved markets, {len(token_map):,} token mappings ({time.time() - t0:.1f}s)")

    # Load market-to-category mapping once
    print("Loading market-to-category mapping...")
    t0 = time.time()
    market_cats = load_market_categories(client)
    print(f"  {len(market_cats):,} markets categorized ({time.time() - t0:.1f}s)")

    # Count categories
    cat_counts: dict[str, int] = {}
    for cat in market_cats.values():
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"  Category distribution: {sorted(cat_counts.items(), key=lambda x: -x[1])}")

    all_results: list[MonthCategoryResult] = []

    for test_month in test_months:
        test_start = test_month
        test_end = test_month + relativedelta(months=1)
        train_start = test_month - relativedelta(months=train_months)
        train_end = test_month

        print(f"\n{'='*80}")
        print(f"TEST MONTH: {test_start} to {test_end}")
        print(f"Train window: {train_start} to {train_end}")
        print(f"{'='*80}")

        # Build pool (global for all categories)
        t0 = time.time()
        pool = build_insider_pool(client, train_start, train_end)
        print(f"  Pool: {len(pool):,} strict-tier traders ({time.time() - t0:.1f}s)")
        if not pool:
            print("  EMPTY POOL -- skipping month")
            continue

        # Load ALL insider BUY trades for this test month
        t0 = time.time()
        all_trades = load_insider_trades(client, pool, test_start, test_end)
        n_markets = len(set(t.condition_id for t in all_trades))
        print(f"  Insider BUY trades: {len(all_trades):,} in {n_markets:,} markets ({time.time() - t0:.1f}s)")
        if not all_trades:
            print("  NO TRADES -- skipping month")
            continue

        # Load per-category base rates for this period
        base_rates = load_per_category_base_rates(client, test_start, test_end)
        print(f"  Per-category base rates: {', '.join(f'{c}={br[0]:.1%}/{br[1]:.1%}' for c, br in sorted(base_rates.items()))}")

        # Split trades by category
        cat_trades: dict[str, list[LightTrade]] = {}
        unclassified = 0
        gambling = 0
        for trade in all_trades:
            cat = market_cats.get(trade.condition_id, "other")
            if cat == "GAMBLING":
                gambling += 1
                continue
            if cat not in cat_trades:
                cat_trades[cat] = []
            cat_trades[cat].append(trade)

        print(f"  Trades by category: {', '.join(f'{c}={len(ts):,}' for c, ts in sorted(cat_trades.items(), key=lambda x: -len(x[1])))}")
        if gambling > 0:
            print(f"  Gambling trades excluded: {gambling:,}")

        # Run per-category simulations
        for category in categories:
            cat_trade_list = cat_trades.get(category, [])
            params = CATEGORY_PARAMS.get(category, [(3, 1.0)])

            if not cat_trade_list:
                print(f"\n  [{category.upper()}] No insider trades in this category -- skipping")
                for cons, max_px in params:
                    all_results.append(MonthCategoryResult(
                        test_month=test_start.isoformat(),
                        category=category,
                        min_consensus=cons,
                        max_entry_price=max_px,
                        pool_size=len(pool),
                    ))
                continue

            n_cat_markets = len(set(t.condition_id for t in cat_trade_list))
            print(f"\n  [{category.upper()}] {len(cat_trade_list):,} trades in {n_cat_markets:,} markets")

            for cons, max_px in params:
                t0 = time.time()
                result = run_category_month(
                    category,
                    cat_trade_list,
                    pool,
                    resolutions,
                    token_map,
                    test_month,
                    base_rates,
                    min_consensus=cons,
                    max_entry_price=max_px,
                )
                elapsed = time.time() - t0

                n_res = result.n_won + result.n_lost
                print(
                    f"    C>={cons} px<{max_px:.2f}: "
                    f"fills={result.n_fills:>4} res={n_res:>4} "
                    f"HR={result.hit_rate:>5.1%} "
                    f"ExHR={result.excess_hr:>+6.1%} "
                    f"PnL=${result.total_pnl_net:>10,.0f} "
                    f"hold={result.avg_hold_days:>5.1f}d "
                    f"comp={result.compounding_score:>8.1f} "
                    f"({elapsed:.1f}s)"
                )

                all_results.append(result)

    return all_results


# ---------------------------------------------------------------------------
# Stage 10: Summary and output
# ---------------------------------------------------------------------------


def print_summary(results: list[MonthCategoryResult]) -> None:
    """Print comprehensive summary with vectorized comparison."""
    # Group by category
    categories = sorted(set(r.category for r in results))

    print(f"\n{'='*100}")
    print("PER-TAG TICK-BY-TICK VALIDATION SUMMARY")
    print(f"{'='*100}")

    for cat in categories:
        cat_results = [r for r in results if r.category == cat]

        # Group by parameter combo
        param_combos = sorted(set((r.min_consensus, r.max_entry_price) for r in cat_results))

        print(f"\n{'='*80}")
        ref = VECTORIZED_REF.get(cat, {})
        vec_hr = ref.get("hr", 0.0)
        vec_pnl = ref.get("pnl_per_pos", 0.0)
        vec_comp = ref.get("comp", 0.0)
        print(f"  {cat.upper()}")
        print(f"  Vectorized ref: HR={vec_hr:.1%}, $/pos=${vec_pnl:.2f}, comp={vec_comp:.0f}")
        print(f"{'='*80}")

        print(f"\n  {'Params':>14} {'Month':>10} {'Fills':>6} {'Res':>5} "
              f"{'Won':>4} {'Lost':>5} {'HR':>7} {'ExHR':>7} "
              f"{'PnL$':>10} {'Hold(d)':>8} {'MedH':>6} {'Comp':>8}")
        print("  " + "-" * 98)

        for cons, max_px in param_combos:
            combo_results = [
                r for r in cat_results
                if r.min_consensus == cons and r.max_entry_price == max_px
            ]
            param_label = f"C>={cons} p<{max_px:.2f}"

            for r in combo_results:
                n_res = r.n_won + r.n_lost
                print(
                    f"  {param_label:>14} {r.test_month:>10} "
                    f"{r.n_fills:>6} {n_res:>5} {r.n_won:>4} {r.n_lost:>5} "
                    f"{r.hit_rate:>6.1%} {r.excess_hr:>+6.1%} "
                    f"{r.total_pnl_net:>10,.0f} {r.avg_hold_days:>8.1f} "
                    f"{r.med_hold_days:>6.1f} {r.compounding_score:>8.1f}"
                )

            # Aggregate row for this param combo
            total_won = sum(r.n_won for r in combo_results)
            total_lost = sum(r.n_lost for r in combo_results)
            total_resolved = total_won + total_lost
            total_fills = sum(r.n_fills for r in combo_results)
            total_pnl = sum(r.total_pnl_net for r in combo_results)

            if total_resolved > 0:
                agg_hr = total_won / total_resolved
                agg_hold = (
                    sum(r.avg_hold_days * (r.n_won + r.n_lost) for r in combo_results)
                    / total_resolved
                )
                agg_med_hold = (
                    sum(r.med_hold_days * (r.n_won + r.n_lost) for r in combo_results)
                    / total_resolved
                )
                agg_pnl_per = total_pnl / total_resolved
                agg_excess = sum(
                    r.excess_hr * (r.n_won + r.n_lost) for r in combo_results
                ) / total_resolved
                agg_comp = agg_excess * agg_pnl_per / max(agg_med_hold, 0.1)
            else:
                agg_hr = agg_hold = agg_med_hold = agg_pnl_per = agg_excess = agg_comp = 0.0

            gap_hr = agg_hr - vec_hr if vec_hr > 0 else 0.0

            print(
                f"  {'>>> AGG':>14} {'':>10} "
                f"{total_fills:>6} {total_resolved:>5} {total_won:>4} {total_lost:>5} "
                f"{agg_hr:>6.1%} {agg_excess:>+6.1%} "
                f"{total_pnl:>10,.0f} {agg_hold:>8.1f} "
                f"{agg_med_hold:>6.1f} {agg_comp:>8.1f}"
            )
            print(
                f"  {'':>14} {'vec gap':>10} "
                f"{'':>6} {'':>5} {'':>4} {'':>5} "
                f"{gap_hr:>+6.1%} {'':>7} "
                f"{'':>10} {'':>8} {'':>6} {'':>8}"
            )
            print("  " + "-" * 98)

    # Final comparison table: best per-category vs vectorized
    print(f"\n{'='*100}")
    print("BEST PER-CATEGORY CONFIG (max compounding score)")
    print(f"{'='*100}")
    print(f"\n  {'Category':>10} {'Params':>14} {'Tick HR':>8} {'Vec HR':>8} "
          f"{'Gap':>7} {'PnL$':>10} {'$/pos':>8} {'MedHold':>8} "
          f"{'TickComp':>10} {'VecComp':>10} {'Verdict':>12}")
    print("  " + "-" * 110)

    for cat in categories:
        cat_results = [r for r in results if r.category == cat]

        # Find best param combo by compounding score (aggregated)
        param_combos = sorted(set((r.min_consensus, r.max_entry_price) for r in cat_results))
        best_comp = float("-inf")
        best_params = (0, 1.0)
        best_agg: dict[str, float] = {}

        for cons, max_px in param_combos:
            combo_results = [
                r for r in cat_results
                if r.min_consensus == cons and r.max_entry_price == max_px
            ]
            tw = sum(r.n_won for r in combo_results)
            tl = sum(r.n_lost for r in combo_results)
            tr_ = tw + tl
            tp = sum(r.total_pnl_net for r in combo_results)

            if tr_ > 0:
                hr = tw / tr_
                avg_h = sum(r.avg_hold_days * (r.n_won + r.n_lost) for r in combo_results) / tr_
                med_h = sum(r.med_hold_days * (r.n_won + r.n_lost) for r in combo_results) / tr_
                pnl_per = tp / tr_
                ex_hr = sum(r.excess_hr * (r.n_won + r.n_lost) for r in combo_results) / tr_
                comp = ex_hr * pnl_per / max(med_h, 0.1)
            else:
                hr = avg_h = med_h = pnl_per = ex_hr = comp = 0.0

            if comp > best_comp:
                best_comp = comp
                best_params = (cons, max_px)
                best_agg = {
                    "hr": hr, "resolved": tr_, "pnl": tp, "pnl_per": pnl_per,
                    "med_hold": med_h, "comp": comp, "excess_hr": ex_hr,
                }

        ref = VECTORIZED_REF.get(cat, {})
        vec_hr = ref.get("hr", 0.0)
        vec_comp = ref.get("comp", 0.0)
        gap = best_agg.get("hr", 0) - vec_hr if vec_hr > 0 else 0.0

        verdict = ""
        if best_agg.get("pnl", 0) > 0 and best_agg.get("hr", 0) > 0.50:
            if best_agg.get("comp", 0) > 5.0:
                verdict = "EXCELLENT"
            elif best_agg.get("comp", 0) > 1.0:
                verdict = "MODERATE"
            else:
                verdict = "POOR"
        elif best_agg.get("pnl", 0) <= 0:
            verdict = "NEGATIVE PnL"
        else:
            verdict = "LOW HR"

        param_label = f"C>={best_params[0]} p<{best_params[1]:.2f}"
        print(
            f"  {cat:>10} {param_label:>14} "
            f"{best_agg.get('hr', 0):>7.1%} {vec_hr:>7.1%} "
            f"{gap:>+6.1%} "
            f"{best_agg.get('pnl', 0):>10,.0f} "
            f"{best_agg.get('pnl_per', 0):>8,.0f} "
            f"{best_agg.get('med_hold', 0):>8.1f} "
            f"{best_agg.get('comp', 0):>10.1f} "
            f"{vec_comp:>10.0f} "
            f"{verdict:>12}"
        )

    # Go/No-Go decision
    print(f"\n{'='*100}")
    print("GO/NO-GO DECISIONS")
    print(f"{'='*100}")

    for cat in categories:
        cat_results = [r for r in results if r.category == cat]
        total_pnl = sum(r.total_pnl_net for r in cat_results)
        total_resolved = sum(r.n_won + r.n_lost for r in cat_results)
        total_won = sum(r.n_won for r in cat_results)
        overall_hr = total_won / total_resolved if total_resolved > 0 else 0.0

        if total_pnl > 0 and overall_hr > 0.50 and total_resolved >= 10:
            print(f"  {cat:>10}: GO    (HR={overall_hr:.1%}, PnL=${total_pnl:,.0f}, N={total_resolved})")
        elif total_resolved < 10:
            print(f"  {cat:>10}: INSUFFICIENT DATA (N={total_resolved})")
        elif total_pnl <= 0:
            print(f"  {cat:>10}: NO-GO (negative PnL: ${total_pnl:,.0f})")
        else:
            print(f"  {cat:>10}: NO-GO (HR={overall_hr:.1%} < 50%)")


def save_results(results: list[MonthCategoryResult], suffix: str = "all") -> None:
    """Save results to parquet."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [asdict(r) for r in results]
    df = pl.DataFrame(rows)
    path = OUTPUT_DIR / f"per_tag_{suffix}.parquet"
    df.write_parquet(path)
    print(f"\nResults saved to {path}")

    # Also save per-category files
    for cat in sorted(set(r.category for r in results)):
        cat_rows = [asdict(r) for r in results if r.category == cat]
        cat_df = pl.DataFrame(cat_rows)
        cat_path = OUTPUT_DIR / f"per_tag_{cat}.parquet"
        cat_df.write_parquet(cat_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S2 Insider Copy: Per-tag tick-by-tick validation"
    )
    parser.add_argument(
        "--categories", type=str, nargs="*", default=None,
        help="Categories to test (default: all 8 categories)"
    )
    parser.add_argument(
        "--months", type=str, default=None,
        help="Comma-separated test months (YYYY-MM). Default: 2025-07,2025-10,2026-01"
    )
    parser.add_argument(
        "--train-months", type=int, default=DEFAULT_TRAIN_MONTHS,
        help=f"Training lookback in months. Default: {DEFAULT_TRAIN_MONTHS}"
    )
    args = parser.parse_args()

    # Parse test months
    if args.months:
        test_months: list[date] = []
        for m in args.months.split(","):
            parts = m.strip().split("-")
            test_months.append(date(int(parts[0]), int(parts[1]), 1))
    else:
        test_months = DEFAULT_TEST_MONTHS

    # Parse categories
    if args.categories:
        categories = args.categories
    else:
        categories = list(CATEGORY_PARAMS.keys())

    print(f"Categories: {categories}")
    print(f"Test months: {[m.isoformat() for m in test_months]}")
    print(f"Train lookback: {args.train_months} months")
    print()

    client = get_client()

    all_results = run_all(
        client,
        test_months,
        categories,
        args.train_months,
    )

    print_summary(all_results)
    save_results(all_results)


if __name__ == "__main__":
    main()
