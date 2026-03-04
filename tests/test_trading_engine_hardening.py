"""Tests for trading engine hardening fixes.

Covers:
- Budget gate race condition (asyncio.Lock)
- Filled price fallback rejection (no more 0.50 guess)
- Mark-to-market cache staleness guard
- Panic timeout + cancel verification
- Gateway file I/O safety
- Strategy exception safety in LiveRunner
- ClobClient timeout configuration
- Intent validation in gateway
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket_pipeline.execution.clob_client import (
    ClobClient,
    ClobOrderbook,
    OrderResult,
)
from polymarket_pipeline.execution.models import FillRecord, Position
from polymarket_pipeline.execution.panic import panic_close_all
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.live import LiveExecutor
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(
    strategy: str = "s1",
    size_usd: float = 100.0,
    max_price: float | None = 0.60,
    condition_id: str = "0xabc123",
    side: str = "BUY",
    outcome: str = "YES",
) -> TradeIntent:
    return TradeIntent(
        strategy=strategy,
        condition_id=condition_id,
        side=side,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        size_usd=size_usd,
        urgency="immediate",
        max_price=max_price,
        reason="test",
        signal_time=time.time(),
    )


def _make_fill(
    intent: TradeIntent,
    status: FillStatus = FillStatus.FILLED,
    filled_size: float | None = None,
) -> Fill:
    return Fill(
        intent_id="test-fill",
        strategy=intent.strategy,
        condition_id=intent.condition_id,
        side=intent.side,
        outcome=intent.outcome,
        filled_price=0.50,
        filled_size_usd=filled_size or intent.size_usd,
        fee_usd=0.0,
        status=status,
        filled_at=time.time(),
    )


class MockClobClient:
    """Fake CLOB client for testing."""

    def __init__(self, result: OrderResult | None = None) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._result = result or OrderResult(
            order_id="test-1", success=True, filled_price=0.55, filled_size=10.0
        )
        self._ob_cache: dict[str, tuple[float, ClobOrderbook]] = {}

    async def submit_order(self, **kwargs: Any) -> OrderResult:
        self.submitted.append(kwargs)
        return self._result

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_open_orders(self, condition_id: str | None = None) -> list[Any]:
        return []


class MockPositionTracker:
    """Fake position tracker."""

    def __init__(
        self,
        positions: list[Position] | None = None,
    ) -> None:
        self._positions = {p.condition_id: p for p in (positions or [])}
        self.recorded_fills: list[FillRecord] = []

    def get_position(self, condition_id: str) -> Position | None:
        return self._positions.get(condition_id)

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def record_fill(self, fill: FillRecord) -> Position | None:
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


_TEST_TOKEN_MAP = {"asset_yes_abc123": ("0xabc123", "YES")}


# ============================================================================
# 1. Budget Gate Race Condition
# ============================================================================


@pytest.mark.asyncio
async def test_budget_gate_serializes_concurrent_intents() -> None:
    """Two concurrent intents for the same strategy cannot both pass budget."""
    # Executor that takes 50ms to simulate concurrent execution
    async def slow_execute(i: TradeIntent) -> Fill:
        await asyncio.sleep(0.05)
        return _make_fill(i)

    executor = AsyncMock()
    executor.execute = slow_execute

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 150.0},  # Only enough for one intent
    )

    # Fire two $100 intents concurrently
    results = await asyncio.gather(
        gw.submit(_intent("s1", 100.0)),
        gw.submit(_intent("s1", 100.0)),
    )

    filled = [r for r in results if r.status == FillStatus.FILLED]
    rejected = [r for r in results if r.status == FillStatus.REJECTED]

    # One must be filled, one must be rejected (budget = 150, each = 100)
    assert len(filled) == 1, f"Expected 1 filled, got {len(filled)}"
    assert len(rejected) == 1, f"Expected 1 rejected, got {len(rejected)}"
    assert "budget" in (rejected[0].error or "").lower()


@pytest.mark.asyncio
async def test_budget_refunded_on_executor_exception() -> None:
    """If executor raises, reserved budget is refunded."""
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("CLOB down"))

    gw = ExecutionGateway(
        executor=executor,
        strategy_budgets={"s1": 200.0},
    )

    fill1 = await gw.submit(_intent("s1", 100.0))
    assert fill1.status == FillStatus.REJECTED

    # Budget should be fully refunded — strategy can retry
    assert gw._strategy_spent.get("s1", 0.0) == 0.0

    # Second attempt should be allowed (budget not exhausted)
    executor.execute = AsyncMock(side_effect=lambda i: _make_fill(i))
    fill2 = await gw.submit(_intent("s1", 200.0))
    assert fill2.status == FillStatus.FILLED


# ============================================================================
# 2. Filled Price Fallback Rejection
# ============================================================================


@pytest.mark.asyncio
async def test_live_executor_rejects_when_no_fill_price() -> None:
    """CLOB returns success=True but filled_price=None.

    With urgency-based execution, this now dispatches to _execute_immediate.
    If the order is still resting (returned by get_open_orders), it gets
    cancelled → REJECTED (FOK semantics).
    """
    from polymarket_pipeline.execution.clob_client import OpenOrder
    from polymarket_pipeline.strategies.execution.live import OrderConfig

    no_price_result = OrderResult(
        order_id="order-123",
        success=True,
        filled_price=None,  # <-- API returned no fill price
        filled_size=None,
    )
    clob = MockClobClient(result=no_price_result)
    # Make get_open_orders return the order (still resting)
    resting = OpenOrder(
        order_id="order-123",
        condition_id="0xabc123",
        asset_id="asset_yes_abc123",
        side="BUY",
        price=0.60,
        size=10.0,
        size_matched=0.0,
    )
    clob.get_open_orders = AsyncMock(return_value=[resting])  # type: ignore[method-assign]
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP,
        order_config=OrderConfig(immediate_cancel_delay_s=0.01),
    )

    intent = _intent(size_usd=10.0, max_price=0.60)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.REJECTED
    assert "FOK" in (fill.error or "")
    assert fill.filled_price == 0.0
    # No fill should be recorded in tracker
    assert len(tracker.recorded_fills) == 0


@pytest.mark.asyncio
async def test_live_executor_rejects_zero_fill_price() -> None:
    """CLOB returns filled_price=0.0 → dispatches to _execute_immediate.

    Order still resting → cancelled → REJECTED.
    """
    from polymarket_pipeline.execution.clob_client import OpenOrder
    from polymarket_pipeline.strategies.execution.live import OrderConfig

    zero_price_result = OrderResult(
        order_id="order-456",
        success=True,
        filled_price=0.0,
        filled_size=10.0,
    )
    clob = MockClobClient(result=zero_price_result)
    resting = OpenOrder(
        order_id="order-456",
        condition_id="0xabc123",
        asset_id="asset_yes_abc123",
        side="BUY",
        price=0.60,
        size=10.0,
        size_matched=0.0,
    )
    clob.get_open_orders = AsyncMock(return_value=[resting])  # type: ignore[method-assign]
    tracker = MockPositionTracker()
    executor = LiveExecutor(
        clob, tracker, token_market_map=_TEST_TOKEN_MAP,
        order_config=OrderConfig(immediate_cancel_delay_s=0.01),
    )

    intent = _intent(size_usd=10.0, max_price=0.60)
    fill = await executor.execute(intent)

    assert fill.status == FillStatus.REJECTED
    assert "FOK" in (fill.error or "")


# ============================================================================
# 3. Mark-to-Market Cache Staleness
# ============================================================================


def test_mtm_ignores_stale_cache() -> None:
    """Cached orderbook older than 30s should NOT be used for MTM."""
    clob = MockClobClient()
    tracker = MockPositionTracker()
    executor = LiveExecutor(clob, tracker, token_market_map=_TEST_TOKEN_MAP)

    # Inject a stale cache entry (60s old)
    stale_ts = time.monotonic() - 60.0
    stale_ob = ClobOrderbook(best_bid=0.90, best_ask=0.92, spread=0.02, fetched_at=time.time() - 60)
    clob._ob_cache["asset_yes_abc123"] = (stale_ts, stale_ob)

    pos = Position(
        condition_id="0xabc123",
        asset_id="asset_yes_abc123",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=0.0,
        last_price=0.55,
    )

    mtm = executor._mark_to_market(pos)
    # Should use last_price fallback (0.55), NOT stale cache (0.91 mid)
    assert mtm == pytest.approx(100.0 * 0.55, abs=0.01)


def test_mtm_uses_fresh_cache() -> None:
    """Cached orderbook less than 30s old SHOULD be used for MTM."""
    clob = MockClobClient()
    tracker = MockPositionTracker()
    executor = LiveExecutor(clob, tracker, token_market_map=_TEST_TOKEN_MAP)

    # Inject a fresh cache entry (2s old)
    fresh_ts = time.monotonic() - 2.0
    fresh_ob = ClobOrderbook(best_bid=0.80, best_ask=0.82, spread=0.02, fetched_at=time.time())
    clob._ob_cache["asset_yes_abc123"] = (fresh_ts, fresh_ob)

    pos = Position(
        condition_id="0xabc123",
        asset_id="asset_yes_abc123",
        side="BUY",
        size=100.0,
        avg_entry=0.50,
        cost_basis=50.0,
        unrealized_pnl=0.0,
        last_price=0.55,
    )

    mtm = executor._mark_to_market(pos)
    # Should use cache mid-price (0.81), NOT last_price (0.55)
    expected_mid = (0.80 + 0.82) / 2
    assert mtm == pytest.approx(100.0 * expected_mid, abs=0.01)


# ============================================================================
# 4. Panic Timeout + Cancel Verification
# ============================================================================


@pytest.mark.asyncio
async def test_panic_times_out_on_hung_api() -> None:
    """Panic should not hang forever if CLOB API is unresponsive."""

    class HungClobClient:
        async def get_open_orders(self) -> list:
            return []

        def get_all_positions(self) -> list:
            return []

    class HungTracker:
        def get_all_positions(self) -> list[Position]:
            return [
                Position(
                    condition_id="0x1", asset_id="a1", side="BUY",
                    size=100.0, avg_entry=0.5, cost_basis=50.0,
                    unrealized_pnl=0.0, last_price=0.5,
                ),
            ]

    class SlowClob(HungClobClient):
        async def get_open_orders(self) -> list:
            return []

        async def submit_order(self, **kw: Any) -> OrderResult:
            await asyncio.sleep(100)  # Simulates hung API
            return OrderResult(order_id="", success=False)

    results = await panic_close_all(SlowClob(), HungTracker(), timeout_s=0.5)  # type: ignore[arg-type]
    # Should return within ~0.5s, not hang for 100s
    assert len(results) >= 1
    assert any(not r.success for r in results)


# ============================================================================
# 5. Gateway File I/O Safety
# ============================================================================


@pytest.mark.asyncio
async def test_gateway_survives_file_io_error(tmp_path: Any) -> None:
    """Gateway should not crash if JSONL log file is unwritable."""
    import os

    # Create a read-only directory to cause file write failure
    log_path = tmp_path / "readonly" / "intents.jsonl"
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    os.chmod(readonly_dir, 0o444)

    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=lambda i: _make_fill(i))

    try:
        gw = ExecutionGateway(
            executor=executor,
            log_path=log_path,
            strategy_budgets={"s1": 500.0},
        )

        # Should NOT crash despite file I/O error
        fill = await gw.submit(_intent("s1", 100.0))
        assert fill.status == FillStatus.FILLED
    finally:
        os.chmod(readonly_dir, 0o755)


# ============================================================================
# 6. Intent Validation in Gateway
# ============================================================================


@pytest.mark.asyncio
async def test_gateway_rejects_zero_size_intent() -> None:
    """Intent with size_usd <= 0 should be immediately rejected."""
    executor = AsyncMock()
    gw = ExecutionGateway(executor=executor)

    fill = await gw.submit(_intent(size_usd=0.0))
    assert fill.status == FillStatus.REJECTED
    assert "invalid size_usd" in (fill.error or "")

    # Executor should NOT be called
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_rejects_negative_size_intent() -> None:
    """Intent with negative size_usd should be immediately rejected."""
    executor = AsyncMock()
    gw = ExecutionGateway(executor=executor)

    fill = await gw.submit(_intent(size_usd=-50.0))
    assert fill.status == FillStatus.REJECTED
    assert "invalid size_usd" in (fill.error or "")
    executor.execute.assert_not_called()


# ============================================================================
# 7. ClobClient Timeout Configuration
# ============================================================================


def test_clob_client_has_split_timeouts() -> None:
    """ClobClient should use split timeouts instead of a single 30s value."""
    import httpx

    client = ClobClient(base_url="https://example.com")
    timeout = client._client.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 5.0
    assert timeout.read == 10.0
    assert timeout.write == 5.0


def test_clob_client_has_orderbook_lock() -> None:
    """ClobClient should have an asyncio.Lock for orderbook cache."""
    client = ClobClient(base_url="https://example.com")
    assert isinstance(client._ob_lock, asyncio.Lock)


# ============================================================================
# 8. Gateway Budget Lock Exists
# ============================================================================


def test_gateway_has_budget_lock() -> None:
    """Gateway should have an asyncio.Lock for budget serialization."""
    executor = AsyncMock()
    gw = ExecutionGateway(executor=executor)
    assert isinstance(gw._budget_lock, asyncio.Lock)
