"""S2 Hit-Rate Copy -- deep diagnosis of settlement and direction.

Manually runs the ReplayRunner with instrumentation to understand:
1. How many markets are settled during replay?
2. Direction distribution of WON vs LOST
3. Per-record enrichment check

Usage:
    uv run python research/scripts/s2_hitrate_diagnose2.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import Counter
from pathlib import Path

import structlog

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.calibrate import (
    calibrate_spreads,
    calibrate_volumes,
)
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.realistic import (
    FillModelConfig,
    RealisticFillSimulator,
)
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.ledger.analytics import compute_summary
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.replay import MarketResolution, ReplayRunner
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s2_data import invert_token_map, load_period_trades
from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def main() -> None:
    periods = ["2025-04", "2025-07", "2025-10"]
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    for period in periods:
        await run_period(backend, period)

    await backend.close()


async def run_period(backend: ClickHouseBackend, period: str) -> None:
    print(f"\n\n{'#'*70}")
    print(f"# PERIOD: {period}")
    print(f"{'#'*70}")

    # Config
    s2_cfg = S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction="BOTH",
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=3,
        seed_threshold=3,
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=0.85,
        max_hold_hours=168.0,
    )

    # Get qualified traders
    query = S2HitRateCopy.qualified_traders_query(
        min_positions=s2_cfg.min_positions,
        min_excess_hr=s2_cfg.min_excess_hr,
        recency_months=s2_cfg.recency_months,
        direction=s2_cfg.direction,
        max_entry_price=s2_cfg.max_entry_price,
        exclude_tags=s2_cfg.exclude_tags,
        use_bayesian_hr=s2_cfg.use_bayesian_hr,
    )
    df = await backend._execute(query)
    traders = set(df["trader"].to_list())
    print(f"Qualified traders: {len(traders)}")

    # Show trader stats
    dir_counts = Counter(df["direction"].to_list())
    print(f"Direction of qualified: {dict(dir_counts)}")
    avg_excess = df["excess_hr"].mean()
    avg_hr = df["hit_rate"].mean()
    print(f"Avg hit_rate: {avg_hr:.3f}, avg excess_hr: {avg_excess:.3f}")

    # Load data
    trades, resolutions, token_map = await load_period_trades(
        period, traders, backend, resolution_months_after=3,
    )
    print(f"\nTrades: {len(trades)}, Resolutions: {len(resolutions)}, "
          f"Token maps: {len(token_map)}")

    # Build strategy
    strategy = S2HitRateCopy(s2_cfg)
    strategy.set_qualified_traders(traders)
    inv_tm = invert_token_map(token_map)
    strategy.set_token_map(inv_tm)

    # Build MarketResolution objects manually
    market_resolutions: dict[str, MarketResolution] = {}
    for cid, (winner_outcome, resolved_at) in resolutions.items():
        cid_tokens = token_map.get(cid, {})
        winning_asset = cid_tokens.get(winner_outcome, "")
        winning_ids = frozenset({winning_asset}) if winning_asset else frozenset()
        market_resolutions[cid] = MarketResolution(
            condition_id=cid,
            resolved_at=resolved_at,
            winning_asset_ids=winning_ids,
        )

    print(f"Market resolutions built: {len(market_resolutions)}")
    # Check for empty winning_asset_ids
    n_empty = sum(1 for mr in market_resolutions.values() if not mr.winning_asset_ids)
    print(f"Resolutions with empty winning_asset_ids: {n_empty}")

    # Executor
    fc = FillModelConfig()
    market_spreads = calibrate_spreads(trades)
    market_volumes = calibrate_volumes(trades)
    executor = RealisticFillSimulator(
        config=fc,
        market_spreads=market_spreads,
        market_volumes=market_volumes,
    )

    # Gateway + Context
    strat_config = StrategyConfig(
        name="test",
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=50_000,
        max_position_usd=200,
        max_open_positions=500,
        cooldown_s=0,
    )
    gateway = ExecutionGateway(executor, strategy_budgets={strategy.name: 1_000_000})
    ctx = InMemoryContext()

    # Ledger
    output_dir = Path("research/output/s2_diag2")
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ledger.parquet"
    ledger = ParquetLedger(ledger_path)

    # Runner
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=strat_config,
        resolutions=market_resolutions,
        token_map=token_map,
        ledger=ledger,
    )

    print(f"\nResolution timeline: {len(runner._res_timeline)} entries")
    if runner._res_timeline:
        first_res = runner._res_timeline[0]
        last_res = runner._res_timeline[-1]
        print(f"  First: epoch={first_res[0]:.0f}, Last: epoch={last_res[0]:.0f}")

    # Run replay
    t0 = time.time()
    result = await runner.run(trades)
    elapsed = time.time() - t0
    print(f"\nReplay done in {elapsed:.1f}s")
    print(f"  Runner settled: {runner.n_settled}")

    # Read enriched records from ledger buffer
    records = await ledger.read_all()
    print(f"  Ledger buffer records: {len(records)}")

    # Resolution check on enriched records
    resolved = [r for r in records if r.resolution is not None]
    pending = [r for r in records if r.resolution is None]
    print(f"  Resolved: {len(resolved)}, Pending: {len(pending)}")

    # Compute summary
    summary = compute_summary(records)
    print(f"\n  Summary: wins={summary.win_count}, losses={summary.loss_count}, "
          f"pending={summary.pending_count}")
    print(f"  HR: {summary.hit_rate:.1%}")
    print(f"  PnL: ${summary.total_pnl_net:.2f}")
    print(f"  Avg Edge: ${summary.avg_edge:.2f}")

    # Direction analysis of resolved records
    for outcome in ["YES", "NO"]:
        subset = [r for r in resolved if r.outcome == outcome]
        won = sum(1 for r in subset if r.resolution == "WON")
        lost = sum(1 for r in subset if r.resolution == "LOST")
        total = won + lost
        hr = won / total if total > 0 else 0
        pnl = sum(r.pnl_net for r in subset if r.pnl_net is not None)
        print(f"  {outcome}: {won}/{total} won = {hr:.1%}, PnL=${pnl:.2f}")

    # Rejection stats
    rej_reasons = Counter(r for _, r in result.rejected_intents)
    print(f"\n  Rejections: {len(result.rejected_intents)}")
    for reason, count in sorted(rej_reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")

    # Check: how many unique markets had fills?
    fill_cids = set(r.condition_id for r in records)
    print(f"\n  Unique markets filled: {len(fill_cids)}")

    # Check positions at end
    positions = ctx.get_all_positions()
    n_open = sum(1 for p in positions.values() if p.qty_yes > 0 or p.qty_no > 0)
    n_settled_pos = sum(1 for p in positions.values() if p.qty_yes == 0 and p.qty_no == 0 and p.realized_pnl != 0)
    print(f"  Positions: {len(positions)} total, {n_open} open, {n_settled_pos} settled")

    # Hold time
    holds = [r.hold_duration_s for r in resolved if r.hold_duration_s and r.hold_duration_s > 0]
    if holds:
        avg_h = sum(holds) / len(holds) / 3600
        med_h = sorted(holds)[len(holds) // 2] / 3600
        print(f"  Hold: avg={avg_h:.1f}h, median={med_h:.1f}h")



if __name__ == "__main__":
    asyncio.run(main())
