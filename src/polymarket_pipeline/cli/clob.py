"""CLI entry point for the CLOB orderbook ingestor (standalone process)."""

from __future__ import annotations

import asyncio

import structlog
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.asset_registry import AssetRegistry
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

        max_slots = settings.clob_orderbook_max_connections
        open_assets = await load_open_asset_ids(
            pg_dsn=settings.pg_dsn,
            ch_host=settings.ch_host,
            ch_port=settings.ch_port,
            ch_database=settings.ch_database,
            limit=max_slots * 500,
        )

        from polymarket_pipeline.live.redis_orderbook import create_redis_client

        redis_client = await create_redis_client(settings.redis_url)
        if redis_client is None:
            raise RuntimeError("Redis is required for CLOB orderbook ingestor")

        registry = AssetRegistry(redis_client)
        # Seed registry with open assets from CH+PG bootstrap
        entries = []
        for aid in open_assets:
            mapping = token_map.get(aid)
            if mapping:
                cid, outcome = mapping
                entries.append((aid, cid, outcome))
            else:
                entries.append((aid, aid, "?"))
        added = await registry.add_many(entries)
        log.info("pm-clob.registry_seeded", total=len(entries), new=added)

        from polymarket_pipeline.live.ingestors.clob_orderbook import CLOBOrderbookIngestor

        ingestor = CLOBOrderbookIngestor(
            broker=broker,
            registry=registry,
            ws_url=settings.clob_orderbook_ws_url,
            topic="orderbooks.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
            markets_events_topic=settings.clob_markets_events_topic,
            max_slots=max_slots,
            redis_client=redis_client,
            redis_orderbook_ttl_s=settings.redis_orderbook_ttl_s,
        )

        log.info("pm-clob.starting", assets=len(open_assets), slots=max_slots)

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
