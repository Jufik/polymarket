"""Health and metrics endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter

from pm_api.deps import WINDOW_S, prune_request_times, request_times

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> dict:
    now = time.time()
    prune_request_times(now)
    count = len(request_times)
    return {
        "window_s": WINDOW_S,
        "requests": count,
        "req_per_s": round(count / WINDOW_S, 2),
    }
