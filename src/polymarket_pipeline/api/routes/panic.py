"""Panic close endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from polymarket_pipeline.execution.panic import panic_close_all

log = structlog.get_logger()

router = APIRouter(tags=["panic"])


@router.post("/panic")
async def trigger_panic(request: Request) -> dict:
    """Emergency: close all positions and cancel all orders."""
    clob = request.app.state.clob
    tracker = request.app.state.tracker

    log.warning("panic.triggered_via_api")
    results = await panic_close_all(clob, tracker)

    return {
        "triggered": True,
        "results": [
            {
                "order_id": r.order_id,
                "success": r.success,
                "error": r.error,
            }
            for r in results
        ],
        "total_closed": sum(1 for r in results if r.success),
        "total_failed": sum(1 for r in results if not r.success),
    }
