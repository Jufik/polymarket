"""Shared enrich stage — consistent token map lookup + taker dedup.

All normalizer sources (RPC, RTDS, Subgraph, PendingBlock, Mempool, WS)
feed their ``DecodedTrade`` through this single function.  This eliminates
the historical bug (H1) where different normalizers handled unknown
asset_ids inconsistently.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pm_core.constants import EXCHANGE_ADDRS, USDC_SCALE
from pm_core.models import NormalizedTrade
from pm_core.token_map import TokenMap
from pm_core.trade_id import make_trade_id_chain, make_trade_id_ws
from pm_core.types import Side, Source

from pm_ingest.normalize.types import DecodedTrade, Rejection

_Q4 = Decimal("0.0001")

# ReplacingMergeTree version: on-chain (2) > off-chain (1) > mempool/pending (0)
_VERSION: dict[Source, int] = {
    Source.ALCHEMY: 2,
    Source.GOLDSKY_SINK: 2,
    Source.GOLDSKY_SUBGRAPH: 2,
    Source.RTDS: 1,
    Source.WEBSOCKET: 1,
    Source.MEMPOOL: 0,
    Source.PENDING_BLOCK: 0,
}


def enrich(
    decoded: DecodedTrade,
    token_map: TokenMap,
) -> NormalizedTrade | Rejection:
    """Enrich a decoded trade into a NormalizedTrade or reject it.

    Steps (in order):
    1. Taker dedup — drop taker-perspective duplicates.
    2. Token map lookup — resolve condition_id from asset_id.
    3. Side + price/size determination.
    4. Trade ID generation.
    5. Version assignment.
    """

    # 1. Taker dedup
    if decoded.taker is not None and decoded.taker.lower() in EXCHANGE_ADDRS:
        return Rejection(
            reason="taker_dedup",
            asset_id=decoded.asset_id,
            source=decoded.source,
            timestamp=decoded.timestamp,
        )

    # 2. Token map lookup
    mapping = token_map.lookup(decoded.asset_id)
    if mapping is None:
        return Rejection(
            reason="unknown_asset",
            asset_id=decoded.asset_id,
            source=decoded.source,
            timestamp=decoded.timestamp,
            details="not in token_map",
        )
    condition_id = mapping[0]

    # 3. Side + price/size determination
    if decoded.price is not None and decoded.size is not None:
        # WS source: pre-computed price/size
        price = decoded.price.quantize(_Q4, rounding=ROUND_HALF_UP)
        size = decoded.size.quantize(_Q4, rounding=ROUND_HALF_UP)
        amount_usd = (price * size).quantize(_Q4, rounding=ROUND_HALF_UP)
        fee_usd = (decoded.fee_raw / USDC_SCALE).quantize(_Q4, rounding=ROUND_HALF_UP)
        side = Side.BUY
    else:
        # On-chain source: compute from raw amounts
        is_buy = str(decoded.maker_asset_id) == "0"
        side = Side.BUY if is_buy else Side.SELL

        if is_buy:
            usdc_amount = decoded.maker_amount
            token_amount = decoded.taker_amount
        else:
            usdc_amount = decoded.taker_amount
            token_amount = decoded.maker_amount

        assert usdc_amount is not None
        assert token_amount is not None

        amount_usd = (usdc_amount / USDC_SCALE).quantize(_Q4, rounding=ROUND_HALF_UP)
        size = (token_amount / USDC_SCALE).quantize(_Q4, rounding=ROUND_HALF_UP)
        price = (
            (amount_usd / size).quantize(_Q4, rounding=ROUND_HALF_UP) if size > 0 else Decimal("0")
        )
        fee_usd = (decoded.fee_raw / USDC_SCALE).quantize(_Q4, rounding=ROUND_HALF_UP)

    # 4. Trade ID generation
    if decoded.tx_hash and decoded.order_hash:
        trade_id = make_trade_id_chain(tx_hash=decoded.tx_hash, order_hash=decoded.order_hash)
    else:
        trade_id = make_trade_id_ws(
            asset_id=decoded.asset_id,
            timestamp_ms=int(decoded.timestamp.timestamp() * 1000),
            price=str(price),
            size=str(size),
        )

    # 5. Version
    version = _VERSION[decoded.source]

    return NormalizedTrade(
        trade_id=trade_id,
        condition_id=condition_id,
        asset_id=decoded.asset_id,
        side=side,
        price=price,
        size=size,
        amount_usd=amount_usd,
        fee_usd=fee_usd,
        maker=decoded.maker,
        taker=decoded.taker,
        timestamp=decoded.timestamp,
        source=decoded.source,
        tx_hash=decoded.tx_hash,
        order_hash=decoded.order_hash,
        block_number=decoded.block_number,
        is_backfill=decoded.is_backfill,
        version=version,
    )
