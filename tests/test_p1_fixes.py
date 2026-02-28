"""Tests for P1 fixes: retry logic, parallel panic, strategy kill switch."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from polymarket_pipeline.execution.clob_client import (
    ClobClient,
    ClobOrderbook,
    OpenOrder,
    OrderResult,
    OrderSide,
    OrderType,
)
from polymarket_pipeline.execution.models import Position
from polymarket_pipeline.execution.panic import panic_close_all


# ============================================================================
# ClobClient retry on transient errors
# ============================================================================


@pytest.mark.asyncio
async def test_submit_order_retries_on_timeout() -> None:
    """TimeoutException triggers retry, then succeeds on second attempt."""
    client = ClobClient(base_url="https://test.example.com", api_key="k")

    call_count = 0
    original_post = client._client.post

    async def mock_post(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("read timeout")
        # Second attempt succeeds
        req = httpx.Request("POST", "https://test.example.com/order")
        return httpx.Response(
            200,
            json={"id": "order-1", "filled_avg_price": 0.50, "size_matched": 10},
            request=req,
        )

    with patch.object(client._client, "post", side_effect=mock_post):
        result = await client.submit_order(
            condition_id="0xabc",
            asset_id="asset-1",
            side=OrderSide.BUY,
            size=10.0,
            order_type=OrderType.MARKET,
            max_retries=3,
        )

    assert result.success is True
    assert result.order_id == "order-1"
    assert call_count == 2  # 1 fail + 1 success


@pytest.mark.asyncio
async def test_submit_order_no_retry_on_4xx() -> None:
    """HTTP 400 (business error) should NOT be retried."""
    client = ClobClient(base_url="https://test.example.com", api_key="k")
    call_count = 0

    async def mock_post(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        resp = httpx.Response(400, json={"error": "bad request"})
        resp.request = httpx.Request("POST", "https://test.example.com/order")
        return resp

    with patch.object(client._client, "post", side_effect=mock_post):
        # httpx raises HTTPStatusError on raise_for_status, so we need
        # to trigger it by having the response status code be 4xx
        result = await client.submit_order(
            condition_id="0xabc",
            asset_id="asset-1",
            side=OrderSide.BUY,
            size=10.0,
            order_type=OrderType.MARKET,
            max_retries=3,
        )

    assert result.success is False
    assert call_count == 1  # No retries on 4xx


@pytest.mark.asyncio
async def test_submit_order_exhausts_retries() -> None:
    """All retries fail → returns error with retry count."""
    client = ClobClient(base_url="https://test.example.com", api_key="k")

    async def always_timeout(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with patch.object(client._client, "post", side_effect=always_timeout):
        result = await client.submit_order(
            condition_id="0xabc",
            asset_id="asset-1",
            side=OrderSide.BUY,
            size=10.0,
            order_type=OrderType.MARKET,
            max_retries=2,
        )

    assert result.success is False
    assert "2 retries" in (result.error or "")


# ============================================================================
# Parallel panic close
# ============================================================================


class FakeTracker:
    """Minimal tracker mock for panic tests."""

    def __init__(self, positions: list[Position]) -> None:
        self._positions = positions

    def get_all_positions(self) -> list[Position]:
        return self._positions


class FakeClobClient:
    """Tracks timing of cancel/submit calls."""

    def __init__(self, cancel_delay: float = 0.05, close_delay: float = 0.05) -> None:
        self.cancel_times: list[float] = []
        self.close_times: list[float] = []
        self._cancel_delay = cancel_delay
        self._close_delay = close_delay

    async def get_open_orders(self) -> list[OpenOrder]:
        return [
            OpenOrder("o1", "c1", "a1", "BUY", 0.50, 10, 0),
            OpenOrder("o2", "c2", "a2", "BUY", 0.60, 20, 0),
        ]

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_times.append(time.monotonic())
        await asyncio.sleep(self._cancel_delay)
        return True

    async def submit_order(self, **kwargs: object) -> OrderResult:
        self.close_times.append(time.monotonic())
        await asyncio.sleep(self._close_delay)
        return OrderResult(order_id="close-1", success=True)


@pytest.mark.asyncio
async def test_panic_close_runs_in_parallel() -> None:
    """Cancels and closes should overlap (parallel, not sequential)."""
    positions = [
        Position("c1", "a1", "BUY", 10, 0.5, 5, 0, 0.5),
        Position("c2", "a2", "BUY", 20, 0.6, 12, 0, 0.6),
        Position("c3", "a3", "BUY", 30, 0.7, 21, 0, 0.7),
    ]
    clob = FakeClobClient(cancel_delay=0.05, close_delay=0.05)
    tracker = FakeTracker(positions)

    t0 = time.monotonic()
    results = await panic_close_all(clob, tracker)  # type: ignore[arg-type]
    elapsed = time.monotonic() - t0

    assert len(results) == 3

    # If sequential: 2 cancels + 3 closes = 5 * 0.05 = 0.25s
    # If parallel: max(2 cancel) + max(3 close) ~ 0.10s
    # Allow generous margin but assert it's clearly parallel
    assert elapsed < 0.20, f"Took {elapsed:.3f}s — likely sequential"


@pytest.mark.asyncio
async def test_panic_close_handles_individual_failure() -> None:
    """One close failure shouldn't prevent others from completing."""
    positions = [
        Position("c1", "a1", "BUY", 10, 0.5, 5, 0, 0.5),
        Position("c2", "a2", "BUY", 20, 0.6, 12, 0, 0.6),
    ]

    call_count = 0

    class FailOnSecondClob(FakeClobClient):
        async def submit_order(self, **kwargs: object) -> OrderResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("CLOB API down")
            return OrderResult(order_id="ok", success=True)

    clob = FailOnSecondClob(cancel_delay=0.0, close_delay=0.0)
    tracker = FakeTracker(positions)

    results = await panic_close_all(clob, tracker)  # type: ignore[arg-type]
    assert len(results) == 2
    # One failed, one succeeded
    assert any(not r.success for r in results)
    assert any(r.success for r in results)


# ============================================================================
# Strategy kill switch (enabled check in runner)
# ============================================================================


def test_strategy_config_enabled_field() -> None:
    """StrategyConfig has an enabled field that defaults... (it's required)."""
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    cfg = StrategyConfig(
        enabled=False,
        mode=ExecutionMode.PAPER_DEV,
        capital_usd=1000,
        max_position_usd=100,
        max_open_positions=10,
        cooldown_s=30,
    )
    assert cfg.enabled is False


def test_live_runner_checks_enabled_in_dispatch() -> None:
    """LiveRunner dispatch loop contains 'config.enabled' guard."""
    import inspect

    from polymarket_pipeline.strategies.runners.live import LiveRunner

    source = inspect.getsource(LiveRunner._handle_trade)
    assert "config.enabled" in source, (
        "_handle_trade should check config.enabled before on_trade dispatch"
    )
