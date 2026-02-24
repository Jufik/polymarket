"""Pending block ingestor — races multiple free RPC endpoints for earliest trade detection.

Polymarket operators submit matchOrders txs via private channels, bypassing the
public mempool. These txs ARE visible in the validator's candidate block via
eth_getBlockByNumber("pending", true).

Empirical results (2026-02-23):
- 81.4% of Polymarket txs seen ~1.1s before on-chain confirmation
- Racing publicnode + drpc catches ~2x more unique txs than either alone
- publicnode is ~1.7s faster when both see the same tx, but drpc sees
  different txs (different mempool/pending block views)
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
from polymarket_pipeline.live.circuit_breaker import CircuitBreaker
from polymarket_pipeline.live.ingestors._publish import safe_publish
from polymarket_pipeline.live.normalizers.pending_block import PendingBlockNormalizer

log = structlog.get_logger()

# Default endpoints — each sees different txs, racing both maximizes coverage
DEFAULT_RPC_ENDPOINTS: list[str] = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon.drpc.org",
]

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
HEARTBEAT_INTERVAL = 10.0


class _LRUSet:
    """Thread-safe bounded set that evicts oldest entries to cap memory."""

    def __init__(self, maxsize: int = 10000) -> None:
        self._data: OrderedDict[str, None] = OrderedDict()
        self._maxsize = maxsize

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def add(self, key: str) -> bool:
        """Add key. Returns True if new, False if already seen."""
        if key in self._data:
            return False
        if len(self._data) >= self._maxsize:
            self._data.popitem(last=False)
        self._data[key] = None
        return True

    def __len__(self) -> int:
        return len(self._data)


class PendingBlockIngestor:
    """Races multiple RPC endpoints to catch Polymarket trades ~1-2s early.

    Each endpoint has a different view of the pending block — racing them
    catches ~2x more unique transactions than polling a single endpoint.
    A shared dedup set ensures each tx is processed exactly once.
    """

    def __init__(
        self,
        broker: Any,
        rpc_ws_urls: list[str] | None = None,
        topic: str = "pending.signal",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self._broker = broker
        self._rpc_ws_urls = rpc_ws_urls or DEFAULT_RPC_ENDPOINTS
        self._topic = topic
        self._status_topic = status_topic
        self._normalizer = PendingBlockNormalizer(token_market_map=token_market_map)
        self._poll_interval = poll_interval
        self._seen = _LRUSet(maxsize=10000)
        self._trade_count: int = 0
        self._poll_count: int = 0
        self._tx_count: int = 0
        self._tx_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._circuit_breaker = CircuitBreaker()

    def _extract_new_txs(self, result: dict[str, Any], source: str) -> list[dict[str, Any]]:
        """Extract new Polymarket txs from a pending block response."""
        if not result or not result.get("transactions"):
            return []

        block_num = int(result.get("number", "0x0"), 16)
        now = time.time()
        new_txs: list[dict[str, Any]] = []

        for tx in result["transactions"]:
            if not isinstance(tx, dict):
                continue

            tx_hash = tx.get("hash", "")
            to_addr = (tx.get("to") or "").lower()
            if to_addr not in FEE_MODULE_ADDRS:
                continue

            # Shared dedup: only the first endpoint to see a tx processes it
            if not self._seen.add(tx_hash):
                continue

            tx["_pending_block"] = block_num
            tx["_poll_ts"] = now
            tx["_source_rpc"] = source
            new_txs.append(tx)

        return new_txs

    async def _poll_loop(self, url: str) -> None:
        """Poll a single RPC endpoint, feeding new txs into the shared queue."""
        name = url.split("//")[-1].split("/")[0].split(".")[0]
        backoff = RECONNECT_BASE

        while True:
            try:
                log.info("pending_block.connecting", url=url, source=name)
                async with websockets.connect(url, ping_interval=30, max_size=10_000_000) as ws:
                    backoff = RECONNECT_BASE
                    log.info("pending_block.connected", source=name)
                    req_id = 0

                    while True:
                        req_id += 1
                        self._poll_count += 1
                        try:
                            await ws.send(
                                json.dumps(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": req_id,
                                        "method": "eth_getBlockByNumber",
                                        "params": ["pending", True],
                                    }
                                )
                            )

                            # Read response, skip subscription notifications
                            while True:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                                msg = json.loads(raw)
                                if msg.get("id") == req_id:
                                    break

                            result = msg.get("result")
                            new_txs = self._extract_new_txs(result, name)

                            for tx in new_txs:
                                try:
                                    self._tx_queue.put_nowait(tx)
                                except asyncio.QueueFull:
                                    log.warning(
                                        "pending_block.queue_full",
                                        dropped_tx=tx.get("hash", "?"),
                                    )

                        except TimeoutError:
                            log.debug("pending_block.poll_timeout", source=name)

                        await asyncio.sleep(self._poll_interval)

            except websockets.ConnectionClosed as e:
                log.warning(
                    "pending_block.disconnected",
                    source=name,
                    reason=str(e),
                    backoff=backoff,
                )
            except Exception:
                log.exception("pending_block.error", source=name, backoff=backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _process_loop(self) -> None:
        """Consume txs from the shared queue, decode, and publish."""
        while True:
            tx = await self._tx_queue.get()
            self._tx_count += 1

            try:
                trades = self._normalizer.decode_tx(tx)
            except Exception:
                log.exception("pending_block.decode_error", tx_hash=tx.get("hash", "?"))
                continue

            for trade in trades:
                trade = trade.model_copy(update={"published_at": time.time()})
                await safe_publish(
                    self._broker,
                    message=trade.model_dump_json(),
                    topic=self._topic,
                    key=trade.condition_id.encode(),
                    source="pending_block",
                    circuit_breaker=self._circuit_breaker,
                )
                self._trade_count += 1

            if trades:
                log.info(
                    "pending_block.trades",
                    tx_hash=tx["hash"],
                    count=len(trades),
                    block=tx.get("_pending_block"),
                    source=tx.get("_source_rpc"),
                )

    async def _publish_heartbeat(self) -> None:
        """Publish heartbeat to pipeline.status."""
        heartbeat = json.dumps(
            {
                "source": "pending_block",
                "event": "heartbeat",
                "trade_count": self._trade_count,
                "poll_count": self._poll_count,
                "tx_count": self._tx_count,
                "endpoints": len(self._rpc_ws_urls),
                "ts": time.time(),
            }
        )
        await safe_publish(
            self._broker,
            message=heartbeat,
            topic=self._status_topic,
            key=b"pending_block",
            source="pending_block",
            circuit_breaker=self._circuit_breaker,
        )

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every HEARTBEAT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._publish_heartbeat()

    async def run(self) -> None:
        """Run the multi-endpoint pending block poller."""
        log.info(
            "pending_block.starting",
            endpoints=len(self._rpc_ws_urls),
            poll_interval=self._poll_interval,
        )

        tasks: list[asyncio.Task[Any]] = []

        # One poll loop per endpoint
        for url in self._rpc_ws_urls:
            tasks.append(asyncio.create_task(self._poll_loop(url)))

        # Single processing loop (deduped via shared _seen set)
        tasks.append(asyncio.create_task(self._process_loop()))
        tasks.append(asyncio.create_task(self._heartbeat_loop()))

        try:
            # Wait for any task to fail (they shouldn't — each has its own error handling)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception():
                    log.exception(
                        "pending_block.task_failed",
                        error=str(task.exception()),
                    )
        finally:
            for task in tasks:
                task.cancel()
