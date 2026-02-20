"""FastStream application for the live sync pipeline."""

from __future__ import annotations

import structlog
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

# Settings loaded at import time — overridable via PM_ env vars
settings = Settings()

broker = KafkaBroker(settings.redpanda_url)
app = FastStream(broker)


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize shared resources on startup."""
    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)


@app.on_shutdown
async def on_shutdown() -> None:
    """Cleanup on shutdown."""
    log.info("live_pipeline.stopping")
