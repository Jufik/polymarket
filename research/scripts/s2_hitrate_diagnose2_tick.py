"""Deep diagnosis: direction mismatch between vectorized and tick-by-tick.

The vectorized analysis computes HR per (trader, direction). A trader qualified
on YES only has their YES positions counted. But the tick-by-tick strategy
counts consensus per condition_id without caring about which direction the
trader is qualified in.

This script tracks:
1. What direction (YES/NO token) each fill enters
2. Whether the trader was qualified in that direction
3. How many fills involve mixed-direction consensus

Usage:
    uv run python research/scripts/s2_hitrate_diagnose2_tick.py
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

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from research.strategies.s2_data import invert_token_map, load_period_trades, parse_period_range
from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def diagnose() -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    for period in ["2025-04", "2025-07", "2025-10"]:
        as_of_date = f"{period}-01"
        print(f"\n{'='*70}")
        print(f"  DIRECTION DIAGNOSIS: {period}")
        print(f"{'='*70}")

        # 1. Get qualified traders WITH their direction info
        query = S2HitRateCopy.qualified_traders_query(
            min_positions=30, min_excess_hr=0.10, recency_months=6,
            direction="BOTH", max_entry_price=0.95,
            exclude_tags=("Sports", "Weather"),
            use_bayesian_hr=True, as_of_date=as_of_date,
        )
        df = await backend._execute(query)
        traders = set(df["trader"].to_list())
        print(f"  Pool: {len(traders)} unique traders")

        # Build trader -> qualified directions mapping
        # A trader can be qualified in BOTH YES and NO directions
        trader_directions: dict[str, set[str]] = defaultdict(set)
        for row in df.iter_rows(named=True):
            trader_directions[row["trader"]].add(row["direction"])

        both_dir = sum(1 for t, dirs in trader_directions.items() if len(dirs) > 1)
        yes_only = sum(1 for t, dirs in trader_directions.items() if dirs == {"YES"})
        no_only = sum(1 for t, dirs in trader_directions.items() if dirs == {"NO"})
        print(f"  Trader directions: YES-only={yes_only}, NO-only={no_only}, BOTH={both_dir}")

        # 2. Load trades
        trades, resolutions, token_map = await load_period_trades(
            period, traders, backend, resolution_months_after=3
        )
        inv_token_map = invert_token_map(token_map)
        print(f"  Trades: {len(trades):,}")

        if not trades:
            continue

        # 3. Simulate strategy logic manually to track direction matching
        consensus: dict[str, set[str]] = {}  # cid -> set(traders)
        filled_markets: set[str] = set()
        scale_threshold = 3

        fills_direction_match: list[dict] = []
        fills_direction_mismatch: list[dict] = []

        for trade in sorted(trades, key=lambda t: t.published_at):
            if str(trade.side) != "BUY":
                continue
            maker = (trade.maker or "").lower()
            if maker not in traders:
                continue
            if float(trade.price) > 0.85:
                continue
            cid = trade.condition_id
            if cid in filled_markets:
                continue

            if cid not in consensus:
                consensus[cid] = set()
            consensus[cid].add(maker)

            if len(consensus[cid]) < scale_threshold:
                continue

            # Consensus reached -- would fill here
            filled_markets.add(cid)

            # What outcome is this trade?
            token_info = inv_token_map.get(trade.asset_id, {})
            fill_outcome = token_info.get("outcome", "UNKNOWN")

            # What direction was the triggering trader qualified in?
            trigger_dirs = trader_directions.get(maker, set())

            # What directions were ALL consensus traders qualified in?
            all_dirs = set()
            for t in consensus[cid]:
                all_dirs.update(trader_directions.get(t, set()))

            # Check resolution
            won = False
            if cid in resolutions:
                winner_outcome, _ = resolutions[cid]
                won = fill_outcome == winner_outcome

            info = {
                "cid": cid,
                "fill_outcome": fill_outcome,
                "trigger_qualified_dirs": trigger_dirs,
                "consensus_size": len(consensus[cid]),
                "won": won,
                "has_resolution": cid in resolutions,
                "price": float(trade.price),
            }

            # Does the fill direction match the trigger trader's qualification?
            if fill_outcome in trigger_dirs:
                fills_direction_match.append(info)
            else:
                fills_direction_mismatch.append(info)

        total_fills = len(fills_direction_match) + len(fills_direction_mismatch)
        print(f"\n  Total consensus triggers: {total_fills}")
        print(f"  Direction MATCH (trigger qualified in fill direction): {len(fills_direction_match)}")
        print(f"  Direction MISMATCH (trigger NOT qualified in fill direction): {len(fills_direction_mismatch)}")

        # HR breakdown
        for label, fills in [("MATCH", fills_direction_match), ("MISMATCH", fills_direction_mismatch)]:
            resolved = [f for f in fills if f["has_resolution"]]
            won = sum(1 for f in resolved if f["won"])
            n = len(resolved)
            hr = won / n if n > 0 else 0
            print(f"  {label}: {won}/{n} won ({hr:.1%})")

        # Token outcome distribution of fills
        outcome_counts = Counter(f["fill_outcome"] for f in fills_direction_match + fills_direction_mismatch)
        print(f"  Fill token outcomes: {dict(outcome_counts)}")

        # Per-outcome HR
        for outcome in ["YES", "NO"]:
            all_fills = [f for f in fills_direction_match + fills_direction_mismatch if f["fill_outcome"] == outcome]
            resolved = [f for f in all_fills if f["has_resolution"]]
            won = sum(1 for f in resolved if f["won"])
            n = len(resolved)
            hr = won / n if n > 0 else 0
            print(f"  {outcome} fills: {won}/{n} won ({hr:.1%})")

        # Price distribution of match vs mismatch
        match_prices = [f["price"] for f in fills_direction_match]
        mismatch_prices = [f["price"] for f in fills_direction_mismatch]
        if match_prices:
            import statistics
            print(f"  MATCH prices: mean={statistics.mean(match_prices):.3f}")
        if mismatch_prices:
            print(f"  MISMATCH prices: mean={statistics.mean(mismatch_prices):.3f}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(diagnose())
