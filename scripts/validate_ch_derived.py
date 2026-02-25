"""Cross-validate ClickHouse derived views against parquet baselines.

Usage:
    uv run python scripts/validate_ch_derived.py
"""

from __future__ import annotations

import asyncio

import polars as pl
import structlog

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend

logger = structlog.get_logger(__name__)


async def main() -> None:
    ch = ClickHouseBackend(host="localhost", port=18123, database="polymarket")

    # --- MVF comparison ---
    parquet_mvf = pl.read_parquet("data/derived/maker_volume_fractions.parquet")
    ch_mvf = await ch.query_mvf()

    if ch_mvf.is_empty():
        print("ERROR: ClickHouse trader_volumes is empty. Run migration 004.")
        await ch.close()
        return

    # Join on trader, compare mvf values
    compare = parquet_mvf.select("trader", pl.col("mvf").alias("parquet_mvf")).join(
        ch_mvf.select("trader", pl.col("mvf").alias("ch_mvf")),
        on="trader",
        how="inner",
    )
    compare = compare.with_columns(
        (pl.col("ch_mvf") - pl.col("parquet_mvf")).abs().alias("mvf_diff")
    )

    print(f"MVF comparison: {len(compare)} traders matched")
    print(f"  Median diff:  {compare['mvf_diff'].median():.6f}")
    print(f"  P99 diff:     {compare['mvf_diff'].quantile(0.99):.6f}")
    print(f"  Max diff:     {compare['mvf_diff'].max():.6f}")
    print()

    # --- PnL comparison (sample from CH traders that also exist in parquet) ---
    parquet_pnl = pl.read_parquet("data/derived/trader_market_pnl.parquet")

    # Get traders from CH that have PnL data (JOIN with resolved markets)
    ch_pnl_all = await ch.query_trader_pnl()
    if ch_pnl_all.is_empty():
        print("ERROR: ClickHouse PnL query returned empty (no resolved market overlap).")
        await ch.close()
        return

    # Find traders present in both CH and parquet
    ch_traders = set(ch_pnl_all["trader"].to_list())
    parquet_traders = set(parquet_pnl["trader"].to_list())
    common_traders = list(ch_traders & parquet_traders)
    print(f"Traders in CH PnL: {len(ch_traders)}")
    print(f"Traders in parquet PnL: {len(parquet_traders)}")
    print(f"Common traders: {len(common_traders)}")

    if not common_traders:
        print("No common traders to compare.")
        await ch.close()
        return

    sample_traders = common_traders[:100]
    ch_pnl = ch_pnl_all.filter(pl.col("trader").is_in(sample_traders))

    if ch_pnl.is_empty():
        print("ERROR: ClickHouse PnL query returned empty.")
        await ch.close()
        return

    pnl_compare = (
        parquet_pnl.filter(pl.col("trader").is_in(sample_traders))
        .select("trader", "condition_id", pl.col("market_pnl").alias("parquet_pnl"))
        .join(
            ch_pnl.select(
                "trader", "condition_id", pl.col("market_pnl").alias("ch_pnl")
            ),
            on=["trader", "condition_id"],
            how="inner",
        )
    )
    pnl_compare = pnl_compare.with_columns(
        (pl.col("ch_pnl") - pl.col("parquet_pnl")).abs().alias("pnl_diff")
    )

    print(f"PnL comparison: {len(pnl_compare)} (trader, market) pairs matched")
    print(f"  Median diff:  ${pnl_compare['pnl_diff'].median():.4f}")
    print(f"  P99 diff:     ${pnl_compare['pnl_diff'].quantile(0.99):.4f}")
    print(f"  Max diff:     ${pnl_compare['pnl_diff'].max():.4f}")

    # Flag if diffs are unexpectedly large (>$0.01 tolerance for float rounding)
    large_diffs = pnl_compare.filter(pl.col("pnl_diff") > 0.01)
    if large_diffs.is_empty():
        print("\n  OK — all PnL values within $0.01 tolerance")
    else:
        print(f"\n  WARNING: {len(large_diffs)} pairs exceed $0.01 tolerance")
        print(large_diffs.sort("pnl_diff", descending=True).head(10))

    await ch.close()


if __name__ == "__main__":
    asyncio.run(main())
