"""Quick Politics YES v3 re-run WITHOUT max_price cap.

At max_price=0.80, all fills are at $0.80 and breakeven is 80% HR.
The signal has +43-47pp excess over 18.8% base, but 62-66% HR < 80% BE.
This re-run uses actual trigger trade prices (like Sports YES does).

Tests N=3 and N=5 only (bracketing).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.harness import run_fast_backtest, print_summary
from research.fast_replay import load_replay_resolutions
from research.strategies.consensus_v2 import TokenMapStrategy

OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")


def make_config(name: str) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=5000.0,
        max_position_usd=100.0,
        max_open_positions=50,
        cooldown_s=0,
    )


def build_token_map_from_db(con) -> dict[str, dict[str, str]]:
    rows = con.execute(
        "SELECT condition_id, asset_id, outcome FROM token_market_map"
    ).fetchall()
    token_map: dict[str, dict[str, str]] = {}
    for cid, aid, outcome in rows:
        token_map.setdefault(cid, {})[outcome] = aid
    return token_map


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading token_map...", flush=True)
    _, token_map_replay = load_replay_resolutions()
    from research.db import db as get_db
    con = get_db().con
    token_map_db = build_token_map_from_db(con)
    token_map = {**token_map_db, **token_map_replay}
    print(f"  token_map: {len(token_map)} markets")

    # Build pool
    import importlib.util as _ilu
    _bp_spec = _ilu.spec_from_file_location(
        "build_pools_v3",
        Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"),
    )
    _bp_mod = _ilu.module_from_spec(_bp_spec)
    _bp_spec.loader.exec_module(_bp_mod)

    pool, tag_markets, gambling_markets = _bp_mod.build_politics_yes_pool_v3(k=100)
    universe = tag_markets - gambling_markets
    print(f"  Pool: {len(pool)} traders, Universe: {len(universe)} markets")

    # Base rate
    _bp_mod._ensure_shared_tables(con)
    row = con.execute("""
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = 'Politics'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND CAST(p.resolved_at AS DATE) < '2026-03-01'
          AND p.volume > 0
    """).fetchone()
    base_rate = round(row[0], 4) if row and row[0] else 0.0
    print(f"  Base rate: {base_rate:.1%}")

    for n in [3, 5]:
        name = f"politics_yes_v3_k100_n{n}_noprice"
        print(f"\n{'='*60}")
        print(f"N={n} — NO max_price cap (trigger_price + 0.02)")
        print(f"{'='*60}")

        strategy = TokenMapStrategy(
            name=name,
            pool=pool,
            tag_markets=tag_markets,
            gambling_markets=gambling_markets,
            n_threshold=n,
            token_map=token_map,
            direction_filter="YES",
            size_usd=100.0,
            max_price=None,  # KEY CHANGE: use actual trigger price
        )

        config = make_config(name)
        t0 = time.time()
        result, summary = run_fast_backtest(
            strategy, config,
            universe=universe,
            output_dir=OUTPUT_DIR,
        )
        elapsed = time.time() - t0
        print(f"  Elapsed: {elapsed:.1f}s | fills: {result.total_fills}")

        if summary:
            print_summary(summary, f"Politics_YES_v3_K100_N{n}_noprice")

        # Quick ledger analysis
        ledger_path = OUTPUT_DIR / f"ledger_{name}.parquet"
        if ledger_path.exists():
            df = pl.read_parquet(ledger_path)
            settled = df.filter(pl.col("resolution").is_not_null())
            if len(settled) > 0:
                hr = (settled["resolution"] == "WON").mean()
                pnl = settled["pnl_net"].sum()
                fill_prices = settled["fill_price"]
                print(f"  Settled: {len(settled)}")
                print(f"  HR: {hr:.1%} (excess: {hr - base_rate:+.1%})")
                print(f"  PnL: ${pnl:,.2f}")
                print(f"  Fill price: median={fill_prices.median():.3f}, "
                      f"mean={fill_prices.mean():.3f}, "
                      f"min={fill_prices.min():.3f}, "
                      f"max={fill_prices.max():.3f}")

                # Price distribution
                for lo, hi in [(0.0, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.90), (0.90, 1.01)]:
                    bucket = settled.filter(
                        (pl.col("fill_price") >= lo) & (pl.col("fill_price") < hi)
                    )
                    if len(bucket) > 0:
                        bhr = (bucket["resolution"] == "WON").mean()
                        bpnl = bucket["pnl_net"].sum()
                        print(f"    [{lo:.2f}-{hi:.2f}): {len(bucket)} fills, "
                              f"HR={bhr:.1%}, PnL=${bpnl:,.2f}")


if __name__ == "__main__":
    main()
