"""CLI wrapper for recompressing raw Goldsky Parquet into compact, sorted files.

Usage:
    # Pass 1: recompress raw parquet into compact batches
    uv run python -m polymarket_pipeline.cli.compact

    # Pass 1 with explicit paths
    uv run python -m polymarket_pipeline.cli.compact \
        --raw-dir order_filled/ --output-dir data/compact/ \
        --token-map data/metadata/token_map.parquet

    # Pass 2: globally sort compact files (disjoint key ranges per file)
    uv run python -m polymarket_pipeline.cli.compact --global-sort
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import structlog

from polymarket_pipeline.loaders.recompress import run_global_sort, run_recompress
from polymarket_pipeline.settings import PipelineSettings

log = structlog.get_logger()


def _build_parser() -> argparse.ArgumentParser:
    settings = PipelineSettings()
    cpu = os.cpu_count() or 8
    default_workers = max(cpu - 4, 4)

    parser = argparse.ArgumentParser(
        description="Recompress raw Goldsky Parquet to compact, sorted format",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path(settings.raw_parquet_dir),
        help=f"Directory containing raw Parquet files (default: {settings.raw_parquet_dir})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=settings.compact_dir,
        help=f"Output directory for compact files (default: {settings.compact_dir})",
    )
    parser.add_argument(
        "--token-map",
        type=Path,
        default=settings.token_map_path,
        help=f"Path to token_map.parquet (default: {settings.token_map_path})",
    )
    parser.add_argument(
        "--batch-files",
        type=int,
        default=20,
        help="Number of raw files per output compact file (default: 20)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"Number of parallel worker processes (default: {default_workers})",
    )
    parser.add_argument(
        "--row-group-size",
        type=int,
        default=500_000,
        help="Row group size in output files (default: 500,000)",
    )
    parser.add_argument(
        "--global-sort",
        action="store_true",
        help="Pass 2: globally sort compact files so each output has disjoint key ranges",
    )
    return parser


def main() -> None:
    """Entrypoint for the compact CLI."""
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    parser = _build_parser()
    args = parser.parse_args()

    if args.global_sort:
        if not args.output_dir.exists():
            log.error("output_dir_not_found", path=str(args.output_dir))
            sys.exit(1)
        run_global_sort(args.output_dir, args.row_group_size)
    else:
        if not args.raw_dir.exists():
            log.error("raw_dir_not_found", path=str(args.raw_dir))
            sys.exit(1)
        asyncio.run(
            run_recompress(
                parquet_dir=args.raw_dir,
                output_dir=args.output_dir,
                pg_dsn=PipelineSettings().pg_dsn,
                batch_files=args.batch_files,
                workers=args.workers,
                row_group_size=args.row_group_size,
                token_map_path=args.token_map,
            )
        )


if __name__ == "__main__":
    main()
