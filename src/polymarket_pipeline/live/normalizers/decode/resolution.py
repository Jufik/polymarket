"""Decode UMA QuestionResolved settledPrice into structured resolution."""
from __future__ import annotations

import structlog

from polymarket_pipeline.live.normalizers.types import ResolutionOutcome

_log = structlog.get_logger()

_YES_PRICE = 10**18
_VOIDED_PRICE = 5 * 10**17


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
