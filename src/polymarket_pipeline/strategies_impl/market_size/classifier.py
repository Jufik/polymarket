"""XGBoost market size classifier — train, predict, serialize.

Wraps XGBoost multiclass classification with volume-bucket labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        from sklearn.metrics import (  # type: ignore[import-untyped]
            accuracy_score,
            classification_report,
        )

        # Build label column
        labels = np.array([
            self._volume_to_label(v) for v in features["total_volume"].to_list()
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
        acc = float(accuracy_score(labels, preds))
        report: dict[str, Any] = classification_report(
            labels,
            preds,
            labels=list(range(n_classes)),
            target_names=list(self._cfg.buckets),
            output_dict=True,
            zero_division=0,
        )

        metrics: dict[str, float] = {"accuracy": acc}
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
        learned_classes = self._model.classes_

        result = features.select("condition_id")
        for i, bucket in enumerate(self._cfg.buckets):
            if i in learned_classes:
                col_idx = int(np.where(learned_classes == i)[0][0])
                result = result.with_columns(
                    pl.Series(f"p_{bucket}", proba[:, col_idx])
                )
            else:
                result = result.with_columns(
                    pl.lit(0.0).alias(f"p_{bucket}")
                )
        return result

    def save(self, path: str) -> None:
        """Serialize the model to disk with joblib."""
        import joblib  # type: ignore[import-untyped]

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
