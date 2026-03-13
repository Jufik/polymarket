"""Consolidated decode functions for all sources.

Pure functions — extract raw fields from various message formats into
``DecodedTrade``.  No token_map lookup, no taker dedup, no price computation.
Those responsibilities belong to the shared ``enrich`` and ``validate`` stages.

Also contains the ``decode_settled_price`` function for UMA resolution events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from pm_core.types import Source

from pm_ingest.normalize.types import DecodedTrade, ResolutionOutcome

_log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# OrderFilled(bytes32 indexed orderHash, address indexed maker, address indexed taker,
#             uint256 makerAssetId, uint256 takerAssetId, uint256 makerAmountFilled,
#             uint256 takerAmountFilled, uint256 fee)
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

_Q2 = Decimal("0.01")

# Resolution decode constants
_YES_PRICE = 10**18
_VOIDED_PRICE = 5 * 10**17


# ---------------------------------------------------------------------------
# RPC log decode
# ---------------------------------------------------------------------------


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
    from eth_abi import decode

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


# ---------------------------------------------------------------------------
# RTDS WebSocket decode
# ---------------------------------------------------------------------------


def decode_rtds_payload(payload: dict[str, Any]) -> DecodedTrade | None:
    """Decode RTDS activity/trades payload into DecodedTrade.

    - Rounds price/size to 2dp ROUND_HALF_UP
    - Uses ``payload["timestamp"]`` (trade time), NOT top-level timestamp
    - maker = proxyWallet, taker = None
    - Returns ``None`` if size rounds to zero (dust trade).
    """
    asset_id = str(payload["asset"])

    # Round price to 2 decimal places to fix float imprecision
    price = Decimal(str(payload["price"])).quantize(_Q2, rounding=ROUND_HALF_UP)
    size = Decimal(str(payload["size"])).quantize(_Q2, rounding=ROUND_HALF_UP)

    if size <= 0:
        return None

    # Use payload.timestamp (seconds) — the actual trade time
    # Top-level msg["timestamp"] is delivery time (~500ms later)
    ts_seconds = int(payload["timestamp"])
    timestamp = datetime.fromtimestamp(ts_seconds, tz=UTC)

    return DecodedTrade(
        asset_id=asset_id,
        maker_asset_id=None,
        taker_asset_id=None,
        price=price,
        size=size,
        maker_amount=None,
        taker_amount=None,
        fee_raw=Decimal("0"),
        maker=payload.get("proxyWallet"),
        taker=None,
        tx_hash=payload.get("transactionHash"),
        order_hash=None,
        block_number=None,
        timestamp=timestamp,
        source=Source.RTDS,
        is_backfill=False,
    )


# ---------------------------------------------------------------------------
# Subgraph decode
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Resolution decode
# ---------------------------------------------------------------------------


def decode_settled_price(raw_hex: str) -> tuple[ResolutionOutcome, float]:
    """Decode int256 settledPrice from QuestionResolved event.

    Values:
        1e18  (1000000000000000000) -> YES  -- winning token pays $1.00
        0                          -> NO   -- winning token pays $1.00
        0.5e18 (500000000000000000) -> VOIDED -- each token pays $0.50 (50/50)

    Any other value is treated as VOIDED with proportional payout.
    """
    cleaned = raw_hex.removeprefix("0x").removeprefix("0X")
    raw = int(cleaned, 16) if cleaned else 0

    if raw == _YES_PRICE:
        return (ResolutionOutcome.YES, 1.0)
    elif raw == 0:
        return (ResolutionOutcome.NO, 0.0)
    elif raw == _VOIDED_PRICE:
        return (ResolutionOutcome.VOIDED, 0.5)
    else:
        _log.warning("resolution.unexpected_settled_price", raw=raw)
        return (ResolutionOutcome.VOIDED, raw / _YES_PRICE)
