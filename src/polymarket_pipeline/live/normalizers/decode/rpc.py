"""Decode stage for Polygon RPC OrderFilled log events.

Pure function — extracts raw fields from eth_subscribe log entries into a
``DecodedTrade``.  No token_map lookup, no taker dedup, no price computation.
Those responsibilities belong to the shared ``enrich`` and ``validate`` stages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from eth_abi import decode

from polymarket_pipeline.live.normalizers.polygon_rpc import ORDER_FILLED_SIG
from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source


def decode_rpc_log(
    log_entry: dict[str, Any],
    block_timestamp: float | None = None,
) -> DecodedTrade | None:
    """Decode an OrderFilled RPC log into DecodedTrade.

    Pure function — no token_map lookup, no taker dedup.
    Returns None for non-OrderFilled events.

    Args:
        log_entry: Raw Polygon log dict with keys: address, topics, data,
                   blockNumber, transactionHash.
        block_timestamp: Unix seconds from block data.  Falls back to
                         ``log_entry["_timestamp"]`` if not provided.
    """
    topics = log_entry.get("topics", [])

    # Only process OrderFilled events (4 topics: sig + 3 indexed params)
    if len(topics) < 4 or topics[0] != ORDER_FILLED_SIG:
        return None

    raw_data = bytes.fromhex(log_entry["data"][2:])

    # Decode indexed params from topics
    order_hash = topics[1]
    maker = "0x" + topics[2][-40:]
    taker = "0x" + topics[3][-40:]

    # Decode non-indexed params
    maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = decode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        raw_data,
    )

    # Determine the conditional token asset_id (non-USDC side)
    is_buy = taker_asset_id == 0
    if is_buy:
        asset_id = str(maker_asset_id)
    else:
        asset_id = str(taker_asset_id)

    # Resolve timestamp: explicit arg > log_entry["_timestamp"]
    if block_timestamp is not None:
        ts = block_timestamp
    elif "_timestamp" in log_entry:
        ts = float(log_entry["_timestamp"])
    else:
        # Should not happen in practice; the ingestor always injects _timestamp
        import time

        ts = time.time()

    tx_hash = log_entry["transactionHash"]
    block_number = int(log_entry["blockNumber"], 16)
    timestamp = datetime.fromtimestamp(ts, tz=UTC)

    return DecodedTrade(
        asset_id=asset_id,
        maker_asset_id=str(maker_asset_id),
        taker_asset_id=str(taker_asset_id),
        price=None,
        size=None,
        maker_amount=Decimal(maker_amount),
        taker_amount=Decimal(taker_amount),
        fee_raw=Decimal(fee),
        maker=maker,
        taker=taker,
        tx_hash=tx_hash,
        order_hash=order_hash,
        block_number=block_number,
        timestamp=timestamp,
        source=Source.ALCHEMY,
        is_backfill=False,
    )
