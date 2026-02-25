"""Configuration for the market size classifier."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSizeConfig:
    """Immutable configuration for the market size classifier.

    Parameters
    ----------
    buckets
        Ordered label names for volume buckets.
    bucket_thresholds
        Volume thresholds separating buckets. len(thresholds) == len(buckets) - 1.
    feature_window_hours
        How many hours of early data to use for features (1, 6, or 24).
    model_path
        Path to the serialized XGBoost model (joblib).
    top_tags
        Tags to one-hot encode. Markets not matching get all zeros.
    """

    buckets: tuple[str, ...] = ("thin", "med", "thick", "heavy")
    bucket_thresholds: tuple[float, ...] = (1_000.0, 10_000.0, 100_000.0)
    feature_window_hours: int = 6
    model_path: str = "models/market_size_xgb.joblib"
    top_tags: tuple[str, ...] = (
        "Crypto",
        "Politics",
        "Sports",
        "Pop Culture",
        "Weather",
        "Global Elections",
        "Science",
        "Earnings",
        "Finance",
        "Other",
    )
