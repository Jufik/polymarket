"""S3 NO Sniper — walk-forward validation sweep.

Runs tick-by-tick replay across 3 OOS periods with parameter sweep:
  - max_market_age_s: [180, 300, 600] (3, 5, 10 minutes)
  - max_yes_price: [0.40, 0.50]
  - eligible_tags: all 3 together, per-tag

Usage:
    uv run python research/scripts/s3_no_sniper_validation.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Suppress noisy structlog/debug output
logging.basicConfig(level=logging.WARNING)
import structlog
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.execution.realistic import FillModelConfig
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s2_replay import run_s2_replay
from research.strategies.s3_data import load_market_tags, load_period_trades
from research.strategies.s3_no_sniper import S3Config, S3NoSniper

# --- Periods ---
PERIODS = ["2025-07", "2025-10", "2026-01"]

# --- Sweep grid ---
AGE_OPTIONS = [180, 300, 600]  # seconds
PRICE_OPTIONS = [0.40, 0.50]
TAG_OPTIONS: list[frozenset[str]] = [
    frozenset({"Tech", "Trump", "Economy"}),  # all 3
    frozenset({"Tech"}),
    frozenset({"Trump"}),
    frozenset({"Economy"}),
]

ALL_ELIGIBLE = frozenset({"Tech", "Trump", "Economy"})


@dataclass
class SweepResult:
    config_name: str
    period: str
    fills: int
    hr: float
    pnl: float
    sharpe: float
    n_trades_fed: int
    wall_s: float


def config_name(tags: frozenset[str], age: int, price: float) -> str:
    tag_str = "+".join(sorted(tags)) if len(tags) > 1 else next(iter(tags))
    return f"{tag_str}_age{age}s_mp{price:.2f}"


async def run_one(
    period: str,
    tags: frozenset[str],
    age_s: int,
    max_yes: float,
    tag_map: dict[str, str],
    trades: list,
    resolutions: dict,
    token_map: dict,
) -> SweepResult:
    name = config_name(tags, age_s, max_yes)

    if not trades:
        return SweepResult(name, period, 0, 0.0, 0.0, 0.0, 0, 0.0)

    cfg = S3Config(
        eligible_tags=tags,
        max_market_age_s=age_s,
        max_yes_price=max_yes,
    )
    strat = S3NoSniper(cfg)
    strat.set_tag_map(tag_map)
    strat.set_token_map(token_map)

    config = StrategyConfig(
        name=strat.name,
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=1000,
        max_position_usd=10,
        max_open_positions=100,
        cooldown_s=0,
    )

    t0 = time.monotonic()
    output_dir = Path(f"research/output/s3_validation/{name}/{period}")
    _, summary = await run_s2_replay(
        strat,
        trades,
        config,
        resolutions=resolutions,
        token_map=token_map,
        fill_config=FillModelConfig(),
        output_dir=output_dir,
    )
    wall_s = time.monotonic() - t0

    return SweepResult(
        config_name=name,
        period=period,
        fills=summary.total_fills,
        hr=summary.hit_rate or 0.0,
        pnl=summary.total_pnl_net or 0.0,
        sharpe=summary.sharpe or 0.0,
        n_trades_fed=len(trades),
        wall_s=wall_s,
    )


async def main() -> None:
    backend = ClickHouseBackend(
        host="192.168.0.148",
        port=18123,
        database="polymarket",
    )

    print("Loading tag map...", flush=True)
    tag_map = await load_market_tags(backend, ALL_ELIGIBLE)
    print(f"Tag map: {len(tag_map)} markets", flush=True)

    results: list[SweepResult] = []

    for period in PERIODS:
        print(f"\n{'='*60}")
        print(f"Period: {period}")
        print(f"{'='*60}")

        print(f"  Loading trades for {period}...", flush=True)
        trades, resolutions, token_map = await load_period_trades(
            period, ALL_ELIGIBLE, backend,
        )
        print(f"  {len(trades)} trades, {len(resolutions)} resolutions", flush=True)

        for tags in TAG_OPTIONS:
            for age_s in AGE_OPTIONS:
                for max_yes in PRICE_OPTIONS:
                    r = await run_one(
                        period, tags, age_s, max_yes,
                        tag_map, trades, resolutions, token_map,
                    )
                    results.append(r)
                    print(
                        f"    {r.config_name:40s} fills={r.fills:4d}  "
                        f"HR={r.hr:.1%}  PnL=${r.pnl:+,.0f}  "
                        f"Sharpe={r.sharpe:.2f}  ({r.wall_s:.1f}s)",
                        flush=True,
                    )

    # --- Aggregate across periods ---
    print(f"\n{'='*60}")
    print("AGGREGATED RESULTS (across all periods)")
    print(f"{'='*60}")
    print(f"{'Config':40s} {'Fills':>6s} {'HR':>7s} {'PnL':>10s} {'Sharpe':>7s}")
    print("-" * 72)

    config_names_seen: list[str] = []
    for tags in TAG_OPTIONS:
        for age_s in AGE_OPTIONS:
            for max_yes in PRICE_OPTIONS:
                name = config_name(tags, age_s, max_yes)
                config_names_seen.append(name)
                period_results = [r for r in results if r.config_name == name]
                total_fills = sum(r.fills for r in period_results)
                total_pnl = sum(r.pnl for r in period_results)
                # Weighted HR by fills
                total_wins = sum(r.fills * r.hr for r in period_results)
                avg_hr = total_wins / total_fills if total_fills > 0 else 0.0
                avg_sharpe = (
                    sum(r.sharpe for r in period_results) / len(period_results)
                    if period_results
                    else 0.0
                )
                marker = " ***" if total_pnl > 0 and avg_hr > 0.55 else ""
                print(
                    f"{name:40s} {total_fills:6d} {avg_hr:7.1%} "
                    f"${total_pnl:+10,.0f} {avg_sharpe:7.2f}{marker}"
                )

    # --- Best config ---
    best = max(results, key=lambda r: r.pnl)
    print(f"\nBest single period: {best.config_name} / {best.period}")
    print(f"  Fills={best.fills}, HR={best.hr:.1%}, PnL=${best.pnl:+,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
