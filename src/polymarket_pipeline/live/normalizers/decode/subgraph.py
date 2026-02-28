"""Decode stage for Goldsky Subgraph orderFilledEvent responses.

Pure function — extracts raw fields from GraphQL ``orderFilledEvent`` dicts
into a ``DecodedTrade``.  No token_map lookup, no taker dedup, no price
computation.  Those responsibilities belong to the shared ``enrich`` and
``validate`` stages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source


def decode_subgraph_event(event: dict[str, Any]) -> DecodedTrade | None:
    """Decode Goldsky Subgraph orderFilledEvent into DecodedTrade.

    On-chain source: side is determined by ``makerAssetId == "0"`` in the
    enrich stage.  Source = GOLDSKY_SUBGRAPH, is_backfill=False.
    """
    maker_asset_id = event["makerAssetId"]
    taker_asset_id = event["takerAssetId"]

    # Determine the conditional token asset_id (non-USDC side)
    is_buy = maker_asset_id == "0"
    if is_buy:
        asset_id = taker_asset_id
    else:
        asset_id = maker_asset_id

    tx_hash = event["transactionHash"]
    order_hash = event.get("orderHash", "")

    timestamp = datetime.fromtimestamp(int(event["timestamp"]), tz=UTC)

    return DecodedTrade(
        asset_id=asset_id,
        maker_asset_id=maker_asset_id,
        taker_asset_id=taker_asset_id,
        price=None,
        size=None,
        maker_amount=Decimal(int(event["makerAmountFilled"])),
        taker_amount=Decimal(int(event["takerAmountFilled"])),
        fee_raw=Decimal(int(event["fee"])),
        maker=event["maker"],
        taker=event["taker"],
        tx_hash=tx_hash,
        order_hash=order_hash,
        block_number=None,
        timestamp=timestamp,
        source=Source.GOLDSKY_SUBGRAPH,
        is_backfill=False,
    )
