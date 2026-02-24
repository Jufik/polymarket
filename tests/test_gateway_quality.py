"""Tests for ExecutionGateway quality-based rejection."""

from __future__ import annotations

import pytest

from polymarket_pipeline.live.quality.state import CheckResult, PipelineState, ReadinessState
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


class MockExecutor:
    async def execute(self, intent: TradeIntent) -> Fill:
        return Fill(
            intent_id="mock",
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=0.50,
            filled_size_usd=intent.size_usd,
            fee_usd=0.0,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )


def _intent() -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id="cond-1",
        side="BUY",
        outcome="YES",
        size_usd=10.0,
        urgency="immediate",
        max_price=0.55,
        reason="test",
        signal_time=1000.0,
    )


@pytest.mark.asyncio
async def test_gateway_allows_when_ready() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=True)})
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_gateway_rejects_when_degraded() -> None:
    state = ReadinessState()
    state.update({"ch": CheckResult(ok=False, reason="down")})
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "degraded" in fill.error


@pytest.mark.asyncio
async def test_gateway_rejects_when_red() -> None:
    state = ReadinessState(degraded_grace_s=0.0)
    state.update({"ch": CheckResult(ok=False)})
    # After grace_s=0.0, first failure goes to RED
    assert state.current == PipelineState.RED
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "red" in fill.error


@pytest.mark.asyncio
async def test_gateway_rejects_when_closing() -> None:
    state = ReadinessState()
    state.set_closing()
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "closing" in fill.error


@pytest.mark.asyncio
async def test_gateway_rejects_when_safe_stop() -> None:
    state = ReadinessState()
    state.set_safe_stop()
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "safe_stop" in fill.error


@pytest.mark.asyncio
async def test_gateway_allows_when_no_quality_state() -> None:
    gw = ExecutionGateway(MockExecutor())
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_gateway_allows_when_checking() -> None:
    """CHECKING state (initial) should allow trades through."""
    state = ReadinessState()
    assert state.current == PipelineState.CHECKING
    gw = ExecutionGateway(MockExecutor(), quality_state=state)
    fill = await gw.submit(_intent())
    assert fill.status == FillStatus.FILLED
