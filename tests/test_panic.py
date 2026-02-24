"""Tests for the panic close module."""

from __future__ import annotations

import pytest

from polymarket_pipeline.execution.clob_client import (
    OpenOrder,
    OrderResult,
    OrderSide,
    OrderType,
)
from polymarket_pipeline.execution.models import Position
from polymarket_pipeline.execution.panic import panic_close_all

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockClobClient:
    def __init__(self, open_orders: list[OpenOrder] | None = None) -> None:
        self.cancelled: list[str] = []
        self.submitted: list[dict[str, object]] = []
        self._open_orders = open_orders or []
        self._call_order: list[str] = []

    async def get_open_orders(self, condition_id: str | None = None) -> list[OpenOrder]:
        self._call_order.append("get_open_orders")
        return self._open_orders

    async def cancel_order(self, order_id: str) -> bool:
        self._call_order.append(f"cancel:{order_id}")
        self.cancelled.append(order_id)
        return True

    async def submit_order(self, **kwargs: object) -> OrderResult:
        self._call_order.append("submit_order")
        self.submitted.append(kwargs)
        return OrderResult(order_id="close-1", success=True)


class MockPositionTracker:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self._positions = positions or []

    def get_all_positions(self) -> list[Position]:
        return self._positions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pos(
    condition_id: str = "cond-1",
    asset_id: str = "asset-1",
    side: str = "BUY",
    size: float = 100.0,
    avg_entry: float = 0.50,
) -> Position:
    return Position(
        condition_id=condition_id,
        asset_id=asset_id,
        side=side,
        size=size,
        avg_entry=avg_entry,
        cost_basis=size * avg_entry,
        unrealized_pnl=0.0,
        last_price=avg_entry,
    )


def _order(order_id: str = "ord-1") -> OpenOrder:
    return OpenOrder(
        order_id=order_id,
        condition_id="cond-1",
        asset_id="asset-1",
        side="BUY",
        price=0.50,
        size=100.0,
        size_matched=0.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_panic_close_all_cancels_orders_first() -> None:
    """Open orders must be cancelled before any position close orders are sent."""
    orders = [_order("ord-1"), _order("ord-2")]
    positions = [_pos("c1"), _pos("c2")]

    clob = MockClobClient(open_orders=orders)
    tracker = MockPositionTracker(positions=positions)

    await panic_close_all(clob, tracker)  # type: ignore[arg-type]

    # Verify cancels happened before submits
    cancel_indices = [i for i, c in enumerate(clob._call_order) if c.startswith("cancel:")]
    submit_indices = [i for i, c in enumerate(clob._call_order) if c == "submit_order"]

    assert len(cancel_indices) == 2
    assert len(submit_indices) == 2
    assert max(cancel_indices) < min(submit_indices)


@pytest.mark.asyncio
async def test_panic_close_all_sells_all_positions() -> None:
    """Each position should get a SELL market order."""
    positions = [_pos("c1", asset_id="a1", size=50.0), _pos("c2", asset_id="a2", size=75.0)]
    clob = MockClobClient()
    tracker = MockPositionTracker(positions=positions)

    results = await panic_close_all(clob, tracker)  # type: ignore[arg-type]

    assert len(results) == 2
    assert all(r.success for r in results)

    # Check submitted orders match positions
    assert clob.submitted[0]["condition_id"] == "c1"
    assert clob.submitted[0]["asset_id"] == "a1"
    assert clob.submitted[0]["side"] == OrderSide.SELL
    assert clob.submitted[0]["size"] == 50.0
    assert clob.submitted[0]["order_type"] == OrderType.MARKET

    assert clob.submitted[1]["condition_id"] == "c2"
    assert clob.submitted[1]["asset_id"] == "a2"
    assert clob.submitted[1]["side"] == OrderSide.SELL
    assert clob.submitted[1]["size"] == 75.0


@pytest.mark.asyncio
async def test_panic_close_all_skips_zero_size() -> None:
    """Positions with size <= 0 should be skipped."""
    positions = [
        _pos("c1", size=100.0),
        _pos("c2", size=0.0),
        _pos("c3", size=-5.0),
    ]
    clob = MockClobClient()
    tracker = MockPositionTracker(positions=positions)

    results = await panic_close_all(clob, tracker)  # type: ignore[arg-type]

    assert len(results) == 1
    assert clob.submitted[0]["condition_id"] == "c1"


@pytest.mark.asyncio
async def test_panic_close_all_empty_portfolio() -> None:
    """Empty portfolio should return empty results with no orders submitted."""
    clob = MockClobClient()
    tracker = MockPositionTracker(positions=[])

    results = await panic_close_all(clob, tracker)  # type: ignore[arg-type]

    assert results == []
    assert clob.submitted == []
    assert clob.cancelled == []
