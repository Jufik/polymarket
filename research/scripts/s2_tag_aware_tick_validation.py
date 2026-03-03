"""S2 Hit-Rate Copy -- TAG-AWARE tick-by-tick validation via ReplayRunner.

Runs Jul 2025 OOS period with tag_aware=True mode.
Compares with BEFORE (global pool): HR=42.6%, PnL=-$618, 166 fills.

Pre-flight checklist (all CRITICAL pitfalls addressed):
  [x] BUY-only filter: S2HitRateCopy.on_trade checks trade.side != "BUY"
  [x] Direction-specific consensus: tag-aware mode tracks YES/NO separately
  [x] Tag+direction pool check: maker must be in correct (tag, direction) pool
  [x] Asset-ID resolution: ReplayRunner uses MarketResolution(winning_asset_ids)
  [x] Settlement mid-replay: ReplayRunner settles tick-by-tick via res_timeline
  [x] Gambling markets excluded: exclude_tags=("Sports", "Weather") in pool query
  [x] RealisticFillSimulator: calibrated spreads + volumes from training trades
  [x] published_at fix: uses toUnixTimestamp(timestamp) when published_at=0
  [x] Extended resolution window: 3 months after test period
  [x] seed_threshold = scale_threshold: no early seeds, direct entry at consensus

Usage:
    uv run python research/scripts/s2_tag_aware_tick_validation.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
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

from research.strategies.s2_data import (
    invert_token_map,
    load_market_tags,
    load_period_trades,
)
from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
from research.strategies.s2_replay import run_s2_replay
from research.strategies.s2_tags import (
    materialize_tag_base_rates_sql,
    materialize_tag_markets_sql,
)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

PERIODS = ["2025-07"]  # Hardest period for HRC
OUTPUT_DIR = Path("research/output/s2_tick_tag_aware")

STRATEGY_CONFIG = StrategyConfig(
    name="test",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50_000,
    max_position_usd=200,
    max_open_positions=500,
    cooldown_s=0,
)

# BEFORE results (global pool, Jul 2025, consensus >= 3)
BEFORE_GLOBAL = {
    "2025-07": {"hr": 0.506, "pnl": -5835, "fills": 1317},
}

# Vectorized upper bounds (tag-aware, consensus >= 3, Jul 2025)
VECTORIZED_TAG_AWARE = {
    "2025-07": {"hr": 0.578, "yes_hr": 0.563, "no_hr": 0.924,
                "pnl_total": 669685, "avg_pnl": 40, "n": 16662},
}


@dataclass
class RunResult:
    period: str
    config_label: str
    n_traders: int
    n_tag_pools: int
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


async def materialize_tag_tables(backend: ClickHouseBackend) -> None:
    """Ensure tag materialization tables exist in CH."""
    import clickhouse_connect

    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    # Drop and recreate tag markets
    client.command("DROP TABLE IF EXISTS _tmp_tag_markets SYNC")
    sql = materialize_tag_markets_sql()
    print("  Materializing _tmp_tag_markets...")
    client.command(sql)
    cnt = client.command("SELECT count() FROM _tmp_tag_markets")
    print(f"  _tmp_tag_markets: {cnt} rows")

    # Drop and recreate tag base rates
    client.command("DROP TABLE IF EXISTS _tmp_tag_base_rates SYNC")
    full_sql = materialize_tag_base_rates_sql()
    parts = [s.strip() for s in full_sql.split(";") if s.strip()]
    for stmt in parts:
        client.command(stmt)
    cnt = client.command("SELECT count() FROM _tmp_tag_base_rates")
    print(f"  _tmp_tag_base_rates: {cnt} rows")

    client.close()


async def get_tag_pools(
    backend: ClickHouseBackend,
    s2_cfg: S2Config,
    as_of_date: str,
) -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    """Query tag-aware qualified traders.

    Returns (tag_pools, all_traders).
    tag_pools: {(tag, direction): set(trader_address)}
    all_traders: union of all pools (for trade pre-filtering)
    """
    sql = S2HitRateCopy.tag_qualified_traders_query(
        min_positions=s2_cfg.tag_min_positions,
        min_excess_hr=s2_cfg.tag_min_excess_hr,
        recency_months=s2_cfg.recency_months,
        direction=s2_cfg.direction,
        max_entry_price=s2_cfg.max_entry_price,
        use_bayesian_hr=s2_cfg.use_bayesian_hr,
        exclude_tags=s2_cfg.exclude_tags,
        as_of_date=as_of_date,
    )
    df = await backend._execute(sql)
    if len(df) == 0:
        return {}, set()

    pools: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_traders: set[str] = set()

    for row in df.iter_rows(named=True):
        trader = str(row["trader"]).lower()
        tag = str(row["tag"])
        direction = str(row["direction"])
        pools[(tag, direction)].add(trader)
        all_traders.add(trader)

    return dict(pools), all_traders


async def run_single_period(
    backend: ClickHouseBackend,
    period: str,
    s2_cfg: S2Config,
    config_label: str,
    scale_threshold: int = 3,
) -> RunResult:
    """Run tag-aware replay for a single period."""
    t0 = time.time()
    print(f"\n{'='*70}")
    print(f"  Period: {period}  Config: {config_label}")
    print(f"{'='*70}")

    as_of_date = f"{period}-01"

    # 1. Get tag-aware pools
    print("  Querying tag-aware pools...")
    tag_pools, all_traders = await get_tag_pools(backend, s2_cfg, as_of_date)
    n_traders = len(all_traders)
    n_pools = len(tag_pools)
    print(f"  Tag-aware pool: {n_traders} unique traders, {n_pools} (tag, dir) combos")

    for (tag, direction), traders in sorted(tag_pools.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"    {tag:20s} {direction:5s}: {len(traders):5d} traders")

    if n_traders == 0:
        return RunResult(
            period=period, config_label=config_label,
            n_traders=0, n_tag_pools=0, n_trades_loaded=0, n_resolutions=0,
            total_fills=0, win_count=0, loss_count=0, pending_count=0,
            hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
            sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
            n_rejected=0, rejection_reasons={}, elapsed_s=time.time() - t0,
        )

    # 2. Load trades + resolutions
    print("  Loading trades...")
    trades, resolutions, token_map = await load_period_trades(
        period, all_traders, backend, resolution_months_after=3
    )
    print(f"  Loaded {len(trades)} trades, {len(resolutions)} resolutions, "
          f"{len(token_map)} token maps")

    if not trades:
        return RunResult(
            period=period, config_label=config_label,
            n_traders=n_traders, n_tag_pools=n_pools,
            n_trades_loaded=0, n_resolutions=0,
            total_fills=0, win_count=0, loss_count=0, pending_count=0,
            hit_rate=0, total_pnl_net=0, total_fees=0, avg_edge=0,
            sharpe=0, max_drawdown=0, profit_factor=0, avg_hold_hours=0,
            n_rejected=0, rejection_reasons={}, elapsed_s=time.time() - t0,
        )

    # 3. Load market tags
    print("  Loading market tags...")
    market_tags = await load_market_tags(backend)
    print(f"  Market tags: {len(market_tags)} mappings")

    # 4. Create strategy with tag-aware config
    strategy = S2HitRateCopy(s2_cfg)
    strategy.set_tag_pools(tag_pools)
    strategy.set_market_tags(market_tags)
    strategy.set_token_map(invert_token_map(token_map))

    # 5. Run replay
    print(f"  Running replay ({len(trades)} trades)...")
    output = OUTPUT_DIR / config_label.replace(" ", "_")
    result, summary = await run_s2_replay(
        strategy=strategy,
        trades=trades,
        config=STRATEGY_CONFIG,
        resolutions=resolutions,
        token_map=token_map,
        market_tags=market_tags,
        output_dir=output,
    )

    # 6. Collect results
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
        n_tag_pools=n_pools,
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


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

    # Materialize tag tables
    print("Phase 0: Materializing tag tables...")
    await materialize_tag_tables(backend)

    all_results: list[RunResult] = []

    # ---------------------------------------------------------------
    # Phase 1: Tag-aware base config (consensus >= 3)
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  PHASE 1: TAG-AWARE BASE CONFIG (consensus >= 3)")
    print("  tag_aware=True, tag_min_positions=20, tag_min_excess_hr=0.10")
    print("  seed_threshold = scale_threshold = 3")
    print("=" * 70)

    for scale_thresh in [1, 2, 3, 4, 5]:
        cfg = S2Config(
            tag_aware=True,
            tag_min_positions=20,
            tag_min_excess_hr=0.10,
            min_positions=20,
            min_excess_hr=0.10,
            recency_months=6,
            direction="BOTH",
            max_entry_price=0.95,
            use_bayesian_hr=True,
            exclude_tags=("Sports", "Weather"),
            scale_threshold=scale_thresh,
            seed_threshold=scale_thresh,  # match vectorized
            seed_pct=0.25,
            position_size_usd=100.0,
            max_signal_price=0.85,
            max_hold_hours=168.0,
        )

        for period in PERIODS:
            r = await run_single_period(
                backend, period, cfg, f"tag_C{scale_thresh}", scale_threshold=scale_thresh
            )
            all_results.append(r)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print("\n\n" + "=" * 130)
    print("  TAG-AWARE TICK-BY-TICK RESULTS SUMMARY")
    print("=" * 130)
    header = (
        f"{'Config':<12} {'Period':<10} {'Traders':>7} {'Pools':>6} {'Trades':>7} "
        f"{'Fills':>6} {'Wins':>5} {'Loss':>5} {'Pend':>5} "
        f"{'HR':>7} {'PnL':>10} {'AvgEdge':>9} {'Sharpe':>7} "
        f"{'MaxDD':>8} {'PF':>6} {'Hold':>7} {'Rej':>5}"
    )
    print(header)
    print("-" * 130)

    for r in all_results:
        print(
            f"{r.config_label:<12} {r.period:<10} {r.n_traders:>7} {r.n_tag_pools:>6} "
            f"{r.n_trades_loaded:>7} "
            f"{r.total_fills:>6} {r.win_count:>5} {r.loss_count:>5} {r.pending_count:>5} "
            f"{r.hit_rate:>6.1%} {r.total_pnl_net:>10.2f} {r.avg_edge:>9.2f} {r.sharpe:>7.2f} "
            f"{r.max_drawdown:>8.2f} {r.profit_factor:>6.2f} "
            f"{r.avg_hold_hours:>6.1f}h {r.n_rejected:>5}"
        )

    # ---------------------------------------------------------------
    # Comparison with BEFORE (global pool)
    # ---------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  COMPARISON: TAG-AWARE vs GLOBAL (Jul 2025, tick-by-tick)")
    print("=" * 100)
    print(f"\n  BEFORE (global, C>=3): HR={BEFORE_GLOBAL['2025-07']['hr']:.1%}, "
          f"PnL=${BEFORE_GLOBAL['2025-07']['pnl']:,.0f}, "
          f"Fills={BEFORE_GLOBAL['2025-07']['fills']}")
    print()
    for r in all_results:
        hr_delta = r.hit_rate - BEFORE_GLOBAL["2025-07"]["hr"]
        pnl_delta = r.total_pnl_net - BEFORE_GLOBAL["2025-07"]["pnl"]
        print(f"  {r.config_label}: HR={r.hit_rate:.1%} ({hr_delta:+.1%}), "
              f"PnL=${r.total_pnl_net:,.0f} ({pnl_delta:+,.0f}), "
              f"Fills={r.total_fills}")

    # ---------------------------------------------------------------
    # Vectorized vs tick comparison
    # ---------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  VECTORIZED (UPPER BOUND) vs TICK-BY-TICK")
    print("=" * 100)
    vec = VECTORIZED_TAG_AWARE["2025-07"]
    for r in all_results:
        if "C3" in r.config_label:
            gap = (vec["hr"] - r.hit_rate) * 100
            flag = "**FLAG**" if gap > 40 else "OK"
            print(f"  Vectorized: HR={vec['hr']:.1%}, N={vec['n']:,}, Avg=${vec['avg_pnl']}")
            print(f"  Tick-by-tick: HR={r.hit_rate:.1%}, Fills={r.total_fills}, "
                  f"Avg=${r.avg_edge:.2f}")
            print(f"  Gap: {gap:.1f}pp ({flag})")

    # ---------------------------------------------------------------
    # Compounding scores
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  COMPOUNDING SCORES")
    print("=" * 70)
    for r in all_results:
        if r.avg_hold_hours > 0 and (r.win_count + r.loss_count) > 0:
            hold_days = r.avg_hold_hours / 24
            excess = r.hit_rate - 0.50
            cs = excess * r.avg_edge / hold_days if hold_days > 0 and r.avg_edge > 0 else 0
            print(f"  {r.config_label}: HR={r.hit_rate:.1%}, excess={excess:+.1%}, "
                  f"avg_edge=${r.avg_edge:.2f}, hold={hold_days:.1f}d, comp={cs:.2f}")
        else:
            print(f"  {r.config_label}: insufficient data")

    # Save
    results_json = OUTPUT_DIR / "tag_aware_results.json"
    with open(results_json, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nResults saved to {results_json}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
