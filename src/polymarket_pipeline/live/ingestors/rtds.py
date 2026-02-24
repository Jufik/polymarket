"""RTDS WebSocket ingestor — redundant connection pool with trade-level dedup.

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

from polymarket_pipeline.live.circuit_breaker import CircuitBreaker
from polymarket_pipeline.live.dedup import TradeDedup
from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0
_DEDUP_TTL_S = 300.0  # 5min TTL for dedup entries
_QUEUE_MAXSIZE = 1000  # backpressure bound between WS read and publish


class RTDSIngestor:
    """Manages a pool of redundant RTDS connections with trade-level dedup.

    Each connection independently subscribes to the RTDS ``trades`` feed and
    reconnects with exponential backoff on failure.  Connections are
    proactively rotated every ``rotation_interval_s`` seconds, staggered so
    that at least one connection remains active during each rotation cycle.
    """

    def __init__(
        self,
        broker: Any,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        pool_size: int = 2,
        rotation_interval_s: int = 300,
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._pool_size = max(pool_size, 1)
        self._rotation_interval_s = rotation_interval_s
        self._normalizer = RTDSNormalizer()
        self._dedup = TradeDedup(ttl_s=_DEDUP_TTL_S)
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0
        self._connections_alive: int = 0
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._circuit_breaker = CircuitBreaker()
        self._drops_queue_full: int = 0
        self._drops_dedup: int = 0

    # ── message handling (shared across connections) ──────────────────

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

        try:
            trade = self._normalizer.normalize(msg)
        except ValueError as exc:
            log.error("rtds.normalize_error", conn=conn_id, reason=str(exc))
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

    # ── heartbeat / ping helpers ─────────────────────────────────────

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status topic."""
        heartbeat = json.dumps(
            {
                "source": "rtds",
                "event": "heartbeat",
                "last_trade_ts": self._last_trade_ts,
                "trade_count": self._trade_count,
                "connections_alive": self._connections_alive,
                "pool_size": self._pool_size,
                "drops_queue_full": self._drops_queue_full,
                "drops_dedup": self._drops_dedup,
                "ts": time.time(),
            }
        )
        await safe_publish(
            self._broker,
            message=heartbeat,
            topic=self._status_topic,
            key=b"rtds",
            source="rtds",
            circuit_breaker=self._circuit_breaker,
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def _ping_loop(self, ws: Any) -> None:
        """Send PING every 5s to keep a single connection alive."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send("PING")

    # ── per-connection loop ──────────────────────────────────────────

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

    # ── entry point ──────────────────────────────────────────────────

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
