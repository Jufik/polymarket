"""S2 Hit-Rate Copy — dedup fix validation sweep.

Tests the corrected dedup implementation (moved _seen.add after price filter)
combined with entry price filters for Kelly-positive economics.

Sweep grid (2 × 3 × 2 = 12 configs):
  - dedup_per_position: [True (fixed), False]
  - max_signal_price:   [0.85 (baseline), 0.65, 0.55]
  - direction:          ["BOTH", "NO"]

Walk-forward: 3 OOS periods (Jul '25, Oct '25, Jan '26), each with its own
training window [period - 6mo, period).

Usage:
    uv run python research/scripts/s2_dedup_fix_validation.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
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
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s2_data import invert_token_map, load_period_trades
from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
from research.strategies.s2_replay import run_s2_replay

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

PERIODS = ["2025-07", "2025-10", "2026-01"]
OUTPUT_DIR = Path("research/output/s2_dedup_fix_validation")

STRATEGY_CONFIG = StrategyConfig(
    name="test",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50_000,
    max_position_usd=200,
    max_open_positions=500,
    cooldown_s=0,
)

# Insider copy baseline for comparison
INSIDER_BASELINE = {"hr": 0.573, "pnl": 783_800, "compounding": 12.37}


@dataclass
class RunResult:
    period: str
    config_label: str
    dedup: bool
    max_signal_price: float
    direction: str
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
    elapsed_s: float


def make_label(dedup: bool, max_sp: float, direction: str) -> str:
    parts = []
    parts.append("dd" if dedup else "nodd")
    parts.append(f"sp{int(max_sp * 100)}")
    parts.append(direction[0])
    return "_".join(parts)


async def get_qualified_traders_directed(
    backend: ClickHouseBackend,
    s2_cfg: S2Config,
    as_of_date: str,
) -> tuple[set[str], dict[str, set[str]]]:
    """Get qualified traders with direction info for walk-forward."""
    # Materialize excluded tags
    mat_sql = S2HitRateCopy.materialize_excluded_tags_sql(
        exclude_tags=s2_cfg.exclude_tags,
    )
    try:
        await backend._execute(f"DROP TABLE IF EXISTS _tmp_excluded_tags")
        await backend._execute(mat_sql)
    except Exception:
        pass

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
        return set(), {}

    traders_flat: set[str] = set()
    traders_dir: dict[str, set[str]] = {}
    for row in df.iter_rows(named=True):
        addr = row["trader"]
        direction = row["direction"]
        traders_flat.add(addr)
        if addr not in traders_dir:
            traders_dir[addr] = set()
        traders_dir[addr].add(direction)

    return traders_flat, traders_dir


async def run_single(
    backend: ClickHouseBackend,
    period: str,
    dedup: bool,
    max_signal_price: float,
    direction: str,
    traders_flat: set[str],
    traders_dir: dict[str, set[str]],
    trades_cache: dict[str, tuple],
) -> RunResult:
    """Run replay for one config/period combo."""
    label = make_label(dedup, max_signal_price, direction)
    t0 = time.time()

    # Load trades (cached per period)
    if period not in trades_cache:
        trades, resolutions, token_map = await load_period_trades(
            period, traders_flat, backend, resolution_months_after=3,
        )
        trades_cache[period] = (trades, resolutions, token_map)
    trades, resolutions, token_map = trades_cache[period]

    empty = RunResult(
        period=period, config_label=label, dedup=dedup,
        max_signal_price=max_signal_price, direction=direction,
        n_traders=len(traders_flat), n_trades_loaded=len(trades),
        n_resolutions=len(resolutions),
        total_fills=0, win_count=0, loss_count=0, pending_count=0,
        hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
        sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
        n_rejected=0, elapsed_s=0,
    )

    if not trades:
        empty.elapsed_s = time.time() - t0
        return empty

    # Build strategy
    s2_cfg = S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction=direction,  # type: ignore[arg-type]
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=3,
        seed_threshold=3,
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=max_signal_price,
        max_hold_hours=168.0,
        dedup_per_position=dedup,
        max_consensus=5,
    )
    strategy = S2HitRateCopy(s2_cfg)
    strategy.set_qualified_traders_directed(traders_dir)
    strategy.set_token_map(invert_token_map(token_map))

    # Run replay
    output = OUTPUT_DIR / label / period.replace("-", "_")
    result, summary = await run_s2_replay(
        strategy=strategy,
        trades=trades,
        config=STRATEGY_CONFIG,
        resolutions=resolutions,
        token_map=token_map,
        output_dir=output,
    )

    elapsed = time.time() - t0
    return RunResult(
        period=period,
        config_label=label,
        dedup=dedup,
        max_signal_price=max_signal_price,
        direction=direction,
        n_traders=len(traders_flat),
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
        avg_hold_hours=(
            summary.avg_hold_duration_s / 3600
            if summary.avg_hold_duration_s > 0
            else 0
        ),
        n_rejected=len(result.rejected_intents),
        elapsed_s=elapsed,
    )


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    # Sweep grid
    dedup_values = [True, False]
    price_values = [0.85, 0.65, 0.55]
    dir_values = ["BOTH", "NO"]

    all_results: list[RunResult] = []

    for period in PERIODS:
        as_of_date = f"{period}-01"
        print(f"\n{'#' * 70}")
        print(f"  PERIOD: {period} (training window ends {as_of_date})")
        print(f"{'#' * 70}")

        # Get qualified traders ONCE per period (direction=BOTH for superset)
        traders_flat, traders_dir = await get_qualified_traders_directed(
            backend,
            S2Config(
                min_positions=30,
                min_excess_hr=0.10,
                recency_months=6,
                direction="BOTH",
                max_entry_price=0.95,
                exclude_tags=("Sports", "Weather"),
                use_bayesian_hr=True,
            ),
            as_of_date,
        )
        print(f"  Qualified: {len(traders_flat)} traders, "
              f"YES-only: {sum(1 for d in traders_dir.values() if d == {'YES'})}, "
              f"NO-only: {sum(1 for d in traders_dir.values() if d == {'NO'})}, "
              f"BOTH: {sum(1 for d in traders_dir.values() if len(d) == 2)}")

        # Trade cache: load once, reuse across configs
        trades_cache: dict[str, tuple] = {}

        for dedup in dedup_values:
            for max_sp in price_values:
                for direction in dir_values:
                    label = make_label(dedup, max_sp, direction)
                    print(f"\n  --- {label} ---")

                    # For NO-only direction, filter traders_dir
                    if direction == "NO":
                        td = {
                            t: d
                            for t, d in traders_dir.items()
                            if "NO" in d
                        }
                        tf = set(td.keys())
                    else:
                        td = traders_dir
                        tf = traders_flat

                    r = await run_single(
                        backend, period, dedup, max_sp, direction,
                        tf, td, trades_cache,
                    )
                    all_results.append(r)
                    print(f"  fills={r.total_fills}, HR={r.hit_rate:.1%}, "
                          f"PnL=${r.total_pnl_net:,.0f}, "
                          f"sharpe={r.sharpe:.2f}, "
                          f"hold={r.avg_hold_hours:.0f}h")

    # ---------------------------------------------------------------
    # Aggregate by config (across periods)
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 120)
    print("  AGGREGATED RESULTS (across 3 periods)")
    print("=" * 120)
    header = (
        f"{'Config':<16} {'Dedup':>5} {'SigPx':>5} {'Dir':>4} "
        f"{'Fills':>6} {'HR':>7} {'TotalPnL':>12} {'AvgEdge':>9} "
        f"{'Sharpe':>7} {'Hold(h)':>7} {'Compound':>9}"
    )
    print(header)
    print("-" * 120)

    # Group by config label
    from collections import defaultdict

    by_config: dict[str, list[RunResult]] = defaultdict(list)
    for r in all_results:
        by_config[r.config_label].append(r)

    summary_rows = []
    for label, runs in sorted(by_config.items()):
        valid = [r for r in runs if r.total_fills > 0]
        if not valid:
            continue

        total_fills = sum(r.total_fills for r in valid)
        total_pnl = sum(r.total_pnl_net for r in valid)
        avg_hr = sum(r.hit_rate for r in valid) / len(valid)
        avg_edge = sum(r.avg_edge for r in valid) / len(valid)
        avg_sharpe = sum(r.sharpe for r in valid) / len(valid)
        avg_hold = sum(r.avg_hold_hours for r in valid) / len(valid)

        # Compounding score
        hold_days = avg_hold / 24 if avg_hold > 0 else 0.1
        excess_hr = avg_hr - 0.50  # blended base rate
        comp = excess_hr * avg_edge / hold_days if avg_edge > 0 else 0

        dedup_str = "Y" if runs[0].dedup else "N"
        sp_str = f"{runs[0].max_signal_price:.2f}"
        dir_str = runs[0].direction

        print(
            f"{label:<16} {dedup_str:>5} {sp_str:>5} {dir_str:>4} "
            f"{total_fills:>6} {avg_hr:>6.1%} {total_pnl:>12,.0f} {avg_edge:>9.2f} "
            f"{avg_sharpe:>7.2f} {avg_hold:>6.0f}h {comp:>9.3f}"
        )
        summary_rows.append({
            "config": label,
            "dedup": runs[0].dedup,
            "max_signal_price": runs[0].max_signal_price,
            "direction": dir_str,
            "fills": total_fills,
            "hr": avg_hr,
            "pnl": total_pnl,
            "avg_edge": avg_edge,
            "sharpe": avg_sharpe,
            "hold_h": avg_hold,
            "compounding": comp,
        })

    # ---------------------------------------------------------------
    # Per-period detail table
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 140)
    print("  PER-PERIOD DETAIL")
    print("=" * 140)
    header2 = (
        f"{'Config':<16} {'Period':<10} {'Traders':>7} {'Trades':>7} "
        f"{'Fills':>6} {'W':>4} {'L':>4} {'P':>4} "
        f"{'HR':>7} {'PnL':>10} {'Edge':>8} {'Shrp':>6} "
        f"{'DD':>8} {'PF':>5} {'Hold':>6} {'Rej':>5} {'t(s)':>5}"
    )
    print(header2)
    print("-" * 140)

    for r in all_results:
        print(
            f"{r.config_label:<16} {r.period:<10} {r.n_traders:>7} "
            f"{r.n_trades_loaded:>7} "
            f"{r.total_fills:>6} {r.win_count:>4} {r.loss_count:>4} "
            f"{r.pending_count:>4} "
            f"{r.hit_rate:>6.1%} {r.total_pnl_net:>10,.0f} "
            f"{r.avg_edge:>8.2f} {r.sharpe:>6.2f} "
            f"{r.max_drawdown:>8,.0f} {r.profit_factor:>5.2f} "
            f"{r.avg_hold_hours:>5.0f}h {r.n_rejected:>5} {r.elapsed_s:>5.0f}"
        )

    # ---------------------------------------------------------------
    # Dedup fix impact analysis
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 90)
    print("  DEDUP FIX IMPACT (dedup=True vs dedup=False, same price/direction)")
    print("=" * 90)
    print(
        f"{'SigPx':>5} {'Dir':>4} "
        f"{'DD HR':>7} {'noDD HR':>8} {'delta':>7} "
        f"{'DD PnL':>10} {'noDD PnL':>10} {'delta':>10} "
        f"{'DD fills':>8} {'noDD fills':>10}"
    )
    print("-" * 90)
    for sp in price_values:
        for d in dir_values:
            dd_label = make_label(True, sp, d)
            nodd_label = make_label(False, sp, d)
            dd_runs = by_config.get(dd_label, [])
            nodd_runs = by_config.get(nodd_label, [])
            dd_valid = [r for r in dd_runs if r.total_fills > 0]
            nodd_valid = [r for r in nodd_runs if r.total_fills > 0]

            dd_hr = sum(r.hit_rate for r in dd_valid) / len(dd_valid) if dd_valid else 0
            nodd_hr = sum(r.hit_rate for r in nodd_valid) / len(nodd_valid) if nodd_valid else 0
            dd_pnl = sum(r.total_pnl_net for r in dd_valid)
            nodd_pnl = sum(r.total_pnl_net for r in nodd_valid)
            dd_fills = sum(r.total_fills for r in dd_valid)
            nodd_fills = sum(r.total_fills for r in nodd_valid)

            hr_delta = (dd_hr - nodd_hr) * 100
            pnl_delta = dd_pnl - nodd_pnl

            print(
                f"{sp:>5.2f} {d:>4} "
                f"{dd_hr:>6.1%} {nodd_hr:>7.1%} {hr_delta:>+6.1f}pp "
                f"${dd_pnl:>9,.0f} ${nodd_pnl:>9,.0f} ${pnl_delta:>+9,.0f} "
                f"{dd_fills:>8} {nodd_fills:>10}"
            )

    # ---------------------------------------------------------------
    # Comparison to insider copy baseline
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 70)
    print("  vs INSIDER COPY BASELINE")
    print(f"  Insider: HR={INSIDER_BASELINE['hr']:.1%}, "
          f"PnL=${INSIDER_BASELINE['pnl']:,.0f}, "
          f"compound={INSIDER_BASELINE['compounding']:.2f}")
    print("=" * 70)

    if summary_rows:
        best = max(summary_rows, key=lambda x: x["compounding"])
        print(f"\n  Best config: {best['config']}")
        print(f"    HR:          {best['hr']:.1%} "
              f"(insider: {INSIDER_BASELINE['hr']:.1%})")
        print(f"    PnL:         ${best['pnl']:,.0f} "
              f"(insider: ${INSIDER_BASELINE['pnl']:,.0f})")
        print(f"    Compounding: {best['compounding']:.3f} "
              f"(insider: {INSIDER_BASELINE['compounding']:.2f})")
        verdict = "VIABLE" if best["pnl"] > 0 and best["hr"] > 0.55 else "NOT VIABLE"
        print(f"\n  Verdict: {verdict}")

    # Save results
    results_json = OUTPUT_DIR / "dedup_fix_results.json"
    with open(results_json, "w") as f:
        json.dump(
            {"runs": [asdict(r) for r in all_results], "summary": summary_rows},
            f,
            indent=2,
            default=str,
        )
    print(f"\nResults saved to {results_json}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
