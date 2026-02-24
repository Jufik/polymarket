"""Tests for safe_publish error handling."""

import pytest
from unittest.mock import AsyncMock

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.circuit_breaker import CircuitBreaker


@pytest.mark.asyncio
async def test_safe_publish_handles_connection_error():
    broker = AsyncMock()
    broker.publish.side_effect = ConnectionError("broker down")
    cb = CircuitBreaker()
    result = await safe_publish(
        broker, message="test", topic="t", key=b"k", source="test", circuit_breaker=cb
    )
    assert result is False
    assert cb._consecutive_failures == 1


@pytest.mark.asyncio
async def test_safe_publish_handles_unexpected_error():
    broker = AsyncMock()
    broker.publish.side_effect = RuntimeError("unexpected")
    result = await safe_publish(
        broker, message="test", topic="t", key=b"k", source="test"
    )
    assert result is False
