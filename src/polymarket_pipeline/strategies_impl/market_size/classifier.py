"""XGBoost market size classifier — log-volume regression approach.

Trains an XGBRegressor on log1p(total_volume), then maps predicted
log-volume back to volume-bucket labels post-hoc.

Optionally enriches features with TF-IDF + SVD text embeddings from
market question text.
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

# Numeric features used by the model (order matters — must match training)
FEATURE_COLS: list[str] = [
    # Full window (6h default)
    "trades_window",
    "vol_window",
    "traders_window",
    # Early sub-window (1/6 of full: 1h for 6h)
    "trades_early",
    "vol_early",
    "traders_early",
    # Mid sub-window (1/2 of full: 3h for 6h)
    "trades_mid",
    "vol_mid",
    "traders_mid",
    # Derived ratios
    "vol_per_trade",
    "vol_per_trader",
    "vol_growth_early_mid",
    "vol_growth_mid_full",
    # Price microstructure
    "price_std",
    "price_range",
    "avg_trade_size",
    "max_trade_size",
    # Log-scale volume features
    "log_vol_window",
    "log_vol_early",
    "log_vol_mid",
    "log_event_volume",
    "log_event_liquidity",
    # Velocity & concentration
    "trades_per_hour",
    "trader_concentration",
    "vol_to_liquidity",
    # Context
    "time_remaining_hours",
    "event_n_markets",
    "neg_risk",
    "hour_of_day",
    "day_of_week",
]

# Tag columns are appended dynamically from config.top_tags
# Text SVD columns are appended at the numpy level (not in FEATURE_COLS)

N_TEXT_COMPONENTS = 20

# Default XGBoost hyperparameters (Optuna-tuned, can be overridden via xgb_params)
DEFAULT_XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 495,
    "max_depth": 10,
    "learning_rate": 0.056,
    "min_child_weight": 9,
    "subsample": 0.84,
    "colsample_bytree": 0.99,
    "reg_alpha": 0.065,
    "reg_lambda": 0.32,
}


class MarketSizeClassifier:
    """XGBoost regressor for market volume prediction.

    Uses log-volume regression: trains on log1p(total_volume), then
    maps predictions back to bucket labels via configured thresholds.

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
        self._tfidf: Any = None
        self._svd: Any = None
        # Pre-compute log thresholds for fast bucket assignment
        self._log_thresholds = np.log1p(np.array(config.bucket_thresholds))

    def _volume_to_bucket(self, volume: float) -> str:
        """Map predicted volume to bucket label."""
        for i, t in enumerate(self._cfg.bucket_thresholds):
            if volume < t:
                return self._cfg.buckets[i]
        return self._cfg.buckets[-1]

    def _log_vol_to_bucket(self, log_vol: float) -> str:
        """Map predicted log1p(volume) to bucket label."""
        vol = np.expm1(min(log_vol, 50.0))
        return self._volume_to_bucket(float(vol))

    # ------------------------------------------------------------------
    # Text feature pipeline (TF-IDF + SVD)
    # ------------------------------------------------------------------

    def _fit_text(self, questions: list[str]) -> np.ndarray:
        """Fit TF-IDF + SVD on training questions, return transformed matrix."""
        from sklearn.decomposition import TruncatedSVD  # type: ignore[import-untyped]
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._tfidf = TfidfVectorizer(
            max_features=500,
            ngram_range=(1, 2),
            stop_words="english",
            min_df=5,
        )
        tfidf_matrix = self._tfidf.fit_transform(questions)

        n_components = min(N_TEXT_COMPONENTS, tfidf_matrix.shape[1])
        self._svd = TruncatedSVD(n_components=n_components, random_state=42)
        result = self._svd.fit_transform(tfidf_matrix).astype(np.float32)
        logger.info(
            "market_size.text_fit",
            vocab_size=len(self._tfidf.vocabulary_),
            svd_components=n_components,
            explained_var=f"{self._svd.explained_variance_ratio_.sum():.2%}",
        )
        return result

    def _transform_text(self, questions: list[str]) -> np.ndarray:
        """Transform questions through fitted TF-IDF + SVD pipeline."""
        if self._tfidf is None or self._svd is None:
            return np.zeros((len(questions), N_TEXT_COMPONENTS), dtype=np.float32)
        tfidf_matrix = self._tfidf.transform(questions)
        return self._svd.transform(tfidf_matrix).astype(np.float32)

    def _build_X(
        self, features: pl.DataFrame, questions: list[str] | None
    ) -> np.ndarray:
        """Build the full feature matrix (numeric + optional text)."""
        # Use only features that exist in the DataFrame
        available = [f for f in self._feature_names if f in features.columns]
        X = features.select(available).to_numpy().astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)

        # Pad with zeros for any missing columns (backward compat with old models)
        if len(available) < len(self._feature_names):
            full_X = np.zeros(
                (X.shape[0], len(self._feature_names)), dtype=np.float32
            )
            idx = [self._feature_names.index(f) for f in available]
            full_X[:, idx] = X
            X = full_X

        if questions is not None:
            X_text = self._transform_text(questions)
            X = np.hstack([X, X_text])
        return X

    def _compute_sample_weights(self, volumes: np.ndarray) -> np.ndarray:
        """Compute sample weights inversely proportional to bucket frequency."""
        from polymarket_pipeline.strategies_impl.market_size.features import (
            bucket_from_volume,
        )

        bucket_labels = [
            bucket_from_volume(v, self._cfg.bucket_thresholds, self._cfg.buckets)
            for v in volumes
        ]
        bucket_indices = [self._cfg.buckets.index(b) for b in bucket_labels]
        n_classes = len(self._cfg.buckets)
        counts = np.bincount(bucket_indices, minlength=n_classes).astype(np.float64)
        counts_safe = np.maximum(counts, 1.0)
        class_weights = len(volumes) / (n_classes * counts_safe)
        return class_weights[bucket_indices].astype(np.float32)

    # ------------------------------------------------------------------
    # Train / predict
    # ------------------------------------------------------------------

    def train(
        self,
        features: pl.DataFrame,
        questions: list[str] | None = None,
        xgb_params: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        """Train the regressor on a feature DataFrame.

        Parameters
        ----------
        features
            Must contain all feature columns + ``total_volume`` for target.
        questions
            Optional list of market question strings (same length as features).
            If provided, TF-IDF + SVD text features are fit and appended.
        xgb_params
            Optional dict of XGBoost hyperparameters to override defaults.
            Used by Optuna tuning.

        Returns
        -------
        dict
            Training metrics: accuracy, per-class F1, RMSE.
        """
        import xgboost as xgb
        from sklearn.metrics import (  # type: ignore[import-untyped]
            accuracy_score,
            classification_report,
            mean_squared_error,
        )

        from polymarket_pipeline.strategies_impl.market_size.features import (
            bucket_from_volume,
        )

        # Log-volume target
        volumes = features["total_volume"].to_numpy().astype(np.float64)
        y = np.log1p(volumes)

        # Build numeric feature matrix
        available = [f for f in self._feature_names if f in features.columns]
        X = features.select(available).to_numpy().astype(np.float32)
        X = np.nan_to_num(X, nan=0.0)

        if len(available) < len(self._feature_names):
            full_X = np.zeros(
                (X.shape[0], len(self._feature_names)), dtype=np.float32
            )
            idx = [self._feature_names.index(f) for f in available]
            full_X[:, idx] = X
            X = full_X

        # Text features (fit + transform)
        if questions is not None:
            X_text = self._fit_text(questions)
            X = np.hstack([X, X_text])

        # Sample weights — upweight rare buckets (heavy, thick)
        sample_weights = self._compute_sample_weights(volumes)

        # Merge default params with any overrides
        params = {**DEFAULT_XGB_PARAMS, **(xgb_params or {})}

        self._model = xgb.XGBRegressor(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            learning_rate=params["learning_rate"],
            min_child_weight=params.get("min_child_weight", 3),
            subsample=params.get("subsample", 0.8),
            colsample_bytree=params.get("colsample_bytree", 0.8),
            reg_alpha=params.get("reg_alpha", 0.1),
            reg_lambda=params.get("reg_lambda", 1.0),
            objective="reg:squarederror",
            eval_metric="rmse",
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X, y, sample_weight=sample_weights)

        # Evaluate: predict volumes -> buckets -> classification metrics
        y_pred = self._model.predict(X)
        pred_vols = np.expm1(np.clip(y_pred, -50, 50))
        pred_buckets = [
            bucket_from_volume(v, self._cfg.bucket_thresholds, self._cfg.buckets)
            for v in pred_vols
        ]
        true_buckets = [
            bucket_from_volume(v, self._cfg.bucket_thresholds, self._cfg.buckets)
            for v in volumes
        ]

        acc = float(accuracy_score(true_buckets, pred_buckets))
        rmse = float(np.sqrt(mean_squared_error(y, y_pred)))

        report: dict[str, Any] = classification_report(
            true_buckets,
            pred_buckets,
            labels=list(self._cfg.buckets),
            output_dict=True,
            zero_division=0,
        )

        metrics: dict[str, float] = {"accuracy": acc, "rmse": rmse}
        for bucket_name in self._cfg.buckets:
            if bucket_name in report:
                metrics[f"f1_{bucket_name}"] = report[bucket_name]["f1-score"]

        logger.info(
            "market_size.train",
            accuracy=f"{acc:.3f}",
            rmse=f"{rmse:.3f}",
            n_samples=len(y),
        )
        return metrics

    def predict(
        self,
        features: pl.DataFrame,
        questions: list[str] | None = None,
    ) -> list[str]:
        """Predict volume buckets for each row.

        Parameters
        ----------
        features
            Must contain all feature columns (no ``total_volume`` needed).
        questions
            Optional list of market question strings. Must match the length
            of the features DataFrame. If the model was trained with text
            features, this should be provided for best accuracy.

        Returns
        -------
        list[str]
            Predicted bucket label per row.
        """
        if self._model is None:
            msg = "Model not trained or loaded — call train() or load() first"
            raise RuntimeError(msg)

        X = self._build_X(features, questions)
        y_pred = self._model.predict(X)
        return [self._log_vol_to_bucket(float(v)) for v in y_pred]

    def predict_proba(
        self,
        features: pl.DataFrame,
        questions: list[str] | None = None,
    ) -> pl.DataFrame:
        """Predict class probabilities for each row.

        Uses distance-based softmax: converts the regression prediction's
        distance from each bucket's log-threshold into a probability.

        Returns a DataFrame with columns: ``condition_id``, plus one
        probability column per bucket (e.g. ``p_thin``, ``p_med``, ...).
        """
        if self._model is None:
            msg = "Model not trained or loaded — call train() or load() first"
            raise RuntimeError(msg)

        X = self._build_X(features, questions)
        y_pred = self._model.predict(X)

        # Compute softmax probabilities based on distance to bucket centers
        bucket_centers = self._get_bucket_log_centers()
        n = len(y_pred)
        n_buckets = len(self._cfg.buckets)
        proba = np.zeros((n, n_buckets), dtype=np.float64)

        for i in range(n):
            # Negative squared distance = similarity
            dists = -((y_pred[i] - bucket_centers) ** 2)
            # Temperature scaling for sharper probabilities
            dists = dists * 2.0
            exp_dists = np.exp(dists - dists.max())
            proba[i] = exp_dists / exp_dists.sum()

        result = features.select("condition_id")
        for j, bucket in enumerate(self._cfg.buckets):
            result = result.with_columns(
                pl.Series(f"p_{bucket}", proba[:, j])
            )
        return result

    def _get_bucket_log_centers(self) -> np.ndarray:
        """Compute log-space center of each bucket for distance-based proba."""
        thresholds = self._cfg.bucket_thresholds
        centers = []
        # First bucket: center at log1p(threshold/2)
        centers.append(np.log1p(thresholds[0] / 2))
        # Middle buckets: geometric mean of adjacent thresholds
        for i in range(len(thresholds) - 1):
            centers.append(
                (np.log1p(thresholds[i]) + np.log1p(thresholds[i + 1])) / 2
            )
        # Last bucket: center at log1p(last_threshold * 3)
        centers.append(np.log1p(thresholds[-1] * 3))
        return np.array(centers)

    def predict_log_volume(
        self,
        features: pl.DataFrame,
        questions: list[str] | None = None,
    ) -> np.ndarray:
        """Predict raw log1p(volume) for each row (for Optuna evaluation)."""
        if self._model is None:
            msg = "Model not trained or loaded — call train() or load() first"
            raise RuntimeError(msg)
        X = self._build_X(features, questions)
        return self._model.predict(X)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialize the model + text pipeline to disk with joblib."""
        import joblib  # type: ignore[import-untyped]

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self._model,
                "feature_names": self._feature_names,
                "tfidf": self._tfidf,
                "svd": self._svd,
                "mode": "regression",
            },
            path,
        )
        logger.info("market_size.save", path=path)

    def load(self, path: str) -> None:
        """Load a serialized model from disk."""
        import joblib

        data = joblib.load(path)
        self._model = data["model"]
        self._feature_names = data["feature_names"]
        self._tfidf = data.get("tfidf")
        self._svd = data.get("svd")
        logger.info("market_size.load", path=path)
