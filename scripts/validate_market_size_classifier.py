"""End-to-end validation of the market size classifier against ClickHouse data.

Trains on 80% of data, evaluates on 20%, then classifies recent markets.

Usage:
    uv run python scripts/validate_market_size_classifier.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import numpy as np
import polars as pl

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
    cfg = MarketSizeConfig(feature_window_hours=6)
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    # Reuse training script logic but with validation focus
    print("Fetching data from ClickHouse...")

    trades = await query_ch(
        client,
        """
        SELECT t.condition_id, t.maker,
               CAST(t.price AS Float64) AS price,
               CAST(t.size AS Float64) AS size,
               toUnixTimestamp(t.timestamp) AS timestamp
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE t.condition_id IN (
            SELECT condition_id FROM markets WHERE question LIKE 'Will %%'
        )
    """,
    )
    for col in ["price", "size", "timestamp"]:
        trades = trades.with_columns(pl.col(col).cast(pl.Float64))

    markets = await query_ch(
        client,
        """
        SELECT condition_id, event_id, neg_risk FROM markets WHERE question LIKE 'Will %%'
    """,
    )
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

    events = await query_ch(
        client,
        """
        SELECT id, toUnixTimestamp(end_date) AS end_date,
               CAST(volume AS Float64) AS volume,
               CAST(liquidity AS Float64) AS liquidity
        FROM events WHERE end_date IS NOT NULL AND end_date != '1970-01-01 00:00:00'
    """,
    )
    for col in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(col).cast(pl.Float64))

    tags = await query_ch(
        client,
        """
        SELECT et.event_id, t.label AS tag FROM event_tags et JOIN tags t ON et.tag_id = t.id
    """,
    )
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")

    await client.aclose()

    features = compute_features_polars(trades, markets, events, tags, cfg)
    features = features.filter(pl.col("total_volume") >= 10)
    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)

    print(f"Feature matrix: {features.shape}")

    # Split: older 80% for train, newer 20% for test (temporal split)
    n = len(features)
    train_n = int(n * 0.8)
    train_df = features[:train_n]
    test_df = features[train_n:]

    clf = MarketSizeClassifier(cfg)
    clf.train(train_df)

    # Evaluate
    test_preds = clf.predict(test_df.drop("total_volume"))
    test_labels = [
        bucket_from_volume(v, cfg.bucket_thresholds, cfg.buckets)
        for v in test_df["total_volume"].to_list()
    ]

    from sklearn.metrics import accuracy_score, classification_report

    print(f"\nTest accuracy: {accuracy_score(test_labels, test_preds):.3f}")
    print(classification_report(test_labels, test_preds, zero_division=0))

    # Show feature importances
    importances = clf._model.feature_importances_
    names = clf._feature_names
    sorted_idx = np.argsort(importances)[::-1]
    print("\nTop 10 features:")
    for i in sorted_idx[:10]:
        print(f"  {names[i]:<30} {importances[i]:.4f}")

    print("\nValidation complete!")


if __name__ == "__main__":
    asyncio.run(main())
