"""Diagnose S2 hit-rate copy tick-by-tick results.

Runs a single period (Apr 2025) and prints detailed breakdown:
- Direction distribution (YES vs NO)
- Per-direction HR
- Entry price distribution
- Consensus count at fill
- Seed vs scale fills
- Which traders are generating the most fills

Usage:
    uv run python research/scripts/s2_hitrate_diagnose_tick.py
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

import structlog

logging.basicConfig(level=logging.WARNING)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.types import ExecutionMode

from research.strategies.s2_data import invert_token_map, load_period_trades
from research.strategies.s2_hitrate_copy import S2HitRateCopy, S2Config
from research.strategies.s2_replay import run_s2_replay

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

STRATEGY_CONFIG = StrategyConfig(
    name="test",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50_000,
    max_position_usd=200,
    max_open_positions=500,
    cooldown_s=0,
)


async def diagnose() -> None:
    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)

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

    for period in ["2025-04", "2025-07", "2025-10"]:
        as_of_date = f"{period}-01"
        print(f"\n{'='*70}")
        print(f"  DIAGNOSING: {period}")
        print(f"{'='*70}")

        # 1. Get pool and check direction distribution
        query = S2HitRateCopy.qualified_traders_query(
            min_positions=30,
            min_excess_hr=0.10,
            recency_months=6,
            direction="BOTH",
            max_entry_price=0.95,
            exclude_tags=("Sports", "Weather"),
            use_bayesian_hr=True,
            as_of_date=as_of_date,
        )
        df = await backend._execute(query)
        traders = set(df["trader"].to_list())
        print(f"  Pool: {len(traders)} traders")

        yes_df = df.filter(df["direction"] == "YES")
        no_df = df.filter(df["direction"] == "NO")
        print(f"  YES qualified traders: {len(yes_df)}")
        print(f"  NO qualified traders: {len(no_df)}")

        if len(yes_df) > 0:
            print(f"    YES mean excess HR: {yes_df['excess_hr'].mean():.3f}")
        if len(no_df) > 0:
            print(f"    NO mean excess HR: {no_df['excess_hr'].mean():.3f}")

        # 2. Load trades
        trades, resolutions, token_map = await load_period_trades(
            period, traders, backend, resolution_months_after=3
        )
        print(f"  Trades: {len(trades):,}, Resolutions: {len(resolutions):,}")

        if not trades:
            print("  NO TRADES -- skipping")
            continue

        # 3. Run replay
        strategy = S2HitRateCopy(s2_cfg)
        strategy.set_qualified_traders(traders)
        strategy.set_token_map(invert_token_map(token_map))

        output = Path(f"research/output/s2_tick_hitrate/diag_{period}")
        result, summary = await run_s2_replay(
            strategy=strategy,
            trades=trades,
            config=STRATEGY_CONFIG,
            resolutions=resolutions,
            token_map=token_map,
            output_dir=output,
        )

        # 4. Read ledger
        ledger_path = output / "ledger_s2_hitrate_copy.parquet"
        ledger = ParquetLedger(ledger_path)
        records = await ledger.read_all()
        print(f"  Total ledger records: {len(records)}")

        # 5. Outcome/direction distribution
        outcome_dist = Counter(r.outcome for r in records)
        print(f"  Fill outcome distribution: {dict(outcome_dist)}")

        # 6. Per-direction HR
        won_by_outcome: dict[str, int] = {}
        total_by_outcome: dict[str, int] = {}
        pnl_by_outcome: dict[str, float] = {}
        for r in records:
            if r.resolution is not None:
                o = r.outcome
                total_by_outcome[o] = total_by_outcome.get(o, 0) + 1
                pnl_by_outcome[o] = pnl_by_outcome.get(o, 0.0) + (r.pnl_net or 0.0)
                if r.resolution == "WON":
                    won_by_outcome[o] = won_by_outcome.get(o, 0) + 1

        print("  Per-direction results:")
        for o in ["YES", "NO"]:
            w = won_by_outcome.get(o, 0)
            t = total_by_outcome.get(o, 0)
            pnl = pnl_by_outcome.get(o, 0.0)
            hr = w / t if t > 0 else 0
            print(f"    {o}: {w}/{t} wins ({hr:.1%}), PnL=${pnl:.2f}")

        # 7. Entry price distribution
        prices = [r.fill_price for r in records if r.fill_price > 0]
        if prices:
            print(f"  Entry prices: mean={statistics.mean(prices):.3f}, "
                  f"median={statistics.median(prices):.3f}, "
                  f"min={min(prices):.3f}, max={max(prices):.3f}")

        # 8. Seed vs scale
        seed_count = sum(1 for r in records if "seed" in (r.reason or ""))
        scale_count = sum(1 for r in records if "scale" in (r.reason or ""))
        print(f"  Seeds: {seed_count}, Scales: {scale_count}")

        # 9. Consensus at fill
        cons_counts: dict[int, int] = {}
        for r in records:
            if r.reason:
                try:
                    parts = r.reason.split("=")
                    if len(parts) >= 2:
                        c = int(parts[1].split()[0])
                        cons_counts[c] = cons_counts.get(c, 0) + 1
                except (ValueError, IndexError):
                    pass
        print(f"  Consensus at fill: {dict(sorted(cons_counts.items()))}")

        # 10. Rejection stats
        rej_reasons: dict[str, int] = {}
        for _, reason in result.rejected_intents:
            rej_reasons[reason] = rej_reasons.get(reason, 0) + 1
        print(f"  Rejections: {len(result.rejected_intents)} total, {rej_reasons}")

        # 11. Hold time distribution (for resolved)
        hold_hours = [
            r.hold_duration_s / 3600 for r in records
            if r.resolution is not None and r.hold_duration_s is not None and r.hold_duration_s > 0
        ]
        if hold_hours:
            print(f"  Hold time (h): mean={statistics.mean(hold_hours):.1f}, "
                  f"median={statistics.median(hold_hours):.1f}, "
                  f"min={min(hold_hours):.1f}, max={max(hold_hours):.1f}")

        # 12. Summary
        print(f"\n  SUMMARY: HR={summary.hit_rate:.1%}, "
              f"PnL=${summary.total_pnl_net:.2f}, "
              f"Fills={summary.total_fills}, "
              f"Sharpe={summary.sharpe:.2f}")

    await backend.close()


if __name__ == "__main__":
    asyncio.run(diagnose())
