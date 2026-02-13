"""Market WebSocket normalizer.

Handles last_trade_price events from the Market WS. Other event types
(book, price_change) are skipped since they're orderbook data, not trades.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_ws


class MarketWSNormalizer:
    """Normalizes Market WebSocket trade messages into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._token_map = token_market_map

    def normalize(self, msg: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a Market WS message. Returns None for non-trade events."""
        if msg.get("event_type") != "last_trade_price":
            return None

        asset_id = str(msg["asset_id"])
        price = Decimal(msg["price"])
        size = Decimal(msg["size"])
        timestamp_ms = int(msg["timestamp"])

        # Fee: bps of notional
        fee_bps = int(msg.get("fee_rate_bps", "0"))
        amount_usd = (price * size).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fee_usd = (amount_usd * fee_bps / 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        trade_id = make_trade_id_ws(
            asset_id=asset_id,
            timestamp_ms=timestamp_ms,
            price=str(price),
            size=str(size),
        )

        condition_id, _ = self._token_map.get(asset_id, ("unknown", "unknown"))

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side(msg["side"]),
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=None,
            taker=None,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            source=Source.WEBSOCKET,
            tx_hash=msg.get("transaction_hash"),
            order_hash=None,
            block_number=None,
            is_backfill=False,
            version=1,
        )
