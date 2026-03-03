"""S3 tick-by-tick analysis — detailed per-bet breakdown for Economy config.

Runs a single replay on the best config (Economy, mp0.50) across all 3 periods,
captures every fill + resolution, and prints detailed per-bet analytics.

Usage:
    PYTHONPATH=. uv run python research/scripts/s3_tick_analysis.py
"""
from __future__ import annotations

import asyncio
import logging
import time

import structlog

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

from pathlib import Path

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
from polymarket_pipeline.strategies.ledger.types import FillStatus, LedgerRecord
from polymarket_pipeline.strategies.runners.replay import MarketResolution, ReplayRunner
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s3_data import load_market_tags, load_period_trades
from research.strategies.s3_no_sniper import S3Config, S3NoSniper

PERIODS = ["2025-07", "2025-10", "2026-01"]
ELIGIBLE = frozenset({"Economy"})


async def run_replay_with_records(
    strat, trades, config, resolutions, token_map, output_dir,
) -> tuple[list[LedgerRecord], any]:
    """Run replay and return the raw ledger records."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = ParquetLedger(output_dir / f"ledger_{strat.name}.parquet")

    # Build resolutions
    market_resolutions: dict[str, MarketResolution] = {}
    token_map = token_map or {}
    if resolutions:
        for cid, (winner_outcome, resolved_at) in resolutions.items():
            cid_tokens = token_map.get(cid, {})
            winning_asset = cid_tokens.get(winner_outcome, "")
            winning_ids = frozenset({winning_asset}) if winning_asset else frozenset()
            market_resolutions[cid] = MarketResolution(
                condition_id=cid, resolved_at=resolved_at,
                winning_asset_ids=winning_ids,
            )

    fc = FillModelConfig()
    if trades:
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
    else:
        market_spreads, market_volumes = {}, {}

    executor = RealisticFillSimulator(
        config=fc, market_spreads=market_spreads, market_volumes=market_volumes,
    )
    gateway = ExecutionGateway(executor, strategy_budgets={strat.name: 1_000_000})
    ctx = InMemoryContext()

    runner = ReplayRunner(
        strategy=strat, ctx=ctx, gateway=gateway, config=config,
        resolutions=market_resolutions, token_map=token_map, ledger=ledger,
    )
    await runner.run(trades)

    records = await ledger.read_all()
    summary = compute_summary(records)
    return records, summary


async def main() -> None:
    backend = ClickHouseBackend(
        host="192.168.0.148", port=18123, database="polymarket",
    )

    tag_map = await load_market_tags(backend, ELIGIBLE)
    print(f"Tag map: {len(tag_map)} Economy markets\n")

    all_records: list[LedgerRecord] = []

    for period in PERIODS:
        print(f"{'='*70}")
        print(f"Period: {period}")
        print(f"{'='*70}")

        trades, resolutions, token_map = await load_period_trades(
            period, ELIGIBLE, backend,
        )
        print(f"  Loaded {len(trades)} trades, {len(resolutions)} resolutions")

        cfg = S3Config(eligible_tags=ELIGIBLE, max_yes_price=0.50)
        strat = S3NoSniper(cfg)
        strat.set_tag_map(tag_map)
        strat.set_token_map(token_map)

        config = StrategyConfig(
            name=strat.name, enabled=True, mode=ExecutionMode.REPLAY,
            capital_usd=1000, max_position_usd=10,
            max_open_positions=100, cooldown_s=0,
        )

        output_dir = Path(f"research/output/s3_tick_analysis/{period}")
        t0 = time.monotonic()
        records, summary = await run_replay_with_records(
            strat, trades, config, resolutions, token_map, output_dir,
        )
        elapsed = time.monotonic() - t0
        all_records.extend(records)

        filled = [r for r in records if r.fill_status == FillStatus.FILLED]
        resolved = [r for r in filled if r.resolution is not None]
        wins = [r for r in resolved if (r.pnl_gross or 0) > 0]
        losses = [r for r in resolved if (r.pnl_gross or 0) <= 0]

        print(f"  Fills: {len(filled)}, Resolved: {len(resolved)}, "
              f"Wins: {len(wins)}, Losses: {len(losses)}")
        print(f"  HR: {summary.hit_rate:.1%}, PnL: ${summary.total_pnl_net:+,.2f}, "
              f"Sharpe: {summary.sharpe:.2f} ({elapsed:.1f}s)")

        if resolved:
            fill_prices = [r.fill_price for r in resolved if r.fill_price]
            fees = [r.fill_fee_usd or 0 for r in resolved]
            win_pnls = [r.pnl_net or 0 for r in wins]
            loss_pnls = [r.pnl_net or 0 for r in losses]

            print(f"\n  NO entry price: min={min(fill_prices):.3f} "
                  f"med={sorted(fill_prices)[len(fill_prices)//2]:.3f} "
                  f"max={max(fill_prices):.3f}")
            print(f"  Avg fee: ${sum(fees)/len(fees):.3f}")
            if win_pnls:
                print(f"  Avg win:  ${sum(win_pnls)/len(win_pnls):+.2f}")
            if loss_pnls:
                print(f"  Avg loss: ${sum(loss_pnls)/len(loss_pnls):+.2f}")

    # ------------------------------------------------------------------
    # Cross-period aggregate
    # ------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("AGGREGATE (all 3 periods)")
    print(f"{'='*70}")

    agg = compute_summary(all_records)
    filled_all = [r for r in all_records if r.fill_status == FillStatus.FILLED]
    resolved_all = [r for r in filled_all if r.resolution is not None]
    unresolved = [r for r in filled_all if r.resolution is None]
    wins_all = [r for r in resolved_all if (r.pnl_gross or 0) > 0]
    losses_all = [r for r in resolved_all if (r.pnl_gross or 0) <= 0]

    print(f"  Total fills: {len(filled_all)}")
    print(f"  Resolved: {len(resolved_all)}, Unresolved: {len(unresolved)}")
    print(f"  Wins: {len(wins_all)}, Losses: {len(losses_all)}")
    print(f"  Hit rate: {agg.hit_rate:.1%}")
    print(f"  PnL net: ${agg.total_pnl_net:+,.2f}")
    print(f"  Total fees: ${agg.total_fees:,.2f}")
    print(f"  Sharpe: {agg.sharpe:.2f}")
    print(f"  Max drawdown: ${agg.max_drawdown:,.2f}")
    print(f"  Profit factor: {agg.profit_factor:.2f}")

    if resolved_all:
        fill_prices = sorted([r.fill_price for r in resolved_all if r.fill_price])
        pnls = [r.pnl_net or 0 for r in resolved_all]
        win_pnls = [r.pnl_net or 0 for r in wins_all]
        loss_pnls = [r.pnl_net or 0 for r in losses_all]

        print(f"\n  NO entry price percentiles:")
        for pct in [10, 25, 50, 75, 90]:
            idx = min(int(len(fill_prices) * pct / 100), len(fill_prices) - 1)
            print(f"    p{pct:2d}: {fill_prices[idx]:.3f}")

        print(f"\n  PnL per bet:")
        print(f"    Avg win:  ${sum(win_pnls)/len(win_pnls):+.2f}" if win_pnls else "")
        print(f"    Avg loss: ${sum(loss_pnls)/len(loss_pnls):+.2f}" if loss_pnls else "")
        print(f"    Avg all:  ${sum(pnls)/len(pnls):+.2f}")

        # Hold duration
        durations = []
        for r in resolved_all:
            if r.signal_time and r.resolved_at:
                dur_h = (r.resolved_at - r.signal_time) / 3600
                durations.append(dur_h)
        if durations:
            durations.sort()
            print(f"\n  Hold duration (hours):")
            for pct in [10, 25, 50, 75, 90]:
                idx = min(int(len(durations) * pct / 100), len(durations) - 1)
                print(f"    p{pct:2d}: {durations[idx]:.1f}h "
                      f"({durations[idx]/24:.1f}d)")

        # Equity curve
        cum = []
        running = 0.0
        for r in sorted(resolved_all, key=lambda x: x.resolved_at or 0):
            running += r.pnl_net or 0
            cum.append(running)

        print(f"\n  Equity curve ({len(cum)} resolutions):")
        step = max(1, len(cum) // 20)
        for i in range(0, len(cum), step):
            pct_done = (i + 1) / len(cum) * 100
            bar_len = max(0, int((cum[i] + 100) / 5))
            bar = "█" * bar_len
            print(f"    [{pct_done:5.1f}%] ${cum[i]:+8.2f}  {bar}")
        print(f"    [100.0%] ${cum[-1]:+8.2f}")


if __name__ == "__main__":
    asyncio.run(main())
