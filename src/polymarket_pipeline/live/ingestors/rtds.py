"""RTDS WebSocket ingestor — redundant connection pool with trade-level dedup.

Maintains N concurrent WebSocket connections to RTDS, each with independent
reconnect logic and staggered rotation.  Trades are deduplicated across
connections so Redpanda receives exactly one copy per trade.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0
_DEDUP_MAXLEN = 50_000  # ~16min buffer at 50 trades/sec


class _TradeDedup:
    """Bounded dedup set — filters duplicate trade_ids across connections."""

    __slots__ = ("_seen", "_maxlen")

    def __init__(self, maxlen: int = _DEDUP_MAXLEN) -> None:
        self._seen: dict[str, float] = {}
        self._maxlen = maxlen

    def is_new(self, trade_id: str) -> bool:
        """Return True and record the id if not seen before."""
        if trade_id in self._seen:
            return False
        self._seen[trade_id] = time.monotonic()
        if len(self._seen) > self._maxlen:
            # Evict oldest 20%
            to_remove = self._maxlen // 5
            for k in list(self._seen.keys())[:to_remove]:
                del self._seen[k]
        return True


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
        self._dedup = _TradeDedup()
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0
        self._connections_alive: int = 0

    # ── message handling (shared across connections) ──────────────────

    async def _handle_message(self, raw: str, conn_id: int) -> None:
        """Process a single raw WebSocket message."""
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

        # Dedup across connections
        if not self._dedup.is_new(trade.trade_id):
            return

        now = time.time()
        trade = trade.model_copy(update={"published_at": now})
        trade_json = trade.model_dump_json()
        await safe_publish(
            self._broker,
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
            source="rtds",
        )
        self._last_trade_ts = now
        self._trade_count += 1

    # ── heartbeat / ping helpers ─────────────────────────────────────

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status topic."""
        heartbeat = json.dumps({
            "source": "rtds",
            "event": "heartbeat",
            "last_trade_ts": self._last_trade_ts,
            "trade_count": self._trade_count,
            "connections_alive": self._connections_alive,
            "pool_size": self._pool_size,
            "ts": time.time(),
        })
        await safe_publish(
            self._broker,
            message=heartbeat,
            topic=self._status_topic,
            key=b"rtds",
            source="rtds",
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

                    subscribe = json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "activity", "type": "trades"}],
                    })
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
        conn_tasks = [
            asyncio.create_task(self._connection_loop(i))
            for i in range(self._pool_size)
        ]

        try:
            await asyncio.gather(heartbeat_task, *conn_tasks)
        finally:
            heartbeat_task.cancel()
            for t in conn_tasks:
                t.cancel()
