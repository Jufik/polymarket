"""Pending block ingestor — polls eth_getBlockByNumber('pending') for early trade detection.

Polymarket operators submit matchOrders txs via private channels (Marlin relay /
mev-bor / direct-to-validator), bypassing the public mempool. These txs are
invisible to newPendingTransactions but ARE visible in the validator's candidate
block via eth_getBlockByNumber("pending", true).

Empirical results: 81.4% of Polymarket txs seen ~1.1s before on-chain confirmation.
Free on public RPC endpoints (drpc, publicnode).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

import structlog
import websockets

from polymarket_pipeline.constants import FEE_MODULE_ADDRS
from polymarket_pipeline.live.normalizers.pending_block import PendingBlockNormalizer

log = structlog.get_logger()

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class _LRUSet:
    """Bounded set that evicts oldest entries to cap memory."""

    def __init__(self, maxsize: int = 5000) -> None:
        self._data: OrderedDict[str, None] = OrderedDict()
        self._maxsize = maxsize

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def add(self, key: str) -> None:
        if key in self._data:
            return
        if len(self._data) >= self._maxsize:
            self._data.popitem(last=False)
        self._data[key] = None

    def __len__(self) -> int:
        return len(self._data)


class PendingBlockIngestor:
    """Polls eth_getBlockByNumber('pending') for early Polymarket trade detection.

    Connects to a free RPC WebSocket endpoint and polls the pending block
    every poll_interval seconds. Filters transactions to FeeModule contracts,
    decodes matchOrders calldata, and publishes NormalizedTrade records.
    """

    def __init__(
        self,
        broker: Any,
        rpc_ws_url: str,
        topic: str = "pending.signal",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self._broker = broker
        self._rpc_ws_url = rpc_ws_url
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = PendingBlockNormalizer(token_market_map=token_market_map)
        self._poll_interval = poll_interval
        self._seen = _LRUSet(maxsize=5000)
        self._trade_count: int = 0
        self._poll_count: int = 0
        self._tx_count: int = 0

    async def _poll_pending_block(
        self, ws: Any
    ) -> list[dict[str, Any]]:
        """Fetch pending block and return new Polymarket txs."""
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBlockByNumber",
            "params": ["pending", True],
        })
        await ws.send(request)

        # Read response (skip any subscription notifications)
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("id") == 1:
                break

        result = msg.get("result")
        if not result or not result.get("transactions"):
            return []

        block_num = int(result.get("number", "0x0"), 16)
        now = time.time()
        new_txs: list[dict[str, Any]] = []

        for tx in result["transactions"]:
            if not isinstance(tx, dict):
                continue

            tx_hash = tx.get("hash", "")
            if tx_hash in self._seen:
                continue

            to_addr = (tx.get("to") or "").lower()
            if to_addr not in FEE_MODULE_ADDRS:
                continue

            self._seen.add(tx_hash)
            tx["_pending_block"] = block_num
            tx["_poll_ts"] = now
            new_txs.append(tx)

        return new_txs

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps({
            "source": "pending_block",
            "event": "heartbeat",
            "trade_count": self._trade_count,
            "poll_count": self._poll_count,
            "tx_count": self._tx_count,
            "ts": time.time(),
        })
        await self._broker.publish(
            message=heartbeat,
            topic=self._status_topic,
            key=b"pending_block",
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the pending block poller with auto-reconnect."""
        backoff = RECONNECT_BASE

        while True:
            try:
                log.info("pending_block.connecting", url=self._rpc_ws_url)
                async with websockets.connect(
                    self._rpc_ws_url,
                    ping_interval=30,
                    max_size=10_000_000,  # pending blocks can be >1MB
                ) as ws:
                    backoff = RECONNECT_BASE
                    log.info("pending_block.connected")

                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        while True:
                            self._poll_count += 1
                            try:
                                new_txs = await self._poll_pending_block(ws)
                            except TimeoutError:
                                log.debug("pending_block.poll_timeout")
                                await asyncio.sleep(self._poll_interval)
                                continue

                            for tx in new_txs:
                                self._tx_count += 1
                                trades = self._normalizer.decode_tx(tx)

                                for trade in trades:
                                    trade = trade.model_copy(
                                        update={"published_at": time.time()}
                                    )
                                    await self._broker.publish(
                                        message=trade.model_dump_json(),
                                        topic=self._topic,
                                        key=trade.condition_id.encode(),
                                    )
                                    self._trade_count += 1

                                if trades:
                                    log.info(
                                        "pending_block.trades",
                                        tx_hash=tx["hash"],
                                        count=len(trades),
                                        block=tx.get("_pending_block"),
                                    )

                            await asyncio.sleep(self._poll_interval)
                    finally:
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed as e:
                log.warning(
                    "pending_block.disconnected", reason=str(e), backoff=backoff
                )
            except Exception:
                log.exception("pending_block.error", backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
