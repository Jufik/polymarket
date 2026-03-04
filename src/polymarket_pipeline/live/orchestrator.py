"""Ingestor lifecycle orchestration for the live pipeline."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor
from polymarket_pipeline.live.ingestors.pending_block import PendingBlockIngestor
from polymarket_pipeline.live.ingestors.rpc import RPCIngestor
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


async def create_ingestors(
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
    rpc = RPCIngestor(
        broker=broker,
        ws_url=settings.rpc_ws_url,
        topic="trades.raw",
        status_topic="pipeline.status",
        token_market_map=token_map,
        markets_events_topic=settings.clob_markets_events_topic,
        resolution_enabled=settings.resolution_rpc_enabled,
    )

    tasks.append(asyncio.create_task(rtds.run()))
    tasks.append(asyncio.create_task(rpc.run()))

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

        # Subscribe to recently active open markets (CH activity → PG filter)
        open_assets = await _load_open_asset_ids(
            pg_dsn=settings.pg_dsn,
            ch_host=settings.ch_host,
            ch_port=settings.ch_port,
            ch_database=settings.ch_database,
            limit=settings.clob_orderbook_max_connections * 500,
        )

        # Optional Redis orderbook cache
        redis_client = None
        if settings.redis_orderbook_enabled:
            from polymarket_pipeline.live.redis_orderbook import create_redis_client

            redis_client = await create_redis_client(settings.redis_url)

        clob_ob = CLOBOrderbookIngestor(
            broker=broker,
            ws_url=settings.clob_orderbook_ws_url,
            topic="orderbooks.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
            markets_events_topic=settings.clob_markets_events_topic,
            max_orderbook_connections=settings.clob_orderbook_max_connections,
            subscribe_asset_ids=open_assets,
            redis_client=redis_client,
            redis_orderbook_ttl_s=settings.redis_orderbook_ttl_s,
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


async def periodic_token_map_refresh(
    token_map: dict[str, tuple[str, str]],
    settings: Settings,
) -> None:
    """Periodically fetch open-market tokens and reload the shared token_map.

    Uses ``fetch_open_tokens()`` (Gamma API, ``closed=false``) which is much
    faster than a full sync (~8K open events vs ~450K+ total).  New tokens are
    upserted to PostgreSQL, then the full token_map is reloaded from PG so all
    ingestors see new markets without a restart.

    First iteration runs immediately (non-blocking background catch-up at
    startup), then repeats every ``token_map_refresh_interval_s``.
    """
    while True:
        try:
            log.info("token_map_refresh.sync_start")
            await _sync_open_tokens(settings.pg_dsn)
        except Exception:
            log.exception("token_map_refresh.sync_error")

        try:
            fresh = await _load_token_map_from_pg(settings.pg_dsn)
            old_keys = set(token_map.keys())
            new_keys = set(fresh.keys())

            # Atomic-ish swap: update first (no empty-map window), then prune stale.
            # Python dict.update is a single C-level call, effectively atomic under
            # the GIL. Doing clear()+update() left a window where ingestors saw an
            # empty dict — this approach eliminates that bug.
            token_map.update(fresh)

            # Remove entries no longer in PG (resolved/delisted markets).
            stale_keys = old_keys - new_keys
            for k in stale_keys:
                token_map.pop(k, None)

            added = new_keys - old_keys
            log.info(
                "token_map_refresh.reloaded",
                old_size=len(old_keys),
                new_size=len(token_map),
                added=len(added),
                removed=len(stale_keys),
            )
        except Exception:
            log.exception("token_map_refresh.reload_error")

        await asyncio.sleep(settings.token_map_refresh_interval_s)


async def _sync_open_tokens(pg_dsn: str) -> None:
    """Fetch open markets from Gamma API and upsert to PostgreSQL (FK order)."""
    from polymarket_pipeline.market_sync import fetch_open_markets
    from polymarket_pipeline.sinks.postgres import PostgresSink

    result = await fetch_open_markets()
    if not result.token_entries:
        return
    async with PostgresSink(dsn=pg_dsn) as pg:
        await pg.upsert_events(result.events)
        await pg.upsert_tags(result.tags)
        await pg.upsert_event_tags(result.event_tag_pairs)
        await pg.upsert_markets(result.markets)
        await pg.upsert_token_map(result.token_entries)
    log.info(
        "token_map_refresh.upserted",
        events=len(result.events),
        markets=len(result.markets),
        tokens=len(result.token_entries),
    )


async def _load_open_asset_ids(
    pg_dsn: str,
    ch_host: str = "localhost",
    ch_port: int = 18123,
    ch_database: str = "polymarket",
    limit: int = 15000,
) -> list[str]:
    """Load asset IDs for open markets, prioritized by recent trading activity.

    1. Query CH for condition_ids with trades in the last 48h, ranked by trade count.
    2. Cross-reference with PG to get asset_ids for open (unresolved) markets only.

    This gives orderbook coverage to the markets that are actually active,
    instead of blindly picking the newest by creation date.
    """
    import asyncpg
    import httpx

    # Step 1: get active condition_ids from CH, ranked by recent activity
    active_cids: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            query = """
                SELECT condition_id, count() AS trades
                FROM trades_raw
                WHERE timestamp >= now() - INTERVAL 48 HOUR
                GROUP BY condition_id
                HAVING trades >= 3
                ORDER BY trades DESC
                FORMAT JSONEachRow
            """
            resp = await client.post(
                f"http://{ch_host}:{ch_port}/",
                content=query,
                params={"database": ch_database},
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            import json
            for line in resp.text.strip().split("\n"):
                if line.strip():
                    row = json.loads(line)
                    active_cids.append(row["condition_id"])
    except Exception:
        log.exception("open_asset_ids.ch_query_failed")

    log.info("open_asset_ids.ch_active", condition_ids=len(active_cids))

    # Step 2: map to asset_ids via PG, filtering to open markets only
    conn = await asyncpg.connect(pg_dsn)
    try:
        if active_cids:
            # Prioritize CH-active markets, then fill remaining slots with newest open
            rows = await conn.fetch(
                """
                WITH active AS (
                    SELECT unnest($1::text[]) AS condition_id
                )
                SELECT t.asset_id
                FROM token_market_map t
                JOIN markets m ON t.condition_id = m.condition_id
                WHERE m.resolved_at IS NULL
                  AND m.closed_at IS NULL
                  AND t.condition_id IN (SELECT condition_id FROM active)
                """,
                active_cids,
            )
            assets = [r["asset_id"] for r in rows]

            # Fill remaining slots with newest open markets not yet covered
            remaining = limit - len(assets)
            if remaining > 0:
                covered = set(active_cids)
                fill_rows = await conn.fetch(
                    """
                    SELECT t.asset_id
                    FROM token_market_map t
                    JOIN markets m ON t.condition_id = m.condition_id
                    WHERE m.resolved_at IS NULL
                      AND m.closed_at IS NULL
                      AND t.condition_id != ALL($1::text[])
                    ORDER BY m.created_at DESC NULLS LAST
                    LIMIT $2
                    """,
                    list(covered),
                    remaining,
                )
                assets.extend(r["asset_id"] for r in fill_rows)
        else:
            # Fallback: just newest open markets
            rows = await conn.fetch(
                """
                SELECT t.asset_id
                FROM token_market_map t
                JOIN markets m ON t.condition_id = m.condition_id
                WHERE m.resolved_at IS NULL
                  AND m.closed_at IS NULL
                ORDER BY m.created_at DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
            assets = [r["asset_id"] for r in rows]

        log.info("open_asset_ids.loaded", count=len(assets), limit=limit)
        return assets[:limit]
    finally:
        await conn.close()


async def _load_token_map_from_pg(pg_dsn: str) -> dict[str, tuple[str, str]]:
    """Fetch token_market_map from PostgreSQL."""
    from polymarket_pipeline.sinks.postgres import PostgresSink

    async with PostgresSink(dsn=pg_dsn) as pg:
        return await pg.fetch_token_market_map()


async def periodic_quality_check(
    checker: QualityChecker,
    settings: Settings,
    protect_fn: Any,
    broker: KafkaBroker | None = None,
) -> None:
    """Run quality checks periodically (independent of caught_up events)."""
    await asyncio.sleep(settings.quality_initial_delay_s)  # short initial delay
    while True:
        await checker.run_all_checks()

        # Publish quality state for API consumption
        if broker is not None and checker.last_quality_message is not None:
            await safe_publish(
                broker,
                message=json.dumps(checker.last_quality_message),
                topic="pipeline.quality",
                key=b"quality",
                source="quality_checker",
            )

        if checker.state.current == PipelineState.RED:
            await protect_fn()
        await asyncio.sleep(settings.quality_check_interval_s)
