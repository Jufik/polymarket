"""Tests for order lifecycle management — urgency-based execution.

Covers:
- immediate (FOK): instant fill, late fill, cancel resting
- patient (resting): polls until filled, timeout rejected, partial fill
- gateway budget reconciliation for PARTIAL
- paper executor ignores urgency
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from polymarket_pipeline.execution.clob_client import OpenOrder, OrderResult
from polymarket_pipeline.execution.models import FillRecord, Position
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.live import LiveExecutor, OrderConfig
from polymarket_pipeline.strategies.execution.paper import PaperExecutor
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockClobClient:
    """Fake CLOB client with controllable open_orders sequence."""

    def __init__(
        self,
        result: OrderResult | None = None,
        open_orders_sequence: list[list[OpenOrder]] | None = None,
    ) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._result = result or OrderResult(
            order_id="ord-1", success=True, filled_price=None, filled_size=None,
        )
        # Each call to get_open_orders pops the next list from the sequence
        self._open_orders_seq = list(open_orders_sequence or [])
        self._open_orders_idx = 0

    async def submit_order(self, **kwargs: Any) -> OrderResult:
        self.submitted.append(kwargs)
        return self._result

    async def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    async def get_open_orders(self, condition_id: str | None = None) -> list[OpenOrder]:
        if self._open_orders_idx < len(self._open_orders_seq):
            result = self._open_orders_seq[self._open_orders_idx]
            self._open_orders_idx += 1
            return result
        return []


class MockPositionTracker:
    """Fake position tracker."""

    def __init__(self) -> None:
        self.recorded_fills: list[FillRecord] = []

    def get_position(self, condition_id: str) -> Position | None:
        return None

    def get_all_positions(self) -> list[Position]:
        return []

    async def record_fill(self, fill: FillRecord) -> Position | None:
        self.recorded_fills.append(fill)
        return None


_TEST_TOKEN_MAP = {"asset_yes_abc": ("0xabc", "YES")}

_FAST_CONFIG = OrderConfig(
    patient_timeout_s=0.5,
    patient_poll_interval_s=0.1,
    immediate_cancel_delay_s=0.05,
)


def _make_intent(
    *,
    urgency: str = "immediate",
    condition_id: str = "0xabc",
    max_price: float | None = 0.60,
    size_usd: float = 10.0,
) -> TradeIntent:
    return TradeIntent(
        strategy="test",
        condition_id=condition_id,
        side="BUY",
        outcome="YES",
        size_usd=size_usd,
        urgency=urgency,  # type: ignore[arg-type]
        max_price=max_price,
        reason="test",
        signal_time=time.time(),
    )


def _make_open_order(
    order_id: str = "ord-1",
    size: float = 10.0,
    size_matched: float = 0.0,
) -> OpenOrder:
    return OpenOrder(
        order_id=order_id,
        condition_id="0xabc",
        asset_id="asset_yes_abc",
        side="BUY",
        price=0.60,
        size=size,
        size_matched=size_matched,
    )


# ---------------------------------------------------------------------------
# 1. Immediate — instant fill
# ---------------------------------------------------------------------------


async def test_immediate_instant_fill() -> None:
    """CLOB returns fill_price → FILLED immediately, no polling."""
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=0.58, filled_size=10.0),
    )
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=_FAST_CONFIG,
    )

    fill = await executor.execute(_make_intent(urgency="immediate"))

    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.58
    assert fill.filled_size_usd == 10.0
    assert fill.order_id == "ord-1"
    assert len(tracker.recorded_fills) == 1
    # Should NOT have called get_open_orders (instant fill)
    assert clob._open_orders_idx == 0


# ---------------------------------------------------------------------------
# 2. Immediate — cancels resting order
# ---------------------------------------------------------------------------


async def test_immediate_cancels_resting() -> None:
    """No fill_price, order still open after delay → cancel → REJECTED."""
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=None),
        open_orders_sequence=[
            [_make_open_order()],  # still open after delay
        ],
    )
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=_FAST_CONFIG,
    )

    fill = await executor.execute(_make_intent(urgency="immediate"))

    assert fill.status == FillStatus.REJECTED
    assert fill.order_id == "ord-1"
    assert "FOK" in (fill.error or "")
    assert "ord-1" in clob.cancelled
    assert len(tracker.recorded_fills) == 0


# ---------------------------------------------------------------------------
# 3. Immediate — late fill (order gone before we check)
# ---------------------------------------------------------------------------


async def test_immediate_late_fill() -> None:
    """No fill_price, but order gone from open list → FILLED."""
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=None),
        open_orders_sequence=[
            [],  # order already gone (filled between submit and check)
        ],
    )
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=_FAST_CONFIG,
    )

    fill = await executor.execute(_make_intent(urgency="immediate", max_price=0.60))

    assert fill.status == FillStatus.FILLED
    assert fill.filled_price == 0.60  # uses max_price
    assert fill.order_id == "ord-1"
    assert len(tracker.recorded_fills) == 1


# ---------------------------------------------------------------------------
# 4. Patient — polls until filled
# ---------------------------------------------------------------------------


async def test_patient_polls_until_filled() -> None:
    """Order fills on 3rd poll → FILLED."""
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=None),
        open_orders_sequence=[
            [_make_open_order(size_matched=0.0)],   # poll 1: still open
            [_make_open_order(size_matched=3.0)],    # poll 2: partial progress
            [],                                       # poll 3: gone → filled
        ],
    )
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=_FAST_CONFIG,
    )

    fill = await executor.execute(_make_intent(urgency="patient", max_price=0.60))

    assert fill.status == FillStatus.FILLED
    assert fill.order_id == "ord-1"
    assert len(tracker.recorded_fills) == 1


# ---------------------------------------------------------------------------
# 5. Patient — timeout rejected
# ---------------------------------------------------------------------------


async def test_patient_timeout_rejected() -> None:
    """Order never fills, never matches → cancel → REJECTED."""
    # Return the same open order forever (more entries than polls will happen)
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=None),
        open_orders_sequence=[
            [_make_open_order(size_matched=0.0)] for _ in range(20)
        ],
    )
    tracker = MockPositionTracker()
    fast_timeout = OrderConfig(
        patient_timeout_s=0.3,
        patient_poll_interval_s=0.05,
        immediate_cancel_delay_s=0.05,
    )
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=fast_timeout,
    )

    fill = await executor.execute(_make_intent(urgency="patient"))

    assert fill.status == FillStatus.REJECTED
    assert fill.order_id == "ord-1"
    assert "patient timeout" in (fill.error or "")
    assert "ord-1" in clob.cancelled
    assert len(tracker.recorded_fills) == 0


# ---------------------------------------------------------------------------
# 6. Patient — partial fill at timeout
# ---------------------------------------------------------------------------


async def test_patient_partial_fill() -> None:
    """size_matched > 0 at timeout → cancel remainder → PARTIAL."""
    clob = MockClobClient(
        result=OrderResult(order_id="ord-1", success=True, filled_price=None),
        open_orders_sequence=[
            [_make_open_order(size=10.0, size_matched=5.0)] for _ in range(20)
        ],
    )
    tracker = MockPositionTracker()
    fast_timeout = OrderConfig(
        patient_timeout_s=0.3,
        patient_poll_interval_s=0.05,
        immediate_cancel_delay_s=0.05,
    )
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP, order_config=fast_timeout,
    )

    fill = await executor.execute(_make_intent(urgency="patient", size_usd=10.0))

    assert fill.status == FillStatus.PARTIAL
    assert fill.order_id == "ord-1"
    # 50% matched → 50% of intent size
    assert fill.filled_size_usd == pytest.approx(5.0)
    assert "ord-1" in clob.cancelled
    assert len(tracker.recorded_fills) == 1


# ---------------------------------------------------------------------------
# 7. Gateway budget — PARTIAL adjusts budget (not full refund)
# ---------------------------------------------------------------------------


async def test_gateway_budget_partial() -> None:
    """PARTIAL fill adjusts budget to actual fill, not full refund."""

    class FakeExecutor:
        async def execute(self, intent: TradeIntent) -> Fill:
            return Fill(
                intent_id="test-1",
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                side=intent.side,
                outcome=intent.outcome,
                filled_price=0.55,
                filled_size_usd=5.0,  # half the requested 10
                fee_usd=0.0,
                status=FillStatus.PARTIAL,
                filled_at=time.time(),
                order_id="ord-1",
            )

    gateway = ExecutionGateway(
        executor=FakeExecutor(),  # type: ignore[arg-type]
        strategy_budgets={"test": 100.0},
    )

    intent = _make_intent(urgency="patient", size_usd=10.0)
    fill = await gateway.submit(intent)

    assert fill.status == FillStatus.PARTIAL
    # Budget spent should be 5.0 (actual fill), not 10.0 (requested) or 0.0 (refund)
    assert gateway._strategy_spent["test"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 8. Paper executor ignores urgency
# ---------------------------------------------------------------------------


async def test_paper_ignores_urgency() -> None:
    """Both urgency modes produce the same result in PaperExecutor."""

    class FakeContext:
        def get_orderbook_by_asset(self, asset_id: str) -> Any:
            return None

    class FakeClobClient:
        _client: Any = None

    ctx = FakeContext()

    executor = PaperExecutor(
        ctx=ctx,  # type: ignore[arg-type]
        clob_client=None,
        token_map={"0xabc": {"YES": "asset_yes_abc"}},
        order_config=None,
    )

    # Both urgency modes should behave the same: rejected (no orderbook)
    immediate = _make_intent(urgency="immediate")
    patient = _make_intent(urgency="patient")

    fill_imm = await executor.execute(immediate)
    fill_pat = await executor.execute(patient)

    assert fill_imm.status == fill_pat.status
    # Both should be rejected since there's no orderbook
    assert fill_imm.status == FillStatus.REJECTED
    assert fill_pat.status == FillStatus.REJECTED
