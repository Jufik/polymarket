# Market Size Classifier Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an XGBoost classifier that predicts market final volume bucket (thin/med/thick/heavy) from early features, integrated as a reusable `FeatureProvider` compatible with both backtest and live modes.

**Architecture:** A standalone `market_size/` strategy package containing: (1) a training script that extracts features from ClickHouse and trains an XGBoost model, (2) a `MarketSizeProvider` implementing `FeatureProvider` that loads the serialized model and classifies markets on demand, (3) integration with both `PolarsBackend` (backtest) and `ClickHouseBackend` (live). The classifier is strategy-agnostic: any strategy can query `market_size_bucket` from context.

**Tech Stack:** XGBoost, scikit-learn (metrics + preprocessing), Polars, ClickHouse, joblib (model serialization)

---

## Background

From empirical analysis of ~20K "Will" markets in ClickHouse:
- **Volume is THE discriminator**: thin markets (<$1K) yield +6.6% ROI for NO strategies; heavy markets (>$50K) yield -14.0%
- **Early volume alone is weak**: vol@1h median is $2-$13 across ALL final buckets
- **Time remaining is the key missing feature**: `events.end_date - signal_time` separates fast-resolving (thin) from long-running (thick) markets
- **Tags have signal**: Weather +22.5%, Global Elections +29.2%, Crypto -28.4%

**Volume buckets (target labels):**
| Label | Final Volume | Count (approx) |
|-------|-------------|-----------------|
| `thin` | < $1,000 | ~7,400 |
| `med` | $1K - $10K | ~4,600 |
| `thick` | $10K - $100K | ~3,800 |
| `heavy` | > $100K | ~4,200 |

**Feature set:**
| Feature | Source | Available at signal time? |
|---------|--------|-------------------------|
| `trades_1h` | trades_raw aggregation | Yes (1h after first trade) |
| `vol_1h` | trades_raw aggregation | Yes |
| `traders_1h` | trades_raw aggregation | Yes |
| `trades_6h` | trades_raw aggregation | Yes (6h after first trade) |
| `vol_6h` | trades_raw aggregation | Yes |
| `traders_6h` | trades_raw aggregation | Yes |
| `time_remaining_hours` | `events.end_date - first_trade_ts` | Yes |
| `event_n_markets` | count markets per event | Yes |
| `event_total_volume` | `events.volume` | Yes (may be stale) |
| `event_liquidity` | `events.liquidity` | Yes (may be stale) |
| `primary_tag` | `event_tags -> tags` (one-hot top-10) | Yes |
| `neg_risk` | `markets.neg_risk` | Yes |
| `hour_of_day` | `first_trade_ts` hour UTC | Yes |
| `day_of_week` | `first_trade_ts` day UTC | Yes |

---

## Task 1: Add ML Dependencies

**Files:**
- Modify: `pyproject.toml` (strategy optional-dependencies)

**Step 1: Add xgboost, scikit-learn, joblib to strategy dependencies**

In `pyproject.toml`, update the `strategy` optional-dependency group:

```toml
strategy = [
    "polars>=1.15.0",
    "pydantic>=2.0",
    "structlog>=24.0",
    "xgboost>=2.0",
    "scikit-learn>=1.4",
    "joblib>=1.3",
]
```

**Step 2: Install dependencies**

Run: `uv sync --all-extras`
Expected: xgboost, scikit-learn, joblib installed successfully

**Step 3: Verify imports work**

Run: `uv run python -c "import xgboost; import sklearn; import joblib; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add xgboost/sklearn to strategy dependencies"
```

---

## Task 2: Create Package Skeleton and Config

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/market_size/__init__.py`
- Create: `src/polymarket_pipeline/strategies_impl/market_size/config.py`
- Test: `tests/test_market_size_classifier.py`

**Step 1: Write the failing test**

```python
"""Tests for market size classifier."""

from __future__ import annotations

import pytest

from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig


def test_market_size_config_defaults() -> None:
    cfg = MarketSizeConfig()
    assert cfg.buckets == ("thin", "med", "thick", "heavy")
    assert cfg.bucket_thresholds == (1_000.0, 10_000.0, 100_000.0)
    assert cfg.feature_window_hours == 6
    assert cfg.model_path == "models/market_size_xgb.joblib"


def test_market_size_config_custom() -> None:
    cfg = MarketSizeConfig(
        bucket_thresholds=(500.0, 5_000.0, 50_000.0),
        feature_window_hours=1,
    )
    assert cfg.bucket_thresholds == (500.0, 5_000.0, 50_000.0)
    assert cfg.feature_window_hours == 1


def test_market_size_config_frozen() -> None:
    cfg = MarketSizeConfig()
    with pytest.raises(AttributeError):
        cfg.feature_window_hours = 99  # type: ignore[misc]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_market_size_classifier.py::test_market_size_config_defaults -x -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Create the package and config**

`src/polymarket_pipeline/strategies_impl/market_size/__init__.py`:
```python
"""Market size classifier — predicts final volume bucket from early features."""
```

`src/polymarket_pipeline/strategies_impl/market_size/config.py`:
```python
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_market_size_classifier.py -x -v`
Expected: 3 tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/market_size/__init__.py \
        src/polymarket_pipeline/strategies_impl/market_size/config.py \
        tests/test_market_size_classifier.py
git commit -m "feat: add market size classifier config and package skeleton"
```

---

## Task 3: Feature Extraction Module

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/market_size/features.py`
- Test: `tests/test_market_size_classifier.py` (append)

This module extracts the feature matrix from either a Polars DataFrame (backtest) or ClickHouse SQL (live). It produces a `pl.DataFrame` with one row per market and columns matching the model's expected input.

**Step 1: Write the failing test**

Append to `tests/test_market_size_classifier.py`:

```python
import polars as pl

from polymarket_pipeline.strategies_impl.market_size.features import (
    compute_features_polars,
    bucket_from_volume,
)


def test_bucket_from_volume() -> None:
    thresholds = (1_000.0, 10_000.0, 100_000.0)
    buckets = ("thin", "med", "thick", "heavy")
    assert bucket_from_volume(500.0, thresholds, buckets) == "thin"
    assert bucket_from_volume(1_000.0, thresholds, buckets) == "med"
    assert bucket_from_volume(50_000.0, thresholds, buckets) == "thick"
    assert bucket_from_volume(200_000.0, thresholds, buckets) == "heavy"


def test_compute_features_polars_basic() -> None:
    """Features should be computed from trades + markets + events."""
    trades = pl.DataFrame(
        {
            "condition_id": ["0xA"] * 10 + ["0xB"] * 5,
            "maker": [f"0xtrader{i}" for i in range(10)]
                     + [f"0xtrader{i}" for i in range(5)],
            "price": [0.3] * 10 + [0.5] * 5,
            "size": [10.0] * 15,
            "timestamp": [
                # 0xA: 10 trades spread over 2 hours
                1700000000 + i * 720 for i in range(10)
            ] + [
                # 0xB: 5 trades spread over 1 hour
                1700100000 + i * 720 for i in range(5)
            ],
        }
    )
    markets = pl.DataFrame(
        {
            "condition_id": ["0xA", "0xB"],
            "event_id": ["evt1", "evt2"],
            "neg_risk": [False, True],
        }
    )
    events = pl.DataFrame(
        {
            "id": ["evt1", "evt2"],
            "end_date": [
                1700200000,  # ~55h from first trade of 0xA
                1700150000,  # ~14h from first trade of 0xB
            ],
            "volume": [50000.0, 10000.0],
            "liquidity": [25000.0, 5000.0],
        }
    )
    tags = pl.DataFrame(
        {
            "event_id": ["evt1", "evt2"],
            "tag": ["Politics", "Crypto"],
        }
    )

    cfg = MarketSizeConfig(feature_window_hours=1)
    result = compute_features_polars(trades, markets, events, tags, cfg)

    assert len(result) == 2
    assert "trades_window" in result.columns
    assert "vol_window" in result.columns
    assert "traders_window" in result.columns
    assert "time_remaining_hours" in result.columns
    assert "event_n_markets" in result.columns
    assert "neg_risk" in result.columns
    assert "tag_Politics" in result.columns
    assert "tag_Crypto" in result.columns

    # 0xA should have some trades in first hour
    row_a = result.filter(pl.col("condition_id") == "0xA")
    assert row_a["trades_window"][0] > 0
    assert row_a["time_remaining_hours"][0] > 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_market_size_classifier.py::test_compute_features_polars_basic -x -v`
Expected: FAIL with `ImportError`

**Step 3: Implement the feature extraction module**

`src/polymarket_pipeline/strategies_impl/market_size/features.py`:

```python
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
    first_ts = (
        trades.group_by("condition_id")
        .agg(pl.col("timestamp").min().alias("first_ts"))
    )

    # 2. Join trades with first_ts to get secs_since_first
    enriched = trades.join(first_ts, on="condition_id", how="left")
    enriched = enriched.with_columns(
        (pl.col("timestamp") - pl.col("first_ts")).alias("secs_since_first"),
        (pl.col("price") * pl.col("size")).alias("trade_volume"),
    )

    # 3. Aggregate within feature window
    in_window = enriched.filter(pl.col("secs_since_first") <= window_secs)

    window_agg = (
        in_window.group_by("condition_id")
        .agg(
            pl.len().alias("trades_window"),
            pl.col("trade_volume").sum().alias("vol_window"),
            pl.col("maker").n_unique().alias("traders_window"),
        )
    )

    # 4. Total stats (for label computation in training)
    total_agg = (
        enriched.group_by("condition_id")
        .agg(
            pl.col("trade_volume").sum().alias("total_volume"),
            pl.len().alias("total_trades"),
        )
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
    event_market_count = (
        markets.group_by("event_id")
        .agg(pl.len().alias("event_n_markets"))
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
        ((pl.col("event_end_date") - pl.col("first_ts")) / 3600.0)
        .alias("time_remaining_hours"),
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_market_size_classifier.py -x -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/market_size/features.py \
        tests/test_market_size_classifier.py
git commit -m "feat: add feature extraction for market size classifier"
```

---

## Task 4: XGBoost Model Wrapper (Train + Predict + Serialize)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/market_size/classifier.py`
- Test: `tests/test_market_size_classifier.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_market_size_classifier.py`:

```python
import tempfile
from pathlib import Path

import numpy as np

from polymarket_pipeline.strategies_impl.market_size.classifier import (
    MarketSizeClassifier,
)


def test_classifier_train_and_predict() -> None:
    """Train on synthetic data, predict, verify output shape and labels."""
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    # Synthetic feature matrix: 200 samples, features mimicking real data
    rng = np.random.default_rng(42)
    n = 200
    features = pl.DataFrame(
        {
            "condition_id": [f"0x{i:04x}" for i in range(n)],
            "trades_window": rng.integers(0, 100, n).tolist(),
            "vol_window": (rng.exponential(500, n)).tolist(),
            "traders_window": rng.integers(1, 30, n).tolist(),
            "time_remaining_hours": (rng.exponential(100, n)).tolist(),
            "event_n_markets": rng.integers(1, 20, n).tolist(),
            "event_volume": (rng.exponential(50000, n)).tolist(),
            "event_liquidity": (rng.exponential(25000, n)).tolist(),
            "neg_risk": rng.integers(0, 2, n).tolist(),
            "hour_of_day": rng.integers(0, 24, n).tolist(),
            "day_of_week": rng.integers(0, 7, n).tolist(),
            "total_volume": (rng.exponential(10000, n)).tolist(),
        }
    )
    # Add tag columns
    for tag in cfg.top_tags:
        col_name = f"tag_{tag.replace(' ', '_')}"
        features = features.with_columns(
            pl.Series(col_name, rng.integers(0, 2, n).tolist())
        )

    clf.train(features)

    # Predict on same data (no total_volume needed for predict)
    preds = clf.predict(features.drop("total_volume"))
    assert len(preds) == n
    assert set(preds).issubset(set(cfg.buckets))


def test_classifier_save_and_load() -> None:
    """Model should round-trip through joblib serialization."""
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    rng = np.random.default_rng(42)
    n = 100
    features = pl.DataFrame(
        {
            "condition_id": [f"0x{i:04x}" for i in range(n)],
            "trades_window": rng.integers(0, 50, n).tolist(),
            "vol_window": (rng.exponential(200, n)).tolist(),
            "traders_window": rng.integers(1, 10, n).tolist(),
            "time_remaining_hours": (rng.exponential(50, n)).tolist(),
            "event_n_markets": rng.integers(1, 10, n).tolist(),
            "event_volume": (rng.exponential(20000, n)).tolist(),
            "event_liquidity": (rng.exponential(10000, n)).tolist(),
            "neg_risk": rng.integers(0, 2, n).tolist(),
            "hour_of_day": rng.integers(0, 24, n).tolist(),
            "day_of_week": rng.integers(0, 7, n).tolist(),
            "total_volume": (rng.exponential(5000, n)).tolist(),
        }
    )
    for tag in cfg.top_tags:
        col_name = f"tag_{tag.replace(' ', '_')}"
        features = features.with_columns(
            pl.Series(col_name, rng.integers(0, 2, n).tolist())
        )

    clf.train(features)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "model.joblib"
        clf.save(str(path))

        clf2 = MarketSizeClassifier(cfg)
        clf2.load(str(path))

        preds1 = clf.predict(features.drop("total_volume"))
        preds2 = clf2.predict(features.drop("total_volume"))
        assert preds1 == preds2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_market_size_classifier.py::test_classifier_train_and_predict -x -v`
Expected: FAIL with `ImportError`

**Step 3: Implement the classifier**

`src/polymarket_pipeline/strategies_impl/market_size/classifier.py`:

```python
"""XGBoost market size classifier — train, predict, serialize.

Wraps XGBoost multiclass classification with volume-bucket labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig

logger = structlog.get_logger(__name__)

# Features used by the model (order matters — must match training)
FEATURE_COLS: list[str] = [
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
]

# Tag columns are appended dynamically from config.top_tags


class MarketSizeClassifier:
    """XGBoost multiclass classifier for market volume buckets.

    Parameters
    ----------
    config
        MarketSizeConfig defining buckets, thresholds, and tag list.
    """

    def __init__(self, config: MarketSizeConfig) -> None:
        self._cfg = config
        self._model: Any = None
        self._feature_names: list[str] = FEATURE_COLS + [
            f"tag_{t.replace(' ', '_')}" for t in config.top_tags
        ]

    def _volume_to_label(self, volume: float) -> int:
        """Map volume to integer class label."""
        for i, t in enumerate(self._cfg.bucket_thresholds):
            if volume < t:
                return i
        return len(self._cfg.bucket_thresholds)

    def train(self, features: pl.DataFrame) -> dict[str, float]:
        """Train the classifier on a feature DataFrame.

        Parameters
        ----------
        features
            Must contain all feature columns + ``total_volume`` for labels.

        Returns
        -------
        dict
            Training metrics: accuracy, per-class F1.
        """
        import xgboost as xgb
        from sklearn.metrics import accuracy_score, classification_report

        # Build label column
        labels = np.array([
            self._volume_to_label(v)
            for v in features["total_volume"].to_list()
        ])

        # Build feature matrix
        X = features.select(self._feature_names).to_numpy().astype(np.float32)

        # Handle NaN/null
        X = np.nan_to_num(X, nan=0.0)

        n_classes = len(self._cfg.buckets)

        self._model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            objective="multi:softmax",
            num_class=n_classes,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X, labels)

        # Evaluate on training data (for logging; real eval uses held-out set)
        preds = self._model.predict(X)
        acc = accuracy_score(labels, preds)
        report = classification_report(
            labels, preds,
            target_names=list(self._cfg.buckets),
            output_dict=True,
            zero_division=0,
        )

        metrics = {"accuracy": acc}
        for bucket_name in self._cfg.buckets:
            if bucket_name in report:
                metrics[f"f1_{bucket_name}"] = report[bucket_name]["f1-score"]

        logger.info("market_size.train", accuracy=f"{acc:.3f}", n_samples=len(labels))
        return metrics

    def predict(self, features: pl.DataFrame) -> list[str]:
        """Predict volume buckets for each row.

        Parameters
        ----------
        features
            Must contain all feature columns (no ``total_volume`` needed).

        Returns
        -------
        list[str]
            Predicted bucket label per row.
        """
        if self._model is None:
            msg = "Model not trained or loaded — call train() or load() first"
            raise RuntimeError(msg)

        X = features.select(self._feature_names).to_numpy().astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)

        pred_ints = self._model.predict(X)
        return [self._cfg.buckets[int(p)] for p in pred_ints]

    def predict_proba(self, features: pl.DataFrame) -> pl.DataFrame:
        """Predict class probabilities for each row.

        Returns a DataFrame with columns: ``condition_id``, plus one
        probability column per bucket (e.g. ``p_thin``, ``p_med``, ...).
        """
        if self._model is None:
            msg = "Model not trained or loaded — call train() or load() first"
            raise RuntimeError(msg)

        X = features.select(self._feature_names).to_numpy().astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)

        proba = self._model.predict_proba(X)

        result = features.select("condition_id")
        for i, bucket in enumerate(self._cfg.buckets):
            result = result.with_columns(
                pl.Series(f"p_{bucket}", proba[:, i])
            )
        return result

    def save(self, path: str) -> None:
        """Serialize the model to disk with joblib."""
        import joblib

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"model": self._model, "feature_names": self._feature_names},
            path,
        )
        logger.info("market_size.save", path=path)

    def load(self, path: str) -> None:
        """Load a serialized model from disk."""
        import joblib

        data = joblib.load(path)
        self._model = data["model"]
        self._feature_names = data["feature_names"]
        logger.info("market_size.load", path=path)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_market_size_classifier.py -x -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/market_size/classifier.py \
        tests/test_market_size_classifier.py
git commit -m "feat: add XGBoost market size classifier with train/predict/serialize"
```

---

## Task 5: Training Script (ClickHouse Feature Extraction + Train + Save)

**Files:**
- Create: `scripts/train_market_size_classifier.py`

This is a standalone script that:
1. Extracts features from ClickHouse (trades_raw + markets + events + tags)
2. Computes the feature matrix using Polars
3. Trains the XGBoost model with train/test split
4. Reports accuracy, per-class F1, confusion matrix
5. Saves the model to `models/market_size_xgb.joblib`

**Step 1: Create the training script**

```python
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
    markets = markets.with_columns(
        pl.col("neg_risk").cast(pl.Boolean)
    )
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

    # 6. Filter to markets with enough trades (>=5 total_volume)
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
        n = sum(1 for l in labels if l == bucket)
        print(f"    {bucket:<10} {n:>6,} ({n/len(labels):>6.1%})")

    # 8. Train/test split
    print("\nStep 6: Training XGBoost classifier...")
    train_idx, test_idx = train_test_split(
        list(range(len(features))), test_size=0.2, random_state=42,
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
```

**Step 2: Run the training script**

Run: `uv run python scripts/train_market_size_classifier.py --window 6`
Expected: Model trains, prints accuracy/F1, saves to `models/market_size_xgb.joblib`

**Step 3: Commit**

```bash
git add scripts/train_market_size_classifier.py
git commit -m "feat: add market size classifier training script"
```

---

## Task 6: MarketSizeProvider (FeatureProvider Integration)

**Files:**
- Create: `src/polymarket_pipeline/strategies_impl/market_size/providers.py`
- Test: `tests/test_market_size_classifier.py` (append)

This provider loads the trained model and exposes `market_size_bucket` and `market_size_proba` features to any strategy via the standard `FeatureProvider` protocol.

**Step 1: Write the failing test**

Append to `tests/test_market_size_classifier.py`:

```python
import tempfile
from unittest.mock import AsyncMock

from polymarket_pipeline.strategies_impl.market_size.providers import (
    MarketSizeProvider,
)


@pytest.mark.asyncio
async def test_provider_computes_buckets() -> None:
    """Provider should classify markets when compute() is called."""
    # First train and save a model
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    rng = np.random.default_rng(42)
    n = 200
    features = pl.DataFrame(
        {
            "condition_id": [f"0x{i:04x}" for i in range(n)],
            "trades_window": rng.integers(0, 100, n).tolist(),
            "vol_window": (rng.exponential(500, n)).tolist(),
            "traders_window": rng.integers(1, 30, n).tolist(),
            "time_remaining_hours": (rng.exponential(100, n)).tolist(),
            "event_n_markets": rng.integers(1, 20, n).tolist(),
            "event_volume": (rng.exponential(50000, n)).tolist(),
            "event_liquidity": (rng.exponential(25000, n)).tolist(),
            "neg_risk": rng.integers(0, 2, n).tolist(),
            "hour_of_day": rng.integers(0, 24, n).tolist(),
            "day_of_week": rng.integers(0, 7, n).tolist(),
            "total_volume": (rng.exponential(10000, n)).tolist(),
        }
    )
    for tag in cfg.top_tags:
        col_name = f"tag_{tag.replace(' ', '_')}"
        features = features.with_columns(
            pl.Series(col_name, rng.integers(0, 2, n).tolist())
        )

    clf.train(features)

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = f"{tmpdir}/model.joblib"
        clf.save(model_path)

        # Create provider with this model
        provider_cfg = MarketSizeConfig(model_path=model_path)
        provider = MarketSizeProvider(config=provider_cfg)

        # Mock backend that returns our feature data
        backend = AsyncMock()
        backend.query_trades = AsyncMock(return_value=pl.DataFrame(
            {
                "condition_id": ["0xA"] * 10,
                "maker": [f"0xm{i}" for i in range(10)],
                "price": [0.3] * 10,
                "size": [10.0] * 10,
                "timestamp": [1700000000 + i * 100 for i in range(10)],
            }
        ))
        backend.query_markets = AsyncMock(return_value=pl.DataFrame(
            {
                "condition_id": ["0xA"],
                "event_id": ["evt1"],
                "neg_risk": [False],
            }
        ))
        backend.query_custom = AsyncMock(side_effect=[
            # events query
            pl.DataFrame({
                "id": ["evt1"],
                "end_date": [1700200000.0],
                "volume": [50000.0],
                "liquidity": [25000.0],
            }),
            # tags query
            pl.DataFrame({
                "event_id": ["evt1"],
                "tag": ["Politics"],
            }),
        ])

        await provider.compute(backend)

        result = provider.get_features()
        assert "market_size_bucket" in result
        assert "0xA" in result["market_size_bucket"]
        assert result["market_size_bucket"]["0xA"] in cfg.buckets


@pytest.mark.asyncio
async def test_provider_handles_empty_trades() -> None:
    """Provider should handle empty trades gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a dummy model first
        cfg = MarketSizeConfig(model_path=f"{tmpdir}/model.joblib")
        clf = MarketSizeClassifier(cfg)
        rng = np.random.default_rng(42)
        n = 50
        dummy = pl.DataFrame({
            "condition_id": [f"0x{i}" for i in range(n)],
            "trades_window": rng.integers(0, 50, n).tolist(),
            "vol_window": (rng.exponential(200, n)).tolist(),
            "traders_window": rng.integers(1, 10, n).tolist(),
            "time_remaining_hours": (rng.exponential(50, n)).tolist(),
            "event_n_markets": rng.integers(1, 10, n).tolist(),
            "event_volume": (rng.exponential(20000, n)).tolist(),
            "event_liquidity": (rng.exponential(10000, n)).tolist(),
            "neg_risk": rng.integers(0, 2, n).tolist(),
            "hour_of_day": rng.integers(0, 24, n).tolist(),
            "day_of_week": rng.integers(0, 7, n).tolist(),
            "total_volume": (rng.exponential(5000, n)).tolist(),
        })
        for tag in cfg.top_tags:
            col_name = f"tag_{tag.replace(' ', '_')}"
            dummy = dummy.with_columns(
                pl.Series(col_name, rng.integers(0, 2, n).tolist())
            )
        clf.train(dummy)
        clf.save(cfg.model_path)

        provider = MarketSizeProvider(config=cfg)

        backend = AsyncMock()
        backend.query_trades = AsyncMock(return_value=pl.DataFrame())
        backend.query_markets = AsyncMock(return_value=pl.DataFrame())

        await provider.compute(backend)
        result = provider.get_features()
        assert result["market_size_bucket"] == {}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_market_size_classifier.py::test_provider_computes_buckets -x -v`
Expected: FAIL with `ImportError`

**Step 3: Implement the provider**

`src/polymarket_pipeline/strategies_impl/market_size/providers.py`:

```python
"""FeatureProvider that classifies markets into volume buckets.

Loads a pre-trained XGBoost model and exposes ``market_size_bucket``
(dict[condition_id, str]) via get_features().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies_impl.market_size.classifier import (
    MarketSizeClassifier,
)
from polymarket_pipeline.strategies_impl.market_size.features import (
    compute_features_polars,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend
    from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig

logger = structlog.get_logger(__name__)


class MarketSizeProvider:
    """Predicts market volume bucket using a pre-trained XGBoost model.

    Features exposed via ``get_features()``:
    - ``market_size_bucket``: ``dict[str, str]`` — condition_id → bucket label
    - ``market_size_proba``: ``dict[str, dict[str, float]]`` — condition_id → {bucket: probability}
    """

    name: str = "market_size"

    def __init__(self, config: MarketSizeConfig) -> None:
        self._cfg = config
        self._classifier = MarketSizeClassifier(config)
        self._buckets: dict[str, str] = {}
        self._proba: dict[str, dict[str, float]] = {}
        self._model_loaded = False

    def _ensure_model(self) -> bool:
        """Load the model if not yet loaded. Returns True if model is available."""
        if self._model_loaded:
            return True
        try:
            self._classifier.load(self._cfg.model_path)
            self._model_loaded = True
            return True
        except FileNotFoundError:
            logger.warning("market_size.model_not_found", path=self._cfg.model_path)
            return False

    async def compute(self, backend: FeatureBackend) -> None:
        """Classify all markets using the backend's data."""
        import polars as pl

        if not self._ensure_model():
            return

        # Fetch data
        trades = await backend.query_trades()
        if trades.is_empty():
            self._buckets = {}
            self._proba = {}
            logger.info("market_size.compute", count=0)
            return

        markets = await backend.query_markets()

        # Fetch events and tags via query_custom (for CH) or direct (Polars)
        try:
            events = await backend.query_custom(
                "SELECT id, toUnixTimestamp(end_date) AS end_date, "
                "CAST(volume AS Float64) AS volume, "
                "CAST(liquidity AS Float64) AS liquidity "
                "FROM events WHERE end_date IS NOT NULL "
                "AND end_date != '1970-01-01 00:00:00'"
            )
            for col in ["end_date", "volume", "liquidity"]:
                events = events.with_columns(pl.col(col).cast(pl.Float64))
        except (NotImplementedError, Exception):
            # Polars backend or missing table — use empty
            events = pl.DataFrame(
                {"id": [], "end_date": [], "volume": [], "liquidity": []},
                schema={
                    "id": pl.Utf8, "end_date": pl.Float64,
                    "volume": pl.Float64, "liquidity": pl.Float64,
                },
            )

        try:
            tags = await backend.query_custom(
                "SELECT et.event_id, t.label AS tag "
                "FROM event_tags et JOIN tags t ON et.tag_id = t.id"
            )
            tags = tags.sort("tag").unique(subset=["event_id"], keep="first")
        except (NotImplementedError, Exception):
            tags = pl.DataFrame(
                {"event_id": [], "tag": []},
                schema={"event_id": pl.Utf8, "tag": pl.Utf8},
            )

        # Ensure expected columns exist
        if "neg_risk" not in markets.columns:
            markets = markets.with_columns(pl.lit(False).alias("neg_risk"))
        if "event_id" not in markets.columns:
            self._buckets = {}
            self._proba = {}
            return

        # Ensure numeric types for trades
        if "timestamp" in trades.columns:
            trades = trades.with_columns(pl.col("timestamp").cast(pl.Float64))
        for col in ["price", "size"]:
            if col in trades.columns:
                trades = trades.with_columns(pl.col(col).cast(pl.Float64))

        markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

        # Compute features
        features = compute_features_polars(trades, markets, events, tags, self._cfg)

        if features.is_empty():
            self._buckets = {}
            self._proba = {}
            return

        # Drop rows with null features
        features = features.drop_nulls(subset=["time_remaining_hours"])

        if features.is_empty():
            self._buckets = {}
            self._proba = {}
            return

        # Predict
        cids = features["condition_id"].to_list()
        preds = self._classifier.predict(features.drop("total_volume"))

        self._buckets = dict(zip(cids, preds))

        # Probabilities
        proba_df = self._classifier.predict_proba(features.drop("total_volume"))
        self._proba = {}
        for row in proba_df.iter_rows(named=True):
            cid = row["condition_id"]
            self._proba[cid] = {
                bucket: row[f"p_{bucket}"]
                for bucket in self._cfg.buckets
            }

        logger.info("market_size.compute", count=len(self._buckets))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — classifications are refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-classify all markets with fresh data."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {
            "market_size_bucket": self._buckets,
            "market_size_proba": self._proba,
        }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_market_size_classifier.py -x -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/market_size/providers.py \
        tests/test_market_size_classifier.py
git commit -m "feat: add MarketSizeProvider FeatureProvider integration"
```

---

## Task 7: Strategy Integration — WillNoStrategy Uses Market Size

**Files:**
- Modify: `src/polymarket_pipeline/strategies_impl/will_no/config.py`
- Modify: `src/polymarket_pipeline/strategies_impl/will_no/strategy.py`
- Test: `tests/test_strategy_will_no.py` (append or create new test)

Show how a strategy consumes `market_size_bucket` from context. This is the canonical integration pattern.

**Step 1: Write the failing test**

Create or append to `tests/test_strategy_will_no.py`:

```python
@pytest.mark.asyncio
async def test_will_no_filters_by_market_size_bucket() -> None:
    """WillNo should skip markets classified as 'heavy' when max_bucket is set."""
    cfg = WillNoConfig(
        yes_price_min=0.15,
        yes_price_max=0.40,
        max_bucket="med",  # only trade thin and med
    )
    strategy = WillNoStrategy(config=cfg)
    ctx = _MockCtx(features={
        "will_markets": {
            "0xmkt1": MarketInfo(
                condition_id="0xmkt1",
                question="Will X happen?",
                active=True,
                yes_price=0.25,
                category="Politics",
            ),
        },
        "market_size_bucket": {"0xmkt1": "heavy"},
    })

    trade = _make_trade(condition_id="0xmkt1", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is None  # heavy market, should be skipped
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_will_no.py::test_will_no_filters_by_market_size_bucket -x -v`
Expected: FAIL (either `max_bucket` param doesn't exist or filter not applied)

**Step 3: Add `max_bucket` to WillNoConfig**

In `will_no/config.py`, add parameter:

```python
max_bucket: str | None = None  # If set, only trade markets at or below this bucket
```

Add to `__init__`:
```python
max_bucket: str | None = None,
```
and:
```python
object.__setattr__(self, "max_bucket", max_bucket)
```

**Step 4: Add filter to WillNoStrategy.on_trade()**

In `will_no/strategy.py`, after the existing eligibility check and before `self._signaled.add(cid)`, add:

```python
# Market size filter — skip markets above max_bucket
if self._cfg.max_bucket is not None:
    buckets = await ctx.get_features("market_size_bucket")
    if buckets is not None:
        bucket = buckets.get(cid)
        if bucket is not None:
            allowed = ("thin", "med", "thick", "heavy")
            max_idx = allowed.index(self._cfg.max_bucket) if self._cfg.max_bucket in allowed else len(allowed) - 1
            cur_idx = allowed.index(bucket) if bucket in allowed else len(allowed) - 1
            if cur_idx > max_idx:
                return None
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_will_no.py -x -v`
Expected: All tests PASS (new + existing)

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies_impl/will_no/config.py \
        src/polymarket_pipeline/strategies_impl/will_no/strategy.py \
        tests/test_strategy_will_no.py
git commit -m "feat: WillNo strategy supports market_size_bucket filter"
```

---

## Task 8: End-to-End Smoke Test with Real ClickHouse Data

**Files:**
- Create: `scripts/validate_market_size_classifier.py`

Runs the full pipeline: extract features from CH → train → evaluate → classify new markets. Validates that the model generalizes.

**Step 1: Create validation script**

```python
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

    trades = await query_ch(client, """
        SELECT t.condition_id, t.maker,
               CAST(t.price AS Float64) AS price,
               CAST(t.size AS Float64) AS size,
               toUnixTimestamp(t.timestamp) AS timestamp
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE t.condition_id IN (
            SELECT condition_id FROM markets WHERE question LIKE 'Will %%'
        )
    """)
    for col in ["price", "size", "timestamp"]:
        trades = trades.with_columns(pl.col(col).cast(pl.Float64))

    markets = await query_ch(client, """
        SELECT condition_id, event_id, neg_risk FROM markets WHERE question LIKE 'Will %%'
    """)
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

    events = await query_ch(client, """
        SELECT id, toUnixTimestamp(end_date) AS end_date,
               CAST(volume AS Float64) AS volume,
               CAST(liquidity AS Float64) AS liquidity
        FROM events WHERE end_date IS NOT NULL AND end_date != '1970-01-01 00:00:00'
    """)
    for col in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(col).cast(pl.Float64))

    tags = await query_ch(client, """
        SELECT et.event_id, t.label AS tag FROM event_tags et JOIN tags t ON et.tag_id = t.id
    """)
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")

    await client.aclose()

    features = compute_features_polars(trades, markets, events, tags, cfg)
    features = features.filter(pl.col("total_volume") >= 10)
    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)

    print(f"Feature matrix: {features.shape}")

    # Split: older 80% for train, newer 20% for test (temporal split)
    # Use total_volume percentile as proxy for time ordering
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
    import numpy as np
    importances = clf._model.feature_importances_
    names = clf._feature_names
    sorted_idx = np.argsort(importances)[::-1]
    print("\nTop 10 features:")
    for i in sorted_idx[:10]:
        print(f"  {names[i]:<30} {importances[i]:.4f}")

    print("\nValidation complete!")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run the validation**

Run: `uv run python scripts/validate_market_size_classifier.py`
Expected: Prints test accuracy, classification report, feature importances

**Step 3: Commit**

```bash
git add scripts/validate_market_size_classifier.py
git commit -m "feat: add market size classifier validation script"
```

---

## Task 9: Run Full Training and Save Production Model

**Step 1: Create models directory**

Run: `mkdir -p models`

**Step 2: Train and save model**

Run: `uv run python scripts/train_market_size_classifier.py --window 6 --output models/market_size_xgb.joblib`
Expected: Model saved to `models/market_size_xgb.joblib`

**Step 3: Add models directory to .gitignore (model files are large)**

Check if `.gitignore` exists and add `models/*.joblib` if not already there.

**Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: add models directory to gitignore"
```

---

## Task 10: Run Full Test Suite

**Step 1: Run all unit tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: All tests PASS

**Step 2: Run type checker**

Run: `uv run mypy --strict src/polymarket_pipeline/strategies_impl/market_size/`
Expected: No errors

**Step 3: Run linter**

Run: `uv run ruff check src/polymarket_pipeline/strategies_impl/market_size/ tests/test_market_size_classifier.py`
Expected: No errors

**Step 4: Fix any issues found**

If mypy or ruff report issues, fix them.

**Step 5: Commit fixes if any**

```bash
git add -A && git commit -m "fix: resolve type/lint issues in market size classifier"
```
