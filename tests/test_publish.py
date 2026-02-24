"""Tests for safe_publish: error handling, timeout, and circuit breaker."""

import asyncio
from unittest.mock import AsyncMock

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker
from polymarket_pipeline.live.ingestors._publish import safe_publish


class _SlowBroker:
    async def publish(self, **kwargs: object) -> None:
        await asyncio.sleep(10)


class _FastBroker:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    async def publish(self, **kwargs: object) -> None:
        self.published.append(kwargs)


async def test_safe_publish_handles_connection_error() -> None:
    broker = AsyncMock()
    broker.publish.side_effect = ConnectionError("broker down")
    cb = CircuitBreaker()
    result = await safe_publish(
        broker, message="test", topic="t", key=b"k", source="test", circuit_breaker=cb
    )
    assert result is False
    assert cb._consecutive_failures == 1


async def test_safe_publish_handles_unexpected_error() -> None:
    broker = AsyncMock()
    broker.publish.side_effect = RuntimeError("unexpected")
    result = await safe_publish(broker, message="test", topic="t", key=b"k", source="test")
    assert result is False


async def test_safe_publish_returns_false_on_timeout() -> None:
    result = await safe_publish(
        _SlowBroker(),
        message="x",
        topic="t",
        key=b"k",
        source="test",
    )
    assert result is False


async def test_safe_publish_returns_true_on_success() -> None:
    broker = _FastBroker()
    result = await safe_publish(
        broker,
        message="x",
        topic="t",
        key=b"k",
        source="test",
    )
    assert result is True
    assert len(broker.published) == 1


async def test_safe_publish_respects_circuit_breaker() -> None:
    cb = CircuitBreaker(failure_threshold=1, cooldown_s=1000.0)
    cb.record_failure()  # Trip the breaker

    broker = _FastBroker()
    result = await safe_publish(
        broker,
        message="x",
        topic="t",
        key=b"k",
        source="test",
        circuit_breaker=cb,
    )
    assert result is False
    assert len(broker.published) == 0  # Should not have published
