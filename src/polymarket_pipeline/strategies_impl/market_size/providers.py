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
    - ``market_size_bucket``: ``dict[str, str]`` — condition_id -> bucket label
    - ``market_size_proba``: ``dict[str, dict[str, float]]`` — condition_id -> {bucket: probability}
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
                    "id": pl.Utf8,
                    "end_date": pl.Float64,
                    "volume": pl.Float64,
                    "liquidity": pl.Float64,
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

        self._buckets = dict(zip(cids, preds, strict=True))

        # Probabilities
        proba_df = self._classifier.predict_proba(features.drop("total_volume"))
        self._proba = {}
        for row in proba_df.iter_rows(named=True):
            cid = row["condition_id"]
            self._proba[cid] = {
                bucket: row[f"p_{bucket}"] for bucket in self._cfg.buckets
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
