"""Feature extraction for market size classification.

Computes early-life features from trades, markets, events, and tags
for use in both training and inference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig


def bucket_from_volume(
    volume: float,
    thresholds: tuple[float, ...],
    buckets: tuple[str, ...],
) -> str:
    """Map a volume value to a bucket label."""
    for i, t in enumerate(thresholds):
        if volume < t:
            return buckets[i]
    return buckets[-1]


def compute_features_polars(
    trades: pl.DataFrame,
    markets: pl.DataFrame,
    events: pl.DataFrame,
    tags: pl.DataFrame,
    cfg: MarketSizeConfig,
) -> pl.DataFrame:
    """Compute the feature matrix from in-memory DataFrames.

    Parameters
    ----------
    trades
        Must have: condition_id, maker, price, size, timestamp.
    markets
        Must have: condition_id, event_id, neg_risk.
    events
        Must have: id, end_date, volume, liquidity.
    tags
        Must have: event_id, tag (primary tag per event).
    cfg
        Classifier configuration (window size, tag list, etc.).

    Returns
    -------
    pl.DataFrame
        One row per condition_id with feature columns + condition_id.
    """
    window_secs = cfg.feature_window_hours * 3600

    # 1. Compute first_ts per market
    first_ts = trades.group_by("condition_id").agg(
        pl.col("timestamp").min().alias("first_ts")
    )

    # 2. Join trades with first_ts to get secs_since_first
    enriched = trades.join(first_ts, on="condition_id", how="left")
    enriched = enriched.with_columns(
        (pl.col("timestamp") - pl.col("first_ts")).alias("secs_since_first"),
        (pl.col("price") * pl.col("size")).alias("trade_volume"),
    )

    # 3. Aggregate within feature window
    in_window = enriched.filter(pl.col("secs_since_first") <= window_secs)

    window_agg = in_window.group_by("condition_id").agg(
        pl.len().alias("trades_window"),
        pl.col("trade_volume").sum().alias("vol_window"),
        pl.col("maker").n_unique().alias("traders_window"),
    )

    # 4. Total stats (for label computation in training)
    total_agg = enriched.group_by("condition_id").agg(
        pl.col("trade_volume").sum().alias("total_volume"),
        pl.len().alias("total_trades"),
    )

    # 5. Join with first_ts for time features
    features = window_agg.join(first_ts, on="condition_id", how="left")
    features = features.join(total_agg, on="condition_id", how="left")

    # 6. Join with markets for event_id + neg_risk
    features = features.join(
        markets.select("condition_id", "event_id", "neg_risk"),
        on="condition_id",
        how="left",
    )

    # 7. Event-level features
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

    # 8. Time remaining
    features = features.with_columns(
        ((pl.col("event_end_date") - pl.col("first_ts")) / 3600.0).alias(
            "time_remaining_hours"
        ),
    )

    # 9. Temporal features
    features = features.with_columns(
        (pl.col("first_ts") % 86400 / 3600).cast(pl.Int32).alias("hour_of_day"),
        (pl.col("first_ts") / 86400).cast(pl.Int32).mod(7).alias("day_of_week"),
    )

    # 10. neg_risk as int
    features = features.with_columns(
        pl.col("neg_risk").cast(pl.Int8).alias("neg_risk"),
    )

    # 11. One-hot encode tags
    for tag_name in cfg.top_tags:
        col_name = f"tag_{tag_name.replace(' ', '_')}"
        matching_events = tags.filter(pl.col("tag") == tag_name)["event_id"].to_list()
        features = features.with_columns(
            pl.col("event_id").is_in(matching_events).cast(pl.Int8).alias(col_name),
        )

    # 12. Select final columns
    feature_cols = [
        "condition_id",
        "trades_window",
        "vol_window",
        "traders_window",
        "time_remaining_hours",
        "event_n_markets",
        "event_volume",
        "event_liquidity",
        "neg_risk",
        "hour_of_day",
        "day_of_week",
        "total_volume",  # kept for label computation in training
    ] + [f"tag_{t.replace(' ', '_')}" for t in cfg.top_tags]

    # Only select columns that exist (tags may be missing)
    existing = [c for c in feature_cols if c in features.columns]
    return features.select(existing)
