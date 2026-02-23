"""Load compact parquet files into ClickHouse (streaming, constant memory).

Usage:
    uv run python -m polymarket_pipeline.cli.load
    uv run python -m polymarket_pipeline.cli.load --compact-dir data/compact/
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.parquet import iter_row_groups_arrow, list_compact_files
from polymarket_pipeline.settings import PipelineSettings
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

log = structlog.get_logger()


def run_load(
    compact_dir: Path,
    ch_host: str,
    ch_port: int,
    ch_database: str,
) -> None:
    """Stream compact parquet row groups into ClickHouse with constant memory.

    No token map needed (data is pre-normalized). Iterates compact files,
    streams row groups via iter_row_groups_arrow(), inserts via insert_arrow().
    """
    files = list_compact_files(compact_dir)
    if not files:
        log.error("no_compact_files_found", dir=str(compact_dir))
        sys.exit(1)

    log.info("load_start", files=len(files), ch_host=ch_host, ch_port=ch_port)
    sink = ClickHouseSink(host=ch_host, port=ch_port, database=ch_database)

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
            "load_file_complete",
            file=path.name,
            progress=f"{i}/{len(files)}",
            rows=file_rows,
            elapsed_s=f"{elapsed:.1f}",
            total_rows=total_rows,
            rows_per_sec=f"{total_rows / max(wall, 1):.0f}",
        )

    total_elapsed = time.monotonic() - start
    log.info(
        "load_complete",
        total_rows=total_rows,
        total_files=len(files),
        elapsed_min=f"{total_elapsed / 60:.1f}",
        rows_per_sec=f"{total_rows / max(total_elapsed, 1):.0f}",
    )


def main() -> None:
    settings = PipelineSettings()

    parser = argparse.ArgumentParser(
        description="Load compact parquet files into ClickHouse",
    )
    parser.add_argument(
        "--compact-dir",
        type=Path,
        default=settings.compact_dir,
        help=f"Directory with compact parquet files (default: {settings.compact_dir})",
    )
    parser.add_argument(
        "--ch-host",
        default=settings.ch_host,
        help=f"ClickHouse host (default: {settings.ch_host})",
    )
    parser.add_argument(
        "--ch-port",
        type=int,
        default=settings.ch_port,
        help=f"ClickHouse HTTP port (default: {settings.ch_port})",
    )
    parser.add_argument(
        "--ch-database",
        default=settings.ch_database,
        help=f"ClickHouse database (default: {settings.ch_database})",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    if not args.compact_dir.exists():
        log.error("compact_dir_not_found", path=str(args.compact_dir))
        sys.exit(1)

    run_load(
        compact_dir=args.compact_dir,
        ch_host=args.ch_host,
        ch_port=args.ch_port,
        ch_database=args.ch_database,
    )


if __name__ == "__main__":
    main()
