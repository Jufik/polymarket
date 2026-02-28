"""Shared panic close logic -- used by CLI, API, and auto-protect.

All cancels and position closes run in **parallel** via ``asyncio.gather``
to minimize the time window where fills can sneak through.

Includes timeout protection (default 60s) so a hung CLOB API call cannot
block the panic indefinitely, and verification that cancels succeeded
before proceeding to close positions.
"""

from __future__ import annotations

import asyncio

import structlog

from polymarket_pipeline.execution.clob_client import ClobClient, OrderResult, OrderSide, OrderType
from polymarket_pipeline.execution.models import Position
from polymarket_pipeline.execution.position_tracker import PositionTracker

log = structlog.get_logger()

# Maximum time (seconds) for the entire panic operation.
_PANIC_TIMEOUT_S = 60.0
# Maximum time for a single cancel or close call.
_PER_ORDER_TIMEOUT_S = 15.0


async def _cancel_one(clob: ClobClient, order_id: str) -> bool:
    """Cancel a single order with per-call timeout."""
    try:
        async with asyncio.timeout(_PER_ORDER_TIMEOUT_S):
            ok = await clob.cancel_order(order_id)
    except TimeoutError:
        log.error("panic.cancel_timeout", order_id=order_id)
        return False
    log.info("panic.order_cancelled", order_id=order_id, success=ok)
    return ok


async def _close_one(clob: ClobClient, pos: Position) -> OrderResult:
    """Market-sell a single position with per-call timeout."""
    try:
        async with asyncio.timeout(_PER_ORDER_TIMEOUT_S):
            result = await clob.submit_order(
                condition_id=pos.condition_id,
                asset_id=pos.asset_id,
                side=OrderSide.SELL,
                size=pos.size,
                order_type=OrderType.MARKET,
            )
    except TimeoutError:
        log.error("panic.close_timeout", condition_id=pos.condition_id)
        return OrderResult(order_id="", success=False, error="timeout")
    log.info(
        "panic.position_closed",
        condition_id=pos.condition_id,
        size=pos.size,
        success=result.success,
    )
    return result


async def panic_close_all(
    clob: ClobClient,
    tracker: PositionTracker,
    *,
    timeout_s: float = _PANIC_TIMEOUT_S,
) -> list[OrderResult]:
    """Cancel all open orders and market-sell all positions.

    Cancels and closes are issued **in parallel** to minimize the time
    window.  The entire operation is bounded by *timeout_s* seconds.

    Returns list of close order results (one per open position).
    """
    try:
        async with asyncio.timeout(timeout_s):
            return await _panic_close_inner(clob, tracker)
    except TimeoutError:
        log.error("panic.global_timeout", timeout_s=timeout_s)
        return [OrderResult(order_id="", success=False, error="panic timed out")]


async def _panic_close_inner(
    clob: ClobClient,
    tracker: PositionTracker,
) -> list[OrderResult]:
    """Inner implementation without global timeout wrapper."""
    # 1. Cancel all open orders in parallel
    open_orders = await clob.get_open_orders()
    cancel_failed = 0
    if open_orders:
        cancel_results = await asyncio.gather(
            *(_cancel_one(clob, o.order_id) for o in open_orders),
            return_exceptions=True,
        )
        for order, result in zip(open_orders, cancel_results):
            if isinstance(result, BaseException) or result is False:
                cancel_failed += 1
                log.error(
                    "panic.cancel_failed",
                    order_id=order.order_id,
                    error=str(result) if isinstance(result, BaseException) else "returned False",
                )

    if cancel_failed:
        log.warning(
            "panic.cancels_incomplete",
            total=len(open_orders),
            failed=cancel_failed,
            hint="proceeding to close positions despite cancel failures",
        )

    # 2. Close all positions in parallel
    positions = [p for p in tracker.get_all_positions() if p.size > 0]
    if not positions:
        log.info("panic.complete", positions_closed=0, cancel_failed=cancel_failed)
        return []

    raw_results = await asyncio.gather(
        *(_close_one(clob, p) for p in positions),
        return_exceptions=True,
    )

    results: list[OrderResult] = []
    for pos, raw in zip(positions, raw_results):
        if isinstance(raw, BaseException):
            log.error(
                "panic.close_error",
                condition_id=pos.condition_id,
                error=str(raw),
            )
            results.append(OrderResult(order_id="", success=False, error=str(raw)))
        else:
            results.append(raw)

    n_ok = sum(1 for r in results if r.success)
    n_fail = len(results) - n_ok
    log.info(
        "panic.complete",
        positions_closed=len(results),
        succeeded=n_ok,
        failed=n_fail,
        cancel_failed=cancel_failed,
    )

    # If any closes failed, log a prominent warning
    if n_fail > 0:
        log.error(
            "panic.INCOMPLETE",
            failed_positions=[
                pos.condition_id
                for pos, r in zip(positions, results)
                if not r.success
            ],
            hint="manual intervention required — some positions still open",
        )

    return results
