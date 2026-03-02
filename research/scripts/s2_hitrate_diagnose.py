"""S2 Hit-Rate Copy -- diagnostic deep-dive of tick-by-tick results.

Runs a single period with verbose output to diagnose:
  1. Direction distribution (YES vs NO fills)
  2. Token map correctness
  3. Directional correctness (ignoring PnL)
  4. Consensus count distribution
  5. Signal vs fill count (how many signals get filtered)
  6. Entry price distribution

Usage:
    uv run python research/scripts/s2_hitrate_diagnose.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

import structlog

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Suppress noisy logging
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


def make_s2_config(scale_threshold: int = 3) -> S2Config:
    return S2Config(
        min_positions=30,
        min_excess_hr=0.10,
        recency_months=6,
        direction="BOTH",
        max_entry_price=0.95,
        exclude_tags=("Sports", "Weather"),
        use_bayesian_hr=True,
        scale_threshold=scale_threshold,
        seed_threshold=scale_threshold,
        seed_pct=0.25,
        position_size_usd=100.0,
        max_signal_price=0.85,
        max_hold_hours=168.0,
    )


async def diagnose_period(period: str = "2025-04") -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)
    s2_cfg = make_s2_config()

    # 1. Get qualified traders
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

    # Show direction distribution of qualified traders
    dir_counts = Counter(df["direction"].to_list())
    print(f"  Direction distribution: {dict(dir_counts)}")

    # 2. Load trades + resolutions
    trades, resolutions, token_map = await load_period_trades(
        period, traders, backend, resolution_months_after=3,
    )
    print(f"\nLoaded: {len(trades)} trades, {len(resolutions)} resolutions, "
          f"{len(token_map)} token maps")

    # 3. Show trade stats
    buy_count = sum(1 for t in trades if t.side == "BUY")
    sell_count = len(trades) - buy_count
    print(f"  BUY trades: {buy_count}, SELL trades: {sell_count}")

    # Count unique makers among qualified
    makers = set(t.maker.lower() for t in trades if t.maker)
    qualified_makers = makers & traders
    print(f"  Unique makers in trades: {len(makers)}")
    print(f"  Of which qualified: {len(qualified_makers)}")

    # Count unique condition_ids
    cids = set(t.condition_id for t in trades)
    print(f"  Unique markets: {len(cids)}")

    # 4. Check token_map coverage
    cids_with_map = set(token_map.keys())
    cids_with_res = set(resolutions.keys())
    print(f"  Markets with token_map: {len(cids_with_map)}")
    print(f"  Markets with resolution: {len(cids_with_res)}")
    print(f"  Overlap (trade markets with resolution): {len(cids & cids_with_res)}")

    # 5. Invert token map and check
    inv_tm = invert_token_map(token_map)
    print(f"\nInverted token map: {len(inv_tm)} entries")

    # Sample a few entries
    for i, (asset_id, info) in enumerate(list(inv_tm.items())[:3]):
        print(f"  asset_id={asset_id[:20]}... -> {info}")

    # 6. Run replay
    strategy = S2HitRateCopy(s2_cfg)
    strategy.set_qualified_traders(traders)
    strategy.set_token_map(inv_tm)

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=50_000,
        max_position_usd=200,
        max_open_positions=500,
        cooldown_s=0,
    )

    output_dir = Path("research/output/s2_diag")
    result, summary = await run_s2_replay(
        strategy=strategy,
        trades=trades,
        config=strat_config,
        resolutions=resolutions,
        token_map=token_map,
        output_dir=output_dir,
    )

    print(f"\n{'='*60}")
    print(f"  RESULTS for {period}")
    print(f"{'='*60}")
    print(f"  Total trades: {result.total_trades}")
    print(f"  Total intents: {result.total_intents}")
    print(f"  Total fills: {result.total_fills}")
    print(f"  Rejected: {len(result.rejected_intents)}")

    # Rejection breakdown
    rejection_reasons = Counter(r for _, r in result.rejected_intents)
    print(f"  Rejection reasons: {dict(rejection_reasons)}")

    # Ledger analysis -- read from ParquetLedger buffer (enriched by ReplayRunner)
    # Note: result.ledger_records has STALE references (not updated by settlement
    # enrichment). The ledger._buffer IS updated via replace().
    from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
    ledger_path = output_dir / f"ledger_{strategy.name}.parquet"
    # We can't access the internal ledger from run_s2_replay() after return.
    # Instead, let's manually run the replay to get the enriched records.
    # For now, we'll use run_s2_replay but access the records via summary.
    # The summary IS computed from enriched records.
    records = result.ledger_records  # stale (no resolution) for per-record analysis
    print(f"  NOTE: result.ledger_records are STALE (not enriched by settlement)")
    print(f"  Summary is correct (computed from ledger._buffer which IS enriched)")

    print(f"\n  Ledger records: {len(records)}")

    # Direction distribution of fills
    outcome_counter = Counter(r.outcome for r in records)
    print(f"  Fill outcomes: {dict(outcome_counter)}")

    # Resolution distribution
    res_counter = Counter(r.resolution for r in records)
    print(f"  Resolution: {dict(res_counter)}")

    # Direction-specific WON/LOST
    for outcome in ["YES", "NO"]:
        subset = [r for r in records if r.outcome == outcome]
        won = sum(1 for r in subset if r.resolution == "WON")
        lost = sum(1 for r in subset if r.resolution == "LOST")
        total = won + lost
        hr = won / total if total > 0 else 0
        print(f"  {outcome}: {won} won / {lost} lost = {hr:.1%} HR "
              f"(of {total} resolved, {len(subset)} total)")

    # PnL by outcome
    for outcome in ["YES", "NO"]:
        subset = [r for r in records if r.outcome == outcome and r.pnl_net is not None]
        if subset:
            total_pnl = sum(r.pnl_net for r in subset)
            avg_pnl = total_pnl / len(subset)
            print(f"  {outcome} PnL: ${total_pnl:.2f} (avg ${avg_pnl:.2f})")

    # Entry price distribution
    prices = [r.fill_price for r in records if r.fill_price > 0]
    if prices:
        avg_p = sum(prices) / len(prices)
        min_p = min(prices)
        max_p = max(prices)
        print(f"\n  Entry price: avg={avg_p:.3f}, min={min_p:.3f}, max={max_p:.3f}")

    # Consensus count from reason field
    consensus_counts = []
    for r in records:
        if "consensus=" in r.reason:
            try:
                c = int(r.reason.split("consensus=")[1].split()[0].strip("()"))
                consensus_counts.append(c)
            except (ValueError, IndexError):
                pass
    if consensus_counts:
        cc = Counter(consensus_counts)
        print(f"  Consensus counts: {dict(sorted(cc.items()))}")

    # Slippage analysis
    fees = [r.fill_fee_usd for r in records if r.fill_fee_usd > 0]
    if fees:
        avg_fee = sum(fees) / len(fees)
        total_fee = sum(fees)
        print(f"\n  Slippage/fees: total=${total_fee:.2f}, avg=${avg_fee:.2f}")

    # Hold time analysis
    holds = [r.hold_duration_s for r in records if r.hold_duration_s is not None and r.hold_duration_s > 0]
    if holds:
        avg_hold_d = sum(holds) / len(holds) / 86400
        med_hold_d = sorted(holds)[len(holds) // 2] / 86400
        print(f"  Hold time: avg={avg_hold_d:.1f}d, median={med_hold_d:.1f}d")

    # Compare to base rates
    print(f"\n  For context: YES base ~29%, NO base ~71% (Apr)")
    print(f"  Summary: wins={summary.win_count}, losses={summary.loss_count}, "
          f"pending={summary.pending_count}")
    print(f"  Summary HR: {summary.hit_rate:.1%}")
    print(f"  Summary PnL: ${summary.total_pnl_net:.2f}")
    print(f"  Summary Sharpe: {summary.sharpe:.2f}")

    # Check: are ANY records resolved?
    resolved = [r for r in records if r.resolution is not None]
    pending = [r for r in records if r.resolution is None]
    print(f"\n  Records resolved: {len(resolved)}, pending: {len(pending)}")

    # Check resolution data passed to replay
    # How many of the markets we filled have resolutions?
    filled_cids = set(r.condition_id for r in records)
    resolved_cids = set(resolutions.keys())
    overlap = filled_cids & resolved_cids
    print(f"  Filled markets: {len(filled_cids)}")
    print(f"  Resolved markets (total): {len(resolved_cids)}")
    print(f"  Filled AND resolved: {len(overlap)}")

    # Check token_map for overlap
    tm_cids = set(token_map.keys())
    overlap2 = filled_cids & tm_cids
    print(f"  Filled AND in token_map: {len(overlap2)}")

    # Check: are the resolutions' winning_asset_ids populated?
    n_empty_winners = 0
    for cid, (winner_outcome, epoch) in list(resolutions.items())[:5]:
        in_tm = cid in token_map
        tm_entry = token_map.get(cid, {})
        winning_asset = tm_entry.get(winner_outcome, "")
        print(f"    Resolution: cid={cid[:20]}..., winner={winner_outcome}, "
              f"epoch={epoch}, in_tm={in_tm}, winning_asset={winning_asset[:20] if winning_asset else 'EMPTY'}...")
        if not winning_asset:
            n_empty_winners += 1
    print(f"  Resolutions with empty winning_asset_id (first 5): {n_empty_winners}")

    # Check strategy internal state
    print(f"\n  Strategy state:")
    print(f"  Seeded markets: {len(strategy._seeded)}")
    print(f"  Scaled markets: {len(strategy._scaled)}")
    print(f"  Consensus entries: {len(strategy._consensus)}")

    # Sample consensus entries
    n_consensus_markets = 0
    for cid, data in strategy._consensus.items():
        n = len(data["traders"])
        if n >= 3:
            n_consensus_markets += 1
    print(f"  Markets with consensus >= 3: {n_consensus_markets}")

    # How many trades have the published_at = 0 issue?
    zero_pub = sum(1 for t in trades if t.published_at == 0)
    print(f"\n  Trades with published_at=0: {zero_pub} (should be 0 with fix)")

    # Check: what's happening with the runner's settlement?
    # The summary shows wins/losses but ledger shows 0 resolved.
    # The summary must be computing from the ledger written to disk (which
    # was written by a prior run).
    # Let's check the runner's n_settled
    print(f"\n  NOTE: summary.win_count={summary.win_count} but ledger resolved=0")
    print(f"  This means the summary is read from disk (ParquetLedger), not from result.")
    print(f"  The issue: run_s2_replay reads ALL records from disk including past runs.")

    # Verify: check what result.fills shows
    from polymarket_pipeline.strategies.types import FillStatus
    n_filled = sum(1 for f in result.fills if f.status == FillStatus.FILLED)
    n_rejected_fill = sum(1 for f in result.fills if f.status == FillStatus.REJECTED)
    print(f"\n  result.fills: {len(result.fills)} total, {n_filled} filled, "
          f"{n_rejected_fill} rejected")

    # Check first few trades' published_at vs resolution times
    if trades and resolutions:
        trade_times = sorted([t.published_at for t in trades])
        res_times = sorted([epoch for _, epoch in resolutions.values()])
        print(f"\n  Trade time range: {trade_times[0]:.0f} - {trade_times[-1]:.0f}")
        print(f"  Resolution time range: {res_times[0]:.0f} - {res_times[-1]:.0f}")
        # How many resolutions fall within trade period?
        in_range = sum(1 for t in res_times if trade_times[0] <= t <= trade_times[-1])
        after = sum(1 for t in res_times if t > trade_times[-1])
        before = sum(1 for t in res_times if t < trade_times[0])
        print(f"  Resolutions during trades: {in_range}")
        print(f"  Resolutions after trades: {after}")
        print(f"  Resolutions before trades: {before}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(diagnose_period("2025-04"))
