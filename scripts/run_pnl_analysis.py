"""PnL analysis for vectorized strategy signals against ClickHouse + local resolution data.

Usage:
    uv run python scripts/run_pnl_analysis.py
    uv run python scripts/run_pnl_analysis.py --host 192.168.0.148
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import polars as pl
import structlog

logger = structlog.get_logger(__name__)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def query_ch(client: httpx.AsyncClient, query: str) -> pl.DataFrame:
    """Execute a ClickHouse query and return as Polars DataFrame."""
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


def compute_pnl(
    signals: pl.DataFrame,
    resolution: pl.DataFrame,
    strategy_name: str,
) -> None:
    """Join signals with resolution data and compute PnL."""
    print(f"\n{'='*70}")
    print(f"  PnL Analysis: {strategy_name}")
    print(f"{'='*70}")
    print(f"  Total signals: {len(signals):,}")

    # Join signals with resolution data
    joined = signals.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id",
        how="left",
    )

    # Filter to resolved markets only (resolution_value=1)
    resolved = joined.filter(pl.col("resolution_value") == 1)
    unresolved = joined.filter(
        (pl.col("resolution_value").is_null()) | (pl.col("resolution_value") != 1)
    )
    print(f"  Resolved signals: {len(resolved):,}")
    print(f"  Unresolved/voided: {len(unresolved):,}")

    if resolved.is_empty():
        print("  No resolved signals — cannot compute PnL.")
        return

    # For each signal, compute PnL based on outcome
    # Signal is BUY NO: we bet on NO side
    #   - If NO won: profit = (1 - entry_price) * size_usd / entry_price
    #     Actually: we buy NO at price (1 - yes_price), payout is 1.0
    #     profit = size_usd * (1.0 / no_price - 1) where no_price = 1 - yes_price
    #     Simpler: profit = size_usd * (yes_price / (1 - yes_price))
    #   - If YES won: loss = -size_usd (we lose our bet)
    #
    # For proportional copy (BUY YES or BUY NO):
    #   BUY YES: if YES won, profit = size_usd * ((1-price)/price), else loss = -size_usd
    #   BUY NO: if NO won, profit = size_usd * (price/(1-price)), else loss = -size_usd

    # Determine if signal won
    resolved_with_result = resolved.with_columns(
        pl.when(
            (pl.col("outcome") == "NO") & (pl.col("winner_outcome") == "No")
        )
        .then(True)
        .when(
            (pl.col("outcome") == "YES") & (pl.col("winner_outcome") == "Yes")
        )
        .then(True)
        .otherwise(False)
        .alias("won"),
    )

    # Compute PnL per signal
    # entry_price is the YES price from the trade
    # For BUY NO: we pay (1 - yes_price) per NO share, payout 1.0 if NO wins
    # For BUY YES: we pay yes_price per YES share, payout 1.0 if YES wins
    resolved_with_pnl = resolved_with_result.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.when(pl.col("outcome") == "NO")
            .then(
                # Won NO: profit = size * (yes_price / (1 - yes_price))
                pl.col("size_usd") * pl.col("entry_price") / (1.0 - pl.col("entry_price"))
            )
            .otherwise(
                # Won YES: profit = size * ((1 - yes_price) / yes_price)
                pl.col("size_usd") * (1.0 - pl.col("entry_price")) / pl.col("entry_price")
            )
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )

    # Aggregate stats
    total_signals = len(resolved_with_pnl)
    wins = resolved_with_pnl.filter(pl.col("won")).shape[0]
    losses = total_signals - wins
    hit_rate = wins / total_signals if total_signals > 0 else 0

    total_pnl = resolved_with_pnl["pnl"].sum()
    total_bet = resolved_with_pnl["size_usd"].sum()
    avg_pnl = resolved_with_pnl["pnl"].mean()

    total_win_pnl = (
        resolved_with_pnl.filter(pl.col("won"))["pnl"].sum()
        if wins > 0
        else 0
    )
    total_loss_pnl = (
        resolved_with_pnl.filter(~pl.col("won"))["pnl"].sum()
        if losses > 0
        else 0
    )

    print(f"\n  --- Results ---")
    print(f"  Wins: {wins:,} / {total_signals:,}  ({hit_rate:.1%} hit rate)")
    print(f"  Total capital deployed: ${total_bet:,.0f}")
    print(f"  Total PnL:    ${total_pnl:,.2f}")
    print(f"  Avg PnL/bet:  ${avg_pnl:,.2f}")
    print(f"  Win PnL:      ${total_win_pnl:,.2f}")
    print(f"  Loss PnL:     ${total_loss_pnl:,.2f}")
    roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
    print(f"  ROI:          {roi:.2f}%")

    # Profit factor
    if total_loss_pnl != 0:
        pf = abs(total_win_pnl / total_loss_pnl)
        print(f"  Profit factor: {pf:.2f}")

    # By outcome breakdown
    print(f"\n  --- By Outcome ---")
    for outcome in ["YES", "NO"]:
        subset = resolved_with_pnl.filter(pl.col("outcome") == outcome)
        if subset.is_empty():
            continue
        n = len(subset)
        w = subset.filter(pl.col("won")).shape[0]
        hr = w / n if n > 0 else 0
        pnl = subset["pnl"].sum()
        bet = subset["size_usd"].sum()
        r = pnl / bet * 100 if bet > 0 else 0
        print(f"  {outcome}: {n:,} signals, {hr:.1%} HR, PnL=${pnl:,.2f}, ROI={r:.2f}%")

    # Show top 10 best and worst
    print(f"\n  --- Top 10 Best Signals ---")
    top = resolved_with_pnl.sort("pnl", descending=True).head(10)
    for row in top.iter_rows(named=True):
        q = row.get("question", "?")
        q = q[:55] if q else "?"
        print(
            f"    {row['outcome']} ${row['pnl']:+,.0f} "
            f"(entry={row['entry_price']:.2f}) | {q}"
        )

    print(f"\n  --- Top 10 Worst Signals ---")
    worst = resolved_with_pnl.sort("pnl").head(10)
    for row in worst.iter_rows(named=True):
        q = row.get("question", "?")
        q = q[:55] if q else "?"
        print(
            f"    {row['outcome']} ${row['pnl']:+,.0f} "
            f"(entry={row['entry_price']:.2f}) | {q}"
        )


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}",
        timeout=120.0,
    )

    # Load resolution data from local parquet
    print("Loading resolution data from data/metadata/markets.parquet...")
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    print(f"  Markets with resolution: {len(resolution):,}")

    # Also load market metadata from CH for questions
    print("Loading market metadata from ClickHouse...")
    markets_df = await query_ch(
        client,
        "SELECT condition_id, question, category FROM polymarket.markets",
    )
    print(f"  Markets loaded: {len(markets_df):,}")

    try:
        # =================================================================
        # S2a — Will NO
        # =================================================================
        print("\n" + "=" * 70)
        print("  Computing S2a (Will NO) signals...")
        print("=" * 70)

        will_markets = markets_df.filter(
            pl.col("question").str.contains(r"^Will\b")
        )
        will_cids = will_markets.sample(
            min(2000, len(will_markets)), seed=42
        )["condition_id"].to_list()
        print(f"  Sampling {len(will_cids)} Will markets...")

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
            print(f"  Trades loaded: {len(trades_will):,}")

            if not trades_will.is_empty():
                from polymarket_pipeline.strategies_impl.will_no.config import (
                    WillNoConfig,
                )
                from polymarket_pipeline.strategies_impl.will_no.strategy import (
                    WillNoStrategy,
                )

                cfg = WillNoConfig(
                    yes_price_min=0.15,
                    yes_price_max=0.40,
                    base_bet_usd=50.0,
                    avoid_keywords={"reach", "hit"},
                )
                strategy = WillNoStrategy(config=cfg)
                signals_s2a = strategy.compute_signals(
                    trades_will.lazy(), markets_df.lazy()
                )

                print(f"  S2a signals: {len(signals_s2a):,}")

                if not signals_s2a.is_empty():
                    # Add question for display
                    signals_with_q = signals_s2a.join(
                        markets_df.select("condition_id", "question"),
                        on="condition_id",
                        how="left",
                    )
                    compute_pnl(signals_with_q, resolution, "S2a: Will NO")

        # =================================================================
        # S1 — Proportional Copy
        # =================================================================
        print("\n" + "=" * 70)
        print("  Computing S1 (Proportional Copy) signals...")
        print("=" * 70)

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
            print(f"  Using top {len(pool_traders)} traders")

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

                cfg_s1 = ProportionalCopyConfig(
                    pool_traders=pool_traders,
                    capital_per_trader_usd=50.0,
                    contradiction_filter=True,
                )
                strat_s1 = ProportionalCopyStrategy(config=cfg_s1)
                signals_s1 = strat_s1.compute_signals(
                    trades_pool.lazy(), markets_df.lazy()
                )

                print(f"  S1 signals: {len(signals_s1):,}")

                if not signals_s1.is_empty():
                    signals_with_q = signals_s1.join(
                        markets_df.select("condition_id", "question"),
                        on="condition_id",
                        how="left",
                    )
                    compute_pnl(signals_with_q, resolution, "S1: Proportional Copy")

        # =================================================================
        # Summary
        # =================================================================
        elapsed = time.time() - t_start
        print(f"\n{'='*70}")
        print(f"  Analysis complete in {elapsed:.1f}s")
        print(f"{'='*70}")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
