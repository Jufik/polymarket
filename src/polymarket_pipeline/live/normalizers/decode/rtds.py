"""Decode stage for RTDS WebSocket trade payloads.

Pure function — extracts raw fields from RTDS ``activity/trades`` messages
into a ``DecodedTrade``.  WS sources provide pre-computed price/size so the
on-chain amount fields are left as ``None``.  The shared ``enrich`` stage
detects this and uses the pre-computed path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from polymarket_pipeline.live.normalizers.types import DecodedTrade
from polymarket_pipeline.models import Source

_Q2 = Decimal("0.01")


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
