"""RTDS WebSocket normalizer.

RTDS provides rich trade data including proxyWallet (maker's on-chain address),
conditionId (no lookup needed), and transactionHash. Prices arrive as floats
with occasional imprecision that must be rounded.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_ws


class RTDSNormalizer:
    """Normalizes RTDS WebSocket messages into NormalizedTrade."""

    def normalize(self, msg: dict[str, Any]) -> NormalizedTrade:
        """Normalize a single RTDS trade message."""
        payload = msg["payload"]

        asset_id = str(payload["asset"])
        side = Side(payload["side"])

        # Round price to 2 decimal places to fix float imprecision
        price = Decimal(str(payload["price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        size = Decimal(str(payload["size"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        amount_usd = (price * size).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Use payload.timestamp (seconds) — the actual trade time
        # Top-level msg["timestamp"] is delivery time (~500ms later)
        ts_seconds = int(payload["timestamp"])

        trade_id = make_trade_id_ws(
            asset_id=asset_id,
            timestamp_ms=ts_seconds * 1000,
            price=str(price),
            size=str(size),
        )

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=payload["conditionId"],
            asset_id=asset_id,
            side=side,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=Decimal("0"),
            maker=payload.get("proxyWallet"),
            taker=None,
            timestamp=datetime.fromtimestamp(ts_seconds, tz=UTC),
            source=Source.RTDS,
            tx_hash=payload.get("transactionHash"),
            order_hash=None,
            block_number=None,
            is_backfill=False,
            version=1,
        )
