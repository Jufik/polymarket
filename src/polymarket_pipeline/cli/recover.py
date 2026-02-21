"""Subgraph recovery CLI — fills gaps from Parquet end or ClickHouse max to now.

Finds the latest trade timestamp from either ClickHouse (if backfill was done)
or the Parquet files, then uses the Goldsky Subgraph to recover all trades
from that point to now. Publishes to Redpanda trades.raw topic.

Usage:
    uv run python -m polymarket_pipeline.cli.recover
    uv run python -m polymarket_pipeline.cli.recover --from-timestamp 1771249303
    uv run python -m polymarket_pipeline.cli.recover --from-parquet order_filled/
"""

from __future__ import annotations

import argparse
import asyncio
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

    # Last file by name typically has the latest data
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
        result = ch.execute("SELECT max(timestamp) FROM trades_raw")
        val = result[0][0] if result and result[0][0] else None
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
) -> None:
    """Run subgraph recovery from the given starting point."""
    from polymarket_pipeline.live.settings import Settings

    settings = Settings()

    # Determine starting timestamp
    if from_timestamp is not None:
        start_ts = from_timestamp
    elif from_parquet:
        start_ts = _max_timestamp_from_parquet(from_parquet)
        if start_ts is None:
            log.error("could_not_determine_start_timestamp")
            return
    else:
        # Try ClickHouse first, fall back to Parquet
        start_ts = _max_timestamp_from_clickhouse(
            settings.ch_host, settings.ch_port, settings.ch_database
        )
        if start_ts is None:
            # Try default Parquet dir
            start_ts = _max_timestamp_from_parquet("order_filled")
        if start_ts is None:
            log.error("no_starting_point", hint="Use --from-timestamp or --from-parquet")
            return

    import time
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
        help="Parquet directory to scan for latest timestamp (default: tries ClickHouse, then order_filled/)",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    asyncio.run(
        run_recovery(
            from_timestamp=args.from_timestamp,
            from_parquet=args.from_parquet,
        )
    )


if __name__ == "__main__":
    main()
