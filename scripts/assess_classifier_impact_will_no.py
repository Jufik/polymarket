"""Assess market size classifier impact on the Will-NO strategy.

Approach: Use the already-trained classifier to predict buckets for all
Will markets, generate Will-NO signals, join with resolution, and
compute PnL per bucket to quantify the classifier's value as a filter.

Usage:
    uv run python scripts/assess_classifier_impact_will_no.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.market_size.classifier import MarketSizeClassifier
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig
from polymarket_pipeline.strategies_impl.market_size.features import bucket_from_volume

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


def _fmt(t0: float) -> str:
    e = time.time() - t0
    return f"{e:.1f}s" if e < 60 else f"{e / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    t0 = time.time()
    print(f"  [{label}] Executing...", flush=True)
    async with client.stream(
        "POST", "/", content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB}, headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            print(f"  CH Error: {body}")
            resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        last_report = t0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            now = time.time()
            if now - last_report >= 5.0:
                print(f"  [{label}] {total / 1e6:.1f} MB ({_fmt(t0)})", flush=True)
                last_report = now
    data = b"".join(chunks)
    if not data.strip():
        return pl.DataFrame()
    df = pl.read_csv(io.BytesIO(data), has_header=True, skip_rows_after_header=1)
    print(f"  [{label}] {len(df):,} rows ({_fmt(t0)})", flush=True)
    return df


def compute_pnl_df(signals: pl.DataFrame, resolution: pl.DataFrame) -> pl.DataFrame:
    """Join signals with resolution and compute per-signal PnL."""
    joined = signals.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )
    resolved = joined.filter(pl.col("resolution_value") == 1)
    if resolved.is_empty():
        return pl.DataFrame()

    resolved = resolved.with_columns(
        pl.when(
            (pl.col("outcome") == "NO") & (pl.col("winner_outcome") == "No")
        ).then(True)
        .when(
            (pl.col("outcome") == "YES") & (pl.col("winner_outcome") == "Yes")
        ).then(True)
        .otherwise(False)
        .alias("won"),
    )
    resolved = resolved.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.when(pl.col("outcome") == "NO")
            .then(pl.col("size_usd") * pl.col("entry_price") / (1.0 - pl.col("entry_price")))
            .otherwise(pl.col("size_usd") * (1.0 - pl.col("entry_price")) / pl.col("entry_price"))
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )
    return resolved


def summarize(label: str, pnl_df: pl.DataFrame) -> dict[str, float]:
    n = len(pnl_df)
    if n == 0:
        print(f"  {label}: no resolved signals")
        return {}
    wins = pnl_df.filter(pl.col("won")).shape[0]
    hr = wins / n
    total_pnl = pnl_df["pnl"].sum()
    total_bet = pnl_df["size_usd"].sum()
    roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
    avg_pnl = total_pnl / n
    print(
        f"  {label:<25} {n:>6,} signals  {hr:>6.1%} HR  "
        f"PnL ${total_pnl:>+10,.0f}  ROI {roi:>+6.1f}%  "
        f"avg ${avg_pnl:>+6.2f}/bet"
    )
    return {"n": n, "wins": wins, "hr": hr, "pnl": total_pnl, "roi": roi}


async def main() -> None:
    t_start = time.time()
    cfg_ms = MarketSizeConfig()
    model_path = "models/market_size_xgb.joblib"

    if not Path(model_path).exists():
        print(f"ERROR: Model not found at {model_path}.")
        return

    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)
    window_secs = cfg_ms.feature_window_hours * 3600
    early_secs = window_secs // 6
    mid_secs = window_secs // 2

    # ==================================================================
    # Step 1: Server-side aggregation for ALL Will markets (same query as validate script)
    # ==================================================================
    print("=" * 70)
    print("Step 1/5: Server-side trade aggregation for Will markets")
    print("=" * 70)

    features_raw = await query_ch(
        client,
        f"""
        WITH
            will_markets AS (
                SELECT condition_id
                FROM markets
                WHERE question LIKE 'Will %%'
            ),
            trades AS (
                SELECT condition_id, maker,
                       CAST(price AS Float64) AS price,
                       CAST(size AS Float64) AS size,
                       toUnixTimestamp(timestamp) AS ts
                FROM (SELECT * FROM polymarket.trades_raw FINAL) t
                WHERE condition_id IN (SELECT condition_id FROM will_markets)
            ),
            first_ts AS (
                SELECT condition_id, min(ts) AS first_ts
                FROM trades
                GROUP BY condition_id
            ),
            window_agg AS (
                SELECT
                    t.condition_id AS condition_id,
                    count() AS trades_window,
                    sum(t.price * t.size) AS vol_window,
                    uniq(t.maker) AS traders_window,
                    countIf((t.ts - f.first_ts) <= {early_secs}) AS trades_early,
                    sumIf(t.price * t.size, (t.ts - f.first_ts) <= {early_secs}) AS vol_early,
                    uniqIf(t.maker, (t.ts - f.first_ts) <= {early_secs}) AS traders_early,
                    countIf((t.ts - f.first_ts) <= {mid_secs}) AS trades_mid,
                    sumIf(t.price * t.size, (t.ts - f.first_ts) <= {mid_secs}) AS vol_mid,
                    uniqIf(t.maker, (t.ts - f.first_ts) <= {mid_secs}) AS traders_mid,
                    stddevPop(t.price) AS price_std,
                    max(t.price) - min(t.price) AS price_range,
                    avg(t.size) AS avg_trade_size,
                    max(t.size) AS max_trade_size
                FROM trades t
                JOIN first_ts f ON t.condition_id = f.condition_id
                WHERE (t.ts - f.first_ts) <= {window_secs}
                GROUP BY t.condition_id
            ),
            total_agg AS (
                SELECT condition_id,
                       sum(price * size) AS total_volume,
                       count() AS total_trades
                FROM trades
                GROUP BY condition_id
            )
        SELECT
            w.condition_id AS condition_id,
            w.trades_window, w.vol_window, w.traders_window,
            w.trades_early, w.vol_early, w.traders_early,
            w.trades_mid, w.vol_mid, w.traders_mid,
            w.price_std, w.price_range, w.avg_trade_size, w.max_trade_size,
            ta.total_volume, ta.total_trades,
            f.first_ts
        FROM window_agg w
        JOIN total_agg ta ON w.condition_id = ta.condition_id
        JOIN first_ts f ON w.condition_id = f.condition_id
        """,
        label="trades-agg",
    )

    for col in features_raw.columns:
        if col != "condition_id":
            features_raw = features_raw.with_columns(pl.col(col).cast(pl.Float64))

    print(f"  Markets with features: {len(features_raw):,}")

    # ==================================================================
    # Step 2: Metadata
    # ==================================================================
    print(f"\nStep 2/5: Metadata")

    markets = await query_ch(
        client,
        "SELECT condition_id, toString(event_id) AS event_id, neg_risk, question FROM markets WHERE question LIKE 'Will %%'",
        label="markets",
    )
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

    events = await query_ch(
        client,
        """SELECT toString(id) AS id,
                  toUnixTimestamp(end_date) AS end_date,
                  CAST(volume AS Float64) AS volume,
                  CAST(liquidity AS Float64) AS liquidity
           FROM events WHERE end_date IS NOT NULL""",
        label="events",
    )
    for c in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(c).cast(pl.Float64))

    tags = await query_ch(
        client,
        "SELECT toString(et.event_id) AS event_id, t.label AS tag FROM event_tags et JOIN tags t ON et.tag_id = t.id",
        label="tags",
    )
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")

    # Get first trade price per market for Will-NO signal eligibility
    first_prices = await query_ch(
        client,
        """
        WITH will_markets AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM will_markets)
        GROUP BY condition_id
        """,
        label="first-prices",
    )
    first_prices = first_prices.with_columns(pl.col("first_price").cast(pl.Float64))

    await client.aclose()

    # ==================================================================
    # Step 3: Build feature matrix + classify
    # ==================================================================
    print(f"\nStep 3/5: Build features & classify")

    # Join markets metadata
    features = features_raw.join(
        markets.select("condition_id", "event_id", "neg_risk", "question"),
        on="condition_id", how="left",
    )

    event_market_count = markets.group_by("event_id").agg(pl.len().alias("event_n_markets"))
    features = features.join(
        events.select(
            pl.col("id").alias("event_id"),
            pl.col("end_date").alias("event_end_date"),
            pl.col("volume").alias("event_volume"),
            pl.col("liquidity").alias("event_liquidity"),
        ),
        on="event_id", how="left",
    )
    features = features.join(event_market_count, on="event_id", how="left")

    # Derived features
    features = features.with_columns(
        (pl.col("vol_window") / pl.col("trades_window").clip(1)).alias("vol_per_trade"),
        (pl.col("vol_window") / pl.col("traders_window").clip(1)).alias("vol_per_trader"),
        (pl.col("vol_mid") / pl.col("vol_early").clip(lower_bound=0.01)).alias("vol_growth_early_mid"),
        (pl.col("vol_window") / pl.col("vol_mid").clip(lower_bound=0.01)).alias("vol_growth_mid_full"),
        (pl.col("vol_window") + 1).log().alias("log_vol_window"),
        (pl.col("vol_early") + 1).log().alias("log_vol_early"),
        (pl.col("vol_mid") + 1).log().alias("log_vol_mid"),
        (pl.col("event_volume").fill_null(0) + 1).log().alias("log_event_volume"),
        (pl.col("event_liquidity").fill_null(0) + 1).log().alias("log_event_liquidity"),
        (pl.col("trades_window") / (window_secs / 3600)).alias("trades_per_hour"),
        (pl.col("traders_window").cast(pl.Float64) / pl.col("trades_window").clip(1)).alias("trader_concentration"),
        (pl.col("vol_window") / pl.col("event_liquidity").fill_null(1).clip(lower_bound=1.0)).alias("vol_to_liquidity"),
    )
    features = features.with_columns(
        ((pl.col("event_end_date") - pl.col("first_ts")) / 3600.0).alias("time_remaining_hours"),
    )
    features = features.with_columns(
        (pl.col("first_ts") % 86400 / 3600).cast(pl.Int32).alias("hour_of_day"),
        (pl.col("first_ts") / 86400).cast(pl.Int32).mod(7).alias("day_of_week"),
    )
    features = features.with_columns(pl.col("neg_risk").cast(pl.Int8).alias("neg_risk"))

    for tag_name in cfg_ms.top_tags:
        col_name = f"tag_{tag_name.replace(' ', '_')}"
        matching = tags.filter(pl.col("tag") == tag_name)["event_id"].to_list()
        features = features.with_columns(
            pl.col("event_id").is_in(matching).cast(pl.Int8).alias(col_name),
        )

    # Filter (same as validate)
    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)
    print(f"  Feature matrix: {features.shape}")

    # Classify
    clf = MarketSizeClassifier(cfg_ms)
    clf.load(model_path)

    questions = features["question"].fill_null("").to_list()
    predict_cols = [c for c in features.columns if c not in (
        "question", "event_end_date", "event_volume", "event_liquidity",
        "first_ts", "event_id", "total_volume", "total_trades",
    )]
    predict_df = features.select(predict_cols)
    predicted_buckets = clf.predict(predict_df, questions=questions)
    features = features.with_columns(pl.Series("predicted_bucket", predicted_buckets))

    # Actual buckets from total_volume
    actual_labels = [
        bucket_from_volume(v, cfg_ms.bucket_thresholds, cfg_ms.buckets)
        for v in features["total_volume"].fill_null(0).to_list()
    ]
    features = features.with_columns(pl.Series("actual_bucket", actual_labels))

    # ==================================================================
    # Step 4: Generate Will-NO signals (vectorized in Polars)
    # ==================================================================
    print(f"\nStep 4/5: Generate Will-NO signals")

    # Will-NO eligibility: question starts with "Will", first trade price 0.15-0.40
    # Avoid keywords: reach, hit
    eligible = features.join(first_prices, on="condition_id", how="left")
    eligible = eligible.filter(
        (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )
    # Avoid keywords
    eligible = eligible.filter(
        ~pl.col("question").fill_null("").str.to_lowercase().str.contains("reach")
    )
    eligible = eligible.filter(
        ~pl.col("question").fill_null("").str.to_lowercase().str.contains("hit")
    )

    print(f"  Eligible Will-NO markets: {len(eligible):,}")

    # Build signal DataFrame matching PnL format
    signals = eligible.select(
        pl.col("condition_id"),
        pl.lit("BUY").alias("side"),
        pl.lit("NO").alias("outcome"),
        pl.lit(50.0).alias("size_usd"),
        pl.col("first_price").alias("entry_price"),
        pl.col("predicted_bucket"),
        pl.col("actual_bucket"),
        pl.col("total_volume"),
        pl.col("question"),
    )

    # ==================================================================
    # Step 5: PnL analysis
    # ==================================================================
    print(f"\nStep 5/5: PnL analysis")

    resolution = pl.read_parquet("data/metadata/markets.parquet")
    pnl_all = compute_pnl_df(signals, resolution)

    if pnl_all.is_empty():
        print("  No resolved signals.")
        return

    print(f"\n{'=' * 70}")
    print("  WILL-NO STRATEGY × MARKET SIZE CLASSIFIER IMPACT")
    print(f"{'=' * 70}")

    # --- A. Baseline ---
    print(f"\n--- A. Baseline (no market size filter) ---")
    summarize("ALL", pnl_all)

    # --- B. PnL by ACTUAL bucket ---
    print(f"\n--- B. PnL by ACTUAL volume bucket ---")
    print(f"  (Where does the strategy's edge actually live?)")
    for bucket in cfg_ms.buckets:
        subset = pnl_all.filter(pl.col("actual_bucket") == bucket)
        summarize(f"  actual={bucket}", subset)

    # --- C. PnL by PREDICTED bucket ---
    print(f"\n--- C. PnL by PREDICTED bucket ---")
    print(f"  (What the 80.9%-accurate classifier sees)")
    for bucket in cfg_ms.buckets:
        subset = pnl_all.filter(pl.col("predicted_bucket") == bucket)
        summarize(f"  predicted={bucket}", subset)

    # --- D. Cumulative max_bucket scenarios ---
    print(f"\n--- D. max_bucket filter scenarios (cumulative) ---")
    print(f"  (Using predicted bucket as the live gate)")
    allowed = list(cfg_ms.buckets)
    for i in range(len(allowed)):
        cutoff = allowed[i]
        included = set(allowed[: i + 1])
        subset = pnl_all.filter(pl.col("predicted_bucket").is_in(included))
        summarize(f"  max_bucket={cutoff}", subset)

    # --- E. Classifier accuracy on signal markets ---
    print(f"\n--- E. Classifier accuracy on strategy signals ---")
    has_both = pnl_all.filter(
        pl.col("actual_bucket").is_not_null() & pl.col("predicted_bucket").is_not_null()
    )
    n_total = len(has_both)
    if n_total > 0:
        n_correct = has_both.filter(pl.col("actual_bucket") == pl.col("predicted_bucket")).shape[0]
        print(f"  Exact:    {n_correct:,} / {n_total:,} ({n_correct / n_total:.1%})")

        bucket_idx = {b: i for i, b in enumerate(cfg_ms.buckets)}
        actual_idx = [bucket_idx.get(b, -1) for b in has_both["actual_bucket"].to_list()]
        pred_idx = [bucket_idx.get(b, -1) for b in has_both["predicted_bucket"].to_list()]
        n_adj = sum(1 for a, p in zip(actual_idx, pred_idx) if abs(a - p) <= 1)
        print(f"  ±1 bucket: {n_adj:,} / {n_total:,} ({n_adj / n_total:.1%})")

    # --- F. Misclassification impact ---
    print(f"\n--- F. Misclassification impact on PnL ---")
    correctly = pnl_all.filter(pl.col("actual_bucket") == pl.col("predicted_bucket"))
    misclassified = pnl_all.filter(pl.col("actual_bucket") != pl.col("predicted_bucket"))
    summarize("  correctly classified", correctly)
    summarize("  misclassified", misclassified)

    bucket_order = {b: i for i, b in enumerate(cfg_ms.buckets)}
    pnl_rows = pnl_all.to_dicts()
    under = pl.DataFrame([
        r for r in pnl_rows
        if bucket_order.get(r.get("predicted_bucket"), 0) < bucket_order.get(r.get("actual_bucket"), 0)
    ])
    over = pl.DataFrame([
        r for r in pnl_rows
        if bucket_order.get(r.get("predicted_bucket"), 0) > bucket_order.get(r.get("actual_bucket"), 0)
    ])
    if not under.is_empty():
        summarize("  under-classified", under)
    if not over.is_empty():
        summarize("  over-classified", over)

    # --- G. Recommendation ---
    print(f"\n--- G. Optimal max_bucket recommendation ---")
    best_roi = -999.0
    best_cutoff = None
    best_pnl = 0.0
    for i in range(len(allowed)):
        cutoff = allowed[i]
        included = set(allowed[: i + 1])
        subset = pnl_all.filter(pl.col("predicted_bucket").is_in(included))
        n = len(subset)
        if n < 10:
            continue
        pnl = subset["pnl"].sum()
        bet = subset["size_usd"].sum()
        roi = pnl / bet * 100 if bet > 0 else 0
        if roi > best_roi:
            best_roi = roi
            best_cutoff = cutoff
            best_pnl = pnl

    if best_cutoff:
        print(f"  Best max_bucket: '{best_cutoff}'")
        print(f"  ROI: {best_roi:+.1f}%, PnL: ${best_pnl:+,.0f}")
        allowed_set = set(allowed[: allowed.index(best_cutoff) + 1])
        kept = pnl_all.filter(pl.col("predicted_bucket").is_in(allowed_set))
        dropped = pnl_all.filter(~pl.col("predicted_bucket").is_in(allowed_set))
        if not dropped.is_empty():
            dropped_pnl = dropped["pnl"].sum()
            dropped_wins = dropped.filter(pl.col("won")).shape[0]
            dropped_losses = dropped.filter(~pl.col("won")).shape[0]
            print(
                f"  Dropped: {len(dropped):,} signals "
                f"(PnL ${dropped_pnl:+,.0f}, {dropped_wins} wins, {dropped_losses} losses)"
            )
            baseline_pnl = pnl_all["pnl"].sum()
            print(f"  Baseline PnL: ${baseline_pnl:+,.0f} → Filtered PnL: ${best_pnl:+,.0f}")
            delta = best_pnl - baseline_pnl
            print(f"  Delta: ${delta:+,.0f} ({delta / abs(baseline_pnl) * 100:+.1f}%)")

    print(f"\n  Analysis complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
