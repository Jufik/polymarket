"""Subgraph recovery CLI — fills gaps from ClickHouse max timestamp to now.

Default behavior: checks PostgreSQL for an active recovery job first (resume),
then falls back to ClickHouse max timestamp. Tracks cursor in PostgreSQL so
recovery always resumes from its own progress, even if pm-live is writing trades.

Usage:
    pm-recover                                      # auto resume or from CH
    pm-recover --batch-size 250                     # smaller batches (fewer 502s)
    pm-recover --from-timestamp 1771249303          # explicit start
    pm-recover --redpanda                           # write to Redpanda instead of CH
    pm-recover --fresh                              # ignore active job, start fresh
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import structlog

log = structlog.get_logger()


def _max_timestamp_from_parquet(parquet_dir: str) -> int | None:
    """Scan the last Parquet file for the max timestamp."""
    import fastparquet

    files = sorted(Path(parquet_dir).glob("*.parquet"))
    if not files:
        log.warning("no_parquet_files", dir=parquet_dir)
        return None

    pf = fastparquet.ParquetFile(str(files[-1]))
    df = pf.to_pandas(columns=["timestamp"])
    max_ts = int(df["timestamp"].max())
    log.info("parquet_max_timestamp", file=files[-1].name, timestamp=max_ts)
    return max_ts


def _max_timestamp_from_clickhouse(ch_host: str, ch_port: int, ch_db: str) -> int | None:
    """Query ClickHouse for the latest subgraph trade timestamp."""
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=ch_host, port=ch_port, database=ch_db)
    try:
        rows = ch.query(
            "SELECT max(timestamp) AS max_ts FROM trades_raw"
            " WHERE source = 'goldsky_subgraph'"
        )
        val = rows[0]["max_ts"] if rows else None
    except Exception:
        return None

    if val is None:
        return None

    if hasattr(val, "timestamp"):
        return int(val.timestamp())
    return int(val)


async def run_recovery(
    *,
    from_timestamp: int | None = None,
    from_parquet: str | None = None,
    batch_size: int = 500,
    use_redpanda: bool = False,
    fresh: bool = False,
) -> None:
    """Run subgraph recovery from the given starting point."""
    from polymarket_pipeline.live.settings import Settings
    from polymarket_pipeline.settings import PipelineSettings

    settings = Settings()
    pipeline = PipelineSettings()

    from polymarket_pipeline.sinks.postgres import PostgresSink

    # Check for active recovery job in PostgreSQL (unless --fresh)
    job_id: int | None = None
    if not fresh and from_timestamp is None and from_parquet is None:
        async with PostgresSink(dsn=settings.pg_dsn) as pg:
            active_job = await pg.get_active_recovery_job()
        if active_job is not None:
            job_id = active_job["id"]
            start_ts = active_job["cursor_ts"]
            log.info(
                "recovery.resuming",
                job_id=job_id,
                cursor_ts=start_ts,
                from_ts=active_job["from_ts"],
                total_so_far=active_job["total_published"],
                status=active_job["status"],
            )
        else:
            start_ts = None  # type: ignore[assignment]
    else:
        start_ts = None  # type: ignore[assignment]

    # Determine starting timestamp if not resuming
    if job_id is None:
        if from_timestamp is not None:
            start_ts = from_timestamp
            log.info("recovery.source", source="cli_flag", timestamp=start_ts)
        elif from_parquet:
            start_ts = _max_timestamp_from_parquet(from_parquet)
            if start_ts is None:
                log.error("could_not_determine_start_timestamp")
                return
            log.info("recovery.source", source="parquet", timestamp=start_ts)
        else:
            # Default: ClickHouse
            start_ts = _max_timestamp_from_clickhouse(
                pipeline.ch_host, pipeline.ch_port, pipeline.ch_database
            )
            if start_ts is not None:
                log.info("recovery.source", source="clickhouse", timestamp=start_ts)
            else:
                log.error(
                    "no_starting_point",
                    hint="No trades in ClickHouse. Use --from-timestamp or --from-parquet",
                )
                return

        # Create new recovery job in PostgreSQL
        target_ts = int(time.time())
        async with PostgresSink(dsn=settings.pg_dsn) as pg:
            job_id = await pg.create_recovery_job(from_ts=start_ts, target_ts=target_ts)
        log.info("recovery.job_created", job_id=job_id, from_ts=start_ts, target_ts=target_ts)

    gap_s = int(time.time()) - start_ts
    log.info(
        "recovery.starting",
        job_id=job_id,
        from_ts=start_ts,
        gap_seconds=gap_s,
        gap_hours=round(gap_s / 3600, 1),
        sink="redpanda" if use_redpanda else "clickhouse",
        batch_size=batch_size,
    )

    # Load token_map from PostgreSQL
    async with PostgresSink(dsn=settings.pg_dsn) as pg:
        token_map = await pg.fetch_token_market_map()
    log.info("token_map.loaded", entries=len(token_map))

    from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

    if use_redpanda:
        # Write to Redpanda (needs a consumer to drain into CH)
        from faststream.kafka import KafkaBroker

        async with KafkaBroker(settings.redpanda_url) as broker:
            poller = SubgraphPoller(
                broker=broker,
                subgraph_url=settings.subgraph_url,
                token_market_map=token_map,
                topic="trades.raw",
                status_topic="pipeline.status",
                batch_size=batch_size,
                pg_dsn=settings.pg_dsn,
                job_id=job_id,
            )
            total = await poller.recover(from_timestamp=start_ts)
    else:
        # Direct to ClickHouse (default — restartable, no Redpanda needed)
        from polymarket_pipeline.live.ingestors.subgraph import ClickHouseDirectSink

        ch_sink = ClickHouseDirectSink(
            ch_host=pipeline.ch_host,
            ch_port=pipeline.ch_port,
            ch_database=pipeline.ch_database,
        )
        poller = SubgraphPoller(
            subgraph_url=settings.subgraph_url,
            token_market_map=token_map,
            batch_size=batch_size,
            sink=ch_sink,
            pg_dsn=settings.pg_dsn,
            job_id=job_id,
        )
        total = await poller.recover(from_timestamp=start_ts)

    log.info("recovery.complete", job_id=job_id, trades_recovered=total)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover trades from Goldsky Subgraph to fill gaps"
    )
    parser.add_argument(
        "--from-timestamp",
        type=int,
        default=None,
        help="Unix timestamp to start recovery from",
    )
    parser.add_argument(
        "--from-parquet",
        type=str,
        default=None,
        help="Parquet directory to scan for latest timestamp",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Goldsky subgraph query batch size (default: 500)",
    )
    parser.add_argument(
        "--redpanda",
        action="store_true",
        help="Write to Redpanda instead of directly to ClickHouse",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any active recovery job — start fresh from CH/parquet/timestamp",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    asyncio.run(
        run_recovery(
            from_timestamp=args.from_timestamp,
            from_parquet=args.from_parquet,
            batch_size=args.batch_size,
            use_redpanda=args.redpanda,
            fresh=args.fresh,
        )
    )


if __name__ == "__main__":
    main()
