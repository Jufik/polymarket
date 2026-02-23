"""Unified metadata sync CLI — CLOB + Gamma API fetch, PostgreSQL upsert, Parquet export.

Replaces cli/market_sync.py as the single entry point for metadata refresh.
Freshness-gated (24h) with --force override.

Usage:
    uv run python -m polymarket_pipeline.cli.sync
    uv run python -m polymarket_pipeline.cli.sync --force
    uv run python -m polymarket_pipeline.cli.sync --skip-pg
    uv run python -m polymarket_pipeline.cli.sync --skip-parquet
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from polymarket_pipeline.market_sync import MetadataSyncResult, SyncResult, fetch_all_metadata
from polymarket_pipeline.settings import PipelineSettings
from polymarket_pipeline.sinks.postgres import PostgresSink

log: structlog.stdlib.BoundLogger = structlog.get_logger()

FRESHNESS_HOURS: int = 24


def _fetch_meta_path(metadata_dir: Path) -> Path:
    """Return path to the freshness marker file."""
    return metadata_dir / "_fetch_meta.json"


def _is_fresh(metadata_dir: Path) -> bool:
    """Check if metadata was fetched within FRESHNESS_HOURS."""
    meta_path = _fetch_meta_path(metadata_dir)
    if not meta_path.exists():
        return False
    try:
        meta: dict[str, Any] = json.loads(meta_path.read_text())
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        age_hours = (datetime.now(UTC) - fetched_at).total_seconds() / 3600
        return age_hours < FRESHNESS_HOURS
    except (KeyError, ValueError, json.JSONDecodeError):
        return False


async def _write_pg(result: SyncResult, pg_dsn: str) -> None:
    """Write metadata to PostgreSQL in FK order."""
    log.info("pg_upsert_start")
    async with PostgresSink(dsn=pg_dsn) as pg:
        await pg.upsert_events(result.events)
        await pg.upsert_tags(result.tags)
        await pg.upsert_event_tags(result.event_tag_pairs)
        await pg.upsert_markets(result.markets)
        await pg.upsert_token_map(result.token_entries)
    log.info(
        "pg_upsert_complete",
        events=len(result.events),
        tags=len(result.tags),
        markets=len(result.markets),
        tokens=len(result.token_entries),
    )


def _write_parquet(
    metadata_dir: Path,
    market_rows: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
) -> None:
    """Write markets.parquet and token_map.parquet using Polars."""
    import polars as pl

    metadata_dir.mkdir(parents=True, exist_ok=True)

    df_markets = pl.DataFrame(market_rows)
    df_markets.write_parquet(metadata_dir / "markets.parquet", compression="zstd")
    log.info("parquet_written", file="markets.parquet", rows=df_markets.height)

    df_tokens = pl.DataFrame(token_rows)
    df_tokens.write_parquet(metadata_dir / "token_map.parquet", compression="zstd")
    log.info("parquet_written", file="token_map.parquet", rows=df_tokens.height)


def _write_freshness_marker(
    metadata_dir: Path,
    result: MetadataSyncResult,
    elapsed_sec: float,
) -> None:
    """Write _fetch_meta.json freshness marker with sync stats."""
    meta: dict[str, Any] = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "clob_markets": len(result.clob.markets),
        "gamma_events": len(result.gamma.events),
        "gamma_markets": len(result.gamma.markets),
        "merged_market_rows": len(result.market_rows),
        "token_rows": len(result.token_rows),
        "elapsed_sec": round(elapsed_sec, 1),
    }
    _fetch_meta_path(metadata_dir).write_text(json.dumps(meta, indent=2))
    log.info("freshness_marker_written", path=str(_fetch_meta_path(metadata_dir)))


async def run_sync(
    settings: PipelineSettings,
    *,
    force: bool = False,
    skip_pg: bool = False,
    skip_parquet: bool = False,
) -> None:
    """Run unified metadata sync: fetch APIs, write to PostgreSQL and/or Parquet.

    Args:
        settings: Pipeline configuration.
        force: If True, ignore freshness check and re-fetch.
        skip_pg: If True, skip PostgreSQL upsert.
        skip_parquet: If True, skip Parquet export.
    """
    metadata_dir = settings.metadata_dir

    # Freshness gate
    if not force and _is_fresh(metadata_dir):
        meta_path = _fetch_meta_path(metadata_dir)
        meta: dict[str, Any] = json.loads(meta_path.read_text())
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        age_hours = (datetime.now(UTC) - fetched_at).total_seconds() / 3600
        log.info("metadata_fresh", age_hours=f"{age_hours:.1f}", skip=True)
        return

    t0 = time.monotonic()

    # Fetch from CLOB + Gamma APIs
    log.info("fetch_all_metadata_start")
    result = await fetch_all_metadata()
    log.info(
        "fetch_all_metadata_complete",
        clob_markets=len(result.clob.markets),
        gamma_events=len(result.gamma.events),
        gamma_markets=len(result.gamma.markets),
        market_rows=len(result.market_rows),
        token_rows=len(result.token_rows),
    )

    # PostgreSQL upsert (FK order: events -> tags -> event_tags -> markets -> token_map)
    if not skip_pg:
        await _write_pg(result.gamma, settings.pg_dsn)

    # Parquet export
    if not skip_parquet:
        _write_parquet(metadata_dir, result.market_rows, result.token_rows)

    elapsed = time.monotonic() - t0

    # Write freshness marker
    metadata_dir.mkdir(parents=True, exist_ok=True)
    _write_freshness_marker(metadata_dir, result, elapsed)

    log.info(
        "sync_complete",
        elapsed=f"{elapsed:.1f}s",
        skip_pg=skip_pg,
        skip_parquet=skip_parquet,
    )


def main() -> None:
    """CLI entrypoint for metadata sync."""
    parser = argparse.ArgumentParser(
        description="Unified metadata sync: CLOB + Gamma API -> PostgreSQL + Parquet"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if metadata is fresh (< 24h)",
    )
    parser.add_argument(
        "--skip-pg",
        action="store_true",
        help="Skip PostgreSQL upsert",
    )
    parser.add_argument(
        "--skip-parquet",
        action="store_true",
        help="Skip Parquet file export",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    settings = PipelineSettings()

    asyncio.run(
        run_sync(
            settings,
            force=args.force,
            skip_pg=args.skip_pg,
            skip_parquet=args.skip_parquet,
        )
    )


if __name__ == "__main__":
    main()
