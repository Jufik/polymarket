"""Tests for LiveExecutor — real execution via CLOB API with position limits."""

from __future__ import annotations

import time
from typing import Any

from polymarket_pipeline.execution.clob_client import OrderResult
from polymarket_pipeline.execution.models import FillRecord, Position
from polymarket_pipeline.strategies.execution.live import LiveExecutor
from polymarket_pipeline.strategies.types import FillStatus, TradeIntent

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockClobClient:
    """Fake CLOB client that records submitted orders."""

    def __init__(self, result: OrderResult | None = None) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._result = result or OrderResult(
            order_id="test-1", success=True, filled_price=0.55, filled_size=10.0
        )

    async def submit_order(self, **kwargs: Any) -> OrderResult:
        self.submitted.append(kwargs)
        return self._result


class MockPositionTracker:
    """Fake position tracker that records fills in memory."""

    def __init__(
        self,
        positions: list[Position] | None = None,
        total_exposure: float = 0.0,
    ) -> None:
        self._positions = {p.condition_id: p for p in (positions or [])}
        self._total_exposure = total_exposure
        self.recorded_fills: list[FillRecord] = []

    def get_position(self, condition_id: str) -> Position | None:
        return self._positions.get(condition_id)

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_total_exposure(self) -> float:
        return self._total_exposure

    async def record_fill(self, fill: FillRecord) -> Position:
        self.recorded_fills.append(fill)
        return Position(
            condition_id=fill.condition_id,
            asset_id=fill.asset_id,
            side=fill.side,
            size=fill.size_usd / fill.price if fill.price > 0 else 0,
            avg_entry=fill.price,
            cost_basis=fill.size_usd,
            unrealized_pnl=0.0,
            last_price=fill.price,
        )


# Default token_market_map for tests: asset_id -> (condition_id, outcome)
_TEST_TOKEN_MAP = {"asset_yes_abc123": ("0xabc123", "YES")}


def _make_intent(
    *,
    condition_id: str = "0xabc123",
    side: str = "BUY",
    outcome: str = "YES",
    size_usd: float = 10.0,
    max_price: float | None = 0.60,
) -> TradeIntent:
    return TradeIntent(
        strategy="test_strategy",
        condition_id=condition_id,
        side=side,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        size_usd=size_usd,
        urgency="immediate",
        max_price=max_price,
        reason="unit test",
        signal_time=time.time(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_live_executor_fills_successfully() -> None:
    """Happy path: CLOB returns success, fill is recorded."""
    clob = MockClobClient()
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker,
        token_market_map=_TEST_TOKEN_MAP,
        max_position_usd=100.0, max_total_exposure_usd=500.0,
    )

    intent = _make_intent(size_usd=10.0, max_price=0.60)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.55  # from mock OrderResult
    assert fill.filled_size_usd == 10.0
    assert fill.condition_id == intent.condition_id
    assert fill.strategy == intent.strategy
    assert fill.side == intent.side
    assert fill.outcome == intent.outcome
    assert fill.error is None

    # Verify CLOB was called
    assert len(clob.submitted) == 1
    assert clob.submitted[0]["condition_id"] == intent.condition_id
    assert clob.submitted[0]["size"] == 10.0

    # Verify fill was recorded in tracker
    assert len(tracker.recorded_fills) == 1
    assert tracker.recorded_fills[0].condition_id == intent.condition_id
    assert tracker.recorded_fills[0].price == 0.55


async def test_live_executor_rejects_over_position_limit() -> None:
    """Position at 95 USD, trying to add 10 USD with limit=100 -> REJECTED."""
    existing = Position(
        condition_id="0xabc123",
        asset_id="0xabc123",
        side="BUY",
        size=100.0,  # 100 tokens
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=45.0,
        last_price=0.95,  # 100 * 0.95 = 95 USD current value
    )
    clob = MockClobClient()
    tracker = MockPositionTracker(positions=[existing], total_exposure=95.0)
    executor = LiveExecutor(
        clob, tracker,
        token_market_map=_TEST_TOKEN_MAP,
        max_position_usd=100.0, max_total_exposure_usd=500.0,
    )

    intent = _make_intent(condition_id="0xabc123", size_usd=10.0)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "position limit" in fill.error
    assert fill.filled_price == 0.0
    assert fill.filled_size_usd == 0.0

    # CLOB should NOT have been called
    assert len(clob.submitted) == 0
    # No fill should be recorded
    assert len(tracker.recorded_fills) == 0


async def test_live_executor_rejects_over_total_exposure() -> None:
    """Total exposure at 490 USD, trying to add 20 USD with limit=500 -> REJECTED."""
    clob = MockClobClient()
    tracker = MockPositionTracker(total_exposure=490.0)
    executor = LiveExecutor(
        clob, tracker,
        token_market_map=_TEST_TOKEN_MAP,
        max_position_usd=1000.0, max_total_exposure_usd=500.0,
    )

    intent = _make_intent(size_usd=20.0)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.REJECTED
    assert fill.error is not None
    assert "exposure limit" in fill.error
    assert fill.filled_price == 0.0

    # CLOB should NOT have been called
    assert len(clob.submitted) == 0
    assert len(tracker.recorded_fills) == 0


async def test_live_executor_handles_order_failure() -> None:
    """CLOB returns success=False -> REJECTED fill with error."""
    failed_result = OrderResult(order_id="", success=False, error="insufficient balance")
    clob = MockClobClient(result=failed_result)
    tracker = MockPositionTracker()
    executor = LiveExecutor(clob, tracker, token_market_map=_TEST_TOKEN_MAP)

    intent = _make_intent(size_usd=10.0)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.REJECTED
    assert fill.error == "insufficient balance"
    assert fill.filled_price == 0.0
    assert fill.filled_size_usd == 0.0

    # CLOB WAS called (limits passed), but order failed
    assert len(clob.submitted) == 1
    # No fill recorded since order failed
    assert len(tracker.recorded_fills) == 0


async def test_live_executor_allows_under_limits() -> None:
    """Position exists but under limit -> order proceeds."""
    existing = Position(
        condition_id="0xabc123",
        asset_id="0xabc123",
        side="BUY",
        size=50.0,
        avg_entry=0.50,
        cost_basis=25.0,
        unrealized_pnl=0.0,
        last_price=0.50,  # 50 * 0.50 = 25 USD current value
    )
    clob = MockClobClient()
    tracker = MockPositionTracker(positions=[existing], total_exposure=25.0)
    executor = LiveExecutor(
        clob, tracker,
        token_market_map=_TEST_TOKEN_MAP,
        max_position_usd=100.0, max_total_exposure_usd=500.0,
    )

    intent = _make_intent(condition_id="0xabc123", size_usd=10.0)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.FILLED
    assert len(clob.submitted) == 1
    assert len(tracker.recorded_fills) == 1


async def test_live_executor_market_order_when_no_max_price() -> None:
    """When max_price is None, order_type should be MARKET."""
    clob = MockClobClient()
    tracker = MockPositionTracker()
    executor = LiveExecutor(clob, tracker, token_market_map=_TEST_TOKEN_MAP)

    intent = _make_intent(max_price=None)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.FILLED
    assert len(clob.submitted) == 1
    assert clob.submitted[0]["order_type"].value == "MARKET"


async def test_live_executor_limit_order_when_max_price_set() -> None:
    """When max_price is set, order_type should be LIMIT."""
    clob = MockClobClient()
    tracker = MockPositionTracker()
    executor = LiveExecutor(clob, tracker, token_market_map=_TEST_TOKEN_MAP)

    intent = _make_intent(max_price=0.65)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.FILLED
    assert len(clob.submitted) == 1
    assert clob.submitted[0]["order_type"].value == "LIMIT"
    assert clob.submitted[0]["price"] == 0.65
