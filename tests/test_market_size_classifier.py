"""Tests for market size classifier."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import polars as pl
import pytest

from polymarket_pipeline.strategies_impl.market_size.classifier import (
    MarketSizeClassifier,
)
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig
from polymarket_pipeline.strategies_impl.market_size.features import (
    bucket_from_volume,
    compute_features_polars,
)
from polymarket_pipeline.strategies_impl.market_size.providers import (
    MarketSizeProvider,
)

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------


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
                1700000000 + i * 720
                for i in range(10)
            ]
            + [
                # 0xB: 5 trades spread over 1 hour
                1700100000 + i * 720
                for i in range(5)
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


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------


def _make_synthetic_features(cfg: MarketSizeConfig, n: int, seed: int = 42) -> pl.DataFrame:
    """Build a synthetic feature DataFrame for testing."""
    rng = np.random.default_rng(seed)
    features = pl.DataFrame(
        {
            "condition_id": [f"0x{i:04x}" for i in range(n)],
            "trades_window": rng.integers(0, 100, n).tolist(),
            "vol_window": rng.exponential(500, n).tolist(),
            "traders_window": rng.integers(1, 30, n).tolist(),
            "time_remaining_hours": rng.exponential(100, n).tolist(),
            "event_n_markets": rng.integers(1, 20, n).tolist(),
            "event_volume": rng.exponential(50000, n).tolist(),
            "event_liquidity": rng.exponential(25000, n).tolist(),
            "neg_risk": rng.integers(0, 2, n).tolist(),
            "hour_of_day": rng.integers(0, 24, n).tolist(),
            "day_of_week": rng.integers(0, 7, n).tolist(),
            "total_volume": rng.exponential(10000, n).tolist(),
        }
    )
    for tag in cfg.top_tags:
        col_name = f"tag_{tag.replace(' ', '_')}"
        features = features.with_columns(
            pl.Series(col_name, rng.integers(0, 2, n).tolist())
        )
    return features


def test_classifier_train_and_predict() -> None:
    """Train on synthetic data, predict, verify output shape and labels."""
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    features = _make_synthetic_features(cfg, 200)
    clf.train(features)

    # Predict on same data (no total_volume needed for predict)
    preds = clf.predict(features.drop("total_volume"))
    assert len(preds) == 200
    assert set(preds).issubset(set(cfg.buckets))


def test_classifier_save_and_load() -> None:
    """Model should round-trip through joblib serialization."""
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    features = _make_synthetic_features(cfg, 100)
    clf.train(features)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "model.joblib"
        clf.save(str(path))

        clf2 = MarketSizeClassifier(cfg)
        clf2.load(str(path))

        preds1 = clf.predict(features.drop("total_volume"))
        preds2 = clf2.predict(features.drop("total_volume"))
        assert preds1 == preds2


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_computes_buckets() -> None:
    """Provider should classify markets when compute() is called."""
    cfg = MarketSizeConfig()
    clf = MarketSizeClassifier(cfg)

    features = _make_synthetic_features(cfg, 200)
    clf.train(features)

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = f"{tmpdir}/model.joblib"
        clf.save(model_path)

        # Create provider with this model
        provider_cfg = MarketSizeConfig(model_path=model_path)
        provider = MarketSizeProvider(config=provider_cfg)

        # Mock backend that returns our feature data
        backend = AsyncMock()
        backend.query_trades = AsyncMock(
            return_value=pl.DataFrame(
                {
                    "condition_id": ["0xA"] * 10,
                    "maker": [f"0xm{i}" for i in range(10)],
                    "price": [0.3] * 10,
                    "size": [10.0] * 10,
                    "timestamp": [1700000000 + i * 100 for i in range(10)],
                }
            )
        )
        backend.query_markets = AsyncMock(
            return_value=pl.DataFrame(
                {
                    "condition_id": ["0xA"],
                    "event_id": ["evt1"],
                    "neg_risk": [False],
                }
            )
        )
        backend.query_custom = AsyncMock(
            side_effect=[
                # events query
                pl.DataFrame(
                    {
                        "id": ["evt1"],
                        "end_date": [1700200000.0],
                        "volume": [50000.0],
                        "liquidity": [25000.0],
                    }
                ),
                # tags query
                pl.DataFrame(
                    {
                        "event_id": ["evt1"],
                        "tag": ["Politics"],
                    }
                ),
            ]
        )

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
        dummy = _make_synthetic_features(cfg, 50)
        clf.train(dummy)
        clf.save(cfg.model_path)

        provider = MarketSizeProvider(config=cfg)

        backend = AsyncMock()
        backend.query_trades = AsyncMock(return_value=pl.DataFrame())
        backend.query_markets = AsyncMock(return_value=pl.DataFrame())

        await provider.compute(backend)
        result = provider.get_features()
        assert result["market_size_bucket"] == {}
