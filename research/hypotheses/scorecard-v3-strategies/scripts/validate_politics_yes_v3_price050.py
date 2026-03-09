"""Quick Politics YES v3 at max_price=0.50 — longshot-only.

Alpha concentrates in <0.30 bucket ($26.6K of $25.4K total PnL for N=3).
Test max_price=0.50 to capture profitable entries only.
"""
from __future__ import annotations

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

    print("Loading...", flush=True)
    _, token_map_replay = load_replay_resolutions()
    from research.db import db as get_db
    con = get_db().con
    token_map_db = build_token_map_from_db(con)
    token_map = {**token_map_db, **token_map_replay}

    import importlib.util as _ilu
    _bp_spec = _ilu.spec_from_file_location(
        "build_pools_v3",
        Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"),
    )
    _bp_mod = _ilu.module_from_spec(_bp_spec)
    _bp_spec.loader.exec_module(_bp_mod)

    pool, tag_markets, gambling_markets = _bp_mod.build_politics_yes_pool_v3(k=100)
    universe = tag_markets - gambling_markets

    _bp_mod._ensure_shared_tables(con)
    row = con.execute("""
        SELECT avg(CAST(p.yes_won AS DOUBLE))
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = 'Politics'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND CAST(p.resolved_at AS DATE) < '2026-03-01'
          AND p.volume > 0
    """).fetchone()
    base_rate = round(row[0], 4) if row and row[0] else 0.0

    for max_price in [0.30, 0.50]:
        for n in [3, 5]:
            name = f"politics_yes_v3_k100_n{n}_mp{int(max_price*100)}"
            print(f"\n{'='*60}")
            print(f"N={n}, max_price={max_price}")
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
                max_price=max_price,
            )

            config = StrategyConfig(
                name=name, enabled=True, mode=ExecutionMode.REPLAY,
                capital_usd=5000.0, max_position_usd=100.0,
                max_open_positions=50, cooldown_s=0,
            )

            t0 = time.time()
            result, summary = run_fast_backtest(
                strategy, config, universe=universe, output_dir=OUTPUT_DIR,
            )
            print(f"  Elapsed: {time.time()-t0:.1f}s | fills: {result.total_fills}")

            if summary:
                print_summary(summary, name)

            ledger_path = OUTPUT_DIR / f"ledger_{name}.parquet"
            if ledger_path.exists():
                df = pl.read_parquet(ledger_path)
                settled = df.filter(pl.col("resolution").is_not_null())
                if len(settled) > 0:
                    hr = (settled["resolution"] == "WON").mean()
                    pnl = settled["pnl_net"].sum()
                    fp = settled["fill_price"]
                    print(f"  HR: {hr:.1%} (excess: {hr-base_rate:+.1%})")
                    print(f"  PnL: ${pnl:,.2f}")
                    print(f"  Fill price: med={fp.median():.3f}, mean={fp.mean():.3f}")


if __name__ == "__main__":
    main()
