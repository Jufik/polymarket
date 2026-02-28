"""Tests for P0 fixes: PositionTracker race condition, gateway budget, fill dedup."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.execution.models import FillRecord, Position
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(tz=UTC)


def _fill_record(
    intent_id: str = "intent-1",
    strategy: str = "test",
    condition_id: str = "0xabc",
    side: str = "BUY",
    outcome: str = "NO",
    price: float = 0.50,
    size_usd: float = 100.0,
    fee_usd: float = 0.0,
) -> FillRecord:
    return FillRecord(
        intent_id=intent_id,
        strategy=strategy,
        condition_id=condition_id,
        asset_id="asset-1",
        side=side,
        outcome=outcome,
        price=price,
        size_usd=size_usd,
        fee_usd=fee_usd,
        filled_at=NOW,
    )


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


def _make_fill(intent: TradeIntent, status: FillStatus = FillStatus.FILLED) -> Fill:
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


# ============================================================================
# Gateway: executor exception does not crash or consume budget
# ============================================================================


@pytest.mark.asyncio
async def test_gateway_catches_executor_exception() -> None:
    """If the executor raises, gateway returns REJECTED (not crash)."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 500.0},
    )

    fill = await gw.submit(_intent("s1", 100.0))
    assert fill.status == FillStatus.REJECTED
    assert "exception" in (fill.error or "").lower()

    # Budget should NOT be consumed
    assert gw._strategy_spent.get("s1", 0.0) == 0.0


@pytest.mark.asyncio
async def test_gateway_rejected_fill_preserves_budget() -> None:
    """A REJECTED fill from executor must not consume budget."""
    executor = AsyncMock()
    executor.execute = AsyncMock(
        side_effect=lambda i: _make_fill(i, FillStatus.REJECTED)
    )

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 200.0},
    )

    fill1 = await gw.submit(_intent("s1", 100.0))
    assert fill1.status == FillStatus.REJECTED

    # Budget should be untouched — strategy can retry
    assert gw._strategy_spent.get("s1", 0.0) == 0.0

    # A subsequent FILLED intent should succeed (budget not exhausted)
    executor.execute = AsyncMock(side_effect=lambda i: _make_fill(i, FillStatus.FILLED))
    fill2 = await gw.submit(_intent("s1", 200.0))
    assert fill2.status == FillStatus.FILLED
    assert gw._strategy_spent["s1"] == 200.0


@pytest.mark.asyncio
async def test_gateway_charges_actual_filled_size() -> None:
    """Budget tracks filled_size_usd (actual), not intent.size_usd (requested)."""
    executor = AsyncMock()

    # Executor fills at partial size (100 requested, 80 filled)
    def partial_fill(i: TradeIntent) -> Fill:
        return Fill(
            intent_id="pf-1",
            strategy=i.strategy,
            condition_id=i.condition_id,
            side=i.side,
            outcome=i.outcome,
            filled_price=0.50,
            filled_size_usd=80.0,  # less than requested
            fee_usd=0.0,
            status=FillStatus.FILLED,
            filled_at=time.time(),
        )

    executor.execute = AsyncMock(side_effect=partial_fill)

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 200.0},
    )

    fill = await gw.submit(_intent("s1", 100.0))
    assert fill.status == FillStatus.FILLED
    # Should charge 80 (actual), not 100 (requested)
    assert gw._strategy_spent["s1"] == 80.0


# ============================================================================
# PositionTracker: verify module-level constants
# ============================================================================


def test_fills_ddl_has_unique_intent_id() -> None:
    """Verify the UNIQUE constraint on intent_id exists in the DDL."""
    from polymarket_pipeline.execution.position_tracker import FILLS_DDL

    assert "uq_fills_intent_id" in FILLS_DDL
    assert "UNIQUE (intent_id)" in FILLS_DDL


def test_position_tracker_has_lock() -> None:
    """Verify PositionTracker uses asyncio.Lock for concurrency safety."""
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    # Create with a dummy pool (won't be used)
    tracker = PositionTracker(pool=None)
    assert isinstance(tracker._lock, asyncio.Lock)


def test_migrate_fills_unique_sql_exists() -> None:
    """Verify the migration SQL for adding the UNIQUE constraint."""
    from polymarket_pipeline.execution.position_tracker import _MIGRATE_FILLS_UNIQUE

    assert "uq_fills_intent_id" in _MIGRATE_FILLS_UNIQUE
    assert "ALTER TABLE fills" in _MIGRATE_FILLS_UNIQUE


def test_record_fill_returns_none_type() -> None:
    """Verify record_fill signature allows None return (for dedup)."""
    import inspect

    from polymarket_pipeline.execution.position_tracker import PositionTracker

    sig = inspect.signature(PositionTracker.record_fill)
    ret = sig.return_annotation
    # Should be Position | None (or Union)
    assert "None" in str(ret)
