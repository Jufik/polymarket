"""Tests for PolarsBackend — in-memory FeatureBackend for backtest."""

from __future__ import annotations

import polars as pl
import pytest

from polymarket_pipeline.strategies.features.backend_polars import PolarsBackend
from polymarket_pipeline.strategies.protocol import FeatureBackend


@pytest.fixture
def trades_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "condition_id": ["0xa", "0xa", "0xb"],
            "maker": ["alice", "bob", "charlie"],
            "side": ["BUY", "SELL", "BUY"],
            "published_at": [1.0, 2.0, 3.0],
        }
    )


@pytest.fixture
def markets_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "condition_id": ["0xa", "0xb"],
            "question": ["Will A?", "Will B?"],
            "active": [True, True],
        }
    )


@pytest.fixture
def backend(trades_df: pl.DataFrame, markets_df: pl.DataFrame) -> PolarsBackend:
    return PolarsBackend(trades=trades_df, markets=markets_df)


async def test_satisfies_feature_backend_protocol(backend: PolarsBackend) -> None:
    assert isinstance(backend, FeatureBackend)


async def test_query_trades_all(backend: PolarsBackend) -> None:
    df = await backend.query_trades()
    assert len(df) == 3


async def test_query_trades_filtered(backend: PolarsBackend) -> None:
    df = await backend.query_trades(condition_ids=["0xa"])
    assert len(df) == 2
    assert df["condition_id"].to_list() == ["0xa", "0xa"]


async def test_query_trades_empty_filter(backend: PolarsBackend) -> None:
    df = await backend.query_trades(condition_ids=["0xnonexistent"])
    assert len(df) == 0


async def test_query_markets(backend: PolarsBackend) -> None:
    df = await backend.query_markets()
    assert len(df) == 2
    assert "condition_id" in df.columns


async def test_query_custom_raises(backend: PolarsBackend) -> None:
    with pytest.raises(NotImplementedError):
        await backend.query_custom("SELECT 1")
