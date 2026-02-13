"""RTDS WebSocket consumer.

Connects to wss://ws-live-data.polymarket.com, subscribes to the global
activity/trades feed, normalizes messages, and calls back with NormalizedTrade.
"""

import json
from collections.abc import Callable
from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

log = structlog.get_logger()


class RTDSConsumer:
    """Consumes RTDS WebSocket trade messages."""

    def __init__(self, on_trade: Callable[[NormalizedTrade], Any]) -> None:
        self._on_trade = on_trade
        self._normalizer = RTDSNormalizer()

    async def consume(self, ws: Any) -> None:
        """Consume messages from an open WebSocket connection."""
        while True:
            raw = await ws.recv()

            if raw == "PING":
                await ws.send("PONG")
                continue

            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("rtds_invalid_json", raw=raw[:100])
                continue

            if not isinstance(msg, dict) or msg.get("type") != "trades":
                continue

            try:
                trade = self._normalizer.normalize(msg)
                self._on_trade(trade)
            except Exception:
                log.exception("rtds_normalize_error", msg_keys=list(msg.keys()))
