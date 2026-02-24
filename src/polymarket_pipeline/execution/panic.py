"""Shared panic close logic -- used by CLI, API, and auto-protect."""

from __future__ import annotations

import structlog

from polymarket_pipeline.execution.clob_client import ClobClient, OrderResult, OrderSide, OrderType
from polymarket_pipeline.execution.position_tracker import PositionTracker

log = structlog.get_logger()


async def panic_close_all(
    clob: ClobClient,
    tracker: PositionTracker,
) -> list[OrderResult]:
    """Cancel all open orders and market-sell all positions.

    Returns list of close order results (one per open position).
    """
    # First cancel all open orders to prevent new fills
    open_orders = await clob.get_open_orders()
    for order in open_orders:
        await clob.cancel_order(order.order_id)
        log.info("panic.order_cancelled", order_id=order.order_id)

    # Then close all positions
    positions = tracker.get_all_positions()
    results: list[OrderResult] = []
    for pos in positions:
        if pos.size <= 0:
            continue
        # Close by selling what we hold
        result = await clob.submit_order(
            condition_id=pos.condition_id,
            asset_id=pos.asset_id,
            side=OrderSide.SELL,
            size=pos.size,
            order_type=OrderType.MARKET,
        )
        results.append(result)
        log.info(
            "panic.position_closed",
            condition_id=pos.condition_id,
            size=pos.size,
            success=result.success,
        )

    log.info("panic.complete", positions_closed=len(results))
    return results
