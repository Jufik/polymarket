"""FastStream application for the live sync pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.consumers.market_events import MarketEventsConsumer, ResolutionPoller
from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.orchestrator import (
    check_and_recover,
    create_ingestors,
    load_token_map,
    periodic_quality_check,
    periodic_token_map_refresh,
    supervise_tasks,
)
from polymarket_pipeline.live.protection import auto_protect
from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.quality.state import PipelineState
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


class _NullRunner:
    """Stub runner for when no strategy runner is attached."""

    def request_refresh(self) -> None:
        log.debug("market_events.no_runner_attached")


# Settings loaded at import time — overridable via PM_ env vars
settings = Settings()

broker = KafkaBroker(settings.redpanda_url, compression_type="lz4", linger_ms=5)
app = FastStream(broker)


async def _health_live(scope: Any, receive: Any, send: Any) -> None:
    """Liveness: 200 if process is running."""
    from faststream.asgi import AsgiResponse

    resp = AsgiResponse(
        body=b'{"status":"alive"}',
        status_code=200,
        headers={"content-type": "application/json"},
    )
    await resp(scope, receive, send)


async def _health_ready(scope: Any, receive: Any, send: Any) -> None:
    """Readiness: 200 if pipeline state is READY, 503 otherwise."""
    from faststream.asgi import AsgiResponse

    if _quality_checker and _quality_checker.state.current == PipelineState.READY:
        body = b'{"status":"ready"}'
        code = 200
    else:
        body = b'{"status":"not_ready"}'
        code = 503
    resp = AsgiResponse(body=body, status_code=code, headers={"content-type": "application/json"})
    await resp(scope, receive, send)


asgi_app = app.as_asgi(
    asgi_routes=[
        ("/health/live", _health_live),
        ("/health/ready", _health_ready),
    ]
)

# Shared state
_quality_checker: QualityChecker | None = None
_ingestor_tasks: list[asyncio.Task[Any]] = []
_market_events_consumer: MarketEventsConsumer | None = None


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize ingestors and quality checker."""
    global _quality_checker

    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)

    # Load token_map from PostgreSQL (fast — uses whatever pm-sync last wrote)
    token_map = await load_token_map(settings.pg_dsn)

    # Check for gaps and recover via subgraph if needed
    _ingestor_tasks.append(asyncio.create_task(check_and_recover(broker, settings, token_map)))

    # Ensure Kafka engine tables exist (idempotent — all CREATE IF NOT EXISTS)
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=settings.ch_host, port=settings.ch_port, database=settings.ch_database)

    from polymarket_pipeline.live.schema import apply_schema

    apply_schema(ch, broker_list=settings.ch_kafka_broker_list)
    log.info("live_pipeline.schema_applied")

    # Connect PG pool for quality checks (metadata freshness, resolved completeness)
    pg_pool = None
    if settings.pg_dsn:
        import asyncpg

        pg_pool = await asyncpg.create_pool(dsn=settings.pg_dsn, min_size=1, max_size=2)

    _quality_checker = QualityChecker(settings=settings, clickhouse=ch, pg_pool=pg_pool)
    context.set_global("quality_checker", _quality_checker)

    global _market_events_consumer
    _market_events_consumer = MarketEventsConsumer(
        pg_pool=pg_pool,
        runner=_NullRunner(),
        debounce_s=5.0,
    )

    # Periodic CLOB API resolution checker (WS firehose doesn't emit
    # market_resolved events, so we poll for filled markets past end_date)
    if pg_pool is not None:
        resolution_poller = ResolutionPoller(
            pg_pool=pg_pool, runner=None, interval_s=60.0
        )
        resolution_poller.start()

    # Periodic quality check (detects silent ingestor failures)
    async def _protect() -> None:
        if _quality_checker:
            await auto_protect(_quality_checker, settings)

    _ingestor_tasks.append(
        asyncio.create_task(
            periodic_quality_check(_quality_checker, settings, _protect, broker)
        )
    )

    # Launch ingestors as background tasks
    _ingestor_tasks.extend(await create_ingestors(broker, settings, token_map))

    # Periodic token_map refresh (re-sync APIs -> PG -> shared dict)
    _ingestor_tasks.append(
        asyncio.create_task(periodic_token_map_refresh(token_map, settings))
    )

    # Supervisor watches for ingestor crashes (immediate visibility)
    _ingestor_tasks.append(
        asyncio.create_task(supervise_tasks(list(_ingestor_tasks), _quality_checker))
    )

    log.info("live_pipeline.ingestors_started", count=len(_ingestor_tasks))


@app.on_shutdown
async def on_shutdown() -> None:
    """Close positions, drain in-flight publishes, cancel ingestors."""
    # 1. Close positions first (auto-protect)
    try:
        if _quality_checker:
            await auto_protect(_quality_checker, settings)
    except Exception:
        log.exception("shutdown.auto_protect_error")

    # 2. Signal ingestors to stop and wait for drain
    for task in _ingestor_tasks:
        task.cancel()

    # 3. Wait up to 10s for in-flight work to complete
    if _ingestor_tasks:
        done, pending = await asyncio.wait(_ingestor_tasks, timeout=10.0)
        if pending:
            log.warning("shutdown.drain_timeout", pending_tasks=len(pending))
            for task in pending:
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
        await _quality_checker.run_all_checks()
        state = _quality_checker.state.current

        # Auto-protect — if RED, close all positions
        if state == PipelineState.RED:
            await auto_protect(_quality_checker, settings)

        await safe_publish(
            broker,
            message=json.dumps(
                {
                    "event": state.value,
                    "failures": _quality_checker.state.failures,
                    "ts": time.time(),
                }
            ),
            topic="pipeline.status",
            key=b"quality",
            source="quality_gate",
        )


@broker.subscriber(settings.clob_markets_events_topic, group_id="market-events")
async def handle_market_event(msg: str) -> None:
    """Process market resolution and new market events."""
    if _market_events_consumer is not None:
        await _market_events_consumer.handle(msg)
