"""Tests for LiveRunner.request_refresh() out-of-cycle refresh."""
from __future__ import annotations

import asyncio

import pytest

from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.runners.live import LiveRunner


class FakeProvider:
    """Minimal provider that tracks refresh calls."""

    name = "fake"

    def __init__(self) -> None:
        self.refresh_count = 0
        self.features: dict[str, object] = {"test_key": "v0"}

    async def compute(self, backend: object) -> None:
        pass

    async def on_trade(self, trade: object) -> None:
        pass

    async def refresh(self, backend: object) -> None:
        self.refresh_count += 1
        self.features = {"test_key": f"v{self.refresh_count}"}

    def get_features(self) -> dict[str, object]:
        return self.features


class FakeGateway:
    async def submit(self, intent: object) -> object:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_request_refresh_triggers_immediate_refresh() -> None:
    """request_refresh() should cause _refresh_loop to run immediately."""
    provider = FakeProvider()
    ctx = InMemoryContext()
    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=FakeGateway(),  # type: ignore[arg-type]
        ctx=ctx,
        backend=object(),  # type: ignore[arg-type]
        refresh_interval_s=3600.0,  # very long — would never fire naturally
    )

    await runner.start_background_loops()

    # Request an immediate refresh
    runner.request_refresh()

    # Give the refresh loop time to process the event
    await asyncio.sleep(0.1)

    assert provider.refresh_count >= 1
    assert ctx._features.get("test_key") == "v1"

    await runner.stop()


@pytest.mark.asyncio
async def test_refresh_loop_still_works_on_timer() -> None:
    """Normal timer-based refresh should still function."""
    provider = FakeProvider()
    ctx = InMemoryContext()
    runner = LiveRunner(
        strategies=[],
        providers=[provider],
        gateway=FakeGateway(),  # type: ignore[arg-type]
        ctx=ctx,
        backend=object(),  # type: ignore[arg-type]
        refresh_interval_s=0.05,  # very short for test
    )

    await runner.start_background_loops()
    await asyncio.sleep(0.15)

    assert provider.refresh_count >= 1

    await runner.stop()
