"""CLOB WebSocket orderbook ingestor -- price_change events from Polymarket.

Subscribes to the CLOB market WebSocket and publishes orderbook snapshots
(best_bid, best_ask) to the ``orderbooks.raw`` Kafka topic.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

log = structlog.get_logger()

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class CLOBOrderbookIngestor:
    """Subscribes to CLOB WS price_change events and publishes orderbook snapshots."""

    def __init__(
        self,
        broker: Any,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        topic: str = "orderbooks.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._broker = broker
        self._ws_url = ws_url
        self._topic = topic
        self._status_topic = status_topic
        self._token_map = token_market_map or {}
        self._update_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WS message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("clob_orderbook.invalid_json", raw=raw[:100])
            return

        # Only process price_change events.
        # The CLOB WS sends messages in format: [{"event_type": "price_change", ...}]
        # or {"event_type": "price_change", ...}
        events: list[dict[str, Any]]
        if isinstance(msg, list):
            events = msg
        elif isinstance(msg, dict):
            events = [msg]
        else:
            return

        for event in events:
            if event.get("event_type") != "price_change":
                continue

            await self._process_price_change(event)

    async def _process_price_change(self, event: dict[str, Any]) -> None:
        """Extract best_bid/best_ask from a price_change event and publish."""
        asset_id = event.get("asset_id")
        if not asset_id:
            return

        # Resolve asset_id -> condition_id via token_market_map
        mapping = self._token_map.get(asset_id)
        condition_id = mapping[0] if mapping else asset_id

        # Extract prices from the event.
        # CLOB WS price_change format includes price changes array.
        changes = event.get("price_changes") or event.get("changes") or []
        best_bid: float | None = None
        best_ask: float | None = None

        if changes and len(changes) > 0:
            change = changes[0]
            best_bid = _safe_float(change.get("best_bid") or change.get("bid"))
            best_ask = _safe_float(change.get("best_ask") or change.get("ask"))

        # Fallback to top-level fields
        if best_bid is None:
            best_bid = _safe_float(event.get("best_bid") or event.get("bid"))
        if best_ask is None:
            best_ask = _safe_float(event.get("best_ask") or event.get("ask"))

        if best_bid is None or best_ask is None:
            return

        snapshot = {
            "condition_id": condition_id,
            "asset_id": asset_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "timestamp": time.time(),
        }

        await self._broker.publish(
            message=json.dumps(snapshot),
            topic=self._topic,
            key=condition_id.encode(),
        )
        self._update_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps(
            {
                "source": "clob_orderbook",
                "event": "heartbeat",
                "update_count": self._update_count,
                "ts": time.time(),
            }
        )
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"clob_orderbook",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the CLOB orderbook ingestor with auto-reconnect."""
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("clob_orderbook.connecting", url=self._ws_url)
                async with websockets.connect(self._ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    log.info("clob_orderbook.connected")

                    # Subscribe to all markets
                    subscribe = json.dumps(
                        {
                            "type": "market",
                            "markets": [],
                            "assets_ids": [],
                        }
                    )
                    await ws.send(subscribe)

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        async for raw in ws:
                            await self._handle_message(str(raw))
                    finally:
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning(
                    "clob_orderbook.disconnected",
                    reason=str(e),
                    backoff=backoff,
                )
            except Exception:
                log.exception("clob_orderbook.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
