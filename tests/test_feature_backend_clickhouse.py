"""Tests for ClickHouseBackend — FeatureBackend for live modes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import polars as pl

from polymarket_pipeline.strategies.features.backend_clickhouse import (
    ClickHouseBackend,
)
from polymarket_pipeline.strategies.protocol import FeatureBackend


def test_satisfies_protocol() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    assert isinstance(backend, FeatureBackend)


async def test_query_custom_calls_execute() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame({"x": [1, 2, 3]})
        result = await backend.query_custom("SELECT 1 AS x")
        mock_exec.assert_called_once_with("SELECT 1 AS x")
        assert len(result) == 3


async def test_query_trades_no_filter() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame(
            {
                "condition_id": ["0xa"],
                "maker": ["alice"],
            }
        )
        result = await backend.query_trades()
        assert len(result) == 1
        query = mock_exec.call_args[0][0]
        assert "WHERE" not in query


async def test_query_trades_with_filter() -> None:
    backend = ClickHouseBackend(host="localhost", port=18123, database="polymarket")
    with patch.object(backend, "_execute", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = pl.DataFrame(
            {
                "condition_id": ["0xa"],
                "maker": ["alice"],
            }
        )
        await backend.query_trades(condition_ids=["0xa", "0xb"])
        query = mock_exec.call_args[0][0]
        assert "WHERE" in query
        assert "condition_id" in query
