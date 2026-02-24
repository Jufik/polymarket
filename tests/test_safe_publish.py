"""Tests for safe_publish error handling."""

from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker
from polymarket_pipeline.live.ingestors._publish import safe_publish


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
    result = await safe_publish(broker, message="test", topic="t", key=b"k", source="test")
    assert result is False
