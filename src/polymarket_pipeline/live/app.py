"""FastStream application for the live sync pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor
from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor
from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

# Settings loaded at import time — overridable via PM_ env vars
settings = Settings()

broker = KafkaBroker(settings.redpanda_url)
app = FastStream(broker)

# Shared state
_quality_checker: QualityChecker | None = None
_ingestor_tasks: list[asyncio.Task[Any]] = []


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize ingestors and quality checker."""
    global _quality_checker

    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)

    # TODO: Load token_map from PostgreSQL for condition_id resolution
    token_map: dict[str, tuple[str, str]] = {}

    # Initialize quality checker
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(
        host=settings.ch_host, port=settings.ch_port, database=settings.ch_database
    )
    _quality_checker = QualityChecker(settings=settings, clickhouse=ch)
    context.set_global("quality_checker", _quality_checker)

    # Launch ingestors as background tasks
    rtds = RTDSIngestor(broker=broker, topic="trades.raw", status_topic="pipeline.status")
    alchemy = AlchemyIngestor(
        broker=broker,
        ws_url=settings.alchemy_ws_url,
        topic="trades.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
    )

    _ingestor_tasks.append(asyncio.create_task(rtds.run()))
    _ingestor_tasks.append(asyncio.create_task(alchemy.run()))

    log.info("live_pipeline.ingestors_started", count=len(_ingestor_tasks))


@app.on_shutdown
async def on_shutdown() -> None:
    """Cancel ingestors and clean up."""
    for task in _ingestor_tasks:
        task.cancel()
    _ingestor_tasks.clear()
    log.info("live_pipeline.stopped")


# ── Status consumer: process heartbeats and quality signals ──────────


@broker.subscriber("pipeline.status", group_id="quality-gate")
async def handle_status(msg: str) -> None:
    """Process heartbeat and status messages from ingestors."""
    if _quality_checker is None:
        return
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return

    event = data.get("event")
    source = data.get("source", "")

    if event == "heartbeat":
        _quality_checker.record_heartbeat(source, data.get("ts", time.time()))
    elif event == "caught_up":
        log.info("status.caught_up", source=source)
        _quality_checker.run_all_checks()
        state = _quality_checker.state.current
        await broker.publish(
            message=json.dumps({
                "event": state.value,
                "failures": _quality_checker.state.failures,
                "ts": time.time(),
            }),
            topic="pipeline.status",
            key=b"quality",
        )
