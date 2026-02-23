"""Tests for VectorizedRunner --- Polars batch execution wrapper."""

from __future__ import annotations

import polars as pl

from polymarket_pipeline.strategies.runners.vectorized import (
    VectorizedResult,
    VectorizedRunner,
)

# ---------------------------------------------------------------------------
# Mock strategies
# ---------------------------------------------------------------------------


class MockVectorizedStrategy:
    """Returns a fixed DataFrame with two signals."""

    name = "mock_vec"

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "condition_id": ["0xa", "0xb"],
                "signal_time": [1000.0, 2000.0],
                "side": ["BUY", "BUY"],
                "outcome": ["YES", "NO"],
                "size_usd": [50.0, 30.0],
            }
        )


class EmptyStrategy:
    """Returns an empty DataFrame with the correct schema."""

    name = "empty_vec"

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "condition_id": pl.Series([], dtype=pl.Utf8),
                "signal_time": pl.Series([], dtype=pl.Float64),
                "side": pl.Series([], dtype=pl.Utf8),
                "outcome": pl.Series([], dtype=pl.Utf8),
                "size_usd": pl.Series([], dtype=pl.Float64),
            }
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_TRADES = pl.LazyFrame({"trade_id": pl.Series([], dtype=pl.Utf8)})
_EMPTY_MARKETS = pl.LazyFrame({"condition_id": pl.Series([], dtype=pl.Utf8)})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_runner_produces_vectorized_result_with_correct_count() -> None:
    """Runner wraps strategy output in VectorizedResult with correct total_signals."""
    runner = VectorizedRunner(strategy=MockVectorizedStrategy())
    result = runner.run(trades=_EMPTY_TRADES, markets=_EMPTY_MARKETS)

    assert isinstance(result, VectorizedResult)
    assert result.total_signals == 2
    assert result.signals.shape == (2, 5)


def test_runner_handles_empty_result() -> None:
    """Runner handles strategies that return zero signals."""
    runner = VectorizedRunner(strategy=EmptyStrategy())
    result = runner.run(trades=_EMPTY_TRADES, markets=_EMPTY_MARKETS)

    assert isinstance(result, VectorizedResult)
    assert result.total_signals == 0
    assert result.signals.shape[0] == 0


def test_signals_dataframe_has_expected_columns() -> None:
    """The signals DataFrame preserves the columns returned by the strategy."""
    runner = VectorizedRunner(strategy=MockVectorizedStrategy())
    result = runner.run(trades=_EMPTY_TRADES, markets=_EMPTY_MARKETS)

    expected_cols = {"condition_id", "signal_time", "side", "outcome", "size_usd"}
    assert set(result.signals.columns) == expected_cols


def test_signals_dataframe_values() -> None:
    """Spot-check that the DataFrame values are passed through unchanged."""
    runner = VectorizedRunner(strategy=MockVectorizedStrategy())
    result = runner.run(trades=_EMPTY_TRADES, markets=_EMPTY_MARKETS)

    assert result.signals["condition_id"].to_list() == ["0xa", "0xb"]
    assert result.signals["signal_time"].to_list() == [1000.0, 2000.0]
    assert result.signals["side"].to_list() == ["BUY", "BUY"]
    assert result.signals["outcome"].to_list() == ["YES", "NO"]
    assert result.signals["size_usd"].to_list() == [50.0, 30.0]
