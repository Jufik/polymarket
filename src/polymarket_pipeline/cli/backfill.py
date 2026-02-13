"""Backfill runner — loads all Goldsky Sink Parquet files into ClickHouse.

Usage:
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.parquet import ParquetLoader
from polymarket_pipeline.market_sync import fetch_events, fetch_token_market_map
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink
from polymarket_pipeline.sinks.postgres import PostgresSink

log = structlog.get_logger()

PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@localhost:5432/polymarket"


async def run_backfill(
    parquet_dir: Path,
    batch_size: int = 10_000,
    no_market_sync: bool = False,
    pg_dsn: str = PG_DSN_DEFAULT,
) -> None:
    """Run the full backfill pipeline."""
    sink = ClickHouseSink()

    # 1. Build token-market map from Gamma API (optionally persist to PG)
    if no_market_sync:
        log.info("building_token_market_map")
        token_map = await fetch_token_market_map()
        log.info("token_market_map_ready", tokens=len(token_map))
    else:
        log.info("fetching_events_with_sync")
        result = await fetch_events()
        token_map = {e.asset_id: (e.condition_id, e.outcome) for e in result.token_entries}
        log.info("persisting_to_postgres", events=len(result.events), markets=len(result.markets))
        async with PostgresSink(dsn=pg_dsn) as pg:
            await pg.upsert_events(result.events)
            await pg.upsert_tags(result.tags)
            await pg.upsert_event_tags(result.event_tag_pairs)
            await pg.upsert_markets(result.markets)
            await pg.upsert_token_map(result.token_entries)
        log.info(
            "market_sync_done",
            events=len(result.events),
            markets=len(result.markets),
            tags=len(result.tags),
            tokens=len(result.token_entries),
        )

    # 2. Set up loader
    loader = ParquetLoader(token_market_map=token_map)

    # 3. List and process files
    files = loader.list_files(parquet_dir)
    log.info("files_found", count=len(files))

    total_trades = 0
    total_dropped = 0
    start = time.monotonic()

    for i, path in enumerate(files):
        file_start = time.monotonic()
        trades, stats = loader.load_file_with_stats(path)

        # Insert in batches
        for j in range(0, len(trades), batch_size):
            batch = trades[j : j + batch_size]
            sink.insert_trades(batch)

        total_trades += len(trades)
        total_dropped += int(stats["dropped_duplicates"])
        elapsed = time.monotonic() - file_start

        log.info(
            "file_complete",
            file=path.name,
            progress=f"{i + 1}/{len(files)}",
            trades=len(trades),
            elapsed_s=f"{elapsed:.1f}",
            total_trades=total_trades,
        )

    total_elapsed = time.monotonic() - start
    log.info(
        "backfill_complete",
        total_trades=total_trades,
        total_dropped=total_dropped,
        total_files=len(files),
        elapsed_min=f"{total_elapsed / 60:.1f}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Goldsky Sink data")
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        default=Path("order_filled"),
        help="Directory containing Parquet files",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="ClickHouse insert batch size",
    )
    parser.add_argument(
        "--no-market-sync",
        action="store_true",
        help="Skip persisting market metadata to PostgreSQL",
    )
    parser.add_argument(
        "--pg-dsn",
        default=PG_DSN_DEFAULT,
        help="PostgreSQL DSN for market metadata",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    if not args.parquet_dir.exists():
        log.error("parquet_dir_not_found", path=str(args.parquet_dir))
        sys.exit(1)

    asyncio.run(run_backfill(args.parquet_dir, args.batch_size, args.no_market_sync, args.pg_dsn))


if __name__ == "__main__":
    main()
