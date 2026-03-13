"""Validate stage — price clamping and size checks.

Stage 3 of the normalization pipeline (Decode -> Enrich -> Validate).
Centralises validation that was previously scattered across individual normalizers.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from pm_core.models import NormalizedTrade

from pm_ingest.normalize.types import Rejection

log = structlog.get_logger()

_PRICE_MIN = Decimal("0")
_PRICE_MAX = Decimal("1")
_PRICE_CLAMP_LOW = Decimal("0.0000")
_PRICE_CLAMP_HIGH = Decimal("1.0000")


def validate(trade: NormalizedTrade) -> NormalizedTrade | Rejection:
    """Validate a normalized trade, clamping price and rejecting bad sizes.

    Rules (applied in order):
        1. ``size <= 0`` -> Rejection with reason ``"zero_size"``.
        2. ``price`` outside ``[0, 1]`` -> clamp and recompute ``amount_usd``.
        3. Otherwise return the trade unchanged.
    """
    # 1. Size must be positive
    if trade.size <= 0:
        return Rejection(
            reason="zero_size",
            asset_id=trade.asset_id,
            source=trade.source,
            timestamp=trade.timestamp,
            details=f"size={trade.size}",
        )

    # 2. Price clamping
    clamped_price: Decimal | None = None

    if trade.price > _PRICE_MAX:
        log.warning(
            "validate.price_clamped",
            trade_id=trade.trade_id,
            original_price=str(trade.price),
            clamped_to="1.0000",
        )
        clamped_price = _PRICE_CLAMP_HIGH
    elif trade.price < _PRICE_MIN:
        log.warning(
            "validate.price_clamped",
            trade_id=trade.trade_id,
            original_price=str(trade.price),
            clamped_to="0.0000",
        )
        clamped_price = _PRICE_CLAMP_LOW

    if clamped_price is not None:
        new_amount_usd = clamped_price * trade.size
        return trade.model_copy(
            update={"price": clamped_price, "amount_usd": new_amount_usd},
        )

    # 3. Trade is valid as-is
    return trade
