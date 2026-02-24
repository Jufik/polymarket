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
from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor
from polymarket_pipeline.live.ingestors.pending_block import PendingBlockIngestor
from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor
from polymarket_pipeline.live.quality.checker import QualityChecker
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()

# Settings loaded at import time — overridable via PM_ env vars
settings = Settings()

broker = KafkaBroker(settings.redpanda_url)
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

    if _quality_checker and _quality_checker.state.current.value == "ready":
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


async def _load_token_map() -> dict[str, tuple[str, str]]:
    """Load token_market_map from PostgreSQL."""
    from polymarket_pipeline.sinks.postgres import PostgresSink

    async with PostgresSink(dsn=settings.pg_dsn) as pg:
        token_map = await pg.fetch_token_market_map()
    log.info("token_map.loaded", entries=len(token_map))
    return token_map


async def _check_and_recover(token_map: dict[str, tuple[str, str]]) -> None:
    """Check for gaps and run subgraph recovery if needed."""
    # Check if pm-recover already has an active job — don't interfere
    from polymarket_pipeline.sinks.postgres import PostgresSink

    async with PostgresSink(dsn=settings.pg_dsn) as pg:
        active_job = await pg.get_active_recovery_job()
    if active_job is not None and active_job["status"] == "running":
        log.warning(
            "recovery.skipped",
            reason="active recovery job in progress",
            job_id=active_job["id"],
            cursor_ts=active_job["cursor_ts"],
        )
        return

    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=settings.ch_host, port=settings.ch_port, database=settings.ch_database)

    # Check latest trade in ClickHouse
    try:
        rows = ch.query("SELECT max(timestamp) AS max_ts FROM trades_raw")
        max_ts = rows[0]["max_ts"] if rows else None
    except Exception:
        max_ts = None

    if max_ts is None:
        log.info("recovery.no_trades_in_clickhouse")
        return

    # Convert to unix timestamp if it's a datetime
    if hasattr(max_ts, "timestamp"):
        last_ts = int(max_ts.timestamp())
    else:
        last_ts = int(max_ts)

    gap_s = int(time.time()) - last_ts
    log.info("recovery.gap_check", gap_seconds=gap_s, threshold=settings.gap_threshold_s)

    if gap_s <= settings.gap_threshold_s:
        log.info("recovery.gap_within_threshold")
        return

    log.info("recovery.starting_subgraph", from_ts=last_ts, gap_hours=gap_s / 3600)
    from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

    poller = SubgraphPoller(
        broker=broker,
        subgraph_url=settings.subgraph_url,
        token_market_map=token_map,
        topic="trades.raw",
        status_topic="pipeline.status",
    )
    try:
        async with asyncio.timeout(300):
            total = await poller.recover(from_timestamp=last_ts)
        log.info("recovery.complete", trades_recovered=total)
    except TimeoutError:
        log.warning("recovery.timeout", timeout_s=300, from_ts=last_ts)


@app.on_startup
async def on_startup(context: ContextRepo) -> None:
    """Initialize ingestors and quality checker."""
    global _quality_checker

    log.info("live_pipeline.starting", redpanda=settings.redpanda_url)
    context.set_global("settings", settings)

    # Load token_map from PostgreSQL
    token_map = await _load_token_map()

    # Check for gaps and recover via subgraph if needed
    _ingestor_tasks.append(asyncio.create_task(_check_and_recover(token_map)))

    # Initialize quality checker
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=settings.ch_host, port=settings.ch_port, database=settings.ch_database)
    _quality_checker = QualityChecker(settings=settings, clickhouse=ch)
    context.set_global("quality_checker", _quality_checker)

    # Launch ingestors as background tasks
    rtds = RTDSIngestor(
        broker=broker,
        topic="trades.raw",
        status_topic="pipeline.status",
        pool_size=settings.rtds_pool_size,
        rotation_interval_s=settings.rtds_rotation_interval_s,
    )
    alchemy = AlchemyIngestor(
        broker=broker,
        ws_url=settings.alchemy_ws_url,
        topic="trades.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
    )

    _ingestor_tasks.append(asyncio.create_task(rtds.run()))
    _ingestor_tasks.append(asyncio.create_task(alchemy.run()))

    if settings.mempool_enabled:
        mempool = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
            listen_port=settings.mempool_listen_port,
        )
        _ingestor_tasks.append(asyncio.create_task(mempool.run()))

    if settings.pending_block_enabled:
        rpc_urls = [u.strip() for u in settings.pending_block_rpc_ws_urls.split(",") if u.strip()]
        pending = PendingBlockIngestor(
            broker=broker,
            rpc_ws_urls=rpc_urls,
            topic="pending.signal",
            status_topic="pipeline.status",
            token_market_map=token_map,
            poll_interval=settings.pending_block_poll_interval_s,
        )
        _ingestor_tasks.append(asyncio.create_task(pending.run()))

    if settings.clob_orderbook_enabled:
        from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

        clob_ob = CLOBOrderbookIngestor(
            broker=broker,
            ws_url=settings.clob_orderbook_ws_url,
            topic="orderbooks.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        _ingestor_tasks.append(asyncio.create_task(clob_ob.run()))

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
            message=json.dumps(
                {
                    "event": state.value,
                    "failures": _quality_checker.state.failures,
                    "ts": time.time(),
                }
            ),
            topic="pipeline.status",
            key=b"quality",
        )
