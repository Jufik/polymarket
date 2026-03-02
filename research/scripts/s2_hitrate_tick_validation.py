"""S2 Hit-Rate Copy -- tick-by-tick validation via ReplayRunner.

Runs 3 OOS periods (Apr, Jul, Oct 2025) with RealisticFillSimulator.
Also tests parameter variants: scale_threshold=4, YES-only, max_hold_hours=72.

Pre-flight checklist (all CRITICAL pitfalls addressed):
  [x] BUY-only filter: S2HitRateCopy.on_trade checks trade.side != "BUY"
  [x] Unique-trader consensus: uses set.add(maker) in _consensus dict
  [x] Asset-ID resolution: ReplayRunner uses MarketResolution(winning_asset_ids)
  [x] Settlement mid-replay: ReplayRunner settles tick-by-tick via res_timeline
  [x] Gambling markets excluded: qualified_traders_query uses tag chain
  [x] RealisticFillSimulator: calibrated spreads + volumes from training trades

Usage:
    uv run python research/scripts/s2_hitrate_tick_validation.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Ensure repo root is on sys.path for research imports
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging
import structlog

# Suppress noisy debug logging from the replay engine
logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s2_data import invert_token_map, load_period_trades
from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
from research.strategies.s2_replay import run_s2_replay

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

PERIODS = ["2025-04", "2025-07", "2025-10"]
OUTPUT_DIR = Path("research/output/s2_tick_hitrate")

# Base strategy config for ReplayRunner
STRATEGY_CONFIG = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10_000,
    max_position_usd=100,
    max_open_positions=50,
    cooldown_s=0,
)

# Vectorized upper bounds (from ideas.md, consensus >= 4 / scale_threshold=3)
VECTORIZED_UPPER = {
    "2025-04": {"hr": 0.829, "pnl_per_pos": 241.0},
    "2025-07": {"hr": 0.842, "pnl_per_pos": 145.0},
    "2025-10": {"hr": 0.855, "pnl_per_pos": 155.0},
}

# Period base rates (tag-based, from knowledge)
PERIOD_BASE_RATES = {
    "2025-04": {"yes": 0.29, "no": 0.71},
    "2025-07": {"yes": 0.26, "no": 0.74},
    "2025-10": {"yes": 0.37, "no": 0.63},
}


@dataclass
class RunResult:
    period: str
    config_label: str
    n_traders: int
    n_trades_loaded: int
    n_resolutions: int
    total_fills: int
    win_count: int
    loss_count: int
    pending_count: int
    hit_rate: float
    total_pnl_net: float
    total_fees: float
    avg_edge: float
    sharpe: float
    max_drawdown: float
    profit_factor: float
    avg_hold_hours: float
    n_rejected: int
    rejection_reasons: dict[str, int]
    elapsed_s: float


def make_s2_config(
    *,
    scale_threshold: int = 3,
    direction: str = "BOTH",
    max_hold_hours: float | None = 168.0,
    max_signal_price: float = 0.85,
) -> S2Config:
    """Build S2Config with recommended defaults and optional overrides."""
    return S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction=direction,  # type: ignore[arg-type]
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=scale_threshold,
        seed_threshold=1,
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=max_signal_price,
        max_hold_hours=max_hold_hours,
    )


async def get_qualified_traders(
    backend: ClickHouseBackend,
    s2_cfg: S2Config,
) -> set[str]:
    """Query qualified traders using S2HitRateCopy.qualified_traders_query."""
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
    if len(df) == 0:
        return set()
    return set(df["trader"].to_list())


async def run_single_period(
    backend: ClickHouseBackend,
    period: str,
    s2_cfg: S2Config,
    config_label: str,
    qualified_traders: set[str] | None = None,
) -> RunResult:
    """Run replay for a single period with given config."""
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  Period: {period}  Config: {config_label}")
    print(f"{'='*60}")

    # 1. Get qualified traders
    if qualified_traders is None:
        qualified_traders = await get_qualified_traders(backend, s2_cfg)
    n_traders = len(qualified_traders)
    print(f"  Qualified traders: {n_traders}")

    if n_traders == 0:
        elapsed = time.time() - t0
        return RunResult(
            period=period, config_label=config_label,
            n_traders=0, n_trades_loaded=0, n_resolutions=0,
            total_fills=0, win_count=0, loss_count=0, pending_count=0,
            hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
            sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
            n_rejected=0, rejection_reasons={}, elapsed_s=elapsed,
        )

    # 2. Load trades + resolutions
    print(f"  Loading trades...")
    trades, resolutions, token_map = await load_period_trades(
        period, qualified_traders, backend
    )
    print(f"  Loaded {len(trades)} trades, {len(resolutions)} resolutions, "
          f"{len(token_map)} token maps")

    if not trades:
        elapsed = time.time() - t0
        return RunResult(
            period=period, config_label=config_label,
            n_traders=n_traders, n_trades_loaded=0, n_resolutions=0,
            total_fills=0, win_count=0, loss_count=0, pending_count=0,
            hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
            sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
            n_rejected=0, rejection_reasons={}, elapsed_s=time.time() - t0,
        )

    # 3. Create strategy
    strategy = S2HitRateCopy(s2_cfg)
    strategy.set_qualified_traders(qualified_traders)
    strategy.set_token_map(invert_token_map(token_map))

    # 4. Run replay
    print(f"  Running replay ({len(trades)} trades)...")
    output = OUTPUT_DIR / config_label.replace(" ", "_")
    result, summary = await run_s2_replay(
        strategy=strategy,
        trades=trades,
        config=STRATEGY_CONFIG,
        resolutions=resolutions,
        token_map=token_map,
        output_dir=output,
    )

    # 5. Collect rejection stats
    rejection_reasons: dict[str, int] = {}
    for _, reason in result.rejected_intents:
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    elapsed = time.time() - t0
    n_rejected = len(result.rejected_intents)

    print(f"  Done in {elapsed:.1f}s")
    print(f"  Fills: {summary.total_fills}, Wins: {summary.win_count}, "
          f"Losses: {summary.loss_count}, Pending: {summary.pending_count}")
    print(f"  HR: {summary.hit_rate:.1%}, PnL: ${summary.total_pnl_net:.2f}, "
          f"Avg Edge: ${summary.avg_edge:.2f}")
    print(f"  Sharpe: {summary.sharpe:.2f}, MaxDD: ${summary.max_drawdown:.2f}, "
          f"ProfitFactor: {summary.profit_factor:.2f}")
    print(f"  Avg Hold: {summary.avg_hold_duration_s / 3600:.1f}h")
    print(f"  Trades processed: {result.total_trades}, "
          f"Intents: {result.total_intents}, Rejected: {n_rejected}")
    if rejection_reasons:
        print(f"  Rejection breakdown: {rejection_reasons}")

    return RunResult(
        period=period,
        config_label=config_label,
        n_traders=n_traders,
        n_trades_loaded=len(trades),
        n_resolutions=len(resolutions),
        total_fills=summary.total_fills,
        win_count=summary.win_count,
        loss_count=summary.loss_count,
        pending_count=summary.pending_count,
        hit_rate=summary.hit_rate,
        total_pnl_net=summary.total_pnl_net,
        total_fees=summary.total_fees,
        avg_edge=summary.avg_edge,
        sharpe=summary.sharpe,
        max_drawdown=summary.max_drawdown,
        profit_factor=summary.profit_factor,
        avg_hold_hours=summary.avg_hold_duration_s / 3600 if summary.avg_hold_duration_s > 0 else 0,
        n_rejected=n_rejected,
        rejection_reasons=rejection_reasons,
        elapsed_s=elapsed,
    )


def print_comparison(base_results: list[RunResult]) -> None:
    """Print vectorized vs tick-by-tick comparison table."""
    print("\n" + "=" * 100)
    print("  VECTORIZED vs TICK-BY-TICK COMPARISON (base config)")
    print("=" * 100)
    print(
        f"{'Period':<10} {'Vec HR':>8} {'Tick HR':>8} {'HR Gap':>8} "
        f"{'Vec PnL/pos':>12} {'Tick PnL/pos':>13} {'Degradation':>12} {'Flag':>8}"
    )
    print("-" * 100)
    for r in base_results:
        vec = VECTORIZED_UPPER.get(r.period, {})
        vec_hr = vec.get("hr", 0)
        vec_pnl = vec.get("pnl_per_pos", 0)
        hr_gap_pp = (vec_hr - r.hit_rate) * 100
        flag = "**FLAG**" if hr_gap_pp > 40 else "OK" if hr_gap_pp <= 40 else ""
        print(
            f"{r.period:<10} {vec_hr:>7.1%} {r.hit_rate:>7.1%} {hr_gap_pp:>+7.1f}pp "
            f"${vec_pnl:>10.2f} ${r.avg_edge:>12.2f} "
            f"{hr_gap_pp:>10.1f}pp {flag:>8}"
        )

    # Base rate context
    print("\n  Period base rates (for excess HR computation):")
    for r in base_results:
        br = PERIOD_BASE_RATES.get(r.period, {"yes": 0.36, "no": 0.64})
        print(f"    {r.period}: YES={br['yes']:.0%}, NO={br['no']:.0%}")


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    all_results: list[RunResult] = []

    # ---------------------------------------------------------------
    # Phase 1: Base config (recommended) across 3 periods
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 1: BASE CONFIG (scale=3, BOTH, max_hold=168h, signal<0.85)")
    print("=" * 70)

    base_cfg = make_s2_config()
    base_traders = await get_qualified_traders(backend, base_cfg)
    print(f"  Global qualified trader pool: {len(base_traders)}")

    for period in PERIODS:
        r = await run_single_period(backend, period, base_cfg, "base", base_traders)
        all_results.append(r)

    # ---------------------------------------------------------------
    # Phase 2: Parameter variants
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 2: PARAMETER VARIANTS")
    print("=" * 70)

    variants: list[tuple[str, S2Config]] = [
        ("scale4", make_s2_config(scale_threshold=4)),
        ("yes_only", make_s2_config(direction="YES")),
        ("hold72h", make_s2_config(max_hold_hours=72.0)),
    ]

    for label, cfg in variants:
        # Re-compute qualified traders if direction changes
        if cfg.direction != "BOTH":
            traders = await get_qualified_traders(backend, cfg)
        else:
            traders = base_traders

        for period in PERIODS:
            r = await run_single_period(backend, period, cfg, label, traders)
            all_results.append(r)

    # ---------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 130)
    print("  RESULTS SUMMARY")
    print("=" * 130)
    header = (
        f"{'Config':<12} {'Period':<10} {'Traders':>7} {'Trades':>7} "
        f"{'Fills':>6} {'Wins':>5} {'Loss':>5} {'Pend':>5} "
        f"{'HR':>7} {'PnL':>10} {'AvgEdge':>9} {'Sharpe':>7} "
        f"{'MaxDD':>8} {'PF':>6} {'Hold':>7} {'Rej':>5}"
    )
    print(header)
    print("-" * 130)

    for r in all_results:
        print(
            f"{r.config_label:<12} {r.period:<10} {r.n_traders:>7} {r.n_trades_loaded:>7} "
            f"{r.total_fills:>6} {r.win_count:>5} {r.loss_count:>5} {r.pending_count:>5} "
            f"{r.hit_rate:>6.1%} {r.total_pnl_net:>10.2f} {r.avg_edge:>9.2f} {r.sharpe:>7.2f} "
            f"{r.max_drawdown:>8.2f} {r.profit_factor:>6.2f} {r.avg_hold_hours:>6.1f}h {r.n_rejected:>5}"
        )

    # ---------------------------------------------------------------
    # Vectorized vs tick comparison
    # ---------------------------------------------------------------
    base_results = [r for r in all_results if r.config_label == "base"]
    print_comparison(base_results)

    # ---------------------------------------------------------------
    # Compounding scores
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  COMPOUNDING SCORES (base config)")
    print("=" * 70)
    for r in base_results:
        br = PERIOD_BASE_RATES.get(r.period, {"yes": 0.36, "no": 0.64})
        avg_base = (br["yes"] + br["no"]) / 2  # will use overall
        # Use blended base rate = 0.5 since BOTH directions
        if r.avg_hold_hours > 0 and (r.win_count + r.loss_count) > 0:
            median_hold_days = r.avg_hold_hours / 24
            excess = r.hit_rate - 0.50  # vs 50/50 since direction-agnostic
            cs = excess * r.avg_edge / median_hold_days if median_hold_days > 0 else 0
            print(f"  {r.period}: HR={r.hit_rate:.1%}, excess={excess:+.1%}, "
                  f"avg_edge=${r.avg_edge:.2f}, hold={median_hold_days:.1f}d, "
                  f"comp_score={cs:.2f}")
        else:
            print(f"  {r.period}: insufficient data")

    # ---------------------------------------------------------------
    # Save all results
    # ---------------------------------------------------------------
    results_json = OUTPUT_DIR / "validation_results.json"
    with open(results_json, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nResults saved to {results_json}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
