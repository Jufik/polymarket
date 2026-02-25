"""Train the market size classifier from ClickHouse data.

Usage:
    uv run python scripts/train_market_size_classifier.py [--window 6] [--output models/market_size_xgb.joblib]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import polars as pl
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.market_size.classifier import MarketSizeClassifier
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig
from polymarket_pipeline.strategies_impl.market_size.features import (
    bucket_from_volume,
    compute_features_polars,
)

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
        print(f"CH Error: {resp.text[:500]}")
        resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return pl.DataFrame()
    rows = [json.loads(line) for line in text.split("\n") if line.strip()]
    return pl.DataFrame(rows) if rows else pl.DataFrame()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Train market size classifier")
    parser.add_argument("--window", type=int, default=6, help="Feature window in hours")
    parser.add_argument("--output", default="models/market_size_xgb.joblib")
    args = parser.parse_args()

    cfg = MarketSizeConfig(feature_window_hours=args.window)
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    # 1. Fetch trades for Will markets (condition_id, maker, price, size, timestamp)
    print("Step 1: Fetching trades for Will markets...")
    trades = await query_ch(
        client,
        """
        SELECT
            t.condition_id,
            t.maker,
            CAST(t.price AS Float64) AS price,
            CAST(t.size AS Float64) AS size,
            toUnixTimestamp(t.timestamp) AS timestamp
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE t.condition_id IN (
            SELECT condition_id FROM markets WHERE question LIKE 'Will %%'
        )
        """,
    )
    print(f"  Trades: {len(trades):,}")

    # Cast columns
    for col in ["price", "size", "timestamp"]:
        trades = trades.with_columns(pl.col(col).cast(pl.Float64))

    # 2. Fetch markets metadata
    print("Step 2: Fetching markets...")
    markets = await query_ch(
        client,
        """
        SELECT condition_id, event_id, neg_risk
        FROM markets
        WHERE question LIKE 'Will %%'
        """,
    )
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))
    print(f"  Markets: {len(markets):,}")

    # 3. Fetch events
    print("Step 3: Fetching events...")
    events = await query_ch(
        client,
        """
        SELECT
            id,
            toUnixTimestamp(end_date) AS end_date,
            CAST(volume AS Float64) AS volume,
            CAST(liquidity AS Float64) AS liquidity
        FROM events
        WHERE end_date IS NOT NULL AND end_date != '1970-01-01 00:00:00'
        """,
    )
    for col in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(col).cast(pl.Float64))
    print(f"  Events: {len(events):,}")

    # 4. Fetch tags (primary tag per event)
    print("Step 4: Fetching tags...")
    tags = await query_ch(
        client,
        """
        SELECT et.event_id, t.label AS tag
        FROM event_tags et
        JOIN tags t ON et.tag_id = t.id
        """,
    )
    # Keep one tag per event (first alphabetically for determinism)
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")
    print(f"  Tags: {len(tags):,}")

    await client.aclose()

    # 5. Compute features
    print(f"\nStep 5: Computing features (window={args.window}h)...")
    features = compute_features_polars(trades, markets, events, tags, cfg)
    print(f"  Feature matrix: {features.shape}")

    # 6. Filter to markets with enough trades (>=10 total_volume)
    features = features.filter(pl.col("total_volume") >= 10)
    print(f"  After min volume filter: {len(features):,}")

    # Drop rows with null time_remaining
    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)
    print(f"  After time_remaining filter: {len(features):,}")

    # 7. Label distribution
    labels = [
        bucket_from_volume(v, cfg.bucket_thresholds, cfg.buckets)
        for v in features["total_volume"].to_list()
    ]
    features = features.with_columns(pl.Series("label", labels))
    print("\n  Label distribution:")
    for bucket in cfg.buckets:
        n = sum(1 for label in labels if label == bucket)
        print(f"    {bucket:<10} {n:>6,} ({n / len(labels):>6.1%})")

    # 8. Train/test split
    print("\nStep 6: Training XGBoost classifier...")
    train_idx, test_idx = train_test_split(
        list(range(len(features))),
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    train_df = features[train_idx]
    test_df = features[test_idx]

    clf = MarketSizeClassifier(cfg)
    train_metrics = clf.train(train_df)
    print(f"  Train accuracy: {train_metrics['accuracy']:.3f}")

    # 9. Evaluate on test set
    print("\nStep 7: Evaluating on test set...")
    test_preds = clf.predict(test_df.drop("total_volume", "label"))
    test_labels = test_df["label"].to_list()

    print(classification_report(test_labels, test_preds, zero_division=0))
    print("Confusion matrix:")
    cm = confusion_matrix(test_labels, test_preds, labels=list(cfg.buckets))
    header = "         " + "  ".join(f"{b:>8}" for b in cfg.buckets)
    print(header)
    for i, row in enumerate(cm):
        print(f"  {cfg.buckets[i]:<8}" + "  ".join(f"{v:>8,}" for v in row))

    # 10. Retrain on full data and save
    print(f"\nStep 8: Retraining on full data and saving to {args.output}...")
    clf_full = MarketSizeClassifier(cfg)
    clf_full.train(features)
    clf_full.save(args.output)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
