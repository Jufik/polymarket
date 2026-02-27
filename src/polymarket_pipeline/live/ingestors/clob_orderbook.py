"""CLOB WebSocket orderbook ingestor -- price data + market events from Polymarket.

Runs two kinds of WebSocket connections:

1. **Firehose** (empty arrays, ``custom_feature_enabled=True``):
   Receives broadcast events (``new_market``, ``market_resolved``) and publishes
   them to the ``markets.events`` Kafka topic.

2. **Targeted** (batches of ≤500 asset IDs):
   Receives ``price_change`` events and initial orderbook snapshots for
   subscribed assets, publishing best_bid/best_ask to ``orderbooks.raw``.
   Multiple connections are spawned to cover more assets.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.ingestors.base import BaseIngestor

log = structlog.get_logger()

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
_MAX_ASSETS_PER_WS = 500


class CLOBOrderbookIngestor(BaseIngestor):
    """CLOB WS ingestor: firehose for market events + targeted for orderbooks."""

    source_name = "clob_orderbook"

    def __init__(
        self,
        broker: Any,
        ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        topic: str = "orderbooks.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        markets_events_topic: str = "markets.events",
        max_orderbook_connections: int = 4,
        subscribe_asset_ids: list[str] | None = None,
    ) -> None:
        super().__init__(broker=broker, topic=topic, status_topic=status_topic)
        self._ws_url = ws_url
        self._token_map = token_market_map or {}
        self._markets_events_topic = markets_events_topic
        self._max_ob_conns = max_orderbook_connections
        self._subscribe_assets = subscribe_asset_ids or []
        self._update_count: int = 0
        self._market_event_count: int = 0

    # ── message handling ─────────────────────────────────────────────

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WS message, routing by structure."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(msg, list):
            if not msg:
                return  # empty ack
            for entry in msg:
                if isinstance(entry, dict) and ("bids" in entry or "asks" in entry):
                    await self._process_orderbook_snapshot(entry)
            return

        if not isinstance(msg, dict):
            return

        if "price_changes" in msg:
            await self._process_price_change(msg)
        elif "question" in msg and "market" in msg:
            await self._process_new_market(msg)
        elif msg.get("event_type") in ("market_resolved", "new_market"):
            await self._process_market_event(msg)

    async def _process_orderbook_snapshot(self, entry: dict[str, Any]) -> None:
        """Extract best_bid/best_ask from an initial orderbook snapshot."""
        asset_id = entry.get("asset_id")
        if not asset_id:
            return
        bids = entry.get("bids") or []
        asks = entry.get("asks") or []
        best_bid = _safe_float(bids[0].get("price")) if bids and isinstance(bids[0], dict) else None
        best_ask = _safe_float(asks[0].get("price")) if asks and isinstance(asks[0], dict) else None
        if best_bid is None or best_ask is None:
            return
        mapping = self._token_map.get(asset_id)
        condition_id = mapping[0] if mapping else entry.get("market", asset_id)
        await self._publish_snapshot(condition_id, asset_id, best_bid, best_ask)

    async def _process_price_change(self, event: dict[str, Any]) -> None:
        """Extract best_bid/best_ask from a price_change event and publish."""
        for change in event.get("price_changes") or []:
            asset_id = change.get("asset_id")
            if not asset_id:
                continue
            best_bid = _safe_float(change.get("best_bid"))
            best_ask = _safe_float(change.get("best_ask"))
            if best_bid is None or best_ask is None:
                continue
            mapping = self._token_map.get(asset_id)
            condition_id = mapping[0] if mapping else event.get("market", asset_id)
            await self._publish_snapshot(condition_id, asset_id, best_bid, best_ask)

    async def _publish_snapshot(
        self, condition_id: str, asset_id: str, best_bid: float, best_ask: float
    ) -> None:
        snapshot = {
            "condition_id": condition_id,
            "asset_id": asset_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "timestamp": time.time(),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(snapshot),
            topic=self._topic,
            key=condition_id.encode(),
            source="clob_orderbook",
        )
        self._update_count += 1

    async def _process_new_market(self, event: dict[str, Any]) -> None:
        """Forward new market broadcast to the events topic."""
        condition_id = event.get("market", "")
        payload = {
            "type": "new_market",
            "condition_id": condition_id,
            "payload": event,
            "timestamp": time.time(),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode() if condition_id else b"unknown",
            source="clob_orderbook",
        )
        self._market_event_count += 1

    async def _process_market_event(self, event: dict[str, Any]) -> None:
        """Forward market_resolved / new_market events to the events topic."""
        condition_id = event.get("condition_id", "")
        payload = {
            "type": event["event_type"],
            "condition_id": condition_id,
            "payload": event,
            "timestamp": event.get("timestamp", time.time()),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode() if condition_id else b"unknown",
            source="clob_orderbook",
        )
        self._market_event_count += 1

    def _heartbeat_fields(self) -> dict[str, Any]:
        return {
            "update_count": self._update_count,
            "market_event_count": self._market_event_count,
        }

    # ── connection management ────────────────────────────────────────

    async def _run_firehose(self) -> None:
        """Firehose connection: empty arrays → broadcast events only."""
        backoff = RECONNECT_BASE
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    payload = {
                        "type": "market",
                        "markets": [],
                        "assets_ids": [],
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(payload))
                    log.info("clob_orderbook.firehose_connected")
                    async for raw in ws:
                        await self._handle_message(str(raw))
            except websockets.ConnectionClosed:
                log.warning("clob_orderbook.firehose_disconnected", backoff=backoff)
            except Exception:
                log.exception("clob_orderbook.firehose_error", backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _run_targeted(self, asset_ids: list[str], conn_id: int) -> None:
        """Targeted connection: subscribe to specific asset IDs for price data."""
        backoff = RECONNECT_BASE
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    payload = {
                        "type": "market",
                        "markets": [],
                        "assets_ids": asset_ids,
                    }
                    await ws.send(json.dumps(payload))
                    log.info(
                        "clob_orderbook.targeted_connected",
                        conn_id=conn_id,
                        assets=len(asset_ids),
                    )
                    async for raw in ws:
                        await self._handle_message(str(raw))
            except websockets.ConnectionClosed:
                log.warning(
                    "clob_orderbook.targeted_disconnected",
                    conn_id=conn_id,
                    backoff=backoff,
                )
            except Exception:
                log.exception(
                    "clob_orderbook.targeted_error",
                    conn_id=conn_id,
                    backoff=backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def run(self) -> None:
        """Run firehose + targeted orderbook connections."""
        tasks: list[asyncio.Task[Any]] = []

        # 1. Firehose for market events (new_market, market_resolved)
        tasks.append(asyncio.create_task(self._run_firehose()))

        # 2. Targeted connections for orderbook price data
        asset_ids = self._subscribe_assets or list(self._token_map.keys())
        max_assets = self._max_ob_conns * _MAX_ASSETS_PER_WS
        if len(asset_ids) > max_assets:
            asset_ids = asset_ids[:max_assets]

        for i in range(0, len(asset_ids), _MAX_ASSETS_PER_WS):
            batch = asset_ids[i : i + _MAX_ASSETS_PER_WS]
            conn_id = i // _MAX_ASSETS_PER_WS
            tasks.append(asyncio.create_task(self._run_targeted(batch, conn_id)))

        log.info(
            "clob_orderbook.started",
            firehose=1,
            targeted=len(tasks) - 1,
            total_assets=min(len(asset_ids), max_assets),
        )

        # Heartbeat loop
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        tasks.append(heartbeat_task)

        try:
            # Wait for any task to crash (they all run forever normally)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if exc := task.exception():
                    log.error("clob_orderbook.task_crashed", error=str(exc))
        finally:
            for task in tasks:
                task.cancel()


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
