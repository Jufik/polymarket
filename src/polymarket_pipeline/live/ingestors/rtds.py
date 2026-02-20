"""RTDS WebSocket ingestor — connects, normalizes, publishes to Redpanda."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class RTDSIngestor:
    """Manages RTDS WebSocket lifecycle and publishes trades to Redpanda."""

    def __init__(
        self,
        broker: Any,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = RTDSNormalizer()
        self._last_trade_ts: float = 0.0
        self._trade_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WebSocket message."""
        if raw in ("PING", "PONG", "pong"):
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("rtds.invalid_json", raw=raw[:100])
            return

        if msg.get("type") != "trades":
            return

        try:
            trade = self._normalizer.normalize(msg)
        except ValueError as exc:
            log.debug("rtds.skip_trade", reason=str(exc))
            return
        except Exception:
            log.exception("rtds.normalize_error")
            return

        trade_json = trade.model_dump_json()
        await self._broker.publish(
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
        )
        self._last_trade_ts = time.time()
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status topic."""
        heartbeat = json.dumps({
            "source": "rtds",
            "event": "heartbeat",
            "last_trade_ts": self._last_trade_ts,
            "trade_count": self._trade_count,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"rtds",
        )

    async def _ping_loop(self, ws: Any) -> None:
        """Send PING every 5s to keep RTDS connection alive."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            await ws.send("PING")

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the RTDS ingestor with auto-reconnect."""
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("rtds.connecting", url=RTDS_URL)
                async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                    backoff = RECONNECT_BASE
                    log.info("rtds.connected")

                    subscribe = json.dumps({
                        "action": "subscribe",
                        "subscriptions": [{"topic": "activity", "type": "trades"}],
                    })
                    await ws.send(subscribe)

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        ping_task.cancel()
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning("rtds.disconnected", reason=str(e), backoff=backoff)
            except Exception:
                log.exception("rtds.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
