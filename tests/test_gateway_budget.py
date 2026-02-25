"""Tests for per-strategy capital budgeting in ExecutionGateway."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


def _intent(strategy: str = "s1", size_usd: float = 100.0) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id="0xmkt1",
        side="BUY",
        outcome="NO",
        size_usd=size_usd,
        urgency="patient",
        max_price=None,
        reason="test",
        signal_time=time.time(),
    )


def _fill(intent: TradeIntent, status: FillStatus = FillStatus.FILLED) -> Fill:
    return Fill(
        intent_id="test-fill",
        strategy=intent.strategy,
        condition_id=intent.condition_id,
        side=intent.side,
        outcome=intent.outcome,
        filled_price=0.50,
        filled_size_usd=intent.size_usd,
        fee_usd=0.0,
        status=status,
        filled_at=time.time(),
    )


@pytest.mark.asyncio
async def test_gateway_rejects_over_budget() -> None:
    """Intents exceeding strategy budget should be rejected."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 200.0},
    )

    # First intent: $100, budget $200 → OK
    fill1 = await gw.submit(_intent("s1", 100.0))
    assert fill1.status == FillStatus.FILLED

    # Second: $100, used=$100, budget=$200 → OK
    fill2 = await gw.submit(_intent("s1", 100.0))
    assert fill2.status == FillStatus.FILLED

    # Third: $100, used=$200, budget=$200 → REJECTED
    fill3 = await gw.submit(_intent("s1", 100.0))
    assert fill3.status == FillStatus.REJECTED
    assert "budget" in (fill3.error or "").lower()


@pytest.mark.asyncio
async def test_gateway_no_budget_passes_through() -> None:
    """Strategies without a budget have no spending cap."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(executor=executor)

    for _ in range(10):
        fill = await gw.submit(_intent("no_budget_strat", 1000.0))
        assert fill.status == FillStatus.FILLED


@pytest.mark.asyncio
async def test_gateway_separate_budgets_per_strategy() -> None:
    """Each strategy has an independent budget."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _fill(i))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 100.0, "s2": 300.0},
    )

    # S1 uses its full budget
    fill_s1 = await gw.submit(_intent("s1", 100.0))
    assert fill_s1.status == FillStatus.FILLED

    # S1 is now exhausted
    fill_s1b = await gw.submit(_intent("s1", 1.0))
    assert fill_s1b.status == FillStatus.REJECTED

    # S2 still has budget
    fill_s2 = await gw.submit(_intent("s2", 300.0))
    assert fill_s2.status == FillStatus.FILLED
