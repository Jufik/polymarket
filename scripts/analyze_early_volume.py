"""Analyze early volume features as predictors of final market size.

Answers: Can we predict at signal time whether a Will market will stay thin?
"""

from __future__ import annotations

import asyncio
import json

import httpx
import polars as pl

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


async def query_ch(client: httpx.AsyncClient, query: str) -> pl.DataFrame:
    resp = await client.post(
        "/",
        content=f"{query} FORMAT JSONEachRow",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
    )
    if resp.status_code != 200:
        print(f"  CH Error: {resp.text[:500]}")
        resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return pl.DataFrame()
    rows = [json.loads(line) for line in text.split("\n") if line.strip()]
    return pl.DataFrame(rows) if rows else pl.DataFrame()


async def main() -> None:
    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0
    )

    # Step 1: first_trade_ts per Will market
    print("Step 1: Computing first trade time per Will market...")
    first_ts = await query_ch(
        client,
        """
        SELECT condition_id, min(timestamp) AS first_ts
        FROM (SELECT condition_id, timestamp FROM polymarket.trades_raw FINAL) sub
        WHERE condition_id IN (
            SELECT condition_id FROM markets WHERE question LIKE 'Will %%'
        )
        GROUP BY condition_id
        """,
    )
    print(f"  Will markets with trades: {len(first_ts):,}")

    # Step 2: Create a temp table with first_ts for efficient join
    # Instead, we'll use a different approach: compute everything in one query
    # using the first_ts as a subquery in a window function
    print("\nStep 2: Computing early volume features...")
    early_features = await query_ch(
        client,
        """
        SELECT
            sub.condition_id,
            countIf(sub.secs_since_first <= 3600) AS trades_1h,
            sumIf(sub.price * sub.sz, sub.secs_since_first <= 3600) AS vol_1h,
            uniqIf(sub.maker, sub.secs_since_first <= 3600) AS traders_1h,
            countIf(sub.secs_since_first <= 21600) AS trades_6h,
            sumIf(sub.price * sub.sz, sub.secs_since_first <= 21600) AS vol_6h,
            uniqIf(sub.maker, sub.secs_since_first <= 21600) AS traders_6h,
            countIf(sub.secs_since_first <= 86400) AS trades_24h,
            sumIf(sub.price * sub.sz, sub.secs_since_first <= 86400) AS vol_24h,
            uniqIf(sub.maker, sub.secs_since_first <= 86400) AS traders_24h,
            count() AS total_trades,
            sum(sub.price * sub.sz) AS total_volume,
            uniq(sub.maker) AS total_traders,
            dateDiff('hour', min(sub.timestamp), max(sub.timestamp)) AS lifespan_hours
        FROM (
            SELECT
                t.condition_id,
                t.timestamp,
                CAST(t.price AS Float64) AS price,
                CAST(t.size AS Float64) AS sz,
                t.maker,
                dateDiff('second', ft.first_ts, t.timestamp) AS secs_since_first
            FROM (SELECT * FROM polymarket.trades_raw FINAL) t
            INNER JOIN (
                SELECT condition_id, min(timestamp) AS first_ts
                FROM (SELECT condition_id, timestamp FROM polymarket.trades_raw FINAL) s1
                WHERE condition_id IN (
                    SELECT condition_id FROM markets WHERE question LIKE 'Will %%'
                )
                GROUP BY condition_id
            ) ft ON t.condition_id = ft.condition_id
        ) sub
        GROUP BY sub.condition_id
        HAVING total_trades >= 5
        """,
    )
    print(f"  Markets analyzed: {len(early_features):,}")

    # Cast all numeric columns
    for col in early_features.columns:
        if col != "condition_id":
            early_features = early_features.with_columns(
                pl.col(col).cast(pl.Float64)
            )

    # Classify final volume
    early_features = early_features.with_columns(
        pl.when(pl.col("total_volume") < 1000)
        .then(pl.lit("thin_<1K"))
        .when(pl.col("total_volume") < 10000)
        .then(pl.lit("med_1K-10K"))
        .when(pl.col("total_volume") < 100000)
        .then(pl.lit("thick_10K-100K"))
        .otherwise(pl.lit("heavy_>100K"))
        .alias("final_bucket")
    )

    # =========================================================================
    # Q1: Early volume vs final volume
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q1: Early volume vs final volume (MEDIAN)")
    print(f"{'='*85}")
    hdr = (
        f"  {'Final Bucket':<18} {'Count':>7} {'Vol@1h':>9} "
        f"{'Vol@6h':>9} {'Vol@24h':>10} {'Trdrs@1h':>9} {'Life(h)':>8}"
    )
    print(hdr)
    print(f"  {'-'*76}")

    for bucket in ["thin_<1K", "med_1K-10K", "thick_10K-100K", "heavy_>100K"]:
        b = early_features.filter(pl.col("final_bucket") == bucket)
        n = len(b)
        if n < 5:
            continue
        print(
            f"  {bucket:<18} {n:>7,} "
            f"${b['vol_1h'].median():>8,.0f} "
            f"${b['vol_6h'].median():>8,.0f} "
            f"${b['vol_24h'].median():>9,.0f} "
            f"{b['traders_1h'].median():>9,.0f} "
            f"{b['lifespan_hours'].median():>8,.0f}"
        )

    # =========================================================================
    # Q2: P(thin) given early trade count
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q2: P(thin <$1K final) given early trade count")
    print(f"{'='*85}")

    for window, tc_col in [
        ("1h", "trades_1h"),
        ("6h", "trades_6h"),
        ("24h", "trades_24h"),
    ]:
        print(f"\n  Window: {window}")
        print(
            f"  {'Early Trades':<20} {'Markets':>8} "
            f"{'P(thin)':>8} {'P(med)':>8} {'P(thick+)':>9}"
        )
        print(f"  {'-'*58}")
        for lo, hi, label in [
            (0, 3, "0-2"),
            (3, 10, "3-9"),
            (10, 30, "10-29"),
            (30, 100, "30-99"),
            (100, 500, "100-499"),
            (500, 1e9, "500+"),
        ]:
            subset = early_features.filter(
                (pl.col(tc_col) >= lo) & (pl.col(tc_col) < hi)
            )
            if len(subset) < 10:
                continue
            n = len(subset)
            p_thin = (
                subset.filter(pl.col("final_bucket") == "thin_<1K").shape[0] / n
            )
            p_med = (
                subset.filter(pl.col("final_bucket") == "med_1K-10K").shape[0]
                / n
            )
            p_thick = 1 - p_thin - p_med
            print(
                f"  {label:<20} {n:>8,} "
                f"{p_thin:>7.1%} {p_med:>7.1%} {p_thick:>8.1%}"
            )

    # =========================================================================
    # Q3: Distinct traders as predictor
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q3: P(thin) given early distinct traders")
    print(f"{'='*85}")

    for window, col in [("1h", "traders_1h"), ("6h", "traders_6h")]:
        print(f"\n  Window: {window}")
        print(
            f"  {'Traders':<20} {'Markets':>8} "
            f"{'P(thin)':>8} {'Med Final Vol':>14}"
        )
        print(f"  {'-'*55}")
        for lo, hi, label in [
            (0, 2, "0-1"),
            (2, 4, "2-3"),
            (4, 8, "4-7"),
            (8, 15, "8-14"),
            (15, 30, "15-29"),
            (30, 1e9, "30+"),
        ]:
            subset = early_features.filter(
                (pl.col(col) >= lo) & (pl.col(col) < hi)
            )
            if len(subset) < 10:
                continue
            n = len(subset)
            p_thin = (
                subset.filter(pl.col("final_bucket") == "thin_<1K").shape[0] / n
            )
            med_final = subset["total_volume"].median()
            print(
                f"  {label:<20} {n:>8,} "
                f"{p_thin:>7.1%} ${med_final:>12,.0f}"
            )

    # =========================================================================
    # Q4: Volume at 1h as predictor
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q4: Volume at 1h as predictor")
    print(f"{'='*85}")
    print(f"  {'Vol@1h':<20} {'Markets':>8} {'P(thin)':>8} {'Median Final':>14}")
    print(f"  {'-'*55}")
    for lo, hi, label in [
        (0, 1, "$0"),
        (1, 10, "$1-10"),
        (10, 50, "$10-50"),
        (50, 200, "$50-200"),
        (200, 1000, "$200-1K"),
        (1000, 1e9, ">$1K"),
    ]:
        subset = early_features.filter(
            (pl.col("vol_1h") >= lo) & (pl.col("vol_1h") < hi)
        )
        if len(subset) < 10:
            continue
        n = len(subset)
        p_thin = (
            subset.filter(pl.col("final_bucket") == "thin_<1K").shape[0] / n
        )
        med_final = subset["total_volume"].median()
        print(f"  {label:<20} {n:>8,} {p_thin:>7.1%} ${med_final:>12,.0f}")

    # =========================================================================
    # Q5: Lifespan as predictor
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q5: Market lifespan as predictor")
    print(f"{'='*85}")
    print(f"  {'Lifespan':<20} {'Markets':>8} {'P(thin)':>8}")
    print(f"  {'-'*40}")
    for lo, hi, label in [
        (0, 1, "<1h"),
        (1, 6, "1-6h"),
        (6, 24, "6-24h"),
        (24, 72, "1-3d"),
        (72, 168, "3-7d"),
        (168, 720, "1-4wk"),
        (720, 1e9, ">4wk"),
    ]:
        subset = early_features.filter(
            (pl.col("lifespan_hours") >= lo) & (pl.col("lifespan_hours") < hi)
        )
        if len(subset) < 10:
            continue
        n = len(subset)
        p_thin = (
            subset.filter(pl.col("final_bucket") == "thin_<1K").shape[0] / n
        )
        print(f"  {label:<20} {n:>8,} {p_thin:>7.1%}")

    # =========================================================================
    # Q6: Combined predictor thresholds
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q6: Combined predictor thresholds")
    print(f"{'='*85}")

    combos = [
        (
            "vol_1h<$50 & traders_1h<=3",
            (pl.col("vol_1h") < 50) & (pl.col("traders_1h") <= 3),
        ),
        (
            "vol_1h<$100 & traders_1h<=5",
            (pl.col("vol_1h") < 100) & (pl.col("traders_1h") <= 5),
        ),
        (
            "trades_1h<10 & traders_1h<=3",
            (pl.col("trades_1h") < 10) & (pl.col("traders_1h") <= 3),
        ),
        (
            "vol_6h<$200 & traders_6h<=5",
            (pl.col("vol_6h") < 200) & (pl.col("traders_6h") <= 5),
        ),
        ("vol_24h<$500", pl.col("vol_24h") < 500),
        (
            "vol_1h<$50 & life<72h",
            (pl.col("vol_1h") < 50) & (pl.col("lifespan_hours") < 72),
        ),
    ]

    print(
        f"  {'Criteria':<40} {'Match':>8} "
        f"{'P(thin)':>8} {'P(med)':>8} {'P(thick+)':>9}"
    )
    print(f"  {'-'*78}")

    for label, cond in combos:
        match = early_features.filter(cond)
        n = len(match)
        if n < 10:
            continue
        p_thin = (
            match.filter(pl.col("final_bucket") == "thin_<1K").shape[0] / n
        )
        p_med = (
            match.filter(pl.col("final_bucket") == "med_1K-10K").shape[0] / n
        )
        p_thick = 1 - p_thin - p_med
        print(
            f"  {label:<40} {n:>8,} "
            f"{p_thin:>7.1%} {p_med:>7.1%} {p_thick:>8.1%}"
        )

    # =========================================================================
    # Q7: Event siblings
    # =========================================================================
    print(f"\n{'='*85}")
    print("  Q7: Event siblings (same event = similar volume?)")
    print(f"{'='*85}")
    markets_df = await query_ch(
        client,
        "SELECT condition_id, event_id FROM markets WHERE question LIKE 'Will %%'",
    )
    ef_with_event = early_features.join(
        markets_df, on="condition_id", how="left"
    )

    event_stats = (
        ef_with_event.group_by("event_id")
        .agg(
            [
                pl.len().alias("n_markets"),
                pl.col("total_volume").std().alias("vol_std"),
                pl.col("total_volume").mean().alias("vol_mean"),
                pl.col("final_bucket").n_unique().alias("n_distinct_buckets"),
            ]
        )
        .filter(pl.col("n_markets") >= 2)
    )

    same_bucket = event_stats.filter(
        pl.col("n_distinct_buckets") == 1
    ).shape[0]
    total_events = len(event_stats)
    print(f"  Events with 2+ Will markets: {total_events:,}")
    print(
        f"  Events where ALL markets same vol bucket: "
        f"{same_bucket}/{total_events} ({same_bucket / total_events:.1%})"
    )

    cv = event_stats.with_columns(
        (pl.col("vol_std") / pl.col("vol_mean").clip(1.0)).alias("cv")
    )
    print(
        f"  Intra-event volume CV: "
        f"median={cv['cv'].median():.2f}, "
        f"p25={cv['cv'].quantile(0.25):.2f}, "
        f"p75={cv['cv'].quantile(0.75):.2f}"
    )

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
