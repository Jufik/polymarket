"""Alchemy eth_subscribe ingestor -- Polygon RPC logs for OrderFilled events."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.live.normalizers.polygon_rpc import ORDER_FILLED_SIG, PolygonRPCNormalizer

log = structlog.get_logger()

# Both CTF Exchange contracts
CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class AlchemyIngestor:
    """Subscribes to Polygon OrderFilled logs via Alchemy WebSocket."""

    def __init__(
        self,
        broker: Any,
        ws_url: str,
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._broker = broker
        self._ws_url = ws_url
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = PolygonRPCNormalizer(token_market_map=token_market_map)
        self._last_block: int = 0
        self._trade_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WebSocket message from Alchemy."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("alchemy.invalid_json", raw=raw[:100])
            return

        # Skip subscription confirmations
        if "method" not in msg:
            return

        if msg.get("method") != "eth_subscription":
            return

        result = msg.get("params", {}).get("result")
        if not result:
            return

        # Use blockTimestamp from Alchemy (hex-encoded unix seconds).
        # Fall back to current time if not present.
        block_ts = result.get("blockTimestamp")
        if block_ts:
            result["_timestamp"] = int(block_ts, 16)
        else:
            result["_timestamp"] = int(time.time())

        trade = self._normalizer.normalize(result)
        if trade is None:
            return  # taker duplicate dropped

        trade = trade.model_copy(update={"published_at": time.time()})
        trade_json = trade.model_dump_json()
        await self._broker.publish(
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
        )

        self._last_block = trade.block_number or self._last_block
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps({
            "source": "alchemy",
            "event": "heartbeat",
            "last_block": self._last_block,
            "trade_count": self._trade_count,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"alchemy",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the Alchemy ingestor with auto-reconnect."""
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("alchemy.connecting")
                async with websockets.connect(self._ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    log.info("alchemy.connected")

                    subscribe = json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": [
                            "logs",
                            {
                                "address": [CTF_EXCHANGE, NEGRISK_EXCHANGE],
                                "topics": [[ORDER_FILLED_SIG]],
                            },
                        ],
                    })
                    await ws.send(subscribe)

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning("alchemy.disconnected", reason=str(e), backoff=backoff)
            except Exception:
                log.exception("alchemy.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
