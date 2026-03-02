"""Direction-aware consensus for all 3 periods + YES-only test.

Usage:
    uv run python research/scripts/s2_hitrate_diagnose4_tick.py
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

# Period base rates (tag-based, excluding gambling)
PERIOD_BASE_RATES = {
    "2025-04": {"YES": 0.29, "NO": 0.71},
    "2025-07": {"YES": 0.26, "NO": 0.74},
    "2025-10": {"YES": 0.37, "NO": 0.63},
}


async def run_period(
    backend: ClickHouseBackend,
    period: str,
) -> dict:
    as_of_date = f"{period}-01"
    print(f"\n{'='*70}")
    print(f"  PERIOD: {period}")
    print(f"{'='*70}")

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

    base = PERIOD_BASE_RATES.get(period, {"YES": 0.381, "NO": 0.619})

    # Run all variants
    variants = {}
    for variant_name, variant_fn in [
        ("naive", lambda maker, outcome, dirs: True),
        ("directed", lambda maker, outcome, dirs: outcome in dirs),
        ("same_dir", lambda maker, outcome, dirs: outcome in dirs),
        ("yes_only_naive", lambda maker, outcome, dirs: True),
        ("yes_only_directed", lambda maker, outcome, dirs: outcome in dirs),
    ]:
        is_yes_only = "yes_only" in variant_name
        is_same_dir = variant_name == "same_dir"

        if is_same_dir:
            # Same-direction: consensus per (cid, direction)
            consensus: dict[str, dict[str, set[str]]] = {}
            filled: set[str] = set()
            results_list: list[dict] = []

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
                outcome = token_info.get("outcome", "UNKNOWN")
                if outcome == "UNKNOWN":
                    continue
                if is_yes_only and outcome != "YES":
                    continue

                maker_dirs = trader_directions.get(maker, set())
                if not variant_fn(maker, outcome, maker_dirs):
                    continue

                if cid not in consensus:
                    consensus[cid] = {}
                if outcome not in consensus[cid]:
                    consensus[cid][outcome] = set()
                consensus[cid][outcome].add(maker)

                key = f"{cid}_{outcome}"
                if key not in filled and len(consensus[cid][outcome]) >= 3:
                    filled.add(key)
                    won = False
                    if cid in resolutions:
                        winner, _ = resolutions[cid]
                        won = outcome == winner
                    results_list.append({"cid": cid, "outcome": outcome, "won": won, "resolved": cid in resolutions})
        else:
            # Standard consensus per cid
            consensus_flat: dict[str, set[str]] = {}
            filled_flat: set[str] = set()
            results_list = []

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
                outcome = token_info.get("outcome", "UNKNOWN")
                if outcome == "UNKNOWN":
                    continue
                if is_yes_only and outcome != "YES":
                    continue

                maker_dirs = trader_directions.get(maker, set())
                if not variant_fn(maker, outcome, maker_dirs):
                    continue

                if cid not in consensus_flat:
                    consensus_flat[cid] = set()
                consensus_flat[cid].add(maker)

                if cid not in filled_flat and len(consensus_flat[cid]) >= 3:
                    filled_flat.add(cid)
                    won = False
                    if cid in resolutions:
                        winner, _ = resolutions[cid]
                        won = outcome == winner
                    results_list.append({"cid": cid, "outcome": outcome, "won": won, "resolved": cid in resolutions})

        # Compute stats
        resolved = [r for r in results_list if r["resolved"]]
        won = sum(1 for r in resolved if r["won"])
        n = len(resolved)
        hr = won / n if n > 0 else 0

        # Per-direction
        yes_res = [r for r in resolved if r["outcome"] == "YES"]
        no_res = [r for r in resolved if r["outcome"] == "NO"]
        yes_won = sum(1 for r in yes_res if r["won"])
        no_won = sum(1 for r in no_res if r["won"])
        yes_hr = yes_won / len(yes_res) if yes_res else 0
        no_hr = no_won / len(no_res) if no_res else 0
        yes_excess = yes_hr - base["YES"]
        no_excess = no_hr - base["NO"]

        variants[variant_name] = {
            "total_fills": len(results_list),
            "resolved": n,
            "won": won,
            "hr": hr,
            "yes_n": len(yes_res), "yes_hr": yes_hr, "yes_excess": yes_excess,
            "no_n": len(no_res), "no_hr": no_hr, "no_excess": no_excess,
        }

    # Print summary
    print(f"  Base rates: YES={base['YES']:.1%}, NO={base['NO']:.1%}")
    print()
    print(f"  {'Variant':<22} {'Fills':>6} {'Res':>5} {'Won':>4} {'HR':>7} "
          f"{'YES n':>5} {'YES HR':>7} {'YES ex':>7} "
          f"{'NO n':>5} {'NO HR':>7} {'NO ex':>7}")
    print("  " + "-" * 95)

    for name, v in variants.items():
        print(f"  {name:<22} {v['total_fills']:>6} {v['resolved']:>5} {v['won']:>4} {v['hr']:>6.1%} "
              f"{v['yes_n']:>5} {v['yes_hr']:>6.1%} {v['yes_excess']:>+6.1%} "
              f"{v['no_n']:>5} {v['no_hr']:>6.1%} {v['no_excess']:>+6.1%}")

    return {"period": period, "variants": variants}


async def main() -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    all_results = []
    for period in ["2025-04", "2025-07", "2025-10"]:
        result = await run_period(backend, period)
        all_results.append(result)

    # Cross-period summary
    print(f"\n\n{'='*80}")
    print("  CROSS-PERIOD SUMMARY: SAME-DIRECTION CONSENSUS (best variant)")
    print(f"{'='*80}")
    for r in all_results:
        v = r["variants"]["same_dir"]
        base = PERIOD_BASE_RATES.get(r["period"], {"YES": 0.381, "NO": 0.619})
        print(f"  {r['period']}: HR={v['hr']:.1%}, fills={v['total_fills']}, "
              f"YES={v['yes_hr']:.1%} (excess {v['yes_excess']:+.1%}), "
              f"NO={v['no_hr']:.1%} (excess {v['no_excess']:+.1%})")

    print(f"\n{'='*80}")
    print("  CROSS-PERIOD SUMMARY: YES-ONLY (directed consensus)")
    print(f"{'='*80}")
    for r in all_results:
        v = r["variants"]["yes_only_directed"]
        print(f"  {r['period']}: HR={v['hr']:.1%}, fills={v['total_fills']}, "
              f"YES={v['yes_hr']:.1%} (excess {v['yes_excess']:+.1%})")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
