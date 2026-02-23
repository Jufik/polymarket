"""Orchestrator CLI — runs the full offline data pipeline.

Replaces ``scripts/build_data.py``. Each step can be run individually or all in order.

Usage:
    pm-build                             # all steps
    pm-build --step sync                 # CLOB + Gamma → PG + parquet
    pm-build --step compact              # raw parquet → data/compact/
    pm-build --step load                 # compact → ClickHouse
    pm-build --step derived              # PnL, MVF, markets_resolved
    pm-build --step prices               # market price timeseries
    pm-build --force-metadata            # re-fetch even if fresh
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

import structlog

from polymarket_pipeline.settings import PipelineSettings

log = structlog.get_logger()

ALL_STEPS = ("sync", "compact", "load", "derived", "prices")


async def _run_sync(settings: PipelineSettings, *, force: bool) -> None:
    from polymarket_pipeline.cli.sync import run_sync

    await run_sync(settings, force=force)


async def _run_compact(settings: PipelineSettings) -> None:
    from pathlib import Path

    from polymarket_pipeline.loaders.recompress import run_recompress

    cpu = os.cpu_count() or 4
    await run_recompress(
        parquet_dir=Path(settings.raw_parquet_dir),
        output_dir=settings.compact_dir,
        pg_dsn=settings.pg_dsn,
        batch_files=20,
        workers=max(cpu - 4, 4),
        row_group_size=500_000,
        token_map_path=settings.token_map_path,
    )


def _run_load(settings: PipelineSettings) -> None:
    from polymarket_pipeline.cli.load import run_load

    run_load(
        compact_dir=settings.compact_dir,
        ch_host=settings.ch_host,
        ch_port=settings.ch_port,
        ch_database=settings.ch_database,
    )


def _run_derived(settings: PipelineSettings) -> None:
    from polymarket_pipeline.derived import compute_derived

    compute_derived(settings)


def _run_prices(settings: PipelineSettings) -> None:
    from polymarket_pipeline.derived import compute_prices

    compute_prices(settings)


async def run_build(
    steps: tuple[str, ...],
    settings: PipelineSettings,
    *,
    force_metadata: bool = False,
) -> None:
    """Run the selected pipeline steps in order."""
    t0 = time.monotonic()

    for step in steps:
        log.info("step_start", step=step)
        step_t0 = time.monotonic()

        if step == "sync":
            await _run_sync(settings, force=force_metadata)
        elif step == "compact":
            await _run_compact(settings)
        elif step == "load":
            _run_load(settings)
        elif step == "derived":
            _run_derived(settings)
        elif step == "prices":
            _run_prices(settings)

        log.info("step_done", step=step, elapsed=f"{time.monotonic() - step_t0:.1f}s")

    log.info("build_complete", steps=list(steps), elapsed=f"{time.monotonic() - t0:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline data pipeline")
    parser.add_argument(
        "--step",
        choices=[*ALL_STEPS, "all"],
        default="all",
        help="Run only this step (default: all steps in order)",
    )
    parser.add_argument(
        "--force-metadata",
        action="store_true",
        help="Re-fetch metadata even if _fetch_meta.json is fresh",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    steps = ALL_STEPS if args.step == "all" else (args.step,)
    settings = PipelineSettings()

    asyncio.run(run_build(steps, settings, force_metadata=args.force_metadata))


if __name__ == "__main__":
    main()
