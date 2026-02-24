"""Mempool ingestor — wraps Rust PyO3 sidecar for pending tx gossip."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.normalizers.mempool import MempoolNormalizer

log = structlog.get_logger()

HEARTBEAT_INTERVAL = 10.0


class MempoolIngestor:
    """Consumes decoded pending txs from the Rust mempool monitor.

    The Rust PyO3 module (polymarket_mempool) handles:
    - devp2p peer discovery and connection (Polygon network)
    - Pending tx filtering (CTF/NegRisk Exchange addresses)
    - fillOrder/fillOrders calldata decoding (alloy sol! macro)

    This Python wrapper handles:
    - Normalization to NormalizedTrade
    - Publishing to Redpanda (mempool.raw topic)
    - Heartbeat reporting to pipeline.status
    """

    def __init__(
        self,
        broker: Any,
        topic: str = "mempool.raw",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        listen_port: int = 30304,
    ) -> None:
        self._broker = broker
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = MempoolNormalizer(token_market_map=token_market_map)
        self._listen_port = listen_port
        self._trade_count: int = 0
        self._peers_active: int = 0

    async def _handle_trade(self, raw: dict[str, Any]) -> None:
        """Process a single decoded trade dict from the Rust sidecar."""
        trade = self._normalizer.normalize(raw)
        if trade is None:
            return

        trade = trade.model_copy(update={"published_at": time.time()})
        trade_json = trade.model_dump_json()
        await safe_publish(
            self._broker,
            message=trade_json,
            topic=self._topic,
            key=trade.condition_id.encode(),
            source="mempool",
        )
        self._trade_count += 1

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps({
            "source": "mempool",
            "event": "heartbeat",
            "trade_count": self._trade_count,
            "peers_active": self._peers_active,
            "ts": time.time(),
        })
        await safe_publish(
            self._broker,
            message=heartbeat,
            topic=self._status_topic,
            key=b"mempool",
            source="mempool",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the mempool ingestor.

        Imports the Rust PyO3 module and iterates its async stream.
        Falls back to a warning if the Rust module is not installed.
        """
        try:
            from polymarket_mempool import MempoolMonitor
        except ImportError:
            log.error(
                "mempool.rust_module_not_installed",
                hint="Install with: cd crates/polymarket-mempool && maturin develop --release",
            )
            return

        monitor = MempoolMonitor(listen_port=self._listen_port)
        log.info("mempool.starting", port=self._listen_port)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            async for raw in monitor.stream():
                # Update peer count from sidecar metadata (if present)
                if "_peers_active" in raw:
                    peers = raw.pop("_peers_active")
                    self._peers_active = peers
                    if peers == 0:
                        log.warning("mempool.zero_peers")

                await self._handle_trade(raw)
        except Exception:
            log.exception("mempool.error")
        finally:
            heartbeat_task.cancel()
