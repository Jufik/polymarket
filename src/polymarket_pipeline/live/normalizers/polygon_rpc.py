"""Normalizer for raw Polygon RPC log events (eth_subscribe)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from eth_abi import decode

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain


# OrderFilled(bytes32 indexed orderHash, address indexed maker, address indexed taker,
#             uint256 makerAssetId, uint256 takerAssetId, uint256 makerAmountFilled,
#             uint256 takerAmountFilled, uint256 fee)
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"


class PolygonRPCNormalizer:
    """Normalizes raw Polygon log events for OrderFilled into NormalizedTrade."""

    def __init__(
        self,
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._token_map = token_market_map or {}

    def normalize(self, log: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single raw log event.

        Args:
            log: Raw Polygon log dict with keys: address, topics, data,
                 blockNumber, transactionHash. Must also have _timestamp
                 (Unix seconds, injected by the ingestor from block data).

        Returns:
            NormalizedTrade or None if not an OrderFilled event or taker duplicate.
        """
        topics = log["topics"]

        # Only process OrderFilled events (4 topics: sig + 3 indexed params)
        if len(topics) < 4 or topics[0] != ORDER_FILLED_SIG:
            return None

        raw_data = bytes.fromhex(log["data"][2:])

        # Decode indexed params from topics
        order_hash = topics[1]
        maker = "0x" + topics[2][-40:]
        taker = "0x" + topics[3][-40:]

        # Drop taker-perspective duplicates
        if taker.lower() in EXCHANGE_ADDRS:
            return None

        # Decode non-indexed params
        maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = decode(
            ["uint256", "uint256", "uint256", "uint256", "uint256"],
            raw_data,
        )

        # Determine side: BUY if taker pays USDC (taker_asset_id == 0)
        is_buy = taker_asset_id == 0
        if is_buy:
            asset_id = str(maker_asset_id)
            usdc_raw = taker_amount
            token_amount = maker_amount
        else:
            asset_id = str(taker_asset_id)
            usdc_raw = maker_amount
            token_amount = taker_amount

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        fee_usd = Decimal(fee) / USDC_SCALE
        size = Decimal(token_amount) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        # Resolve condition_id
        if asset_id in self._token_map:
            condition_id = self._token_map[asset_id][0]
        else:
            condition_id = asset_id  # best effort fallback

        tx_hash = log["transactionHash"]
        block_number = int(log["blockNumber"], 16)
        timestamp = datetime.fromtimestamp(log["_timestamp"], tz=UTC)

        return NormalizedTrade(
            trade_id=make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash),
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=maker,
            taker=taker,
            timestamp=timestamp,
            source=Source.ALCHEMY,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=block_number,
            is_backfill=False,
            version=2,
        )
