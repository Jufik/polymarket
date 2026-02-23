"""Subgraph recovery CLI — fills gaps from ClickHouse max timestamp to now.

Default behavior (no args): queries ClickHouse for the latest trade, then
uses the Goldsky Subgraph to recover all trades from that point to now.
Publishes to Redpanda trades.raw topic.

Usage:
    pm-recover                                      # auto-detect from ClickHouse
    pm-recover --from-timestamp 1771249303          # explicit start
    pm-recover --from-parquet order_filled/         # scan Parquet for max ts
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
    """Query ClickHouse for the latest trade timestamp."""
    from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

    ch = ClickHouseSink(host=ch_host, port=ch_port, database=ch_db)
    try:
        rows = ch.query("SELECT max(timestamp) AS max_ts FROM trades_raw")
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
) -> None:
    """Run subgraph recovery from the given starting point."""
    from polymarket_pipeline.live.settings import Settings
    from polymarket_pipeline.settings import PipelineSettings

    settings = Settings()
    pipeline = PipelineSettings()

    # Determine starting timestamp
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

    gap_s = int(time.time()) - start_ts
    log.info(
        "recovery.starting",
        from_ts=start_ts,
        gap_seconds=gap_s,
        gap_hours=round(gap_s / 3600, 1),
    )

    # Load token_map from PostgreSQL
    from polymarket_pipeline.sinks.postgres import PostgresSink

    async with PostgresSink(dsn=settings.pg_dsn) as pg:
        token_map = await pg.fetch_token_market_map()
    log.info("token_map.loaded", entries=len(token_map))

    # Run recovery — publish to Redpanda
    from faststream.kafka import KafkaBroker

    from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

    async with KafkaBroker(settings.redpanda_url) as broker:
        poller = SubgraphPoller(
            broker=broker,
            subgraph_url=settings.subgraph_url,
            token_market_map=token_map,
            topic="trades.raw",
            status_topic="pipeline.status",
            batch_size=batch_size,
        )
        total = await poller.recover(from_timestamp=start_ts)

    log.info("recovery.complete", trades_recovered=total)


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
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    asyncio.run(
        run_recovery(
            from_timestamp=args.from_timestamp,
            from_parquet=args.from_parquet,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
