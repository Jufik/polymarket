"""Ingestor lifecycle orchestration for the live pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor
from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor
from polymarket_pipeline.live.ingestors.pending_block import PendingBlockIngestor
from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor
from polymarket_pipeline.live.quality.state import PipelineState

if TYPE_CHECKING:
    from faststream.kafka import KafkaBroker

    from polymarket_pipeline.live.quality.checker import QualityChecker
    from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


async def load_token_map(pg_dsn: str) -> dict[str, tuple[str, str]]:
    """Load token_market_map from PostgreSQL."""
    from polymarket_pipeline.sinks.postgres import PostgresSink

    async with PostgresSink(dsn=pg_dsn) as pg:
        token_map = await pg.fetch_token_market_map()
    log.info("token_map.loaded", entries=len(token_map))
    return token_map


def create_ingestors(
    broker: KafkaBroker,
    settings: Settings,
    token_map: dict[str, tuple[str, str]],
) -> list[asyncio.Task[Any]]:
    """Create and start all enabled ingestors as background tasks."""
    tasks: list[asyncio.Task[Any]] = []

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

    tasks.append(asyncio.create_task(rtds.run()))
    tasks.append(asyncio.create_task(alchemy.run()))

    if settings.mempool_enabled:
        mempool = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
            listen_port=settings.mempool_listen_port,
        )
        tasks.append(asyncio.create_task(mempool.run()))

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
        tasks.append(asyncio.create_task(pending.run()))

    if settings.clob_orderbook_enabled:
        from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

        clob_ob = CLOBOrderbookIngestor(
            broker=broker,
            ws_url=settings.clob_orderbook_ws_url,
            topic="orderbooks.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        tasks.append(asyncio.create_task(clob_ob.run()))

    log.info("orchestrator.ingestors_started", count=len(tasks))
    return tasks


async def check_and_recover(
    broker: KafkaBroker,
    settings: Settings,
    token_map: dict[str, tuple[str, str]],
) -> None:
    """Check for gaps in ClickHouse and run subgraph recovery if needed."""
    # Check if pm-recover already has an active job -- don't interfere
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

    # Check latest trade in ClickHouse (run in thread to avoid blocking the loop)
    try:
        rows = await asyncio.to_thread(ch.query, "SELECT max(timestamp) AS max_ts FROM trades_raw")
        max_ts = rows[0]["max_ts"] if rows else None
    except Exception:
        max_ts = None

    if max_ts is None:
        log.info("recovery.no_trades_in_clickhouse")
        return

    # Convert to unix timestamp if it's a datetime
    last_ts = int(max_ts.timestamp()) if hasattr(max_ts, "timestamp") else int(max_ts)

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
        async with asyncio.timeout(settings.recovery_timeout_s):
            total = await poller.recover(from_timestamp=last_ts)
        log.info("recovery.complete", trades_recovered=total)
    except TimeoutError:
        log.warning("recovery.timeout", timeout_s=settings.recovery_timeout_s, from_ts=last_ts)

    await safe_publish(
        broker,
        message=json.dumps({"event": "caught_up", "source": "subgraph_recovery"}),
        topic="pipeline.status",
        key=b"recovery",
        source="recovery",
    )


async def supervise_tasks(
    tasks: list[asyncio.Task[Any]],
    checker: QualityChecker | None,
) -> None:
    """Watch ingestor tasks and log if any crash unexpectedly.

    Does NOT restart them — the quality checker will detect stale heartbeats
    and trigger auto-protect.  This just provides immediate crash visibility.
    """
    while tasks:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            tasks.remove(task)
            if task.cancelled():
                log.info("task.cancelled", task_name=task.get_name())
            elif exc := task.exception():
                log.error(
                    "task.crashed",
                    task_name=task.get_name(),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            else:
                log.info("task.completed", task_name=task.get_name())


async def periodic_quality_check(
    checker: QualityChecker,
    settings: Settings,
    protect_fn: Any,
) -> None:
    """Run quality checks periodically (independent of caught_up events)."""
    await asyncio.sleep(settings.quality_initial_delay_s)  # short initial delay
    while True:
        await checker.run_all_checks()
        if checker.state.current == PipelineState.RED:
            await protect_fn()
        await asyncio.sleep(settings.quality_check_interval_s)
