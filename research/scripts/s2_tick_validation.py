"""S2 Insider Copy: Tick-by-tick validation of vectorized discovery results.

Walk-forward tick-by-tick replay with:
  - Tag-based susceptibility for insider pool selection
  - Strict tier: train_hr >= 0.75, high_pct >= 0.20, train_hr < 0.99
  - Entry price filter: max_entry_price (default 0.65)
  - Consensus: min unique insiders before triggering
  - Asset_id-based resolution (no string matching)
  - Capital constraints + mid-replay settlement
  - RealisticFillSimulator slippage model

Architecture:
  Stage 1: Build insider pool from ClickHouse (training window)
  Stage 2: Pull insider BUY trades from test window (signal trades)
  Stage 3: Build consensus signals in Python (trade by trade)
  Stage 4: For each triggered signal, record entry via RealisticFillSimulator
  Stage 5: Settle resolved positions, compute PnL

This avoids constructing millions of NormalizedTrade objects by implementing
the strategy logic directly against lightweight trade tuples from ClickHouse.
The logic mirrors InsiderCopyStrategy.on_trade() exactly.

Usage:
    uv run python research/scripts/s2_tick_validation.py
    uv run python research/scripts/s2_tick_validation.py --consensus 3
    uv run python research/scripts/s2_tick_validation.py --months 2025-07
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field, replace
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
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "s2_tick"

# Default parameters (from vectorized discovery)
DEFAULT_MIN_CONSENSUS = 3
DEFAULT_MAX_ENTRY_PRICE = 0.65
DEFAULT_SIZE_USD = 50.0
DEFAULT_CAPITAL_USD = 5000.0
DEFAULT_MAX_OPEN = 50
DEFAULT_TRAIN_MONTHS = 12

# Default test months (quarterly samples)
DEFAULT_TEST_MONTHS = [
    date(2025, 7, 1),
    date(2025, 10, 1),
    date(2026, 1, 1),
]


# ---------------------------------------------------------------------------
# Lightweight trade record (avoids Pydantic overhead for millions of rows)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LightTrade:
    """Minimal trade record for tick-by-tick replay."""

    condition_id: str
    asset_id: str
    side: str  # "BUY" or "SELL"
    price: float
    amount_usd: float
    maker: str  # lowercased
    timestamp_epoch: float  # unix epoch seconds
    published_at: float


@dataclass(slots=True)
class SimPosition:
    """Mutable position tracker for the simulation."""

    condition_id: str
    outcome: str  # "YES" or "NO"
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
    direction: str  # insider direction


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
class MonthResult:
    """Aggregated results for a single test month."""

    test_month: str
    pool_size: int = 0
    n_insider_trades: int = 0
    n_markets_touched: int = 0
    n_signals_triggered: int = 0
    n_fills: int = 0
    n_rejected_capital: int = 0
    n_rejected_duplicate: int = 0
    n_settled: int = 0
    n_pending: int = 0
    n_won: int = 0
    n_lost: int = 0
    total_pnl_gross: float = 0.0
    total_pnl_net: float = 0.0
    total_slippage: float = 0.0
    avg_entry_price: float = 0.0
    avg_hold_days: float = 0.0
    hit_rate: float = 0.0
    base_rate_yes: float = 0.0
    base_rate_no: float = 0.0
    excess_hr: float = 0.0


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
    """Return query results as list of dicts."""
    r = client.query(sql)
    if not r.result_rows:
        return []
    cols = r.column_names
    return [dict(zip(cols, row)) for row in r.result_rows]


# ---------------------------------------------------------------------------
# Stage 1: Build insider pool
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
# Stage 2: Load insider trades from test window
# ---------------------------------------------------------------------------


def load_insider_trades(
    client: Any,
    pool: dict[str, dict[str, Any]],
    test_start: date,
    test_end: date,
) -> list[LightTrade]:
    """Load BUY trades from insider pool members during the test window.

    Only loads trades from insiders (not all trades in their markets).
    This is sufficient because:
    - We only need BUY trades from insiders to build consensus
    - We don't implement stop-loss in this validation (hold to resolution)
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
    # Insert in batches
    batch_size = 10000
    for i in range(0, len(traders_list), batch_size):
        batch = traders_list[i : i + batch_size]
        client.insert(
            "_tmp_s2_tick_pool",
            [[t] for t in batch],
            column_names=["trader"],
        )

    # Load only insider BUY trades (not all trades in their markets)
    # This is the key optimization: ~50K-100K trades vs ~6M
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
# Stage 3: Load resolution data
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

        # Token map
        if cid not in token_map:
            token_map[cid] = {}
        token_map[cid][outcome] = asset_id

        # Resolutions (accumulate winning asset_ids)
        if epoch > 0:
            if cid not in resolutions:
                resolutions[cid] = {"resolved_epoch": epoch, "winning_asset_ids": set()}
            if token_won == 1:
                resolutions[cid]["winning_asset_ids"].add(asset_id)

    return resolutions, token_map


# ---------------------------------------------------------------------------
# Stage 4: Load period base rates
# ---------------------------------------------------------------------------


def load_period_base_rates(
    client: Any,
    test_start: date,
    test_end: date,
) -> tuple[float, float]:
    """Compute YES/NO base rates for the test period.

    Uses tag-based gambling exclusion (not market_categories).
    Returns (yes_base_rate, no_base_rate).
    """
    sql = f"""
        WITH gambling_markets AS (
            SELECT DISTINCT m.condition_id
            FROM markets AS m
            INNER JOIN events AS e ON m.event_id = e.id
            INNER JOIN event_tags AS et ON e.id = et.event_id
            INNER JOIN tags AS t ON et.tag_id = t.id
            WHERE t.label IN (
                'Up or Down', 'Crypto Prices', '5M', '15M',
                'Hit Price', 'Multi Strikes', '4H', '1H'
            )
        )
        SELECT
            countIf(mr.token_won = 1 AND mr.outcome = 'YES') AS yes_won,
            countIf(mr.token_won = 1 AND mr.outcome = 'NO') AS no_won
        FROM markets_resolved AS mr
        WHERE mr.resolved_at >= '{test_start.isoformat()}'
          AND mr.resolved_at < '{test_end.isoformat()}'
          AND mr.resolved_at > '1970-01-02'
          AND mr.condition_id NOT IN (SELECT condition_id FROM gambling_markets)
    """
    df = ch_query(client, sql)
    if df.is_empty():
        return 0.5, 0.5
    row = df.row(0, named=True)
    yes_won = int(row["yes_won"])
    no_won = int(row["no_won"])
    total = yes_won + no_won
    if total == 0:
        return 0.5, 0.5
    return yes_won / total, no_won / total


# ---------------------------------------------------------------------------
# Stage 5: Slippage model (simplified RealisticFillSimulator)
# ---------------------------------------------------------------------------


def compute_slippage(
    size_usd: float,
    *,
    fallback_half_spread: float = 0.005,
    default_liquidity_usd: float = 5000.0,
    impact_scale: float = 1.0,
) -> float:
    """Compute slippage cost (half_spread + linear impact) * size_usd.

    Per-market calibration requires the full trade history which is too expensive.
    We use fallback values which gives a conservative (higher) slippage estimate.
    """
    impact = (size_usd / default_liquidity_usd) * impact_scale
    return (fallback_half_spread + impact) * size_usd


# ---------------------------------------------------------------------------
# Stage 6: Tick-by-tick simulation engine
# ---------------------------------------------------------------------------


def run_tick_simulation(
    trades: list[LightTrade],
    pool: dict[str, dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    *,
    min_consensus: int = DEFAULT_MIN_CONSENSUS,
    max_entry_price: float = DEFAULT_MAX_ENTRY_PRICE,
    size_usd: float = DEFAULT_SIZE_USD,
    capital_usd: float = DEFAULT_CAPITAL_USD,
    max_open: int = DEFAULT_MAX_OPEN,
) -> tuple[list[SimFill], list[SimResult], dict[str, Any]]:
    """Run tick-by-tick simulation of insider copy strategy.

    Processes trades in timestamp order. For each insider BUY:
    1. Add maker to consensus set for this market
    2. If consensus threshold reached and no existing position:
       a. Check entry price filter
       b. Check capital + open position limits
       c. Record fill with slippage
    3. Settle resolved positions as clock advances

    Returns (fills, results, stats).
    """
    # State
    # consensus: cid -> {direction: str, insiders: set[str], first_price: float}
    consensus: dict[str, dict[str, Any]] = {}
    # positions: cid -> SimPosition
    positions: dict[str, SimPosition] = {}
    # fills (all entries)
    fills: list[SimFill] = []
    # results (settled positions)
    results: list[SimResult] = []
    # Track entered markets to prevent re-entry after settlement
    entered_markets: set[str] = set()

    # Pre-sort resolution timeline for settlement
    res_timeline: list[tuple[float, str]] = sorted(
        (r["resolved_epoch"], cid) for cid, r in resolutions.items() if r["resolved_epoch"] > 0
    )
    res_idx = 0
    n_res = len(res_timeline)

    # Stats
    stats: dict[str, Any] = {
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

        # --- Only BUY trades from insiders (already filtered in SQL) ---
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

        # Check: no existing position (includes already-entered AND already-settled)
        if cid in positions:
            stats["n_rejected_duplicate"] += 1
            continue
        # Track markets we've already entered to avoid re-entry after settlement
        if cid in entered_markets:
            stats["n_rejected_duplicate"] += 1
            continue

        # Check: entry price filter
        if trade.price >= max_entry_price:
            stats["n_rejected_price"] += 1
            continue

        # Check: capital limit
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

        # Determine outcome from insider direction
        outcome = signal["direction"]

        # Determine asset_id from token_map
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

        # Record position
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
    """Settle a position using asset_id-based resolution."""
    pos = positions.get(condition_id)
    if pos is None or pos.qty <= 0:
        return

    resolution = resolutions.get(condition_id)
    if resolution is None:
        return

    winning_assets = resolution.get("winning_asset_ids", set())
    resolved_epoch = resolution["resolved_epoch"]

    # Find the fill for this position
    matching_fill = None
    for f in fills:
        if f.condition_id == condition_id and f.side == "BUY":
            matching_fill = f
            break

    if matching_fill is None:
        return

    # Determine win/loss via asset_id (CRITICAL: no string matching)
    asset_id = matching_fill.asset_id
    won = asset_id in winning_assets

    # PnL computation
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
# Main
# ---------------------------------------------------------------------------


def run_month(
    client: Any,
    test_month: date,
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    *,
    min_consensus: int = DEFAULT_MIN_CONSENSUS,
    max_entry_price: float = DEFAULT_MAX_ENTRY_PRICE,
    train_months: int = DEFAULT_TRAIN_MONTHS,
) -> MonthResult:
    """Run tick-by-tick validation for a single test month."""
    test_start = test_month
    test_end = test_month + relativedelta(months=1)
    train_start = test_month - relativedelta(months=train_months)
    train_end = test_month

    print(f"\n{'='*70}")
    print(f"Test month: {test_start} to {test_end}")
    print(f"Train window: {train_start} to {train_end}")
    print(f"Min consensus: {min_consensus}, Max entry price: {max_entry_price}")
    print(f"{'='*70}")

    # Stage 1: Build pool
    t0 = time.time()
    pool = build_insider_pool(client, train_start, train_end)
    print(f"  Pool size: {len(pool):,} strict-tier traders ({time.time() - t0:.1f}s)")
    if not pool:
        print("  EMPTY POOL -- skipping month")
        return MonthResult(test_month=test_start.isoformat())

    # Stage 2: Load insider trades
    t0 = time.time()
    trades = load_insider_trades(client, pool, test_start, test_end)
    n_markets = len(set(t.condition_id for t in trades))
    print(f"  Insider BUY trades: {len(trades):,} in {n_markets:,} markets ({time.time() - t0:.1f}s)")
    if not trades:
        print("  NO TRADES -- skipping month")
        return MonthResult(test_month=test_start.isoformat(), pool_size=len(pool))

    # Stage 3: Period base rates
    yes_br, no_br = load_period_base_rates(client, test_start, test_end)
    print(f"  Period base rates: YES={yes_br:.1%}, NO={no_br:.1%}")

    # Stage 4: Run simulation
    t0 = time.time()
    fills, results, stats = run_tick_simulation(
        trades,
        pool,
        resolutions,
        token_map,
        min_consensus=min_consensus,
        max_entry_price=max_entry_price,
    )
    print(f"  Simulation: {time.time() - t0:.1f}s")
    print(f"    Trades processed: {stats['n_trades_processed']:,}")
    print(f"    Insider trades: {stats['n_insider_trades']:,}")
    print(f"    Signals triggered: {stats['n_signals_triggered']:,}")
    print(f"    Fills: {stats['n_fills']:,}")
    print(f"    Rejected (capital): {stats['n_rejected_capital']:,}")
    print(f"    Rejected (duplicate): {stats['n_rejected_duplicate']:,}")
    print(f"    Rejected (price): {stats['n_rejected_price']:,}")
    print(f"    Settled: {stats['n_settled']:,}")

    # Stage 5: Analyze results
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

    # Compute direction-specific HR
    yes_results = [r for r in results if r.outcome == "YES"]
    no_results = [r for r in results if r.outcome == "NO"]
    yes_hr = sum(1 for r in yes_results if r.won) / len(yes_results) if yes_results else 0.0
    no_hr = sum(1 for r in no_results if r.won) / len(no_results) if no_results else 0.0

    # Weighted excess HR
    if n_resolved > 0:
        yes_frac = len(yes_results) / n_resolved
        no_frac = len(no_results) / n_resolved
        excess_hr = yes_frac * (yes_hr - yes_br) + no_frac * (no_hr - no_br)
    else:
        excess_hr = 0.0

    print(f"\n  --- Results ---")
    print(f"  Resolved:  {n_resolved} (pending: {n_pending})")
    print(f"  Won/Lost:  {n_won}/{n_lost}")
    print(f"  Hit Rate:  {hit_rate:.1%} (overall)")
    print(f"  YES HR:    {yes_hr:.1%} ({len(yes_results)} positions, base: {yes_br:.1%})")
    print(f"  NO HR:     {no_hr:.1%} ({len(no_results)} positions, base: {no_br:.1%})")
    print(f"  Excess HR: {excess_hr:+.1%}")
    print(f"  PnL gross: ${total_pnl_gross:,.2f}")
    print(f"  PnL net:   ${total_pnl_net:,.2f}")
    print(f"  Slippage:  ${total_slippage:,.2f}")
    print(f"  Avg entry: {avg_entry:.3f}")
    print(f"  Avg hold:  {avg_hold:.1f} days")

    return MonthResult(
        test_month=test_start.isoformat(),
        pool_size=len(pool),
        n_insider_trades=stats["n_insider_trades"],
        n_markets_touched=n_markets,
        n_signals_triggered=stats["n_signals_triggered"],
        n_fills=stats["n_fills"],
        n_rejected_capital=stats["n_rejected_capital"],
        n_rejected_duplicate=stats["n_rejected_duplicate"],
        n_settled=stats["n_settled"],
        n_pending=n_pending,
        n_won=n_won,
        n_lost=n_lost,
        total_pnl_gross=total_pnl_gross,
        total_pnl_net=total_pnl_net,
        total_slippage=total_slippage,
        avg_entry_price=avg_entry,
        avg_hold_days=avg_hold,
        hit_rate=hit_rate,
        base_rate_yes=yes_br,
        base_rate_no=no_br,
        excess_hr=excess_hr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="S2 Insider Copy: Tick-by-tick validation")
    parser.add_argument("--consensus", type=int, default=DEFAULT_MIN_CONSENSUS,
                        help=f"Min consensus (unique insiders). Default: {DEFAULT_MIN_CONSENSUS}")
    parser.add_argument("--max-price", type=float, default=DEFAULT_MAX_ENTRY_PRICE,
                        help=f"Max entry price filter. Default: {DEFAULT_MAX_ENTRY_PRICE}")
    parser.add_argument("--months", type=str, default=None,
                        help="Comma-separated test months (YYYY-MM). Default: 2025-07,2025-10,2026-01")
    parser.add_argument("--train-months", type=int, default=DEFAULT_TRAIN_MONTHS,
                        help=f"Training lookback in months. Default: {DEFAULT_TRAIN_MONTHS}")
    parser.add_argument("--sweep", action="store_true",
                        help="Run parameter sweep over consensus=[1,2,3,5] x max_price=[0.65,0.85,1.0]")
    args = parser.parse_args()

    # Parse test months
    if args.months:
        test_months = []
        for m in args.months.split(","):
            parts = m.strip().split("-")
            test_months.append(date(int(parts[0]), int(parts[1]), 1))
    else:
        test_months = DEFAULT_TEST_MONTHS

    client = get_client()

    # Load resolutions once (global, used across all months)
    print("Loading resolution data...")
    t0 = time.time()
    resolutions, token_map = load_resolutions(client)
    print(f"  {len(resolutions):,} resolved markets, {len(token_map):,} token mappings ({time.time() - t0:.1f}s)")

    if args.sweep:
        _run_sweep(client, test_months, resolutions, token_map, args.train_months)
    else:
        _run_single(
            client, test_months, resolutions, token_map,
            min_consensus=args.consensus,
            max_entry_price=args.max_price,
            train_months=args.train_months,
        )


def _run_single(
    client: Any,
    test_months: list[date],
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    *,
    min_consensus: int,
    max_entry_price: float,
    train_months: int,
) -> None:
    """Run single parameter configuration across all test months."""
    all_results: list[MonthResult] = []

    for month in test_months:
        result = run_month(
            client, month, resolutions, token_map,
            min_consensus=min_consensus,
            max_entry_price=max_entry_price,
            train_months=train_months,
        )
        all_results.append(result)

    _print_summary(all_results, min_consensus, max_entry_price)
    _save_results(all_results, f"consensus{min_consensus}_price{max_entry_price}")


def _run_sweep(
    client: Any,
    test_months: list[date],
    resolutions: dict[str, dict[str, Any]],
    token_map: dict[str, dict[str, str]],
    train_months: int,
) -> None:
    """Parameter sweep: consensus x max_entry_price."""
    consensus_grid = [1, 2, 3, 5]
    price_grid = [0.65, 0.85, 1.0]

    sweep_results: list[dict[str, Any]] = []

    for consensus in consensus_grid:
        for max_price in price_grid:
            print(f"\n{'#'*70}")
            print(f"# SWEEP: consensus={consensus}, max_price={max_price}")
            print(f"{'#'*70}")

            month_results: list[MonthResult] = []
            for month in test_months:
                result = run_month(
                    client, month, resolutions, token_map,
                    min_consensus=consensus,
                    max_entry_price=max_price,
                    train_months=train_months,
                )
                month_results.append(result)

            # Aggregate across months
            total_resolved = sum(r.n_won + r.n_lost for r in month_results)
            total_won = sum(r.n_won for r in month_results)
            total_pnl_net = sum(r.total_pnl_net for r in month_results)
            total_fills = sum(r.n_fills for r in month_results)
            avg_hr = total_won / total_resolved if total_resolved > 0 else 0.0
            avg_hold = (
                sum(r.avg_hold_days * (r.n_won + r.n_lost) for r in month_results) / total_resolved
                if total_resolved > 0 else 0.0
            )

            sweep_results.append({
                "consensus": consensus,
                "max_price": max_price,
                "total_fills": total_fills,
                "total_resolved": total_resolved,
                "total_won": total_won,
                "hit_rate": avg_hr,
                "total_pnl_net": total_pnl_net,
                "avg_hold_days": avg_hold,
            })

    # Print sweep summary
    print(f"\n{'='*80}")
    print("PARAMETER SWEEP SUMMARY")
    print(f"{'='*80}")
    print(f"{'Consensus':>10} {'MaxPrice':>10} {'Fills':>8} {'Resolved':>10} {'Won':>6} "
          f"{'HR':>8} {'PnL Net':>12} {'Hold(d)':>8}")
    print("-" * 80)
    for s in sweep_results:
        print(f"{s['consensus']:>10} {s['max_price']:>10.2f} {s['total_fills']:>8} "
              f"{s['total_resolved']:>10} {s['total_won']:>6} "
              f"{s['hit_rate']:>7.1%} {s['total_pnl_net']:>12,.2f} {s['avg_hold_days']:>8.1f}")

    # Save sweep
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(sweep_results)
    df.write_parquet(OUTPUT_DIR / "sweep_results.parquet")
    print(f"\nSweep saved to {OUTPUT_DIR / 'sweep_results.parquet'}")


def _print_summary(
    results: list[MonthResult],
    min_consensus: int,
    max_entry_price: float,
) -> None:
    """Print cross-month summary with vectorized comparison."""
    print(f"\n{'='*70}")
    print("TICK-BY-TICK VALIDATION SUMMARY")
    print(f"Parameters: consensus >= {min_consensus}, entry price < {max_entry_price}")
    print(f"{'='*70}")

    # Per-month table
    print(f"\n{'Month':>10} {'Fills':>6} {'Res':>5} {'Won':>4} {'Lost':>5} "
          f"{'HR':>7} {'ExHR':>7} {'PnL$':>10} {'Slip$':>8} {'Hold(d)':>8}")
    print("-" * 80)

    for r in results:
        n_res = r.n_won + r.n_lost
        print(f"{r.test_month:>10} {r.n_fills:>6} {n_res:>5} {r.n_won:>4} {r.n_lost:>5} "
              f"{r.hit_rate:>6.1%} {r.excess_hr:>+6.1%} "
              f"{r.total_pnl_net:>10,.2f} {r.total_slippage:>8,.2f} {r.avg_hold_days:>8.1f}")

    # Totals
    total_fills = sum(r.n_fills for r in results)
    total_won = sum(r.n_won for r in results)
    total_lost = sum(r.n_lost for r in results)
    total_resolved = total_won + total_lost
    total_pnl_net = sum(r.total_pnl_net for r in results)
    total_slippage = sum(r.total_slippage for r in results)
    overall_hr = total_won / total_resolved if total_resolved > 0 else 0.0
    avg_hold = (
        sum(r.avg_hold_days * (r.n_won + r.n_lost) for r in results) / total_resolved
        if total_resolved > 0 else 0.0
    )

    print("-" * 80)
    print(f"{'TOTAL':>10} {total_fills:>6} {total_resolved:>5} {total_won:>4} {total_lost:>5} "
          f"{overall_hr:>6.1%} {'':>7} "
          f"{total_pnl_net:>10,.2f} {total_slippage:>8,.2f} {avg_hold:>8.1f}")

    # Vectorized comparison
    print(f"\n{'='*70}")
    print("VECTORIZED vs TICK-BY-TICK COMPARISON")
    print(f"{'='*70}")
    print(f"{'Metric':<25} {'Vectorized (UB)':<20} {'Tick-by-Tick':<20} {'Gap':<15}")
    print("-" * 70)

    vec_hr = 0.859 if max_entry_price <= 0.65 else 0.832
    vec_pnl_per_pos = 71.74 if max_entry_price <= 0.65 else 45.33

    avg_pnl_per_fill = total_pnl_net / total_resolved if total_resolved > 0 else 0.0
    gap_hr = overall_hr - vec_hr

    print(f"{'Hit Rate':<25} {vec_hr:<20.1%} {overall_hr:<20.1%} {gap_hr:<+15.1%}")
    print(f"{'PnL per position':<25} ${vec_pnl_per_pos:<19,.2f} ${avg_pnl_per_fill:<19,.2f} "
          f"${avg_pnl_per_fill - vec_pnl_per_pos:<+14,.2f}")
    print(f"{'Total fills':<25} {'~308K (14 months)':<20} {total_fills:<20,} {'N/A':<15}")
    print(f"{'Avg hold (days)':<25} {'N/A':<20} {avg_hold:<20.1f} {'N/A':<15}")

    # Quality assessment
    print(f"\n{'='*70}")
    print("ASSESSMENT")
    print(f"{'='*70}")

    if gap_hr > -0.20:
        print("  Gap < 20pp -- BETTER than expected (typical: 20-40pp degradation)")
    elif gap_hr > -0.40:
        print("  Gap 20-40pp -- WITHIN expected range")
    else:
        print("  Gap > 40pp -- WORSE than expected. Investigate pre-flight checklist.")

    if overall_hr > 0.50 and total_pnl_net > 0:
        print("  Strategy SURVIVES tick-by-tick validation with positive PnL.")
    elif overall_hr > 0.50:
        print("  HR above 50% but negative PnL -- slippage/hold time issue.")
    else:
        print("  HR below 50% -- strategy does NOT survive tick-by-tick validation.")

    # Compounding score
    if total_resolved > 0 and avg_hold > 0:
        base_hr = 0.443  # susceptible base rate
        compounding = (overall_hr - base_hr) * avg_pnl_per_fill / avg_hold
        print(f"  Compounding score: {compounding:.2f}")
        if compounding > 5.0:
            print("  --> EXCELLENT. Promote to strategies_impl/.")
        elif compounding > 1.0:
            print("  --> MODERATE. Deploy with position limits.")
        elif compounding > 0:
            print("  --> POOR. Park or combine with other signals.")
        else:
            print("  --> NEGATIVE. Abandon this configuration.")


def _save_results(results: list[MonthResult], suffix: str) -> None:
    """Save results to parquet."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    rows = [asdict(r) for r in results]
    df = pl.DataFrame(rows)
    path = OUTPUT_DIR / f"monthly_{suffix}.parquet"
    df.write_parquet(path)
    print(f"\nResults saved to {path}")


if __name__ == "__main__":
    main()
