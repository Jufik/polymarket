"""S2 Hit-Rate Copy -- Gap Fix Tick-by-Tick Validation.

Validates 3 fixes that address the 34pp vectorized-to-tick gap:
  1. Position-level dedup (dedup_per_position=True): 1 signal per (trader, market)
  2. Consensus cap (max_consensus=5): skip markets with >5 qualified traders
  3. Direction-aware filtering (set_qualified_traders_directed): traders only
     counted when trading in their qualified direction

Runs 3 OOS periods: 2025-04, 2025-07, 2025-10 (walk-forward).
Also runs a no-dedup variant to isolate dedup impact.

Pre-flight checklist (ALL CRITICAL pitfalls addressed):
  [x] BUY-only filter: S2HitRateCopy.on_trade checks trade.side != "BUY"
  [x] Unique-trader consensus: uses set.add(maker) in _consensus dict
  [x] Asset-ID resolution: ReplayRunner uses MarketResolution(winning_asset_ids)
  [x] Settlement mid-replay: ReplayRunner settles tick-by-tick via res_timeline
  [x] Gambling/sports/weather excluded: tag-based exclusion table
  [x] RealisticFillSimulator: calibrated spreads + volumes
  [x] Gateway budget: $1M (never binds; real constraint is risk gate cost_basis)
  [x] published_at fix: toUnixTimestamp(timestamp) when published_at=0
  [x] Extended resolution window: 3 months after test period
  [x] Direction-aware consensus: set_qualified_traders_directed()
  [x] Position-level dedup: dedup_per_position=True
  [x] Consensus cap: max_consensus=5
  [x] on_timer IS called: ReplayRunner fires on_timer at timer_interval_s (3600s)

BEFORE results (Jul '25, no fixes):
  HR: 42.6%, PnL: -$618, Fills: 166, Sharpe: -0.61

Usage:
    uv run python research/scripts/s2_hitrate_gapfix_validation.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

# Ensure repo root is on sys.path for research imports
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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
OUTPUT_DIR = Path("research/output/s2_tick_hitrate_gapfix")

# Capital config: same as original validation for comparison
STRATEGY_CONFIG = StrategyConfig(
    name="s2_hitrate_copy",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50_000,
    max_position_usd=200,
    max_open_positions=500,
    cooldown_s=0,
)

# Vectorized upper bounds (from ideas.md, consensus >= 3, FIXED variant)
VECTORIZED_UPPER = {
    "2025-04": {"hr": 0.829, "pnl_per_pos": 241.0},
    "2025-07": {"hr": 0.842, "pnl_per_pos": 145.0},
    "2025-10": {"hr": 0.855, "pnl_per_pos": 155.0},
}

# BEFORE results (original tick validation, no fixes, base config C>=3)
BEFORE_RESULTS = {
    "2025-04": {"hr": 0.459, "pnl": -6417, "fills": 740, "sharpe": -0.21},
    "2025-07": {"hr": 0.506, "pnl": -5835, "fills": 1317, "sharpe": -0.14},
    "2025-10": {"hr": 0.461, "pnl": 2385, "fills": 1579, "sharpe": 0.04},
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
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    n_settled: int = 0
    n_unique_markets: int = 0
    elapsed_s: float = 0.0


async def ensure_excluded_table(backend: ClickHouseBackend) -> None:
    """Ensure _tmp_excluded_tags table exists."""
    check_sql = "SELECT count() FROM _tmp_excluded_tags"
    try:
        df = await backend._execute(check_sql)
        count = df["count()"][0]
        print(f"  _tmp_excluded_tags exists with {count} rows")
    except Exception:
        print("  Creating _tmp_excluded_tags...")
        create_sql = S2HitRateCopy.materialize_excluded_tags_sql()
        await backend._execute(create_sql)
        df = await backend._execute(check_sql)
        count = df["count()"][0]
        print(f"  Created _tmp_excluded_tags with {count} rows")


async def get_qualified_traders_directed(
    backend: ClickHouseBackend,
    s2_cfg: S2Config,
    as_of_date: str,
) -> tuple[dict[str, set[str]], set[str]]:
    """Query qualified traders with direction info for walk-forward.

    Returns (directed_dict, flat_set) where:
      directed_dict: trader -> set of qualified directions {"YES"} or {"NO"} or both
      flat_set: all qualified trader addresses
    """
    query = S2HitRateCopy.qualified_traders_query(
        min_positions=s2_cfg.min_positions,
        min_excess_hr=s2_cfg.min_excess_hr,
        recency_months=s2_cfg.recency_months,
        direction=s2_cfg.direction,
        max_entry_price=s2_cfg.max_entry_price,
        exclude_tags=s2_cfg.exclude_tags,
        use_bayesian_hr=s2_cfg.use_bayesian_hr,
        as_of_date=as_of_date,
    )
    df = await backend._execute(query)
    if len(df) == 0:
        return {}, set()

    directed: dict[str, set[str]] = {}
    for row in df.iter_rows(named=True):
        trader = str(row["trader"]).lower()
        direction = str(row["direction"])
        if trader not in directed:
            directed[trader] = set()
        directed[trader].add(direction)

    flat = set(directed.keys())
    return directed, flat


async def run_single_period(
    backend: ClickHouseBackend,
    period: str,
    s2_cfg: S2Config,
    config_label: str,
    directed_traders: dict[str, set[str]],
    flat_traders: set[str],
) -> RunResult:
    """Run replay for a single period with given config and directed traders."""
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  Period: {period}  Config: {config_label}")
    print(f"{'='*60}")

    n_traders = len(flat_traders)
    print(f"  Qualified traders: {n_traders}")

    empty = RunResult(
        period=period, config_label=config_label,
        n_traders=n_traders, n_trades_loaded=0, n_resolutions=0,
        total_fills=0, win_count=0, loss_count=0, pending_count=0,
        hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
        sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
        n_rejected=0,
    )

    if n_traders == 0:
        empty.elapsed_s = time.time() - t0
        return empty

    # Load trades + resolutions (extended window for settlement)
    print(f"  Loading trades...")
    trades, resolutions, token_map = await load_period_trades(
        period, flat_traders, backend, resolution_months_after=3
    )
    print(f"  Loaded {len(trades)} trades, {len(resolutions)} resolutions, "
          f"{len(token_map)} token maps")

    if not trades:
        empty.n_trades_loaded = 0
        empty.n_resolutions = len(resolutions)
        empty.elapsed_s = time.time() - t0
        return empty

    # Create strategy with gap fixes
    strategy = S2HitRateCopy(s2_cfg)

    # Use direction-aware mode (fix #3)
    if directed_traders:
        strategy.set_qualified_traders_directed(directed_traders)
    else:
        strategy.set_qualified_traders(flat_traders)

    strategy.set_token_map(invert_token_map(token_map))

    # Run replay
    print(f"  Running replay ({len(trades)} trades, dedup={s2_cfg.dedup_per_position}, "
          f"max_cons={s2_cfg.max_consensus})...")
    output = OUTPUT_DIR / config_label.replace(" ", "_") / period
    result, summary = await run_s2_replay(
        strategy=strategy,
        trades=trades,
        config=STRATEGY_CONFIG,
        resolutions=resolutions,
        token_map=token_map,
        output_dir=output,
    )

    # Collect rejection stats
    rejection_reasons: dict[str, int] = {}
    for _, reason in result.rejected_intents:
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    elapsed = time.time() - t0
    n_rejected = len(result.rejected_intents)

    # Count unique markets entered
    n_unique_markets = len(strategy._scaled) + len(
        strategy._seeded - strategy._scaled
    )

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
    print(f"  Unique markets entered: {n_unique_markets}")
    print(f"  Settled: {getattr(result, 'n_settled', 'N/A')}")
    if rejection_reasons:
        print(f"  Rejection breakdown: {rejection_reasons}")

    # Seeded/scaled counts
    n_seeded_only = len(strategy._seeded - strategy._scaled)
    n_scaled = len(strategy._scaled)
    print(f"  Seeded-only: {n_seeded_only}, Scaled: {n_scaled}")

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
        n_unique_markets=n_unique_markets,
        elapsed_s=elapsed,
    )


def print_comparison(results: list[RunResult], label: str) -> None:
    """Print BEFORE vs AFTER and vectorized comparison."""
    print(f"\n{'='*120}")
    print(f"  {label}")
    print(f"{'='*120}")
    print(
        f"{'Period':<10} {'Vec HR':>8} {'BEFORE HR':>10} {'AFTER HR':>9} "
        f"{'Delta':>7} {'BEFORE PnL':>11} {'AFTER PnL':>10} "
        f"{'BEFORE Fills':>12} {'AFTER Fills':>11} {'Gap(pp)':>8}"
    )
    print("-" * 120)
    for r in results:
        vec = VECTORIZED_UPPER.get(r.period, {})
        vec_hr = vec.get("hr", 0)
        before = BEFORE_RESULTS.get(r.period, {})
        before_hr = before.get("hr", 0)
        before_pnl = before.get("pnl", 0)
        before_fills = before.get("fills", 0)
        delta_pp = (r.hit_rate - before_hr) * 100
        gap_pp = (vec_hr - r.hit_rate) * 100
        print(
            f"{r.period:<10} {vec_hr:>7.1%} {before_hr:>9.1%} {r.hit_rate:>8.1%} "
            f"{delta_pp:>+6.1f}pp ${before_pnl:>9.0f} ${r.total_pnl_net:>9.2f} "
            f"{before_fills:>11} {r.total_fills:>10} {gap_pp:>+7.1f}"
        )


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    # Ensure excluded tags table exists
    await ensure_excluded_table(backend)

    all_results: list[RunResult] = []

    # ---------------------------------------------------------------
    # Phase 1: ALL 3 FIXES enabled (walk-forward, C>=3)
    # dedup_per_position=True, max_consensus=5, direction-aware
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 1: ALL 3 GAP FIXES (walk-forward)")
    print("  dedup_per_position=True, max_consensus=5, direction-aware")
    print("  scale=3, seed=3, BOTH, max_hold=168h, signal<0.85")
    print("=" * 70)

    cfg_fixes = S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction="BOTH",
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=3,
        seed_threshold=3,  # match vectorized: no early seeds
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=0.85,
        max_hold_hours=168.0,
        # Gap fixes ON
        dedup_per_position=True,
        max_consensus=5,
    )

    for period in PERIODS:
        as_of_date = f"{period}-01"
        directed, flat = await get_qualified_traders_directed(
            backend, cfg_fixes, as_of_date
        )
        print(f"\n  {period}: {len(flat)} qualified traders, "
              f"{sum(1 for d in directed.values() if len(d) == 2)} BOTH-dir, "
              f"{sum(1 for d in directed.values() if d == {'YES'})} YES-only, "
              f"{sum(1 for d in directed.values() if d == {'NO'})} NO-only")
        r = await run_single_period(
            backend, period, cfg_fixes, "all_fixes", directed, flat
        )
        all_results.append(r)

    # ---------------------------------------------------------------
    # Phase 2: NO dedup variant (direction-aware + consensus cap, NO dedup)
    # Isolates the dedup impact
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 2: WITHOUT DEDUP (direction-aware + consensus cap only)")
    print("  dedup_per_position=False, max_consensus=5, direction-aware")
    print("=" * 70)

    cfg_no_dedup = S2Config(
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
        # Dedup OFF, other fixes ON
        dedup_per_position=False,
        max_consensus=5,
    )

    for period in PERIODS:
        as_of_date = f"{period}-01"
        directed, flat = await get_qualified_traders_directed(
            backend, cfg_no_dedup, as_of_date
        )
        r = await run_single_period(
            backend, period, cfg_no_dedup, "no_dedup", directed, flat
        )
        all_results.append(r)

    # ---------------------------------------------------------------
    # Phase 3: Higher consensus threshold (C>=4) with all fixes
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 3: ALL FIXES + C>=4")
    print("=" * 70)

    cfg_c4 = S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction="BOTH",
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=4,
        seed_threshold=4,
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=0.85,
        max_hold_hours=168.0,
        dedup_per_position=True,
        max_consensus=5,
    )

    for period in PERIODS:
        as_of_date = f"{period}-01"
        directed, flat = await get_qualified_traders_directed(
            backend, cfg_c4, as_of_date
        )
        r = await run_single_period(
            backend, period, cfg_c4, "c4_fixes", directed, flat
        )
        all_results.append(r)

    # ---------------------------------------------------------------
    # Phase 4: All fixes + NO max_consensus cap (isolate cap impact)
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 4: ALL FIXES + NO CONSENSUS CAP")
    print("  dedup_per_position=True, max_consensus=None, direction-aware")
    print("=" * 70)

    cfg_no_cap = S2Config(
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
        dedup_per_position=True,
        max_consensus=None,  # No cap
    )

    for period in PERIODS:
        as_of_date = f"{period}-01"
        directed, flat = await get_qualified_traders_directed(
            backend, cfg_no_cap, as_of_date
        )
        r = await run_single_period(
            backend, period, cfg_no_cap, "no_cap", directed, flat
        )
        all_results.append(r)

    # ---------------------------------------------------------------
    # Summary tables
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 150)
    print("  FULL RESULTS SUMMARY")
    print("=" * 150)
    header = (
        f"{'Config':<12} {'Period':<10} {'Traders':>7} {'Trades':>7} "
        f"{'Fills':>6} {'Wins':>5} {'Loss':>5} {'Pend':>5} "
        f"{'HR':>7} {'PnL':>10} {'AvgEdge':>9} {'Sharpe':>7} "
        f"{'MaxDD':>8} {'PF':>6} {'Hold':>7} {'Rej':>5} {'Mkts':>5}"
    )
    print(header)
    print("-" * 150)

    for r in all_results:
        print(
            f"{r.config_label:<12} {r.period:<10} {r.n_traders:>7} {r.n_trades_loaded:>7} "
            f"{r.total_fills:>6} {r.win_count:>5} {r.loss_count:>5} {r.pending_count:>5} "
            f"{r.hit_rate:>6.1%} {r.total_pnl_net:>10.2f} {r.avg_edge:>9.2f} {r.sharpe:>7.2f} "
            f"{r.max_drawdown:>8.2f} {r.profit_factor:>6.2f} {r.avg_hold_hours:>6.1f}h {r.n_rejected:>5} "
            f"{r.n_unique_markets:>5}"
        )

    # ---------------------------------------------------------------
    # BEFORE vs AFTER comparison (all_fixes vs original)
    # ---------------------------------------------------------------
    fixes_results = [r for r in all_results if r.config_label == "all_fixes"]
    print_comparison(fixes_results, "ALL 3 FIXES vs BEFORE (no fixes)")

    no_dedup_results = [r for r in all_results if r.config_label == "no_dedup"]
    print_comparison(no_dedup_results, "NO DEDUP (dir + cap only) vs BEFORE")

    # ---------------------------------------------------------------
    # Dedup isolation
    # ---------------------------------------------------------------
    print(f"\n{'='*90}")
    print("  DEDUP ISOLATION: all_fixes vs no_dedup")
    print(f"{'='*90}")
    print(f"{'Period':<10} {'Fixes HR':>9} {'NoDed HR':>9} {'Dedup Delta':>12} "
          f"{'Fixes PnL':>10} {'NoDed PnL':>10} {'Fixes Fills':>11} {'NoDed Fills':>11}")
    print("-" * 90)
    for fix_r, nod_r in zip(fixes_results, no_dedup_results):
        delta_pp = (fix_r.hit_rate - nod_r.hit_rate) * 100
        print(
            f"{fix_r.period:<10} {fix_r.hit_rate:>8.1%} {nod_r.hit_rate:>8.1%} "
            f"{delta_pp:>+10.1f}pp ${fix_r.total_pnl_net:>9.2f} ${nod_r.total_pnl_net:>9.2f} "
            f"{fix_r.total_fills:>10} {nod_r.total_fills:>10}"
        )

    # ---------------------------------------------------------------
    # Consensus cap isolation
    # ---------------------------------------------------------------
    no_cap_results = [r for r in all_results if r.config_label == "no_cap"]
    print(f"\n{'='*90}")
    print("  CONSENSUS CAP ISOLATION: all_fixes (cap=5) vs no_cap")
    print(f"{'='*90}")
    print(f"{'Period':<10} {'Cap5 HR':>8} {'NoCap HR':>9} {'Cap Delta':>10} "
          f"{'Cap5 PnL':>9} {'NoCap PnL':>10} {'Cap5 Fills':>11} {'NoCap Fills':>11}")
    print("-" * 90)
    for fix_r, nc_r in zip(fixes_results, no_cap_results):
        delta_pp = (fix_r.hit_rate - nc_r.hit_rate) * 100
        print(
            f"{fix_r.period:<10} {fix_r.hit_rate:>7.1%} {nc_r.hit_rate:>8.1%} "
            f"{delta_pp:>+8.1f}pp ${fix_r.total_pnl_net:>8.2f} ${nc_r.total_pnl_net:>9.2f} "
            f"{fix_r.total_fills:>10} {nc_r.total_fills:>10}"
        )

    # ---------------------------------------------------------------
    # Compounding scores
    # ---------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  COMPOUNDING SCORES")
    print(f"{'='*70}")
    for r in all_results:
        if r.avg_hold_hours > 0 and (r.win_count + r.loss_count) > 0:
            hold_days = r.avg_hold_hours / 24
            excess = r.hit_rate - 0.50  # blended base for BOTH
            cs = excess * r.avg_edge / hold_days if hold_days > 0 else 0
            print(f"  {r.config_label:<12} {r.period}: HR={r.hit_rate:.1%}, "
                  f"excess={excess:+.1%}, edge=${r.avg_edge:.2f}, "
                  f"hold={hold_days:.1f}d, comp={cs:.2f}")
        else:
            print(f"  {r.config_label:<12} {r.period}: insufficient data")

    # ---------------------------------------------------------------
    # Rejection breakdown per config
    # ---------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  REJECTION BREAKDOWN")
    print(f"{'='*70}")
    for r in all_results:
        if r.rejection_reasons:
            print(f"  {r.config_label:<12} {r.period}: {r.rejection_reasons}")

    # ---------------------------------------------------------------
    # Save all results
    # ---------------------------------------------------------------
    results_json = OUTPUT_DIR / "gapfix_validation_results.json"
    with open(results_json, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nResults saved to {results_json}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
