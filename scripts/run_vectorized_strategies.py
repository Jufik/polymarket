"""Run all four strategies (vectorized) against ClickHouse.

Usage:
    uv run python scripts/run_vectorized_strategies.py
    uv run python scripts/run_vectorized_strategies.py --host 192.168.0.148
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import httpx
import polars as pl
import structlog

logger = structlog.get_logger(__name__)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def query_ch(client: httpx.AsyncClient, query: str) -> pl.DataFrame:
    """Execute a ClickHouse query and return as Polars DataFrame."""
    import json

    resp = await client.post(
        "/",
        content=f"{query} FORMAT JSONEachRow",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
    )
    resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        return pl.DataFrame()

    rows = [json.loads(line) for line in text.split("\n") if line.strip()]
    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows)


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}",
        timeout=120.0,
    )

    try:
        # -------------------------------------------------------------------
        # 1. Load market metadata
        # -------------------------------------------------------------------
        print("Loading markets from ClickHouse...")
        markets_df = await query_ch(
            client,
            "SELECT condition_id, question, category FROM polymarket.markets",
        )
        print(f"  Markets loaded: {len(markets_df):,}")

        # -------------------------------------------------------------------
        # 2. S2b — Crypto OTM NO
        # -------------------------------------------------------------------
        print("\n=== S2b: Crypto OTM NO ===")

        # Get crypto markets
        crypto_markets = markets_df.filter(
            pl.col("category").str.to_lowercase().str.contains("crypto")
        )
        crypto_cids = crypto_markets["condition_id"].to_list()
        print(f"  Crypto markets: {len(crypto_cids)}")

        if crypto_cids:
            # Query trades for crypto markets only (huge table, need to filter)
            cids_str = ", ".join(f"'{c}'" for c in crypto_cids[:500])
            trades_crypto = await query_ch(
                client,
                f"""
                SELECT
                    condition_id,
                    CAST(price AS Float64) as price,
                    toUnixTimestamp64Milli(timestamp) / 1000.0 as published_at,
                    maker,
                    side
                FROM polymarket.trades_raw FINAL
                WHERE condition_id IN ({cids_str})
                """,
            )
            print(f"  Crypto trades loaded: {len(trades_crypto):,}")

            if not trades_crypto.is_empty():
                from polymarket_pipeline.strategies_impl.crypto_otm_no.config import (
                    CryptoOTMNoConfig,
                )
                from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import (
                    CryptoOTMNoStrategy,
                )
                from polymarket_pipeline.strategies.runners.vectorized import (
                    VectorizedRunner,
                )

                cfg_s2b = CryptoOTMNoConfig(
                    yes_price_min=0.05,
                    yes_price_max=0.25,
                    assets={"BTC", "ETH", "SOL", "XRP"},
                    base_bet_usd=100.0,
                    min_volume_usd=0.0,  # no volume filter for backtest
                )
                strat_s2b = CryptoOTMNoStrategy(config=cfg_s2b)
                runner_s2b = VectorizedRunner(strat_s2b)

                result_s2b = runner_s2b.run(
                    trades_crypto.lazy(),
                    markets_df.lazy(),
                )

                print(f"  Total signals: {result_s2b.total_signals}")
                if result_s2b.total_signals > 0:
                    print(f"  Signal breakdown:")
                    print(f"    Unique markets: {result_s2b.signals['condition_id'].n_unique()}")
                    print(
                        f"    Total bet size: ${result_s2b.signals['size_usd'].sum():,.0f}"
                    )
                    # Show sample signals
                    print(f"\n  Sample signals (first 10):")
                    sample = result_s2b.signals.head(10).join(
                        markets_df.select("condition_id", "question"),
                        on="condition_id",
                        how="left",
                    )
                    for row in sample.iter_rows(named=True):
                        q = row.get("question", "?")[:60]
                        print(
                            f"    {row['outcome']} ${row['size_usd']:.0f} "
                            f"@ signal_time={row['signal_time']:.0f} | {q}"
                        )

        # -------------------------------------------------------------------
        # 3. S2a — Will NO
        # -------------------------------------------------------------------
        print("\n=== S2a: Will NO ===")

        # Will markets — use a sample to avoid huge trade query
        will_markets = markets_df.filter(
            pl.col("question").str.contains(r"^Will\b")
        )
        print(f"  Will markets: {len(will_markets):,}")

        # Sample a subset of Will markets for tractable backtest
        will_cids = will_markets.sample(
            min(2000, len(will_markets)), seed=42
        )["condition_id"].to_list()
        print(f"  Sampling {len(will_cids)} Will markets for backtest...")

        if will_cids:
            cids_str = ", ".join(f"'{c}'" for c in will_cids)
            trades_will = await query_ch(
                client,
                f"""
                SELECT
                    condition_id,
                    CAST(price AS Float64) as price,
                    toUnixTimestamp64Milli(timestamp) / 1000.0 as published_at,
                    maker,
                    side
                FROM polymarket.trades_raw FINAL
                WHERE condition_id IN ({cids_str})
                LIMIT 2000000
                """,
            )
            print(f"  Will trades loaded: {len(trades_will):,}")

            if not trades_will.is_empty():
                from polymarket_pipeline.strategies_impl.will_no.config import (
                    WillNoConfig,
                )
                from polymarket_pipeline.strategies_impl.will_no.strategy import (
                    WillNoStrategy,
                )
                from polymarket_pipeline.strategies.runners.vectorized import (
                    VectorizedRunner,
                )

                cfg_s2a = WillNoConfig(
                    yes_price_min=0.15,
                    yes_price_max=0.40,
                    base_bet_usd=50.0,
                    avoid_keywords={"reach", "hit"},
                )
                strat_s2a = WillNoStrategy(config=cfg_s2a)
                runner_s2a = VectorizedRunner(strat_s2a)

                result_s2a = runner_s2a.run(
                    trades_will.lazy(),
                    markets_df.lazy(),
                )

                print(f"  Total signals: {result_s2a.total_signals}")
                if result_s2a.total_signals > 0:
                    print(f"  Signal breakdown:")
                    print(f"    Unique markets: {result_s2a.signals['condition_id'].n_unique()}")
                    print(
                        f"    Total bet size: ${result_s2a.signals['size_usd'].sum():,.0f}"
                    )
                    # Show sample
                    print(f"\n  Sample signals (first 10):")
                    sample = result_s2a.signals.head(10).join(
                        markets_df.select("condition_id", "question"),
                        on="condition_id",
                        how="left",
                    )
                    for row in sample.iter_rows(named=True):
                        q = row.get("question", "?")[:60]
                        print(
                            f"    {row['outcome']} ${row['size_usd']:.0f} "
                            f"@ signal_time={row['signal_time']:.0f} | {q}"
                        )

        # -------------------------------------------------------------------
        # 4. S1 — Proportional Copy (needs pool traders)
        # -------------------------------------------------------------------
        print("\n=== S1: Proportional Copy ===")

        # First, find skilled traders (top by distinct markets)
        print("  Computing skilled trader pool...")
        top_traders_df = await query_ch(
            client,
            """
            SELECT
                maker,
                count(DISTINCT condition_id) as n_markets
            FROM polymarket.trades_raw FINAL
            WHERE maker IS NOT NULL
            GROUP BY maker
            HAVING n_markets >= 50
            ORDER BY n_markets DESC
            LIMIT 100
            """,
        )
        print(f"  Traders with >= 50 markets: {len(top_traders_df)}")

        if not top_traders_df.is_empty():
            pool_traders = set(top_traders_df["maker"].to_list()[:20])
            print(f"  Using top {len(pool_traders)} traders for pool")

            # Get their trades
            makers_str = ", ".join(f"'{m}'" for m in pool_traders)
            trades_pool = await query_ch(
                client,
                f"""
                SELECT
                    condition_id,
                    CAST(price AS Float64) as price,
                    toUnixTimestamp64Milli(timestamp) / 1000.0 as published_at,
                    maker,
                    side
                FROM polymarket.trades_raw FINAL
                WHERE maker IN ({makers_str})
                LIMIT 5000000
                """,
            )
            print(f"  Pool trades loaded: {len(trades_pool):,}")

            if not trades_pool.is_empty():
                from polymarket_pipeline.strategies_impl.proportional_copy.config import (
                    ProportionalCopyConfig,
                )
                from polymarket_pipeline.strategies_impl.proportional_copy.strategy import (
                    ProportionalCopyStrategy,
                )
                from polymarket_pipeline.strategies.runners.vectorized import (
                    VectorizedRunner,
                )

                cfg_s1 = ProportionalCopyConfig(
                    pool_traders=pool_traders,
                    capital_per_trader_usd=50.0,
                    contradiction_filter=True,
                )
                strat_s1 = ProportionalCopyStrategy(config=cfg_s1)
                runner_s1 = VectorizedRunner(strat_s1)

                result_s1 = runner_s1.run(
                    trades_pool.lazy(),
                    markets_df.lazy(),
                )

                print(f"  Total signals: {result_s1.total_signals}")
                if result_s1.total_signals > 0:
                    print(f"  Signal breakdown:")
                    print(f"    Unique markets: {result_s1.signals['condition_id'].n_unique()}")
                    yes_count = result_s1.signals.filter(
                        pl.col("outcome") == "YES"
                    ).shape[0]
                    no_count = result_s1.signals.filter(
                        pl.col("outcome") == "NO"
                    ).shape[0]
                    print(f"    YES signals: {yes_count}, NO signals: {no_count}")
                    print(
                        f"    Total bet size: ${result_s1.signals['size_usd'].sum():,.0f}"
                    )

        # -------------------------------------------------------------------
        # 5. S3 — Consensus Copy (consistency-filtered skilled pool)
        # -------------------------------------------------------------------
        print("\n=== S3: Consensus Copy ===")

        from pathlib import Path

        data_dir = Path("data/derived")
        required_files = [
            data_dir / "trader_market_pnl.parquet",
            data_dir / "markets_resolved.parquet",
            data_dir / "maker_volume_fractions.parquet",
        ]

        if all(f.exists() for f in required_files):
            from polymarket_pipeline.strategies_impl.consensus_copy.providers import (
                load_skilled_provider,
            )

            provider = load_skilled_provider(
                data_dir=data_dir,
                train_start="2023-01-01",
                train_end="2026-02-01",
                min_periods=6,
                min_markets=10,
                max_mvf=0.10,
                max_median_entry=0.90,
            )

            # Consistency mode computes synchronously — backend unused
            from unittest.mock import AsyncMock

            await provider.compute(AsyncMock())
            skilled_pool = provider.get_features()["skilled_traders"]
            print(f"  Consistency-filtered pool: {len(skilled_pool)} traders")

            if skilled_pool:
                # Query trades for skilled traders
                makers_str = ", ".join(f"'{m}'" for m in list(skilled_pool)[:200])
                trades_s3 = await query_ch(
                    client,
                    f"""
                    SELECT
                        condition_id,
                        CAST(price AS Float64) as price,
                        toUnixTimestamp64Milli(timestamp) / 1000.0 as published_at,
                        maker,
                        side
                    FROM polymarket.trades_raw FINAL
                    WHERE maker IN ({makers_str})
                    LIMIT 5000000
                    """,
                )
                print(f"  Skilled trades loaded: {len(trades_s3):,}")

                if not trades_s3.is_empty():
                    from polymarket_pipeline.strategies_impl.consensus_copy.config import (
                        ConsensusCopyConfig,
                    )
                    from polymarket_pipeline.strategies_impl.consensus_copy.strategy import (
                        ConsensusCopyStrategy,
                    )
                    from polymarket_pipeline.strategies.runners.vectorized import (
                        VectorizedRunner,
                    )

                    cfg_s3 = ConsensusCopyConfig(
                        skilled_traders=skilled_pool,
                        min_traders=5,
                        agreement_pct=0.80,
                        direction="NO",
                        base_bet_usd=10.0,
                    )
                    strat_s3 = ConsensusCopyStrategy(config=cfg_s3)
                    runner_s3 = VectorizedRunner(strat_s3)

                    result_s3 = runner_s3.run(
                        trades_s3.lazy(),
                        markets_df.lazy(),
                    )

                    print(f"  Total signals: {result_s3.total_signals}")
                    if result_s3.total_signals > 0:
                        print(f"  Signal breakdown:")
                        print(
                            f"    Unique markets: "
                            f"{result_s3.signals['condition_id'].n_unique()}"
                        )
                        print(
                            f"    Total bet size: "
                            f"${result_s3.signals['size_usd'].sum():,.0f}"
                        )
                        # Show sample
                        print(f"\n  Sample signals (first 10):")
                        sample = result_s3.signals.head(10).join(
                            markets_df.select("condition_id", "question"),
                            on="condition_id",
                            how="left",
                        )
                        for row in sample.iter_rows(named=True):
                            q = row.get("question", "?")[:60]
                            print(
                                f"    {row['outcome']} ${row['size_usd']:.0f} "
                                f"@ signal_time={row['signal_time']:.0f} | {q}"
                            )
        else:
            missing = [f.name for f in required_files if not f.exists()]
            print(f"  SKIPPED — missing derived data: {', '.join(missing)}")
            print("  Run: uv run python scripts/build_data.py --step derived")

        # -------------------------------------------------------------------
        # Summary
        # -------------------------------------------------------------------
        elapsed = time.time() - t_start
        print(f"\n{'='*60}")
        print(f"All strategies complete in {elapsed:.1f}s")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
