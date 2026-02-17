"""Market sync CLI — fetches events + markets from Gamma API and persists to PostgreSQL.

ClickHouse reads market/event data directly from PostgreSQL via the PostgreSQL engine,
so only PG needs to be written to.

Usage:
    uv run python -m polymarket_pipeline.cli.market_sync
    uv run python -m polymarket_pipeline.cli.market_sync --limit 500
"""

from __future__ import annotations

import argparse
import asyncio

import structlog

from polymarket_pipeline.market_sync import fetch_events

log = structlog.get_logger()

PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@192.168.0.148:15432/polymarket"


async def run_market_sync(
    *,
    limit: int = 0,
    pg_dsn: str = PG_DSN_DEFAULT,
) -> None:
    """Fetch events + markets and persist to PostgreSQL."""
    log.info("fetching_events", limit=limit)
    result = await fetch_events(limit=limit)
    log.info(
        "data_fetched",
        events=len(result.events),
        markets=len(result.markets),
        tags=len(result.tags),
        tokens=len(result.token_entries),
    )

    if not result.events:
        log.warning("no_events_fetched")
        return

    from polymarket_pipeline.sinks.postgres import PostgresSink

    log.info("upserting_postgres")
    async with PostgresSink(dsn=pg_dsn) as pg:
        await pg.upsert_events(result.events)
        await pg.upsert_tags(result.tags)
        await pg.upsert_event_tags(result.event_tag_pairs)
        await pg.upsert_markets(result.markets)
        await pg.upsert_token_map(result.token_entries)
    log.info(
        "market_sync_complete",
        events=len(result.events),
        markets=len(result.markets),
        tags=len(result.tags),
        tokens=len(result.token_entries),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync events + markets to PostgreSQL")
    parser.add_argument("--limit", type=int, default=0, help="Max events to fetch (0=all)")
    parser.add_argument(
        "--pg-dsn",
        default=PG_DSN_DEFAULT,
        help="PostgreSQL DSN",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    asyncio.run(
        run_market_sync(
            limit=args.limit,
            pg_dsn=args.pg_dsn,
        )
    )


if __name__ == "__main__":
    main()
