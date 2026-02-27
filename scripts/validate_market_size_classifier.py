"""End-to-end validation of the market size classifier against ClickHouse data.

Trains on 80% of data (temporal split), evaluates on 20%, then shows
feature importances.

Pushes aggregation to ClickHouse — transfers ~20K rows instead of ~20M raw trades.

Usage:
    uv run python scripts/validate_market_size_classifier.py [--window 6] [--model models/market_size_xgb.joblib]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import polars as pl

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.market_size.classifier import MarketSizeClassifier
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig
from polymarket_pipeline.strategies_impl.market_size.features import bucket_from_volume

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


def _fmt_elapsed(t0: float) -> str:
    elapsed = time.time() - t0
    if elapsed < 60:
        return f"{elapsed:.1f}s"
    return f"{elapsed / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    """Execute a ClickHouse query with streaming download and progress."""
    t0 = time.time()
    print(f"  [{label}] Executing...", flush=True)

    async with client.stream(
        "POST",
        "/",
        content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            print(f"  CH Error: {body}")
            resp.raise_for_status()

        chunks: list[bytes] = []
        total_bytes = 0
        last_report = t0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            total_bytes += len(chunk)
            now = time.time()
            if now - last_report >= 5.0:
                print(
                    f"  [{label}] {total_bytes / 1_000_000:.1f} MB ({_fmt_elapsed(t0)})",
                    flush=True,
                )
                last_report = now

    data = b"".join(chunks)
    if not data.strip():
        print(f"  [{label}] Empty result ({_fmt_elapsed(t0)})", flush=True)
        return pl.DataFrame()

    df = pl.read_csv(io.BytesIO(data), has_header=True, skip_rows_after_header=1)
    print(
        f"  [{label}] {len(df):,} rows, {total_bytes / 1_000_000:.1f} MB "
        f"({_fmt_elapsed(t0)})",
        flush=True,
    )
    return df


async def main() -> None:
    parser = argparse.ArgumentParser(description="Validate market size classifier")
    parser.add_argument("--window", type=int, default=6, help="Feature window in hours")
    parser.add_argument("--model", default=None, help="Path to pre-trained model (skip training)")
    args = parser.parse_args()

    t_start = time.time()
    cfg = MarketSizeConfig(feature_window_hours=args.window)
    window_secs = args.window * 3600
    early_secs = window_secs // 6
    mid_secs = window_secs // 2
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    # ---------------------------------------------------------------
    # Step 1: Server-side aggregation — multi-window conditional agg
    # ---------------------------------------------------------------
    print("Step 1/4: Aggregating trades server-side in ClickHouse...")
    print(f"  Windows: early={early_secs}s, mid={mid_secs}s, full={window_secs}s")
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
                    -- Price microstructure
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
                SELECT
                    condition_id,
                    sum(price * size) AS total_volume,
                    count() AS total_trades
                FROM trades
                GROUP BY condition_id
            )
        SELECT
            w.condition_id AS condition_id,
            w.trades_window AS trades_window,
            w.vol_window AS vol_window,
            w.traders_window AS traders_window,
            w.trades_early AS trades_early,
            w.vol_early AS vol_early,
            w.traders_early AS traders_early,
            w.trades_mid AS trades_mid,
            w.vol_mid AS vol_mid,
            w.traders_mid AS traders_mid,
            w.price_std AS price_std,
            w.price_range AS price_range,
            w.avg_trade_size AS avg_trade_size,
            w.max_trade_size AS max_trade_size,
            ta.total_volume AS total_volume,
            ta.total_trades AS total_trades,
            f.first_ts AS first_ts
        FROM window_agg w
        JOIN total_agg ta ON w.condition_id = ta.condition_id
        JOIN first_ts f ON w.condition_id = f.condition_id
        """,
        label="trades-agg",
    )

    # Cast to float
    for col in ["trades_window", "vol_window", "traders_window",
                 "trades_early", "vol_early", "traders_early",
                 "trades_mid", "vol_mid", "traders_mid",
                 "price_std", "price_range", "avg_trade_size", "max_trade_size",
                 "total_volume", "total_trades", "first_ts"]:
        features_raw = features_raw.with_columns(pl.col(col).cast(pl.Float64))

    # ---------------------------------------------------------------
    # Step 2: Fetch metadata
    # ---------------------------------------------------------------
    print("\nStep 2/4: Fetching metadata...")
    markets = await query_ch(
        client,
        "SELECT condition_id, toString(event_id) AS event_id, neg_risk, question FROM markets WHERE question LIKE 'Will %%'",
        label="markets",
    )
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

    events = await query_ch(
        client,
        """
        SELECT toString(id) AS id,
               dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_date,
               CAST(volume AS Float64) AS volume,
               CAST(liquidity AS Float64) AS liquidity
        FROM events
        WHERE end_date IS NOT NULL
        """,
        label="events",
    )
    for col in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(col).cast(pl.Float64))

    tags = await query_ch(
        client,
        "SELECT toString(et.event_id) AS event_id, t.label AS tag FROM event_tags et JOIN tags t ON et.tag_id = t.id",
        label="tags",
    )
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")

    await client.aclose()
    print(f"\n  Data fetch complete ({_fmt_elapsed(t_start)})")

    # ---------------------------------------------------------------
    # Step 3: Build feature matrix
    # ---------------------------------------------------------------
    print("\nStep 3/4: Building feature matrix...")
    t_feat = time.time()

    # Join markets for event_id + neg_risk + question
    features = features_raw.join(
        markets.select("condition_id", "event_id", "neg_risk", "question"),
        on="condition_id",
        how="left",
    )

    # Event-level features
    event_market_count = markets.group_by("event_id").agg(
        pl.len().alias("event_n_markets")
    )
    features = features.join(
        events.select(
            pl.col("id").alias("event_id"),
            pl.col("end_date").alias("event_end_date"),
            pl.col("volume").alias("event_volume"),
            pl.col("liquidity").alias("event_liquidity"),
        ),
        on="event_id",
        how="left",
    )
    features = features.join(event_market_count, on="event_id", how="left")

    # Derived ratio features
    features = features.with_columns(
        (pl.col("vol_window") / pl.col("trades_window").clip(1)).alias("vol_per_trade"),
        (pl.col("vol_window") / pl.col("traders_window").clip(1)).alias("vol_per_trader"),
        (pl.col("vol_mid") / pl.col("vol_early").clip(lower_bound=0.01)).alias(
            "vol_growth_early_mid"
        ),
        (pl.col("vol_window") / pl.col("vol_mid").clip(lower_bound=0.01)).alias(
            "vol_growth_mid_full"
        ),
        # Log-scale volume features
        (pl.col("vol_window") + 1).log().alias("log_vol_window"),
        (pl.col("vol_early") + 1).log().alias("log_vol_early"),
        (pl.col("vol_mid") + 1).log().alias("log_vol_mid"),
        (pl.col("event_volume").fill_null(0) + 1).log().alias("log_event_volume"),
        (pl.col("event_liquidity").fill_null(0) + 1).log().alias("log_event_liquidity"),
        # Velocity & concentration
        (pl.col("trades_window") / (window_secs / 3600)).alias("trades_per_hour"),
        (pl.col("traders_window").cast(pl.Float64) / pl.col("trades_window").clip(1)).alias(
            "trader_concentration"
        ),
        (pl.col("vol_window") / pl.col("event_liquidity").fill_null(1).clip(lower_bound=1.0)).alias(
            "vol_to_liquidity"
        ),
    )

    # Time remaining
    features = features.with_columns(
        ((pl.col("event_end_date") - pl.col("first_ts")) / 3600.0).alias(
            "time_remaining_hours"
        ),
    )

    # Temporal features
    features = features.with_columns(
        (pl.col("first_ts") % 86400 / 3600).cast(pl.Int32).alias("hour_of_day"),
        (pl.col("first_ts") / 86400).cast(pl.Int32).mod(7).alias("day_of_week"),
    )

    # neg_risk as int
    features = features.with_columns(
        pl.col("neg_risk").cast(pl.Int8).alias("neg_risk"),
    )

    # One-hot tags
    for tag_name in cfg.top_tags:
        col_name = f"tag_{tag_name.replace(' ', '_')}"
        matching_events = tags.filter(pl.col("tag") == tag_name)["event_id"].to_list()
        features = features.with_columns(
            pl.col("event_id").is_in(matching_events).cast(pl.Int8).alias(col_name),
        )

    print(f"  Feature matrix: {features.shape} ({time.time() - t_feat:.1f}s)")

    # Filter
    features = features.filter(pl.col("total_volume") >= 10)
    print(f"  After min volume filter: {len(features):,}")

    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)
    print(f"  After time_remaining filter: {len(features):,}")

    # Labels
    labels = [
        bucket_from_volume(v, cfg.bucket_thresholds, cfg.buckets)
        for v in features["total_volume"].to_list()
    ]
    features = features.with_columns(pl.Series("label", labels))

    print("\n  Label distribution:")
    for bucket in cfg.buckets:
        n = sum(1 for label in labels if label == bucket)
        print(f"    {bucket:<10} {n:>6,} ({n / len(labels):>6.1%})")

    # ---------------------------------------------------------------
    # Step 4: Train/evaluate or load model
    # ---------------------------------------------------------------
    # Select model columns
    model_cols = [
        "condition_id", "trades_window", "vol_window", "traders_window",
        "trades_early", "vol_early", "traders_early",
        "trades_mid", "vol_mid", "traders_mid",
        "vol_per_trade", "vol_per_trader",
        "vol_growth_early_mid", "vol_growth_mid_full",
        "price_std", "price_range", "avg_trade_size", "max_trade_size",
        "log_vol_window", "log_vol_early", "log_vol_mid",
        "log_event_volume", "log_event_liquidity",
        "trades_per_hour", "trader_concentration", "vol_to_liquidity",
        "time_remaining_hours", "event_n_markets",
        "neg_risk", "hour_of_day", "day_of_week", "total_volume",
    ] + [f"tag_{t.replace(' ', '_')}" for t in cfg.top_tags]
    existing = [c for c in model_cols if c in features.columns]
    model_df = features.select(existing + ["label"])

    # Extract question texts (aligned with model_df rows before sort)
    all_questions = features["question"].fill_null("").to_list()

    # Temporal split: sort by first_ts, train on older 80%, test on newer 20%
    sort_order = features["first_ts"].arg_sort().to_list()
    model_df = model_df[sort_order]
    all_questions = [all_questions[i] for i in sort_order]

    n = len(model_df)
    train_n = int(n * 0.8)
    train_df = model_df[:train_n]
    test_df = model_df[train_n:]
    train_questions = all_questions[:train_n]
    test_questions = all_questions[train_n:]

    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

    if args.model:
        print(f"\nStep 4/4: Loading pre-trained model from {args.model}...")
        clf = MarketSizeClassifier(cfg)
        clf.load(args.model)
    else:
        print(f"\nStep 4/4: Training on {len(train_df):,} samples, testing on {len(test_df):,}...")
        clf = MarketSizeClassifier(cfg)
        train_metrics = clf.train(train_df, questions=train_questions)
        print(f"  Train accuracy: {train_metrics['accuracy']:.3f}, RMSE: {train_metrics.get('rmse', 0):.3f}")

    # Evaluate on test set
    test_preds = clf.predict(
        test_df.drop("total_volume", "label"), questions=test_questions
    )
    test_labels = test_df["label"].to_list()

    print(f"\n  Test accuracy: {accuracy_score(test_labels, test_preds):.3f}")
    print(classification_report(test_labels, test_preds, zero_division=0))

    print("Confusion matrix:")
    cm = confusion_matrix(test_labels, test_preds, labels=list(cfg.buckets))
    header = "         " + "  ".join(f"{b:>8}" for b in cfg.buckets)
    print(header)
    for i, row in enumerate(cm):
        print(f"  {cfg.buckets[i]:<8}" + "  ".join(f"{v:>8,}" for v in row))

    # Feature importances (numeric features + text SVD components)
    importances = clf._model.feature_importances_
    n_numeric = len(clf._feature_names)
    names = list(clf._feature_names) + [f"text_svd_{i}" for i in range(len(importances) - n_numeric)]
    sorted_idx = np.argsort(importances)[::-1]
    print("\nTop 15 features:")
    for i in sorted_idx[:15]:
        print(f"  {names[i]:<30} {importances[i]:.4f}")

    print(f"\nValidation complete! Total time: {_fmt_elapsed(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
