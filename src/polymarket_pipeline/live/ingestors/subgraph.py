"""Goldsky Subgraph recovery poller -- cursor-based catch-up for gap filling."""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

import structlog
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport

from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer

log = structlog.get_logger()

BATCH_SIZE = 1000
ETA_WINDOW = 600  # rolling window for ETA calculation

QUERY_TEMPLATE = gql("""
query FetchOrders($timestamp_gt: String!, $first: Int!) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp_gt: $timestamp_gt }
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
query FetchOrdersSticky($timestamp: String!, $id_gt: String!, $first: Int!) {
    orderFilledEvents(
        orderBy: timestamp
        orderDirection: asc
        first: $first
        where: { timestamp: $timestamp, id_gt: $id_gt }
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


class SubgraphPoller:
    """Polls Goldsky Subgraph to recover missed trades after an outage."""

    def __init__(
        self,
        broker: Any,
        subgraph_url: str,
        token_market_map: dict[str, tuple[str, str]],
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
    ) -> None:
        self._broker = broker
        self._subgraph_url = subgraph_url
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = SubgraphNormalizer(token_market_map=token_market_map)

    async def _process_batch(self, events: list[dict[str, Any]]) -> int:
        """Normalize and publish a batch of subgraph events. Returns count published."""
        published = 0
        for event in events:
            trade = self._normalizer.normalize(event)
            if trade is None:
                continue
            await self._broker.publish(
                message=trade.model_dump_json(),
                topic=self._topic,
                key=trade.condition_id.encode(),
            )
            published += 1
        return published

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
                    result = await client.execute(
                        QUERY_STICKY,
                        variable_values={
                            "timestamp": cursor_ts,
                            "id_gt": cursor_id,
                            "first": BATCH_SIZE,
                        },
                    )
                else:
                    result = await client.execute(
                        QUERY_TEMPLATE,
                        variable_values={
                            "timestamp_gt": cursor_ts,
                            "first": BATCH_SIZE,
                        },
                    )

                events = result.get("orderFilledEvents", [])
                if not events:
                    break

                published = await self._process_batch(events)
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

                # Compute ETA from rolling window
                remaining_data_s = target_ts - int(cursor_ts)
                progress_pct = (1 - remaining_data_s / total_gap) * 100 if total_gap > 0 else 100

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

                if len(events) < BATCH_SIZE:
                    break

            total_wall = time.monotonic() - recovery_start
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
