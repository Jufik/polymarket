"""RTDS WebSocket ingestor -- redundant connection pool with trade-level dedup.

Maintains N concurrent WebSocket connections to RTDS, each with independent
reconnect logic and staggered rotation.  Trades are deduplicated across
connections so Redpanda receives exactly one copy per trade.

A bounded asyncio.Queue decouples WS reading from Redpanda publishing so
that a slow broker never stalls the WebSocket read loop.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets
from pm_core.token_map import TokenMap

from pm_ingest.base import BaseIngestor
from pm_ingest.dedup import TradeDedup
from pm_ingest.normalize.decode import decode_rtds_payload
from pm_ingest.normalize.enrich import enrich
from pm_ingest.normalize.types import Rejection
from pm_ingest.normalize.validate import validate
from pm_ingest.publish import safe_publish

log = structlog.get_logger()

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
_DEDUP_TTL_S = 600.0  # 10 min — increased from 5min; late RTDS arrivals slip through at shorter TTL
_QUEUE_MAXSIZE = 1000  # backpressure bound between WS read and publish


class RTDSIngestor(BaseIngestor):
    """Manages a pool of redundant RTDS connections with trade-level dedup.

    Each connection independently subscribes to the RTDS ``trades`` feed and
    reconnects with exponential backoff on failure.  Connections are
    proactively rotated every ``rotation_interval_s`` seconds, staggered so
    that at least one connection remains active during each rotation cycle.
    """

    source_name = "rtds"

    def __init__(
        self,
        publisher: Any | None = None,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        pool_size: int = 2,
        rotation_interval_s: int = 300,
        *,
        broker: Any = None,
    ) -> None:
        super().__init__(publisher=publisher, topic=topic, status_topic=status_topic, broker=broker)
        self._pool_size = max(pool_size, 1)
        self._rotation_interval_s = rotation_interval_s
        # Mutable dict built from RTDS payload conditionId — RTDS provides
        # conditionId directly, so we self-populate the token_map.
        self._token_map_dict: dict[str, tuple[str, str]] = {}
        self._dedup = TradeDedup(ttl_s=_DEDUP_TTL_S)
        self._last_trade_ts: float = 0.0
        self._connections_alive: int = 0
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._drops_dedup: int = 0

    # -- message handling (shared across connections) -----------------------

    async def _handle_message(self, raw: str, conn_id: int) -> None:
        """Process a single raw WebSocket message.

        Normalizes and deduplicates, then enqueues for async publishing so the
        WS read loop is never blocked by a slow broker.
        """
        if raw in ("PING", "PONG", "pong"):
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("rtds.invalid_json", conn=conn_id, raw=raw[:100])
            return

        if msg.get("type") != "trades":
            return

        payload = msg.get("payload")
        if not payload:
            return

        try:
            # Decode -> Enrich -> Validate pipeline
            decoded = decode_rtds_payload(payload)
            if decoded is None:
                return  # dust trade (size rounded to zero)

            # RTDS provides conditionId directly — self-populate the token_map
            # so the shared enrich stage can resolve asset_id -> condition_id.
            condition_id = payload.get("conditionId", "")
            if condition_id and decoded.asset_id not in self._token_map_dict:
                outcome = payload.get("outcome", "")
                self._token_map_dict[decoded.asset_id] = (condition_id, outcome)

            token_map = TokenMap(self._token_map_dict)
            enriched = enrich(decoded, token_map)
            if isinstance(enriched, Rejection):
                return

            trade = validate(enriched)
            if isinstance(trade, Rejection):
                return
        except Exception:
            log.exception("rtds.normalize_error", conn=conn_id)
            return

        # Dedup across connections (TTL-based eviction)
        if self._dedup.is_duplicate(trade.trade_id):
            self._drops_dedup += 1
            return

        now = time.time()
        trade = trade.model_copy(update={"published_at": now})
        trade_json = trade.model_dump_json()

        try:
            self._queue.put_nowait((trade.condition_id, trade_json))
        except asyncio.QueueFull:
            self._drops_queue_full += 1
            log.warning(
                "rtds.queue_full",
                trade_id=trade.trade_id,
                total_drops=self._drops_queue_full,
            )
            return

        self._last_trade_ts = now
        self._trade_count += 1

    async def _publish_loop(self) -> None:
        """Drain the backpressure queue and publish to Redpanda."""
        while True:
            cid, trade_json = await self._queue.get()
            await safe_publish(
                self._broker,
                message=trade_json,
                topic=self._topic,
                key=cid.encode(),
                source="rtds",
                circuit_breaker=self._circuit_breaker,
            )

    # -- heartbeat / ping helpers ------------------------------------------

    def _heartbeat_fields(self) -> dict[str, Any]:
        """RTDS-specific heartbeat fields."""
        return {
            "last_trade_ts": self._last_trade_ts,
            "connections_alive": self._connections_alive,
            "pool_size": self._pool_size,
            "drops_dedup": self._drops_dedup,
        }

    async def _ping_loop(self, ws: Any) -> None:
        """Send PING every 5s to keep a single connection alive."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send("PING")

    # -- per-connection loop -----------------------------------------------

    async def _connection_loop(self, conn_id: int) -> None:
        """Run a single RTDS connection with reconnect + periodic rotation."""
        backoff = RECONNECT_BASE

        # Stagger first rotation: conn 0 rotates at 1/N of the interval,
        # conn 1 at 2/N, etc.  After the first rotation all connections
        # use the full interval, keeping them permanently offset.
        first_max_age = self._rotation_interval_s * (conn_id + 1) / self._pool_size
        next_max_age = first_max_age

        while True:
            rotated = False
            try:
                log.info("rtds.connecting", conn=conn_id, url=RTDS_URL)
                async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                    backoff = RECONNECT_BASE
                    self._connections_alive += 1
                    log.info(
                        "rtds.connected",
                        conn=conn_id,
                        alive=self._connections_alive,
                    )

                    subscribe = json.dumps(
                        {
                            "action": "subscribe",
                            "subscriptions": [{"topic": "activity", "type": "trades"}],
                        }
                    )
                    await ws.send(subscribe)

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    connected_at = time.monotonic()
                    max_age = next_max_age
                    next_max_age = self._rotation_interval_s

                    try:
                        async for raw in ws:
                            await self._handle_message(raw, conn_id)
                            if time.monotonic() - connected_at >= max_age:
                                rotated = True
                                log.info(
                                    "rtds.rotating",
                                    conn=conn_id,
                                    age_s=int(max_age),
                                    alive=self._connections_alive,
                                )
                                break
                    finally:
                        ping_task.cancel()
                        self._connections_alive -= 1

            except websockets.ConnectionClosed as e:
                log.warning(
                    "rtds.connection_lost",
                    conn=conn_id,
                    reason=str(e),
                    backoff=backoff,
                    alive=self._connections_alive,
                )
            except Exception:
                log.exception(
                    "rtds.error",
                    conn=conn_id,
                    backoff=backoff,
                    alive=self._connections_alive,
                )

            if rotated:
                # Planned rotation — reconnect immediately
                continue

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    # -- entry point -------------------------------------------------------

    async def run(self) -> None:
        """Run the RTDS ingestor with a pool of redundant connections."""
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        publish_task = asyncio.create_task(self._publish_loop())
        conn_tasks = [asyncio.create_task(self._connection_loop(i)) for i in range(self._pool_size)]

        try:
            await asyncio.gather(heartbeat_task, publish_task, *conn_tasks)
        finally:
            heartbeat_task.cancel()
            publish_task.cancel()
            for t in conn_tasks:
                t.cancel()
