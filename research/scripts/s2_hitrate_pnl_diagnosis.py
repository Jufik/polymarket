"""PnL diagnosis for direction-aware S2 hit-rate copy variants.

Computes PnL (not just HR) for each variant to determine if any are profitable
after slippage. Key variants:
- naive (current strategy implementation)
- same_dir (direction-aware consensus)
- yes_only_directed (YES-only with direction filter)

Usage:
    uv run python research/scripts/s2_hitrate_pnl_diagnosis.py
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

PERIOD_BASE_RATES = {
    "2025-04": {"YES": 0.29, "NO": 0.71},
    "2025-07": {"YES": 0.26, "NO": 0.74},
    "2025-10": {"YES": 0.37, "NO": 0.63},
}

SIZE_USD = 100.0
SLIPPAGE_PCT = 0.01  # 1% slippage estimate


def compute_pnl(entry_price: float, won: bool, size_usd: float = SIZE_USD) -> float:
    """Compute PnL for a single position."""
    if entry_price <= 0:
        return 0.0
    qty = size_usd / entry_price
    slippage = size_usd * SLIPPAGE_PCT
    if won:
        pnl_gross = (1.0 - entry_price) * qty
    else:
        pnl_gross = -(entry_price * qty)
    return pnl_gross - slippage


async def run_period(backend: ClickHouseBackend, period: str) -> dict:
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

    results_all = {}

    for variant_name in ["naive", "same_dir", "yes_only_directed"]:
        is_yes_only = "yes_only" in variant_name
        is_same_dir = variant_name == "same_dir"
        is_directed = "directed" in variant_name or is_same_dir

        if is_same_dir:
            consensus: dict[str, dict[str, set[str]]] = {}
            filled: set[str] = set()
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
                ti = inv_token_map.get(trade.asset_id, {})
                outcome = ti.get("outcome", "UNKNOWN")
                if outcome == "UNKNOWN":
                    continue
                if is_yes_only and outcome != "YES":
                    continue

                maker_dirs = trader_directions.get(maker, set())
                if is_directed and outcome not in maker_dirs:
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
                    pnl = compute_pnl(float(trade.price), won) if cid in resolutions else 0.0
                    fills.append({
                        "cid": cid, "outcome": outcome, "won": won,
                        "resolved": cid in resolutions,
                        "entry_price": float(trade.price), "pnl": pnl,
                    })
        else:
            consensus_flat: dict[str, set[str]] = {}
            filled_flat: set[str] = set()
            fills = []

            for trade in sorted(trades, key=lambda t: t.published_at):
                if str(trade.side) != "BUY":
                    continue
                maker = (trade.maker or "").lower()
                if maker not in traders:
                    continue
                if float(trade.price) > 0.85:
                    continue

                cid = trade.condition_id
                ti = inv_token_map.get(trade.asset_id, {})
                outcome = ti.get("outcome", "UNKNOWN")
                if outcome == "UNKNOWN":
                    continue
                if is_yes_only and outcome != "YES":
                    continue

                maker_dirs = trader_directions.get(maker, set())
                if is_directed and outcome not in maker_dirs:
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
                    pnl = compute_pnl(float(trade.price), won) if cid in resolutions else 0.0
                    fills.append({
                        "cid": cid, "outcome": outcome, "won": won,
                        "resolved": cid in resolutions,
                        "entry_price": float(trade.price), "pnl": pnl,
                    })

        resolved = [f for f in fills if f["resolved"]]
        won = sum(1 for f in resolved if f["won"])
        n = len(resolved)
        hr = won / n if n > 0 else 0
        total_pnl = sum(f["pnl"] for f in resolved)
        avg_pnl = total_pnl / n if n > 0 else 0
        avg_price = sum(f["entry_price"] for f in resolved) / n if n > 0 else 0

        # Per-direction
        yes_fills = [f for f in resolved if f["outcome"] == "YES"]
        no_fills = [f for f in resolved if f["outcome"] == "NO"]
        yes_won = sum(1 for f in yes_fills if f["won"])
        no_won = sum(1 for f in no_fills if f["won"])
        yes_pnl = sum(f["pnl"] for f in yes_fills)
        no_pnl = sum(f["pnl"] for f in no_fills)
        yes_hr = yes_won / len(yes_fills) if yes_fills else 0
        no_hr = no_won / len(no_fills) if no_fills else 0

        results_all[variant_name] = {
            "fills": len(fills), "resolved": n, "won": won, "hr": hr,
            "total_pnl": total_pnl, "avg_pnl": avg_pnl, "avg_price": avg_price,
            "yes_n": len(yes_fills), "yes_hr": yes_hr, "yes_pnl": yes_pnl,
            "no_n": len(no_fills), "no_hr": no_hr, "no_pnl": no_pnl,
        }

    # Print
    print(f"  Base: YES={base['YES']:.1%}, NO={base['NO']:.1%}")
    print(f"  Slippage: {SLIPPAGE_PCT:.1%}, Size: ${SIZE_USD}")
    print()
    print(f"  {'Variant':<22} {'Fills':>5} {'HR':>7} {'PnL':>10} {'Avg PnL':>9} {'AvgP':>6} "
          f"{'Y_n':>4} {'Y_HR':>6} {'Y_PnL':>9} "
          f"{'N_n':>4} {'N_HR':>6} {'N_PnL':>9}")
    print("  " + "-" * 110)

    for name, v in results_all.items():
        print(f"  {name:<22} {v['fills']:>5} {v['hr']:>6.1%} "
              f"${v['total_pnl']:>9,.0f} ${v['avg_pnl']:>8.2f} {v['avg_price']:>5.3f} "
              f"{v['yes_n']:>4} {v['yes_hr']:>5.1%} ${v['yes_pnl']:>8,.0f} "
              f"{v['no_n']:>4} {v['no_hr']:>5.1%} ${v['no_pnl']:>8,.0f}")

    return {"period": period, "results": results_all}


async def main() -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    all_periods = []
    for period in ["2025-04", "2025-07", "2025-10"]:
        r = await run_period(backend, period)
        all_periods.append(r)

    # Aggregate summary
    print(f"\n\n{'='*80}")
    print("  AGGREGATE ACROSS ALL 3 PERIODS")
    print(f"{'='*80}")

    for variant in ["naive", "same_dir", "yes_only_directed"]:
        total_fills = sum(r["results"][variant]["fills"] for r in all_periods)
        total_resolved = sum(r["results"][variant]["resolved"] for r in all_periods)
        total_won = sum(r["results"][variant]["won"] for r in all_periods)
        total_pnl = sum(r["results"][variant]["total_pnl"] for r in all_periods)
        hr = total_won / total_resolved if total_resolved > 0 else 0
        avg_pnl = total_pnl / total_resolved if total_resolved > 0 else 0

        total_yes_pnl = sum(r["results"][variant]["yes_pnl"] for r in all_periods)
        total_no_pnl = sum(r["results"][variant]["no_pnl"] for r in all_periods)

        print(f"  {variant:<22}: {total_fills} fills, {total_resolved} resolved, "
              f"HR={hr:.1%}, PnL=${total_pnl:,.0f}, avg=${avg_pnl:.2f}/pos, "
              f"YES PnL=${total_yes_pnl:,.0f}, NO PnL=${total_no_pnl:,.0f}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
