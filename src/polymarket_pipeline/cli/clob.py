"""CLI entry point for the CLOB orderbook ingestor (standalone process)."""

from __future__ import annotations

import asyncio

import structlog
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.orchestrator import (
    load_open_asset_ids,
    load_token_map,
    periodic_token_map_refresh,
)
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


async def _run() -> None:
    settings = Settings()
    broker = KafkaBroker(settings.redpanda_url, compression_type="lz4", linger_ms=5)
    async with broker:
        token_map = await load_token_map(settings.pg_dsn)

        open_assets = await load_open_asset_ids(
            pg_dsn=settings.pg_dsn,
            ch_host=settings.ch_host,
            ch_port=settings.ch_port,
            ch_database=settings.ch_database,
            limit=settings.clob_orderbook_max_connections * 500,
        )

        redis_client = None
        if settings.redis_orderbook_enabled:
            from polymarket_pipeline.live.redis_orderbook import create_redis_client

            redis_client = await create_redis_client(settings.redis_url)

        from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

        ingestor = CLOBOrderbookIngestor(
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

        max_conns = settings.clob_orderbook_max_connections
        log.info("pm-clob.starting", assets=len(open_assets), conns=max_conns)

        # Run ingestor + token map refresh (subscribes new markets to orderbook)
        refresh_task = asyncio.create_task(
            periodic_token_map_refresh(token_map, settings, clob_ingestor=ingestor)
        )
        ingestor_task = asyncio.create_task(ingestor.run())

        done, pending = await asyncio.wait(
            [ingestor_task, refresh_task], return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            if exc := task.exception():
                log.error("pm-clob.task_crashed", error=str(exc))
        for task in pending:
            task.cancel()


def main() -> None:
    """Run the CLOB orderbook ingestor as a standalone process."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
