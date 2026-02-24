"""Goldsky Subgraph recovery poller -- cursor-based catch-up for gap filling."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, Protocol

import structlog
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport

from polymarket_pipeline.constants import EXCHANGE_ADDRS
from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer
from polymarket_pipeline.models import NormalizedTrade

log = structlog.get_logger()

DEFAULT_BATCH_SIZE = 500
ETA_WINDOW = 600  # rolling window for ETA calculation
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry

# Filter taker-perspective duplicates at query level (~40% of rows)
_EXCHANGE_ADDR_LIST = sorted(EXCHANGE_ADDRS)

QUERY_TEMPLATE = gql("""
query FetchOrders($timestamp_gt: String!, $first: Int!, $taker_not_in: [String!]!) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp_gt: $timestamp_gt, taker_not_in: $taker_not_in }
    ) {
        id
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
        timestamp
        transactionHash
        orderHash
    }
}
""")

QUERY_STICKY = gql("""
query FetchOrdersSticky(
    $timestamp: String!, $id_gt: String!, $first: Int!, $taker_not_in: [String!]!
) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp: $timestamp, id_gt: $id_gt, taker_not_in: $taker_not_in }
    ) {
        id
        maker
        taker
        makerAssetId
        takerAssetId
        makerAmountFilled
        takerAmountFilled
        fee
        timestamp
        transactionHash
        orderHash
    }
}
""")


class TradeSink(Protocol):
    """Anything that can receive a batch of normalized trades."""

    def write(self, trades: list[NormalizedTrade]) -> None: ...


class RedpandaSink:
    """Publishes trades to Redpanda (original behavior)."""

    def __init__(self, broker: Any, topic: str) -> None:
        self._broker = broker
        self._topic = topic
        self._loop = asyncio.get_event_loop()

    def write(self, trades: list[NormalizedTrade]) -> None:
        for t in trades:
            self._loop.run_until_complete(
                self._broker.publish(
                    message=t.model_dump_json(),
                    topic=self._topic,
                    key=t.condition_id.encode(),
                )
            )


class ClickHouseDirectSink:
    """Inserts trades directly into ClickHouse (bypass Redpanda)."""

    def __init__(self, ch_host: str, ch_port: int, ch_database: str) -> None:
        from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

        self._sink = ClickHouseSink(host=ch_host, port=ch_port, database=ch_database)

    def write(self, trades: list[NormalizedTrade]) -> None:
        self._sink.insert_trades(trades)


class SubgraphPoller:
    """Polls Goldsky Subgraph to recover missed trades after an outage."""

    def __init__(
        self,
        broker: Any = None,
        subgraph_url: str = "",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        batch_size: int = DEFAULT_BATCH_SIZE,
        sink: TradeSink | None = None,
        pg_dsn: str | None = None,
        job_id: int | None = None,
    ) -> None:
        self._broker = broker
        self._subgraph_url = subgraph_url
        self._topic = topic
        self._status_topic = status_topic
        self._batch_size = batch_size
        self._sink = sink
        self._pg_dsn = pg_dsn
        self._job_id = job_id
        self._normalizer = SubgraphNormalizer(token_market_map=token_market_map or {})

    def _normalize_batch(self, events: list[dict[str, Any]]) -> list[NormalizedTrade]:
        """Normalize a batch of subgraph events, filtering taker dups."""
        trades = []
        for event in events:
            trade = self._normalizer.normalize(event)
            if trade is not None:
                trades.append(trade)
        return trades

    async def _publish_batch_redpanda(self, trades: list[NormalizedTrade]) -> None:
        """Publish trades to Redpanda (async)."""
        for t in trades:
            await self._broker.publish(
                message=t.model_dump_json(),
                topic=self._topic,
                key=t.condition_id.encode(),
            )

    async def _execute_with_retry(self, client: Client, query: Any, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a GQL query with exponential backoff retry on transient errors."""
        for attempt in range(MAX_RETRIES):
            try:
                return await asyncio.wait_for(
                    client.execute(query, variable_values=variables),
                    timeout=30.0,
                )
            except Exception as exc:
                err_str = str(exc)
                is_transient = any(s in err_str for s in ("502", "503", "504", "timeout", "Timeout", "ConnectionError"))
                if not is_transient or attempt == MAX_RETRIES - 1:
                    raise
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    "subgraph.retry",
                    attempt=attempt + 1,
                    delay_s=f"{delay:.1f}",
                    error=err_str[:120],
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _checkpoint(self, cursor_ts: int, total: int) -> None:
        """Persist recovery cursor to PostgreSQL (if tracking enabled)."""
        if self._pg_dsn is None or self._job_id is None:
            return
        from polymarket_pipeline.sinks.postgres import PostgresSink

        async with PostgresSink(dsn=self._pg_dsn) as pg:
            await pg.update_recovery_cursor(self._job_id, cursor_ts, total)
        log.debug("recovery.checkpoint", job_id=self._job_id, cursor_ts=cursor_ts, total=total)

    async def _complete_job(self, status: str = "completed") -> None:
        """Mark recovery job as completed or failed in PostgreSQL."""
        if self._pg_dsn is None or self._job_id is None:
            return
        from polymarket_pipeline.sinks.postgres import PostgresSink

        async with PostgresSink(dsn=self._pg_dsn) as pg:
            await pg.complete_recovery_job(self._job_id, status=status)

    async def recover(self, from_timestamp: int) -> int:
        """Run recovery from a given Unix timestamp until caught up.

        Args:
            from_timestamp: Unix seconds to start recovery from.

        Returns:
            Total number of trades published.
        """
        target_ts = int(time.time())
        total_gap = target_ts - from_timestamp

        # Rolling window: (wall_clock_elapsed, data_seconds_covered) per batch
        batch_stats: deque[tuple[float, int]] = deque(maxlen=ETA_WINDOW)

        transport = AIOHTTPTransport(url=self._subgraph_url)
        try:
            async with Client(transport=transport, fetch_schema_from_transport=False) as client:
                total = 0
                cursor_ts = str(from_timestamp)
                cursor_id = ""
                batch_num = 0
                recovery_start = time.monotonic()

                while True:
                    batch_start = time.monotonic()
                    prev_cursor_ts = int(cursor_ts)

                    if cursor_id:
                        result = await self._execute_with_retry(
                            client,
                            QUERY_STICKY,
                            {
                                "timestamp": cursor_ts,
                                "id_gt": cursor_id,
                                "first": self._batch_size,
                                "taker_not_in": _EXCHANGE_ADDR_LIST,
                            },
                        )
                    else:
                        result = await self._execute_with_retry(
                            client,
                            QUERY_TEMPLATE,
                            {
                                "timestamp_gt": cursor_ts,
                                "first": self._batch_size,
                                "taker_not_in": _EXCHANGE_ADDR_LIST,
                            },
                        )

                    events = result.get("orderFilledEvents", [])
                    if not events:
                        break

                    trades = self._normalize_batch(events)
                    published = len(trades)

                    # Write to sink (direct CH or Redpanda)
                    if self._sink is not None:
                        self._sink.write(trades)
                    elif self._broker is not None:
                        await self._publish_batch_redpanda(trades)

                    total += published

                    last = events[-1]
                    new_ts = last["timestamp"]

                    if new_ts == cursor_ts:
                        cursor_id = last["id"]
                    else:
                        cursor_ts = new_ts
                        cursor_id = ""

                    batch_elapsed = time.monotonic() - batch_start
                    data_covered = int(cursor_ts) - prev_cursor_ts
                    batch_stats.append((batch_elapsed, data_covered))
                    batch_num += 1

                    # Checkpoint cursor to PG every 50 batches
                    if batch_num % 50 == 0:
                        await self._checkpoint(int(cursor_ts), total)

                    # Compute ETA from rolling window
                    remaining_data_s = target_ts - int(cursor_ts)
                    progress_pct = (
                        (1 - remaining_data_s / total_gap) * 100 if total_gap > 0 else 100
                    )

                    eta_str = "calculating..."
                    latency_ms = batch_elapsed * 1000
                    if len(batch_stats) >= 10:
                        total_wall = sum(s[0] for s in batch_stats)
                        total_data = sum(s[1] for s in batch_stats)
                        if total_data > 0:
                            wall_per_data_s = total_wall / total_data
                            eta_s = remaining_data_s * wall_per_data_s
                            eta_h = eta_s / 3600
                            if eta_h >= 1:
                                eta_str = f"{eta_h:.1f}h"
                            else:
                                eta_str = f"{eta_s / 60:.0f}m"

                    log.info(
                        "subgraph.batch",
                        fetched=len(events),
                        published=published,
                        total=total,
                        cursor_ts=cursor_ts,
                        latency_ms=f"{latency_ms:.0f}",
                        progress=f"{progress_pct:.1f}%",
                        eta=eta_str,
                    )

                    if len(events) < self._batch_size:
                        break

                total_wall = time.monotonic() - recovery_start

                # Final checkpoint before marking complete
                await self._checkpoint(int(cursor_ts), total)
                await self._complete_job("completed")

                # Status message to Redpanda if broker available
                if self._broker is not None:
                    await self._broker.publish(
                        message=json.dumps({
                            "source": "subgraph",
                            "event": "caught_up",
                            "total_recovered": total,
                            "ts": time.time(),
                        }),
                        topic=self._status_topic,
                        key=b"subgraph",
                    )

                log.info(
                    "subgraph.recovery_complete",
                    total=total,
                    wall_minutes=f"{total_wall / 60:.1f}",
                    trades_per_sec=f"{total / max(total_wall, 1):.0f}",
                )
                return total

        except Exception:
            await self._complete_job("failed")
            raise
