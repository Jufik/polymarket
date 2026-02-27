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
        """Load the model if not yet loaded. Returns True if model is available.

        Raises ``FileNotFoundError`` if the model file is missing — fail-fast
        so supervisor restarts the process and the operator sees the error.
        """
        if self._model_loaded:
            return True
        try:
            self._classifier.load(self._cfg.model_path)
            self._model_loaded = True
            return True
        except FileNotFoundError:
            raise FileNotFoundError(
                f"MarketSize model not found at {self._cfg.model_path!r}. "
                f"Train with: uv run python scripts/train_market_size_classifier.py"
            ) from None

    async def compute(self, backend: FeatureBackend) -> None:
        """Classify all markets using the backend's data.

        For ClickHouse backends, pushes trade aggregation to the server
        to avoid loading 438M+ raw rows into Python memory.
        """
        if not self._ensure_model():
            return

        if getattr(backend, "supports_sql", False) is True:
            await self._compute_from_ch(backend)
        else:
            await self._compute_from_polars(backend)

    async def _compute_from_ch(self, backend: FeatureBackend) -> None:
        """Push aggregation to ClickHouse — returns ~455K rows, not 438M."""
        import polars as pl

        cfg = self._cfg
        window_secs = cfg.feature_window_hours * 3600
        early_secs = window_secs // 6
        mid_secs = window_secs // 2

        # Pre-aggregate trades per condition_id in ClickHouse
        trade_agg = await backend.query_custom(f"""
            SELECT
                condition_id,
                count() AS trades_window,
                sum(toFloat64(price) * toFloat64(size)) AS vol_window,
                uniq(maker) AS traders_window,
                countIf(secs <= {early_secs}) AS trades_early,
                sumIf(toFloat64(price) * toFloat64(size), secs <= {early_secs}) AS vol_early,
                uniqIf(maker, secs <= {early_secs}) AS traders_early,
                countIf(secs <= {mid_secs}) AS trades_mid,
                sumIf(toFloat64(price) * toFloat64(size), secs <= {mid_secs}) AS vol_mid,
                uniqIf(maker, secs <= {mid_secs}) AS traders_mid,
                stddevPopStable(toFloat64(price)) AS price_std,
                max(toFloat64(price)) - min(toFloat64(price)) AS price_range,
                avg(toFloat64(size)) AS avg_trade_size,
                max(toFloat64(size)) AS max_trade_size,
                sum(toFloat64(price) * toFloat64(size)) AS total_volume,
                toUnixTimestamp64Milli(min(timestamp)) / 1000.0 AS first_ts
            FROM (
                SELECT *,
                    dateDiff('second', min(timestamp) OVER (PARTITION BY condition_id), timestamp) AS secs
                FROM trades_raw
                WHERE timestamp >= now() - INTERVAL 30 DAY
                  AND condition_id IN (
                      SELECT condition_id FROM markets WHERE resolution_value = 0
                  )
            )
            WHERE secs <= {window_secs}
            GROUP BY condition_id
            HAVING trades_window >= 2
        """)

        if trade_agg.is_empty():
            self._buckets = {}
            self._proba = {}
            logger.info("market_size.compute", count=0)
            return

        # Cast to float
        for col in trade_agg.columns:
            if col != "condition_id":
                trade_agg = trade_agg.with_columns(pl.col(col).cast(pl.Float64))

        # Derived ratio features (in Polars — cheap on ~455K rows)
        features = trade_agg.with_columns(
            (pl.col("vol_window") / pl.col("trades_window").clip(1)).alias("vol_per_trade"),
            (pl.col("vol_window") / pl.col("traders_window").clip(1)).alias("vol_per_trader"),
            (pl.col("vol_mid") / pl.col("vol_early").clip(lower_bound=0.01)).alias("vol_growth_early_mid"),
            (pl.col("vol_window") / pl.col("vol_mid").clip(lower_bound=0.01)).alias("vol_growth_mid_full"),
            (pl.col("vol_window") + 1).log().alias("log_vol_window"),
            (pl.col("vol_early") + 1).log().alias("log_vol_early"),
            (pl.col("vol_mid") + 1).log().alias("log_vol_mid"),
            (pl.col("trades_window") / (window_secs / 3600)).alias("trades_per_hour"),
            (pl.col("traders_window").cast(pl.Float64) / pl.col("trades_window").clip(1)).alias("trader_concentration"),
            (pl.col("first_ts") % 86400 / 3600).cast(pl.Int32).alias("hour_of_day"),
            (pl.col("first_ts") / 86400).cast(pl.Int32).mod(7).alias("day_of_week"),
        )

        # Markets metadata (small — ~455K rows)
        markets = await backend.query_markets()
        if "neg_risk" not in markets.columns:
            markets = markets.with_columns(pl.lit(False).alias("neg_risk"))
        if "event_id" not in markets.columns:
            self._buckets = {}
            self._proba = {}
            return
        markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

        features = features.join(
            markets.select("condition_id", "event_id", "neg_risk"),
            on="condition_id", how="left",
        )
        features = features.with_columns(pl.col("neg_risk").cast(pl.Int8))

        # Events
        try:
            events = await backend.query_custom(
                "SELECT id AS event_id, dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_date, "
                "CAST(volume AS Float64) AS volume, "
                "CAST(liquidity AS Float64) AS liquidity "
                "FROM events WHERE end_date IS NOT NULL "
                "AND end_date > toDateTime64('1970-01-02', 6)"
            )
            for col in ["end_date", "volume", "liquidity"]:
                events = events.with_columns(pl.col(col).cast(pl.Float64))
        except Exception:
            events = pl.DataFrame(schema={"event_id": pl.Utf8, "end_date": pl.Float64, "volume": pl.Float64, "liquidity": pl.Float64})

        event_market_count = markets.group_by("event_id").agg(pl.len().alias("event_n_markets"))
        features = features.join(events, on="event_id", how="left")
        features = features.join(event_market_count, on="event_id", how="left")
        features = features.with_columns(
            ((pl.col("end_date") - pl.col("first_ts")) / 3600.0).alias("time_remaining_hours"),
            (pl.col("volume").fill_null(0) + 1).log().alias("log_event_volume"),
            (pl.col("liquidity").fill_null(0) + 1).log().alias("log_event_liquidity"),
            (pl.col("vol_window") / pl.col("liquidity").fill_null(1).clip(lower_bound=1.0)).alias("vol_to_liquidity"),
        )
        features = features.rename({"volume": "event_volume", "liquidity": "event_liquidity"})

        # Tags
        try:
            tags = await backend.query_custom(
                "SELECT et.event_id, t.label AS tag "
                "FROM event_tags et JOIN tags t ON et.tag_id = t.id"
            )
            tags = tags.sort("tag").unique(subset=["event_id"], keep="first")
        except Exception:
            tags = pl.DataFrame(schema={"event_id": pl.Utf8, "tag": pl.Utf8})

        for tag_name in cfg.top_tags:
            col_name = f"tag_{tag_name.replace(' ', '_')}"
            matching = tags.filter(pl.col("tag") == tag_name)["event_id"].to_list()
            features = features.with_columns(
                pl.col("event_id").is_in(matching).cast(pl.Int8).alias(col_name),
            )

        features = features.drop_nulls(subset=["time_remaining_hours"])
        if features.is_empty():
            self._buckets = {}
            self._proba = {}
            return

        self._predict_and_store(features, markets)

    async def _compute_from_polars(self, backend: FeatureBackend) -> None:
        """Fallback for Polars backend (offline/backtest)."""
        import polars as pl

        trades = await backend.query_trades()
        if trades.is_empty():
            self._buckets = {}
            self._proba = {}
            logger.info("market_size.compute", count=0)
            return

        markets = await backend.query_markets()
        events = pl.DataFrame(schema={"id": pl.Utf8, "end_date": pl.Float64, "volume": pl.Float64, "liquidity": pl.Float64})
        tags = pl.DataFrame(schema={"event_id": pl.Utf8, "tag": pl.Utf8})

        if "neg_risk" not in markets.columns:
            markets = markets.with_columns(pl.lit(False).alias("neg_risk"))
        if "event_id" not in markets.columns:
            self._buckets = {}
            self._proba = {}
            return

        if "timestamp" in trades.columns:
            trades = trades.with_columns(pl.col("timestamp").cast(pl.Float64))
        for col in ["price", "size"]:
            if col in trades.columns:
                trades = trades.with_columns(pl.col(col).cast(pl.Float64))
        markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

        features = compute_features_polars(trades, markets, events, tags, self._cfg)
        if features.is_empty():
            self._buckets = {}
            self._proba = {}
            return
        features = features.drop_nulls(subset=["time_remaining_hours"])
        if features.is_empty():
            self._buckets = {}
            self._proba = {}
            return

        self._predict_and_store(features, markets)

    def _predict_and_store(self, features: Any, markets: Any) -> None:
        """Run XGBoost prediction and store buckets/probabilities."""
        questions: list[str] | None = None
        if "question" in markets.columns:
            question_map = dict(
                zip(
                    markets["condition_id"].to_list(),
                    markets["question"].to_list(),
                    strict=False,
                )
            )
            cids = features["condition_id"].to_list()
            questions = [question_map.get(cid, "") for cid in cids]
        else:
            cids = features["condition_id"].to_list()

        feat_cols = features.drop("total_volume")
        preds = self._classifier.predict(feat_cols, questions=questions)
        self._buckets = dict(zip(cids, preds, strict=True))

        proba_df = self._classifier.predict_proba(feat_cols, questions=questions)
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
