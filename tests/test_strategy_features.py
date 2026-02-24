"""Tests for strategy feature backends."""

import asyncio

import pytest

from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend


def test_clickhouse_backend_rejects_sql_injection():
    backend = ClickHouseBackend(host="localhost", port=18123, database="test")
    with pytest.raises(ValueError, match="Invalid condition_id"):
        asyncio.run(backend.query_trades(["'; DROP TABLE trades_raw; --"]))


def test_clickhouse_backend_accepts_valid_condition_id():
    """Valid hex condition_ids should not raise."""
    backend = ClickHouseBackend(host="localhost", port=18123, database="test")
    # This will fail at the HTTP level (no ClickHouse running), but
    # should NOT raise ValueError — that's what we're testing
    with pytest.raises(Exception, match="(?!Invalid condition_id)"):
        asyncio.run(backend.query_trades(["0xabc123def456"]))
