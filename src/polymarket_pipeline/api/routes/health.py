"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    """Deep health check -- verify PG connection is alive."""
    pool = request.app.state.pool
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        return {"status": "healthy", "pg": "ok"}
    except Exception as e:
        return {"status": "unhealthy", "pg": str(e)}
