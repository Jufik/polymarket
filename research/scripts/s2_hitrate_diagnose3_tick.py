"""Deep diagnosis: what happens with UNKNOWN outcomes and cross-direction consensus.

Usage:
    uv run python research/scripts/s2_hitrate_diagnose3_tick.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

import structlog

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from research.strategies.s2_data import invert_token_map, load_period_trades
from research.strategies.s2_hitrate_copy import S2HitRateCopy

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def diagnose() -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    period = "2025-04"
    as_of_date = f"{period}-01"
    print(f"\n{'='*70}")
    print(f"  DEEP DIAGNOSIS: {period}")
    print(f"{'='*70}")

    # Get pool
    query = S2HitRateCopy.qualified_traders_query(
        min_positions=30, min_excess_hr=0.10, recency_months=6,
        direction="BOTH", max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True, as_of_date=as_of_date,
    )
    df = await backend._execute(query)
    traders = set(df["trader"].to_list())

    trader_directions: dict[str, set[str]] = defaultdict(set)
    for row in df.iter_rows(named=True):
        trader_directions[row["trader"]].add(row["direction"])

    trades, resolutions, token_map = await load_period_trades(
        period, traders, backend, resolution_months_after=3
    )
    inv_token_map = invert_token_map(token_map)

    # Check UNKNOWN: trades with asset_id not in inverted token map
    unknown_assets = set()
    for trade in trades:
        if trade.asset_id not in inv_token_map:
            unknown_assets.add(trade.asset_id)
    print(f"  Asset IDs NOT in inverted token_map: {len(unknown_assets)} unique")
    print(f"  Total trades with unknown asset: {sum(1 for t in trades if t.asset_id not in inv_token_map):,}")

    # Check condition_ids in trades vs in token_map
    trade_cids = set(t.condition_id for t in trades)
    tokenmap_cids = set(token_map.keys())
    missing_cids = trade_cids - tokenmap_cids
    print(f"  Condition IDs in trades: {len(trade_cids)}")
    print(f"  Condition IDs in token_map: {len(tokenmap_cids)}")
    print(f"  Missing from token_map: {len(missing_cids)}")

    # Check resolution coverage
    resolved_cids = set(resolutions.keys())
    print(f"  Resolved condition IDs: {len(resolved_cids)}")
    print(f"  Trades in resolved markets: {sum(1 for t in trades if t.condition_id in resolved_cids):,}")

    # Now: What if we force direction-aware consensus?
    # Only count a trader in consensus if they're qualified in the direction
    # they're actually trading in.
    print(f"\n  --- DIRECTION-AWARE CONSENSUS SIMULATION ---")

    consensus_naive: dict[str, set[str]] = {}  # any qualified trader
    consensus_directed: dict[str, set[str]] = {}  # only if qualified in that direction
    filled_naive: set[str] = set()
    filled_directed: set[str] = set()
    scale_threshold = 3

    results_naive: list[dict] = []
    results_directed: list[dict] = []

    for trade in sorted(trades, key=lambda t: t.published_at):
        if str(trade.side) != "BUY":
            continue
        maker = (trade.maker or "").lower()
        if maker not in traders:
            continue
        if float(trade.price) > 0.85:
            continue

        cid = trade.condition_id
        token_info = inv_token_map.get(trade.asset_id, {})
        fill_outcome = token_info.get("outcome", "UNKNOWN")
        if fill_outcome == "UNKNOWN":
            continue  # skip unknown for both

        # Naive consensus (current behavior)
        if cid not in consensus_naive:
            consensus_naive[cid] = set()
        consensus_naive[cid].add(maker)

        if cid not in filled_naive and len(consensus_naive[cid]) >= scale_threshold:
            filled_naive.add(cid)
            won = False
            if cid in resolutions:
                winner, _ = resolutions[cid]
                won = fill_outcome == winner
            results_naive.append({"cid": cid, "outcome": fill_outcome, "won": won, "resolved": cid in resolutions})

        # Directed consensus: only count if trader qualified in this outcome's direction
        if cid not in consensus_directed:
            consensus_directed[cid] = set()
        maker_dirs = trader_directions.get(maker, set())
        if fill_outcome in maker_dirs:
            consensus_directed[cid].add(maker)

        if cid not in filled_directed and len(consensus_directed.get(cid, set())) >= scale_threshold:
            filled_directed.add(cid)
            won = False
            if cid in resolutions:
                winner, _ = resolutions[cid]
                won = fill_outcome == winner
            results_directed.append({"cid": cid, "outcome": fill_outcome, "won": won, "resolved": cid in resolutions})

    # Compare
    def compute_hr(results):
        resolved = [r for r in results if r["resolved"]]
        won = sum(1 for r in resolved if r["won"])
        return won, len(resolved), won / len(resolved) if resolved else 0

    n_w, n_r, hr_n = compute_hr(results_naive)
    d_w, d_r, hr_d = compute_hr(results_directed)

    print(f"  NAIVE (current):    {n_w}/{n_r} won ({hr_n:.1%}), total fills={len(results_naive)}")
    print(f"  DIRECTED (proposed): {d_w}/{d_r} won ({hr_d:.1%}), total fills={len(results_directed)}")
    print(f"  HR improvement:      {(hr_d - hr_n)*100:+.1f}pp")

    # Per-direction breakdown for directed
    for outcome in ["YES", "NO"]:
        o_results = [r for r in results_directed if r["outcome"] == outcome]
        o_resolved = [r for r in o_results if r["resolved"]]
        o_won = sum(1 for r in o_resolved if r["won"])
        o_n = len(o_resolved)
        o_hr = o_won / o_n if o_n > 0 else 0
        print(f"  DIRECTED {outcome}: {o_won}/{o_n} won ({o_hr:.1%})")

    # Also try: direction-aware consensus WITH same-direction-only consensus
    # (all 3+ traders in consensus must agree on the SAME direction)
    print(f"\n  --- SAME-DIRECTION-ONLY CONSENSUS ---")
    consensus_same: dict[str, dict[str, set[str]]] = {}  # cid -> {outcome -> set(traders)}
    filled_same: set[str] = set()
    results_same: list[dict] = []

    for trade in sorted(trades, key=lambda t: t.published_at):
        if str(trade.side) != "BUY":
            continue
        maker = (trade.maker or "").lower()
        if maker not in traders:
            continue
        if float(trade.price) > 0.85:
            continue

        cid = trade.condition_id
        token_info = inv_token_map.get(trade.asset_id, {})
        fill_outcome = token_info.get("outcome", "UNKNOWN")
        if fill_outcome == "UNKNOWN":
            continue

        # Only count if trader is qualified in THIS direction
        maker_dirs = trader_directions.get(maker, set())
        if fill_outcome not in maker_dirs:
            continue

        if cid not in consensus_same:
            consensus_same[cid] = {}
        if fill_outcome not in consensus_same[cid]:
            consensus_same[cid][fill_outcome] = set()
        consensus_same[cid][fill_outcome].add(maker)

        key = f"{cid}_{fill_outcome}"
        if key not in filled_same and len(consensus_same[cid][fill_outcome]) >= scale_threshold:
            filled_same.add(key)
            won = False
            if cid in resolutions:
                winner, _ = resolutions[cid]
                won = fill_outcome == winner
            results_same.append({"cid": cid, "outcome": fill_outcome, "won": won, "resolved": cid in resolutions})

    s_w, s_r, hr_s = compute_hr(results_same)
    print(f"  SAME-DIRECTION:      {s_w}/{s_r} won ({hr_s:.1%}), total fills={len(results_same)}")
    print(f"  HR improvement:      {(hr_s - hr_n)*100:+.1f}pp vs naive")

    for outcome in ["YES", "NO"]:
        o_results = [r for r in results_same if r["outcome"] == outcome]
        o_resolved = [r for r in o_results if r["resolved"]]
        o_won = sum(1 for r in o_resolved if r["won"])
        o_n = len(o_resolved)
        o_hr = o_won / o_n if o_n > 0 else 0
        print(f"  SAME-DIR {outcome}: {o_won}/{o_n} won ({o_hr:.1%})")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(diagnose())
