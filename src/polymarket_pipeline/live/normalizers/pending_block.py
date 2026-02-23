"""Normalizer for pending block transactions — decodes FeeModule matchOrders calldata."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from eth_abi import decode  # type: ignore[attr-defined]

from polymarket_pipeline.constants import USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_pending

log = structlog.get_logger()

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
    ORDER_TUPLE,            # takerOrder
    f"{ORDER_TUPLE}[]",     # makerOrders
    "uint256",              # takerFillAmount
    "uint256",              # takerReceiveAmount
    "uint256[]",            # makerFillAmounts
    "uint256",              # takerFeeAmount
    "uint256[]",            # makerFeeAmounts
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
