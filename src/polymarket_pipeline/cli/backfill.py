"""Backfill runner — loads all Goldsky Sink Parquet files into ClickHouse.

Usage:
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/ --workers 6
"""

import argparse
import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.parquet import ParquetLoader, list_parquet_files, load_file_fast
from polymarket_pipeline.market_sync import fetch_events, fetch_token_market_map
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink
from polymarket_pipeline.sinks.postgres import PostgresSink

log = structlog.get_logger()

PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@localhost:5432/polymarket"

# Thread-local ClickHouse connections (one per worker thread)
_thread_local = threading.local()


def _get_thread_sink() -> ClickHouseSink:
    if not hasattr(_thread_local, "sink"):
        _thread_local.sink = ClickHouseSink()
    return _thread_local.sink


def _process_file(
    path: Path,
    token_map: dict[str, tuple[str, str]],
    batch_size: int,
) -> dict:
    """Load a single parquet file (vectorized) and insert into ClickHouse."""
    file_start = time.monotonic()

    df, total_rows, dropped = load_file_fast(path, token_map)
    trades = len(df)

    if trades > 0:
        sink = _get_thread_sink()
        sink.insert_dataframe(df, batch_size=batch_size)

    elapsed = time.monotonic() - file_start
    return {
        "file": path.name,
        "total_rows": total_rows,
        "trades": trades,
        "dropped": dropped,
        "elapsed_s": elapsed,
    }


async def run_backfill(
    parquet_dir: Path,
    batch_size: int = 100_000,
    workers: int = 4,
    no_market_sync: bool = False,
    pg_dsn: str = PG_DSN_DEFAULT,
) -> None:
    """Run the full backfill pipeline."""

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

    # 2. List files
    files = list_parquet_files(parquet_dir)
    log.info("files_found", count=len(files), workers=workers, batch_size=batch_size)

    # 3. Parallel load + insert
    total_trades = 0
    total_dropped = 0
    completed = 0
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_file, path, token_map, batch_size): path
            for path in files
        }

        for future in as_completed(futures):
            result = future.result()
            completed += 1
            total_trades += result["trades"]
            total_dropped += result["dropped"]

            log.info(
                "file_complete",
                file=result["file"],
                progress=f"{completed}/{len(files)}",
                trades=result["trades"],
                elapsed_s=f"{result['elapsed_s']:.1f}",
                total_trades=total_trades,
            )

    total_elapsed = time.monotonic() - start
    log.info(
        "backfill_complete",
        total_trades=total_trades,
        total_dropped=total_dropped,
        total_files=len(files),
        workers=workers,
        elapsed_min=f"{total_elapsed / 60:.1f}",
        trades_per_sec=f"{total_trades / max(total_elapsed, 1):.0f}",
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
        default=100_000,
        help="ClickHouse insert batch size (default: 100k)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(os.cpu_count() or 4, 6),
        help="Number of parallel worker threads",
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

    asyncio.run(
        run_backfill(
            args.parquet_dir,
            args.batch_size,
            args.workers,
            args.no_market_sync,
            args.pg_dsn,
        )
    )


if __name__ == "__main__":
    main()
