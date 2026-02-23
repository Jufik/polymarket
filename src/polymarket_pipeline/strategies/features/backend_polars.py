"""Polars-backed FeatureBackend for backtest and replay modes.

Holds trades and markets as in-memory DataFrames. No external dependencies.
"""

from __future__ import annotations

from typing import Any

import polars as pl


class PolarsBackend:
    """FeatureBackend backed by in-memory Polars DataFrames.

    Parameters
    ----------
    trades:
        DataFrame of trades (must have ``condition_id`` column).
    markets:
        DataFrame of market metadata.
    """

    __slots__ = ("_markets", "_trades")

    def __init__(self, trades: pl.DataFrame, markets: pl.DataFrame) -> None:
        self._trades = trades
        self._markets = markets

    async def query_trades(self, condition_ids: list[str] | None = None) -> pl.DataFrame:
        """Return trades, optionally filtered by *condition_ids*."""
        if condition_ids is None:
            return self._trades
        return self._trades.filter(pl.col("condition_id").is_in(condition_ids))

    async def query_markets(self) -> pl.DataFrame:
        """Return market metadata."""
        return self._markets

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Not supported for Polars backend — use ClickHouseBackend for SQL."""
        msg = "query_custom is not supported by PolarsBackend; use ClickHouseBackend"
        raise NotImplementedError(msg)
