"""Tests for ExecutionGateway delay support."""

from __future__ import annotations

import time

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.types import TradeIntent


def _intent() -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id="0xcond",
        side="BUY",
        outcome="YES",
        size_usd=10.0,
        urgency="immediate",
        max_price=0.60,
        reason="test",
        signal_time=1000.0,
    )


async def test_gateway_no_delay_is_fast() -> None:
    """Gateway with delay_s=0 should execute almost instantly."""
    gw = ExecutionGateway(executor=SimulatedExecutor(), delay_s=0.0)
    t0 = time.monotonic()
    fill = await gw.submit(_intent())
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert fill.condition_id == "0xcond"


async def test_gateway_with_delay_introduces_sleep() -> None:
    """Gateway with delay_s > 0 should introduce actual delay."""
    gw = ExecutionGateway(executor=SimulatedExecutor(), delay_s=0.15)
    t0 = time.monotonic()
    fill = await gw.submit(_intent())
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.14  # allow small timing variance
    assert fill.condition_id == "0xcond"


async def test_gateway_default_no_delay() -> None:
    """Default gateway has no delay."""
    gw = ExecutionGateway(executor=SimulatedExecutor())
    assert gw.delay_s == 0.0
