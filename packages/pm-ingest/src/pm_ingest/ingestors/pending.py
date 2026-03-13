"""Pending block ingestor -- races multiple free RPC endpoints for earliest trade detection.

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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
import websockets
from pm_core.constants import FEE_MODULE_ADDRS, USDC_SCALE
from pm_core.models import NormalizedTrade
from pm_core.trade_id import make_trade_id_pending
from pm_core.types import Side, Source

from pm_ingest.base import BaseIngestor
from pm_ingest.publish import safe_publish

log = structlog.get_logger()

# Default endpoints -- each sees different txs, racing both maximizes coverage
DEFAULT_RPC_ENDPOINTS: list[str] = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon.drpc.org",
]

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0

# matchOrders(Order,Order[],uint256,uint256,uint256[],uint256,uint256[])
# Selector: 0x2287e350
MATCH_ORDERS_SELECTOR = bytes.fromhex("2287e350")

# Order struct: (salt, maker, signer, taker, tokenId, makerAmount, takerAmount,
#                expiration, nonce, feeRateBps, side, signatureType, signature)
ORDER_TUPLE = (
    "(uint256,address,address,address,uint256,uint256,uint256,"
    "uint256,uint256,uint256,uint8,uint8,bytes)"
)

MATCH_ORDERS_TYPES = [
    ORDER_TUPLE,  # takerOrder
    f"{ORDER_TUPLE}[]",  # makerOrders
    "uint256",  # takerFillAmount
    "uint256",  # takerReceiveAmount
    "uint256[]",  # makerFillAmounts
    "uint256",  # takerFeeAmount
    "uint256[]",  # makerFeeAmounts
]

# Order struct field indices
_MAKER = 1
_TOKENID = 4
_MAKER_AMOUNT = 5
_TAKER_AMOUNT = 6
_SIDE = 10

# Side values in the Order struct
_BUY = 0
_SELL = 1


class PendingBlockNormalizer:
    """Decodes FeeModule matchOrders calldata into NormalizedTrade records."""

    def __init__(
        self,
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._token_map = token_market_map or {}

    def decode_tx(self, tx: dict[str, Any]) -> list[NormalizedTrade]:
        """Decode a pending block transaction into trade records.

        Args:
            tx: Raw transaction dict from eth_getBlockByNumber("pending").
                Must have: hash, input, from.
                Injected by ingestor: _pending_block, _poll_ts.

        Returns:
            List of NormalizedTrade records (one per maker order fill).
        """
        from eth_abi import decode  # type: ignore[attr-defined]

        input_hex = tx.get("input", "")
        if len(input_hex) < 10:  # "0x" + 4 bytes selector
            return []

        input_bytes = bytes.fromhex(input_hex[2:])
        selector = input_bytes[:4]

        if selector != MATCH_ORDERS_SELECTOR:
            log.debug(
                "pending_block.unknown_selector",
                selector=f"0x{selector.hex()}",
                tx_hash=tx.get("hash"),
            )
            return []

        try:
            decoded = decode(MATCH_ORDERS_TYPES, input_bytes[4:])
        except Exception as e:
            log.warning(
                "pending_block.decode_failed",
                tx_hash=tx.get("hash"),
                error=str(e),
            )
            return []

        taker_order = decoded[0]
        maker_orders = decoded[1]
        maker_fill_amounts = decoded[4]
        maker_fee_amounts = decoded[6]

        tx_hash = tx["hash"]
        pending_block = tx.get("_pending_block", 0)
        poll_ts = tx.get("_poll_ts", 0.0)
        # Use poll timestamp as the trade timestamp (pending blocks have no final ts)
        timestamp = datetime.fromtimestamp(poll_ts, tz=UTC) if poll_ts else datetime.now(tz=UTC)

        # Taker address is the maker field of the taker Order struct
        taker_addr = taker_order[_MAKER]
        taker_side_raw = taker_order[_SIDE]
        asset_id = str(taker_order[_TOKENID])

        trades: list[NormalizedTrade] = []

        for i, maker_order in enumerate(maker_orders):
            if i >= len(maker_fill_amounts):
                break

            fill_amount = maker_fill_amounts[i]
            if fill_amount == 0:
                continue

            maker_addr = maker_order[_MAKER]
            maker_side = maker_order[_SIDE]
            maker_amount = maker_order[_MAKER_AMOUNT]
            taker_amount = maker_order[_TAKER_AMOUNT]

            if maker_amount == 0 or taker_amount == 0:
                continue

            # Compute price and size from maker order parameters + fill amount
            if maker_side == _SELL:
                # Maker sells tokens: makerAmount=tokens, takerAmount=USDC
                tokens_filled = Decimal(fill_amount)
                price_raw = Decimal(taker_amount) / Decimal(maker_amount)
                usdc_filled = tokens_filled * price_raw
            else:
                # Maker buys tokens: makerAmount=USDC, takerAmount=tokens
                usdc_filled = Decimal(fill_amount)
                price_raw = Decimal(maker_amount) / Decimal(taker_amount)
                tokens_filled = usdc_filled / price_raw if price_raw > 0 else Decimal(0)

            amount_usd = usdc_filled / USDC_SCALE
            size = tokens_filled / USDC_SCALE
            price = price_raw.quantize(Decimal("0.0001")) if price_raw > 0 else Decimal("0")

            # Clamp price to [0, 1] range for Polymarket binary markets
            if price > 1:
                price = Decimal("1.0000")
            if price < 0:
                price = Decimal("0.0000")

            # Fee for this maker fill (proportional)
            maker_fee = Decimal(0)
            if i < len(maker_fee_amounts):
                maker_fee = Decimal(maker_fee_amounts[i]) / USDC_SCALE

            condition_id = self._token_map[asset_id][0] if asset_id in self._token_map else asset_id

            trade = NormalizedTrade(
                trade_id=make_trade_id_pending(tx_hash=tx_hash, index=i),
                condition_id=condition_id,
                asset_id=asset_id,
                side=Side.BUY if taker_side_raw == _BUY else Side.SELL,
                price=price,
                size=size,
                amount_usd=amount_usd,
                fee_usd=maker_fee,
                maker=maker_addr,
                taker=taker_addr,
                timestamp=timestamp,
                source=Source.PENDING_BLOCK,
                tx_hash=tx_hash,
                order_hash=None,
                block_number=pending_block,
                is_backfill=False,
                version=0,
            )
            trades.append(trade)

        return trades


class _LRUSet:
    """Coroutine-safe bounded set that evicts oldest entries to cap memory."""

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


class PendingBlockIngestor(BaseIngestor):
    """Races multiple RPC endpoints to catch Polymarket trades ~1-2s early.

    Each endpoint has a different view of the pending block -- racing them
    catches ~2x more unique transactions than polling a single endpoint.
    A shared dedup set ensures each tx is processed exactly once.
    """

    source_name = "pending_block"

    def __init__(
        self,
        publisher: Any | None = None,
        rpc_ws_urls: list[str] | None = None,
        topic: str = "pending.signal",
        status_topic: str = "pipeline.status",
        token_market_map: dict[str, tuple[str, str]] | None = None,
        poll_interval: float = 0.5,
        *,
        broker: Any = None,
    ) -> None:
        super().__init__(publisher=publisher, topic=topic, status_topic=status_topic, broker=broker)
        self._rpc_ws_urls = rpc_ws_urls or DEFAULT_RPC_ENDPOINTS
        self._normalizer = PendingBlockNormalizer(token_market_map=token_market_map)
        self._poll_interval = poll_interval
        self._seen = _LRUSet(maxsize=10000)
        self._poll_count: int = 0
        self._tx_count: int = 0
        self._tx_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._drops_dedup: int = 0

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
                self._drops_dedup += 1
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
                                    self._drops_queue_full += 1
                                    log.warning(
                                        "pending_block.queue_full",
                                        dropped_tx=tx.get("hash", "?"),
                                        total_drops=self._drops_queue_full,
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

    def _heartbeat_fields(self) -> dict[str, Any]:
        """PendingBlock-specific heartbeat fields."""
        return {
            "poll_count": self._poll_count,
            "tx_count": self._tx_count,
            "endpoints": len(self._rpc_ws_urls),
            "drops_dedup": self._drops_dedup,
        }

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
