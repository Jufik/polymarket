"""Analyze overlap between S1 (proportional copy) and S2 (will-NO) strategies.

Question: When S1's trader pool trades in "Will" binary markets,
does S2 add independent edge?

Usage:
    uv run python scripts/analyze_s1_s2_overlap.py
"""

from __future__ import annotations

import polars as pl

from polymarket_pipeline.strategies.runners.combined import CombinedBacktestRunner
from polymarket_pipeline.strategies_impl.proportional_copy.config import (
    ProportionalCopyConfig,
)
from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
    ProportionalCopyStrategy,
)
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy


def load_data() -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Load trades and market metadata from derived parquet files."""
    trades = pl.scan_parquet("data/derived/trader_market_pnl.parquet")
    markets = pl.scan_parquet("data/metadata/markets.parquet")
    return trades, markets


def main() -> None:
    trades, markets = load_data()

    # S1: proportional copy with graded pool
    # NOTE: Replace with actual pool_traders from latest grading run
    s1 = ProportionalCopyStrategy(
        config=ProportionalCopyConfig(
            pool_traders=set(),  # TODO: load from grading output
            capital_per_trader_usd=50.0,
            contradiction_filter=True,
        )
    )

    # S2: will-NO
    s2 = WillNoStrategy(
        config=WillNoConfig(
            yes_price_min=0.15,
            yes_price_max=0.40,
            base_bet_usd=50.0,
            avoid_keywords={"reach", "hit"},
        )
    )

    runner = CombinedBacktestRunner(
        strategies=[s1, s2],
        budgets={"proportional_copy": 1000.0, "will_no": 300.0},
    )

    signals = runner.run(trades, markets)

    if signals.is_empty():
        print("No signals generated. Check pool_traders is populated.")
        return

    # Find overlap: markets where both S1 and S2 fired
    s1_markets = set(
        signals.filter(pl.col("strategy") == "proportional_copy")["condition_id"].to_list()
    )
    s2_markets = set(
        signals.filter(pl.col("strategy") == "will_no")["condition_id"].to_list()
    )

    overlap = s1_markets & s2_markets
    s1_only = s1_markets - s2_markets
    s2_only = s2_markets - s1_markets

    print(f"S1 signals: {len(s1_markets)}")
    print(f"S2 signals: {len(s2_markets)}")
    print(f"Overlap:    {len(overlap)} ({len(overlap) / max(len(s2_markets), 1):.1%} of S2)")
    print(f"S1 only:    {len(s1_only)}")
    print(f"S2 only:    {len(s2_only)}")
    print()

    if overlap:
        # Direction agreement in overlapping markets
        overlap_signals = signals.filter(pl.col("condition_id").is_in(list(overlap)))
        print("Overlap signal details:")
        print(overlap_signals.select("strategy", "condition_id", "outcome", "size_usd"))


if __name__ == "__main__":
    main()
