"""Validate the updated S2 Will-NO config against ClickHouse data.

Quick sanity check: does the production config match the exploration results?

Usage:
    uv run python scripts/validate_s2_config.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.will_no.config import (
    DEFAULT_PRICE_BANDS,
    WillNoConfig,
)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def query_ch(client: httpx.AsyncClient, query: str, *, label: str = "query") -> pl.DataFrame:
    t0 = time.time()
    print(f"  [{label}] …", end="", flush=True)
    async with client.stream(
        "POST", "/", content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB}, headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            raise RuntimeError(body)
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
    data = b"".join(chunks)
    df = pl.read_csv(io.BytesIO(data), has_header=True, skip_rows_after_header=1)
    print(f" {len(df):,} rows ({time.time()-t0:.1f}s)")
    return df


async def main() -> None:
    cfg = WillNoConfig()
    print("=" * 80)
    print("  S2 Will-NO Config Validation")
    print("=" * 80)
    print(f"\n  prefer_keywords = {cfg.prefer_keywords}")
    print(f"  avoid_keywords  = {cfg.avoid_keywords}")
    print(f"  max_volume_usd  = {cfg.max_volume_usd}")
    print(f"  price_bands     = {cfg.price_bands}")
    print(f"  max_bucket      = {cfg.max_bucket}")
    print(f"  fee_pct         = {cfg.fee_pct}")

    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    markets = await query_ch(client,
        "SELECT condition_id, question FROM markets WHERE question LIKE 'Will %%'",
        label="markets")
    first_trades = await query_ch(client, """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id""", label="first-trades")
    first_trades = first_trades.with_columns(pl.col("first_price").cast(pl.Float64))

    volume = await query_ch(client, """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               sum(CAST(price AS Float64) * CAST(size AS Float64)) AS total_volume
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id""", label="volume")
    volume = volume.with_columns(pl.col("total_volume").cast(pl.Float64))

    # Lockup: first trade → event end_date
    lockup = await query_ch(client, """
        WITH
            wm AS (
                SELECT condition_id, toString(event_id) AS event_id
                FROM markets WHERE question LIKE 'Will %%'
            ),
            ft AS (
                SELECT condition_id,
                       min(toUnixTimestamp(timestamp)) AS first_ts
                FROM (SELECT * FROM polymarket.trades_raw FINAL) t
                WHERE condition_id IN (SELECT condition_id FROM wm)
                GROUP BY condition_id
            )
        SELECT w.condition_id AS condition_id,
               (CAST(toUnixTimestamp(e.end_date) AS Float64) - ft.first_ts) / 86400.0
                   AS lockup_days
        FROM wm w
        JOIN ft ON ft.condition_id = w.condition_id
        JOIN events e ON toString(e.id) = w.event_id
        WHERE e.end_date IS NOT NULL""", label="lockup")
    lockup = lockup.with_columns(pl.col("lockup_days").cast(pl.Float64))

    await client.aclose()

    # Build base
    base = markets.join(first_trades, on="condition_id", how="inner")
    base = base.join(volume, on="condition_id", how="left")
    base = base.join(lockup, on="condition_id", how="left")
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left")

    # Apply production config filters
    q_lower = pl.col("question").str.to_lowercase()

    # 1. Price band filter
    band_cond = pl.lit(False)
    n = len(cfg.price_bands)
    for i, (lo, hi, _) in enumerate(cfg.price_bands):
        if i < n - 1:
            band_cond = band_cond | ((pl.col("first_price") >= lo) & (pl.col("first_price") < hi))
        else:
            band_cond = band_cond | ((pl.col("first_price") >= lo) & (pl.col("first_price") <= hi))
    filtered = base.filter(band_cond)

    # 2. Prefer keywords
    if cfg.prefer_keywords:
        prefer_cond = pl.lit(False)
        for kw in cfg.prefer_keywords:
            prefer_cond = prefer_cond | q_lower.str.contains(kw.lower())
        filtered = filtered.filter(prefer_cond)

    # 3. Avoid keywords
    for kw in cfg.avoid_keywords:
        filtered = filtered.filter(~q_lower.str.contains(kw.lower()))

    # 4. Volume filter
    if cfg.max_volume_usd > 0:
        filtered = filtered.filter(pl.col("total_volume") <= cfg.max_volume_usd)

    # 5. Only resolved markets
    resolved = filtered.filter(pl.col("resolution_value") == 1)

    # Compute PnL with band-adjusted sizing
    def band_mult(price: float) -> float:
        return cfg.band_multiplier(price)

    resolved = resolved.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )

    # Band-adjusted sizing
    size_expr = pl.lit(0.0)
    for i, (lo, hi, mult) in enumerate(cfg.price_bands):
        if i < n - 1:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
        else:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
        size_expr = pl.when(c).then(pl.lit(cfg.base_bet_usd * mult)).otherwise(size_expr)
    resolved = resolved.with_columns(size_expr.alias("size_usd"))

    for fee_label, fee in [("0% fee", 0.0), ("2% fee", 0.02)]:
        pnl_df = resolved.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
                - pl.col("size_usd") * fee
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl")
        )

        n_signals = len(pnl_df)
        wins = pnl_df.filter(pl.col("won")).shape[0]
        hr = wins / n_signals if n_signals > 0 else 0
        total_pnl = pnl_df["pnl"].sum()
        total_bet = pnl_df["size_usd"].sum()
        roi = total_pnl / total_bet * 100 if total_bet > 0 else 0

        print(f"\n  PRODUCTION CONFIG @ {fee_label}:")
        print(f"    Signals:  {n_signals:,}")
        print(f"    HR:       {hr:.1%} ({wins}/{n_signals})")
        print(f"    ROI:      {roi:+.1f}%")
        print(f"    PnL:      ${total_pnl:+,.0f}")
        print(f"    Avg bet:  ${total_bet/n_signals:.1f}" if n_signals > 0 else "")


    # ── Capital efficiency ──────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  CAPITAL EFFICIENCY")
    print("=" * 80)

    # Use the 2% fee PnL DataFrame (last iteration of the loop)
    ld = resolved.filter(pl.col("lockup_days").is_not_null())["lockup_days"]
    if len(ld) > 0:
        print(f"\n  Lockup distribution (days) for {len(ld):,} signals with end_date:")
        p25 = ld.quantile(0.25)
        p50 = ld.quantile(0.50)
        p75 = ld.quantile(0.75)
        mean_ld = ld.mean()
        print(f"    p25={p25:.1f}  median={p50:.1f}  p75={p75:.1f}  mean={mean_ld:.1f}")

        # Brackets
        brackets = [
            ("< 1 day", 0, 1),
            ("1-3 days", 1, 3),
            ("3-7 days", 3, 7),
            ("7-14 days", 7, 14),
            ("14-30 days", 14, 30),
            ("30+ days", 30, 1e6),
        ]
        print(f"\n  {'Bracket':<15} {'Count':>6} {'Pct':>6}")
        print(f"  {'-' * 15} {'-' * 6} {'-' * 6}")
        for label, lo, hi in brackets:
            ct = resolved.filter(
                (pl.col("lockup_days") >= lo) & (pl.col("lockup_days") < hi)
                & pl.col("lockup_days").is_not_null()
            ).height
            pct = ct / len(ld) * 100 if len(ld) > 0 else 0
            print(f"  {label:<15} {ct:>6,} {pct:>5.1f}%")

        # Capital math
        total_bet = resolved["size_usd"].sum()
        total_pnl_2pct = resolved.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
                - pl.col("size_usd") * 0.02
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl")
        )["pnl"].sum()
        n_signals = len(resolved)
        avg_bet = total_bet / n_signals if n_signals > 0 else 0
        roi_per_wager = total_pnl_2pct / total_bet if total_bet > 0 else 0

        # Estimate months from data range
        n_months = 18.0  # approximate from rolling window data
        sigs_per_month = n_signals / n_months
        monthly_wagered = sigs_per_month * avg_bet

        print(f"\n  Key metrics:")
        print(f"    Avg bet size:        ${avg_bet:.1f}")
        print(f"    ROI per $ wagered:   {roi_per_wager:.1%} (at 2% fee)")
        print(f"    Signals/month:       {sigs_per_month:.1f}")
        print(f"    Monthly wagered:     ${monthly_wagered:,.0f}")
        print(f"    Monthly PnL (2%fee): ${monthly_wagered * roi_per_wager:,.0f}")

        print(f"\n  Deployed capital ROI (at 2% fee):")
        print(f"  {'Lockup':<12} {'Capital':>10} {'Turnover':>10} {'Mo ROI':>8} {'Yr ROI':>8}")
        print(f"  {'-' * 12} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8}")
        for ld_days, label in [(p25, f"p25={p25:.0f}d"), (p50, f"med={p50:.0f}d"),
                                (p75, f"p75={p75:.0f}d"), (mean_ld, f"avg={mean_ld:.0f}d")]:
            if ld_days and ld_days > 0:
                concurrent = sigs_per_month * ld_days / 30.0
                capital = concurrent * avg_bet
                turnover = 30.0 / ld_days
                mo_roi = roi_per_wager * turnover
                yr_roi = mo_roi * 12
                print(f"  {label:<12} ${capital:>9,.0f} {turnover:>9.1f}x {mo_roi:>7.0%} {yr_roi:>7.0%}")
    else:
        print("\n  No lockup data available (end_date missing)")


if __name__ == "__main__":
    asyncio.run(main())
