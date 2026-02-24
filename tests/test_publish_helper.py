"""Tests for safe_publish timeout helper."""

import asyncio

import pytest

from polymarket_pipeline.live.ingestors._publish import safe_publish


class _SlowBroker:
    async def publish(self, **kwargs: object) -> None:
        await asyncio.sleep(10)


class _FastBroker:
    def __init__(self) -> None:
        self.published: list[dict[str, object]] = []

    async def publish(self, **kwargs: object) -> None:
        self.published.append(kwargs)


@pytest.mark.asyncio
async def test_safe_publish_returns_false_on_timeout() -> None:
    result = await safe_publish(
        _SlowBroker(), message="x", topic="t", key=b"k", source="test",
    )
    assert result is False


@pytest.mark.asyncio
async def test_safe_publish_returns_true_on_success() -> None:
    broker = _FastBroker()
    result = await safe_publish(
        broker, message="x", topic="t", key=b"k", source="test",
    )
    assert result is True
    assert len(broker.published) == 1
