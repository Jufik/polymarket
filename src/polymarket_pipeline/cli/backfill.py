"""Backfill runner — loads all Goldsky Sink Parquet files into ClickHouse.

Usage:
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/
    uv run python -m polymarket_pipeline.cli.backfill --parquet-dir order_filled/ --workers 6

    # Compact mode (pre-compressed parquet, streaming, constant memory):
    uv run python -m polymarket_pipeline.cli.backfill --compact --compact-dir order_filled_compact/
"""

import argparse
import asyncio
import multiprocessing
import multiprocessing.synchronize
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.parquet import list_parquet_files, load_file_fast
from polymarket_pipeline.market_sync import fetch_events
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink
from polymarket_pipeline.sinks.postgres import PostgresSink

log = structlog.get_logger()

PG_DSN_DEFAULT = ""  # Set via PM_PG_DSN env var or --pg-dsn flag

# ---------------------------------------------------------------------------
# Worker process globals (set once per worker via _init_worker)
# ---------------------------------------------------------------------------

_worker_cond_map: dict[str, str] = {}
_worker_sink: ClickHouseSink | None = None
_worker_ch_sem: multiprocessing.synchronize.Semaphore | None = None


def _init_worker(
    token_map: dict[str, tuple[str, str]],
    ch_sem: multiprocessing.synchronize.Semaphore,
) -> None:
    """Called once per worker process. Pre-computes cond_map and creates a CH connection."""
    global _worker_cond_map, _worker_sink, _worker_ch_sem
    # Pre-compute once per worker — avoids rebuilding 943K-entry dict on every file
    _worker_cond_map = {k: v[0] for k, v in token_map.items()}
    _worker_sink = ClickHouseSink()
    _worker_ch_sem = ch_sem


def _process_file(args: tuple[str, int]) -> dict:
    """Load a single parquet file (vectorized) and insert into ClickHouse.

    Takes (file_path_str, batch_size) to keep pickle overhead minimal.
    """
    file_path_str, batch_size = args
    path = Path(file_path_str)
    file_start = time.monotonic()

    df, total_rows, dropped = load_file_fast(path, _worker_cond_map)
    trades = len(df)

    if trades > 0:
        assert _worker_sink is not None
        assert _worker_ch_sem is not None
        _worker_ch_sem.acquire()
        try:
            _worker_sink.insert_dataframe(df, batch_size=batch_size)
        finally:
            _worker_ch_sem.release()

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
    ch_concurrency: int = 3,
    no_market_sync: bool = False,
    pg_dsn: str = PG_DSN_DEFAULT,
) -> None:
    """Run the full backfill pipeline."""

    # 1. Build token-market map (from PG if skipping sync, else from Gamma API)
    if no_market_sync:
        log.info("loading_token_map_from_postgres")
        async with PostgresSink(dsn=pg_dsn) as pg:
            token_map = await pg.fetch_token_market_map()
        log.info("token_map_loaded", tokens=len(token_map))
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

    # 3. Parallel load + insert (ProcessPoolExecutor for true parallelism)
    total_trades = 0
    total_dropped = 0
    completed = 0
    start = time.monotonic()

    # Use fork on Linux (inherits token_map via COW, no serialization).
    # On macOS use spawn (default) — token_map is pickled to each worker once.
    mp_context = multiprocessing.get_context("fork" if sys.platform == "linux" else "spawn")
    ch_sem = mp_context.Semaphore(ch_concurrency)

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=mp_context,
        initializer=_init_worker,
        initargs=(token_map, ch_sem),
    ) as executor:
        # Submit all files — pass (path_str, batch_size) to minimize pickle cost
        futures = {executor.submit(_process_file, (str(path), batch_size)): path for path in files}

        for future in as_completed(futures):
            result = future.result()
            completed += 1
            total_trades += result["trades"]
            total_dropped += result["dropped"]
            wall = time.monotonic() - start

            log.info(
                "file_complete",
                file=result["file"],
                progress=f"{completed}/{len(files)}",
                trades=result["trades"],
                elapsed_s=f"{result['elapsed_s']:.1f}",
                total_trades=total_trades,
                trades_per_sec=f"{total_trades / max(wall, 1):.0f}",
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


async def run_backfill_compact(
    compact_dir: Path,
) -> None:
    """Stream compact parquet files into ClickHouse with constant memory.

    No token map needed (data is pre-normalized). No ProcessPoolExecutor needed
    (streaming is I/O-bound). Iterates compact files, streams row groups via
    iter_row_groups_arrow(), inserts via insert_arrow().
    """
    from polymarket_pipeline.loaders.parquet import iter_row_groups_arrow, list_compact_files

    files = list_compact_files(compact_dir)
    if not files:
        log.error("no_compact_files_found", dir=str(compact_dir))
        sys.exit(1)

    log.info("compact_backfill_start", files=len(files))
    sink = ClickHouseSink()

    total_rows = 0
    start = time.monotonic()

    for i, path in enumerate(files, 1):
        file_rows = 0
        file_start = time.monotonic()

        for table in iter_row_groups_arrow(path):
            sink.insert_arrow(table)
            file_rows += table.num_rows

        total_rows += file_rows
        elapsed = time.monotonic() - file_start
        wall = time.monotonic() - start

        log.info(
            "compact_file_complete",
            file=path.name,
            progress=f"{i}/{len(files)}",
            rows=file_rows,
            elapsed_s=f"{elapsed:.1f}",
            total_rows=total_rows,
            rows_per_sec=f"{total_rows / max(wall, 1):.0f}",
        )

    total_elapsed = time.monotonic() - start
    log.info(
        "compact_backfill_complete",
        total_rows=total_rows,
        total_files=len(files),
        elapsed_min=f"{total_elapsed / 60:.1f}",
        rows_per_sec=f"{total_rows / max(total_elapsed, 1):.0f}",
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
    cpu = os.cpu_count() or 4
    parser.add_argument(
        "--workers",
        type=int,
        default=max(cpu - 4, 4),
        help=f"Number of parallel worker processes (default: {max(cpu - 4, 4)})",
    )
    parser.add_argument(
        "--ch-concurrency",
        type=int,
        default=min(max(cpu // 4, 3), 8),
        help=f"Max concurrent ClickHouse inserts (default: {min(max(cpu // 4, 3), 8)})",
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
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Use pre-normalized compact parquet (streaming, constant memory)",
    )
    parser.add_argument(
        "--compact-dir",
        type=Path,
        default=Path("order_filled_compact"),
        help="Directory with compact parquet files (default: order_filled_compact/)",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    if args.compact:
        from polymarket_pipeline.cli.load import run_load
        from polymarket_pipeline.settings import PipelineSettings

        if not args.compact_dir.exists():
            log.error("compact_dir_not_found", path=str(args.compact_dir))
            sys.exit(1)
        _settings = PipelineSettings()
        run_load(
            compact_dir=args.compact_dir,
            ch_host=_settings.ch_host,
            ch_port=_settings.ch_port,
            ch_database=_settings.ch_database,
        )
    else:
        if not args.parquet_dir.exists():
            log.error("parquet_dir_not_found", path=str(args.parquet_dir))
            sys.exit(1)
        asyncio.run(
            run_backfill(
                args.parquet_dir,
                args.batch_size,
                args.workers,
                args.ch_concurrency,
                args.no_market_sync,
                args.pg_dsn,
            )
        )


if __name__ == "__main__":
    main()
