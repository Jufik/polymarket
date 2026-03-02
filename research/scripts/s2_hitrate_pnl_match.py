"""Match the ReplayRunner's PnL computation exactly.

This script replicates the ReplayRunner logic exactly:
- fill_price = min(price + 0.02, 0.95) (the max_price)
- slippage = calibrated from the actual trade set
- UNKNOWN outcomes default to "YES"
- Resolution via asset_id matching
- Settlement PnL uses qty_yes/qty_no + yes_won

Usage:
    uv run python research/scripts/s2_hitrate_pnl_match.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

import structlog

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import clickhouse_connect
import polars as pl

from research.strategies.s2_data import invert_token_map
from research.strategies.s2_hitrate_copy import S2HitRateCopy

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


def ch_query(client, sql: str) -> pl.DataFrame:
    r = client.query(sql)
    if not r.result_rows:
        return pl.DataFrame()
    return pl.DataFrame({col: [row[i] for row in r.result_rows]
                         for i, col in enumerate(r.column_names)})


async def diagnose() -> None:
    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    period = "2025-04"
    as_of_date = f"{period}-01"

    query = S2HitRateCopy.qualified_traders_query(
        min_positions=30, min_excess_hr=0.10, recency_months=6,
        direction="BOTH", max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True, as_of_date=as_of_date,
    )
    df = ch_query(client, query)
    traders = set(df["trader"].to_list())

    # Load trades via direct CH query
    from research.strategies.s2_data import qualified_trades_query, resolutions_query, parse_period_range
    from polymarket_pipeline.models import NormalizedTrade
    from datetime import datetime

    start_date, end_date = parse_period_range(period)
    trades_sql = qualified_trades_query(traders, start_date, end_date)
    trades_df = ch_query(client, trades_sql)

    trades = []
    for row in trades_df.iter_rows(named=True):
        try:
            trade = NormalizedTrade(**row)
            trades.append(trade)
        except Exception:
            continue

    # Extended resolution window (3 months after)
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    extended_end = f"{dt_end.year}-{dt_end.month + 3:02d}-01" if dt_end.month <= 9 else f"{dt_end.year + 1}-{(dt_end.month + 3 - 12):02d}-01"
    res_sql = resolutions_query(start_date, extended_end)
    res_df = ch_query(client, res_sql)

    resolutions: dict[str, tuple[str, float]] = {}
    token_map: dict[str, dict[str, str]] = {}
    for row in res_df.iter_rows(named=True):
        cid = str(row["condition_id"])
        asset_id = str(row["asset_id"])
        outcome = str(row["outcome"])
        token_won = int(row["token_won"])
        epoch = float(row.get("resolved_epoch", 0))
        if cid not in token_map:
            token_map[cid] = {}
        token_map[cid][outcome] = asset_id
        if token_won == 1 and epoch > 0:
            resolutions[cid] = (outcome, epoch)
    inv_token_map = invert_token_map(token_map)

    print(f"Pool: {len(traders)}, Trades: {len(trades):,}, Resolutions: {len(resolutions):,}")

    # Simulate exactly matching the strategy + ReplayRunner
    consensus: dict[str, set[str]] = {}
    scaled: set[str] = set()
    size_usd = 100.0
    threshold = 3

    # Resolution data for settlement (asset_id based)
    # Build winning_asset_ids per cid
    res_assets: dict[str, set[str]] = defaultdict(set)  # cid -> winning asset_ids
    res_epochs: dict[str, float] = {}
    for cid, (winner_outcome, epoch) in resolutions.items():
        # The winning asset_id is the one for the winner outcome
        if cid in token_map:
            win_aid = token_map[cid].get(winner_outcome, "")
            if win_aid:
                res_assets[cid].add(win_aid)
        res_epochs[cid] = epoch

    # Simulate fill-by-fill
    fills: list[dict] = []
    for trade in sorted(trades, key=lambda t: t.published_at):
        if str(trade.side) != "BUY":
            continue
        maker = (trade.maker or "").lower()
        if maker not in traders:
            continue
        if float(trade.price) > 0.85:
            continue

        cid = trade.condition_id
        if cid in scaled:
            continue

        if cid not in consensus:
            consensus[cid] = set()
        consensus[cid].add(maker)

        if len(consensus[cid]) < threshold:
            continue

        # Fill at max_price = min(price + 0.02, 0.95)
        trade_price = float(trade.price)
        fill_price = min(trade_price + 0.02, 0.95)
        scaled.add(cid)

        # Determine outcome from token_map (same as strategy)
        token_info = inv_token_map.get(trade.asset_id, {})
        outcome = token_info.get("outcome", "YES")  # default YES

        # Resolution check using asset_id
        # For settlement PnL: we need to check if the asset we hold (trade.asset_id) won
        asset_won = trade.asset_id in res_assets.get(cid, set())

        # Alternative: check via outcome label
        has_resolution = cid in resolutions
        if has_resolution:
            winner, _ = resolutions[cid]
            outcome_won = (outcome == winner)
        else:
            outcome_won = False

        # ReplayRunner PnL: uses yes_won, then checks pos.qty_yes
        # yes_won = yes_asset in resolution.winning_asset_ids
        yes_asset = token_map.get(cid, {}).get("YES", "")
        yes_won = yes_asset in res_assets.get(cid, set())

        # Compute PnL exactly as ReplayRunner._settle_market
        qty = size_usd / fill_price
        if outcome == "YES":
            # qty_yes = qty, qty_no = 0
            if yes_won:
                pnl_replay = (1.0 - fill_price) * qty
            else:
                pnl_replay = -(fill_price) * qty
        else:
            # qty_no = qty, qty_yes = 0
            if not yes_won:  # NO wins
                pnl_replay = (1.0 - fill_price) * qty
            else:
                pnl_replay = -(fill_price) * qty

        # Compute PnL from diagnostic (simpler model using asset_id)
        if has_resolution:
            if asset_won:
                pnl_simple = (1.0 - fill_price) * qty
            else:
                pnl_simple = -(fill_price) * qty
        else:
            pnl_simple = 0.0

        fills.append({
            "cid": cid,
            "outcome": outcome,
            "asset_id": trade.asset_id,
            "fill_price": fill_price,
            "has_resolution": has_resolution,
            "asset_won": asset_won,
            "outcome_won": outcome_won,
            "yes_won": yes_won,
            "pnl_replay": pnl_replay if has_resolution else 0.0,
            "pnl_simple": pnl_simple,
            "pnl_match": (pnl_replay == pnl_simple) if has_resolution else True,
        })

    # Analyze
    resolved_fills = [f for f in fills if f["has_resolution"]]
    mismatched = [f for f in resolved_fills if not f["pnl_match"]]

    print(f"\nTotal fills: {len(fills)}")
    print(f"Resolved: {len(resolved_fills)}")
    print(f"PnL MISMATCHED between replay and simple: {len(mismatched)}")

    total_replay_pnl = sum(f["pnl_replay"] for f in resolved_fills)
    total_simple_pnl = sum(f["pnl_simple"] for f in resolved_fills)
    print(f"Total replay PnL: ${total_replay_pnl:,.2f}")
    print(f"Total simple PnL: ${total_simple_pnl:,.2f}")

    # Breakdown of mismatches
    if mismatched:
        print(f"\n--- MISMATCHED fills (first 10) ---")
        for f in mismatched[:10]:
            print(f"  cid={f['cid'][:20]}... outcome={f['outcome']} "
                  f"asset_won={f['asset_won']} outcome_won={f['outcome_won']} "
                  f"yes_won={f['yes_won']} pnl_replay=${f['pnl_replay']:.2f} "
                  f"pnl_simple=${f['pnl_simple']:.2f}")

        # What's causing the mismatch?
        mismatch_reasons = Counter()
        for f in mismatched:
            if f["asset_won"] != f["outcome_won"]:
                mismatch_reasons["asset_vs_outcome"] += 1
            if f["outcome"] == "YES" and not f["yes_won"] and f["asset_won"]:
                mismatch_reasons["outcome_YES_but_asset_NO_won"] += 1
            if f["outcome"] == "NO" and f["yes_won"] and f["asset_won"]:
                mismatch_reasons["outcome_NO_but_YES_won_and_asset_won"] += 1

        print(f"\nMismatch reasons: {dict(mismatch_reasons)}")

        # Total PnL impact of mismatches
        mismatch_pnl_diff = sum(f["pnl_replay"] - f["pnl_simple"] for f in mismatched)
        print(f"PnL impact of mismatches: ${mismatch_pnl_diff:,.2f}")

    # UNKNOWN outcome fills
    unknown_fills = [f for f in fills if f["outcome"] == "YES" and f["asset_id"] not in inv_token_map.values()]
    # Check if outcome was defaulted to YES due to missing token_map
    default_yes = [f for f in fills if inv_token_map.get(f["asset_id"]) is None]
    print(f"\nFills with defaulted outcome (no token_map match): {len(default_yes)}")
    default_resolved = [f for f in default_yes if f["has_resolution"]]
    default_won = sum(1 for f in default_resolved if f["pnl_replay"] > 0)
    default_pnl = sum(f["pnl_replay"] for f in default_resolved)
    print(f"  Resolved: {len(default_resolved)}, Won: {default_won}, PnL: ${default_pnl:,.2f}")

    # Now compute: what should the ReplayRunner PnL be without these default fills?
    non_default = [f for f in resolved_fills if f not in default_resolved]
    print(f"\nNon-default fills: {len(non_default)}")
    non_default_pnl = sum(f["pnl_replay"] for f in non_default)
    non_default_won = sum(1 for f in non_default if f["pnl_replay"] > 0)
    non_default_hr = non_default_won / len(non_default) if non_default else 0
    print(f"  PnL: ${non_default_pnl:,.2f}, HR: {non_default_hr:.1%}")

    # Per-direction
    for outcome in ["YES", "NO"]:
        o_fills = [f for f in non_default if f["outcome"] == outcome]
        o_won = sum(1 for f in o_fills if f["pnl_replay"] > 0)
        o_pnl = sum(f["pnl_replay"] for f in o_fills)
        o_hr = o_won / len(o_fills) if o_fills else 0
        print(f"  {outcome}: n={len(o_fills)}, won={o_won} ({o_hr:.1%}), PnL=${o_pnl:,.2f}")

    client.close()


if __name__ == "__main__":
    asyncio.run(diagnose())
