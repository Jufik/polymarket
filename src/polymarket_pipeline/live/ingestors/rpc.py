"""RPC eth_subscribe ingestor -- Polygon RPC logs for OrderFilled events.

Races multiple public RPC WebSocket endpoints in parallel for redundancy.
Each endpoint runs its own ``_connection_loop``, all feeding a shared queue.
Duplicates across endpoints are dropped via TTL-based ``TradeDedup``.

Also runs an optional resolution detection loop that listens for the UMA CTF
Adapter's ``QuestionResolved`` event on-chain and publishes resolution events
to the ``markets.events`` Kafka topic (~3s latency, 100% reliable).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog
import websockets

from polymarket_pipeline.constants import NEGRISK_UMA_ADAPTER, UMA_CTF_ADAPTER_V3
from polymarket_pipeline.live.dedup import TradeDedup
from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.ingestors.base import BaseIngestor
from polymarket_pipeline.live.normalizers.decode.resolution import decode_settled_price
from polymarket_pipeline.live.normalizers.decode.rpc import decode_rpc_log
from polymarket_pipeline.live.normalizers.enrich import enrich
from polymarket_pipeline.live.normalizers.polygon_rpc import ORDER_FILLED_SIG
from polymarket_pipeline.live.normalizers.token_map import TokenMap
from polymarket_pipeline.live.normalizers.types import Rejection
from polymarket_pipeline.live.normalizers.validate import validate

log = structlog.get_logger()

# Both CTF Exchange contracts (checksummed — publicnode WS requires mixed-case)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEGRISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# keccak256("QuestionResolved(bytes32,int256)")
QUESTION_RESOLVED_SIG = "0x566c3fbd0982e206be981f8d7a42e3e436525258ecc0adc044023b81ab281d0e"

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
_QUEUE_MAXSIZE = 1000  # backpressure bound between WS read and publish
_STALE_TIMEOUT = 120.0  # Force reconnect if no messages for 2 minutes
_DEDUP_TTL_S = 60.0  # TTL for cross-endpoint trade dedup


class RPCIngestor(BaseIngestor):
    """Subscribes to Polygon OrderFilled logs via multiple public RPC WebSockets.

    Races all endpoints in parallel — whichever delivers first wins.
    Duplicates across endpoints are dropped via TTL-based dedup.

    Optionally runs a parallel resolution detection loop that listens for
    ``QuestionResolved`` events from the UMA CTF Adapter and publishes
    them to the ``markets.events`` Kafka topic.
    """

    source_name = "alchemy"  # backward compat with CH/Kafka stored data + quality checker

    def __init__(
        self,
        broker: Any,
        ws_urls: list[str],
        topic: str = "trades.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        markets_events_topic: str = "markets.events",
        resolution_enabled: bool = True,
    ) -> None:
        super().__init__(broker=broker, topic=topic, status_topic=status_topic)
        self._ws_urls = ws_urls
        self._token_map = TokenMap(token_market_map or {})
        self._last_block: int = 0
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._dedup = TradeDedup(ttl_s=_DEDUP_TTL_S)
        self._drops_taker_dedup: int = 0
        self._drops_dedup: int = 0
        self._markets_events_topic = markets_events_topic
        self._resolution_enabled = resolution_enabled
        self._resolution_count: int = 0

    async def _handle_message(self, raw: str) -> None:
        """Process a single raw WebSocket message from RPC.

        Normalizes and enqueues for async publishing so the WS read loop is
        never blocked by a slow broker.
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("rpc.invalid_json", raw=raw[:100])
            return

        # Skip subscription confirmations
        if "method" not in msg:
            return

        if msg.get("method") != "eth_subscription":
            return

        result = msg.get("params", {}).get("result")
        if not result:
            return

        # Use blockTimestamp from RPC (hex-encoded unix seconds).
        # Fall back to current time if not present.
        block_ts_hex = result.get("blockTimestamp")
        if block_ts_hex:
            block_ts: float = float(int(block_ts_hex, 16))
        else:
            block_ts = float(int(time.time()))

        # Decode -> Enrich -> Validate pipeline
        decoded = decode_rpc_log(result, block_timestamp=block_ts)
        if decoded is None:
            return  # not an OrderFilled event

        enriched = enrich(decoded, self._token_map)
        if isinstance(enriched, Rejection):
            if enriched.reason == "taker_dedup":
                self._drops_taker_dedup += 1
            return

        trade = validate(enriched)
        if isinstance(trade, Rejection):
            return

        # Cross-endpoint dedup — first endpoint to deliver wins
        if self._dedup.is_duplicate(trade.trade_id):
            self._drops_dedup += 1
            return

        trade = trade.model_copy(update={"published_at": time.time()})
        trade_json = trade.model_dump_json()

        try:
            self._queue.put_nowait((trade.condition_id, trade_json))
        except asyncio.QueueFull:
            self._drops_queue_full += 1
            log.warning(
                "rpc.queue_full",
                trade_id=trade.trade_id,
                total_drops=self._drops_queue_full,
            )
            return

        self._last_block = trade.block_number or self._last_block
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
                source="alchemy",
                circuit_breaker=self._circuit_breaker,
            )

    def _heartbeat_fields(self) -> dict[str, Any]:
        """RPC-specific heartbeat fields."""
        fields: dict[str, Any] = {
            "last_block": self._last_block,
            "drops_taker_dedup": self._drops_taker_dedup,
            "drops_dedup": self._drops_dedup,
            "endpoints": len(self._ws_urls),
        }
        if self._resolution_enabled:
            fields["resolution_count"] = self._resolution_count
        return fields

    async def run(self) -> None:
        """Run the RPC ingestor with auto-reconnect + optional resolution loop."""
        log.info(
            "rpc.starting",
            endpoints=len(self._ws_urls),
            urls=[u.split("//")[-1].split("/")[0] for u in self._ws_urls],
        )
        tasks = [
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._publish_loop()),
        ]
        # One connection loop per endpoint
        for url in self._ws_urls:
            tasks.append(asyncio.create_task(self._connection_loop(url)))
        # Resolution on first endpoint only (events are rare)
        if self._resolution_enabled:
            tasks.append(asyncio.create_task(self._resolution_loop(self._ws_urls[0])))
        try:
            # Wait for any task to crash — they all run forever normally
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if exc := task.exception():
                    log.error("rpc.task_crashed", error=str(exc))
        finally:
            for task in tasks:
                task.cancel()

    async def _connection_loop(self, ws_url: str) -> None:
        """Reconnect loop for a single RPC OrderFilled WebSocket endpoint."""
        endpoint = ws_url.split("//")[-1].split("/")[0]  # short name for logs
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("rpc.connecting", endpoint=endpoint)
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    log.info("rpc.connected", endpoint=endpoint)

                    subscribe = json.dumps(
                        {
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
                        }
                    )
                    await ws.send(subscribe)

                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=_STALE_TIMEOUT
                            )
                        except TimeoutError:
                            log.warning(
                                "rpc.stale",
                                endpoint=endpoint,
                                last_block=self._last_block,
                                timeout_s=_STALE_TIMEOUT,
                            )
                            break  # Force reconnect
                        await self._handle_message(str(raw))

            except websockets.ConnectionClosed as e:
                log.warning("rpc.disconnected", endpoint=endpoint, reason=str(e), backoff=backoff)
            except Exception:
                log.exception("rpc.error", endpoint=endpoint, backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    # ── Resolution detection loop ────────────────────────────────────────

    async def _resolution_loop(self, ws_url: str) -> None:
        """Subscribe to UMA CTF Adapter QuestionResolved events on-chain.

        Publishes resolution events to the ``markets.events`` Kafka topic
        in the same format as the CLOB WS ``market_resolved`` event.
        """
        endpoint = ws_url.split("//")[-1].split("/")[0]
        backoff = RECONNECT_BASE
        while True:
            try:
                log.info("rpc.resolution.connecting", endpoint=endpoint)
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    backoff = RECONNECT_BASE
                    log.info("rpc.resolution.connected", endpoint=endpoint)

                    subscribe = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "eth_subscribe",
                            "params": [
                                "logs",
                                {
                                    "address": [UMA_CTF_ADAPTER_V3, NEGRISK_UMA_ADAPTER],
                                    "topics": [[QUESTION_RESOLVED_SIG]],
                                },
                            ],
                        }
                    )
                    await ws.send(subscribe)

                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=_STALE_TIMEOUT
                            )
                        except TimeoutError:
                            log.warning(
                                "rpc.resolution.stale",
                                endpoint=endpoint,
                                timeout_s=_STALE_TIMEOUT,
                            )
                            break  # Force reconnect
                        await self._handle_resolution_message(str(raw))

            except websockets.ConnectionClosed as e:
                log.warning(
                    "rpc.resolution.disconnected",
                    endpoint=endpoint, reason=str(e), backoff=backoff,
                )
            except Exception:
                log.exception("rpc.resolution.error", endpoint=endpoint, backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _handle_resolution_message(self, raw: str) -> None:
        """Decode QuestionResolved log and publish to markets.events.

        Event signature: ``QuestionResolved(bytes32 questionID, int256 settledPrice)``
        - ``questionID`` (topic[1]) = condition_id (hex, 0x-prefixed 32 bytes)
        - ``settledPrice`` (topic[2]) = 1e18 for YES, 0 for NO
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("method") != "eth_subscription":
            return

        result = msg.get("params", {}).get("result")
        if not result:
            return

        topics = result.get("topics", [])
        if len(topics) < 3:
            return

        # topic[0] = event sig, topic[1] = questionID, topic[2] = settledPrice
        condition_id = topics[1]  # 0x-prefixed 32-byte hex
        outcome, payout = decode_settled_price(topics[2])
        winner = outcome.value  # "YES", "NO", or "VOIDED"

        payload = {
            "type": "market_resolved",
            "condition_id": condition_id,
            "payload": {
                "event_type": "market_resolved",
                "condition_id": condition_id,
                "winner": winner,
                "settled_price": payout,
                "source": "rpc_on_chain",
                "tx_hash": result.get("transactionHash"),
                "block_number": (
                    int(result["blockNumber"], 16)
                    if result.get("blockNumber")
                    else None
                ),
            },
            "timestamp": time.time(),
        }
        await safe_publish(
            self._broker,
            message=json.dumps(payload),
            topic=self._markets_events_topic,
            key=condition_id.encode(),
            source="rpc_resolution",
        )
        self._resolution_count += 1
        log.info(
            "rpc.resolution.detected",
            condition_id=condition_id,
            winner=winner,
            block=payload["payload"]["block_number"],
        )
