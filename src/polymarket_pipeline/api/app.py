"""FastAPI service for the Polymarket trading platform."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup, clean up on shutdown."""
    import asyncpg
    import httpx

    from polymarket_pipeline.execution.clob_client import ClobClient
    from polymarket_pipeline.execution.position_tracker import PositionTracker

    # Create PG pool
    pool = await asyncpg.create_pool(dsn=settings.pg_dsn)

    # Initialize position tracker
    tracker = PositionTracker(pool=pool)
    await tracker.initialize()

    # Create CLOB client
    clob = ClobClient(
        base_url=settings.clob_api_url,
        api_key=settings.clob_api_key,
        api_secret=settings.clob_api_secret,
        api_passphrase=settings.clob_api_passphrase,
    )

    # ClickHouse HTTP client (same pattern as ClickHouseBackend)
    ch_client = httpx.AsyncClient(
        base_url=f"http://{settings.ch_host}:{settings.ch_port}",
        timeout=30.0,
    )

    # Store in app state
    app.state.pool = pool
    app.state.tracker = tracker
    app.state.clob = clob
    app.state.ch_client = ch_client
    app.state.ch_database = settings.ch_database
    app.state.settings = settings

    log.info("api.started")
    yield

    # Cleanup
    await ch_client.aclose()
    await clob.close()
    await pool.close()
    log.info("api.stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Polymarket Trading API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow UI from localhost or LAN IP
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|(\d{1,3}\.){3}\d{1,3})(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Import and include routers
    from polymarket_pipeline.api.routes.analytics import router as analytics_router
    from polymarket_pipeline.api.routes.health import router as health_router
    from polymarket_pipeline.api.routes.intent_detail import router as intent_detail_router
    from polymarket_pipeline.api.routes.intents import router as intents_router
    from polymarket_pipeline.api.routes.markets import router as markets_router
    from polymarket_pipeline.api.routes.metrics import router as metrics_router
    from polymarket_pipeline.api.routes.panic import router as panic_router
    from polymarket_pipeline.api.routes.pnl import router as pnl_router
    from polymarket_pipeline.api.routes.positions import router as positions_router
    from polymarket_pipeline.api.routes.quality import router as quality_router

    app.include_router(health_router, prefix="/api")
    app.include_router(positions_router, prefix="/api")
    app.include_router(intents_router, prefix="/api")
    app.include_router(intent_detail_router, prefix="/api")
    app.include_router(analytics_router, prefix="/api")
    app.include_router(panic_router, prefix="/api")
    app.include_router(quality_router, prefix="/api")
    app.include_router(markets_router, prefix="/api")
    app.include_router(pnl_router, prefix="/api")
    app.include_router(metrics_router)  # No prefix — /metrics at root

    return app


app = create_app()


def main() -> None:
    """Entry point for pm-api."""
    uvicorn.run(
        "polymarket_pipeline.api.app:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )
