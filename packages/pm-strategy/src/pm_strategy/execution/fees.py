"""Polymarket fee calculation.

Reference: https://docs.polymarket.com/trading/fees

Fee formula:  fee = shares * price * fee_rate * (price * (1 - price))^exponent

Since shares * price = size_usd:
    fee = size_usd * fee_rate * (price * (1 - price))^exponent

Market types:
    crypto:  fee_rate=0.25, exponent=2, max effective ~1.56% at p=0.50
    sports:  fee_rate=0.0175, exponent=1, max effective ~0.44% at p=0.50
    default: fee_rate=0.0 (most markets are fee-free)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeeSchedule(StrEnum):
    """Market fee schedule type."""

    NONE = "none"
    CRYPTO = "crypto"
    SPORTS = "sports"


@dataclass(frozen=True)
class FeeParams:
    fee_rate: float
    exponent: int


_SCHEDULE_PARAMS: dict[FeeSchedule, FeeParams] = {
    FeeSchedule.NONE: FeeParams(fee_rate=0.0, exponent=1),
    FeeSchedule.CRYPTO: FeeParams(fee_rate=0.25, exponent=2),
    FeeSchedule.SPORTS: FeeParams(fee_rate=0.0175, exponent=1),
}


def compute_fee(size_usd: float, price: float, schedule: FeeSchedule = FeeSchedule.NONE) -> float:
    """Compute Polymarket taker fee.

    Parameters
    ----------
    size_usd:
        Dollar size of the trade (shares * price).
    price:
        Fill price (0-1).
    schedule:
        Fee schedule for the market type.

    Returns
    -------
    Fee in USD. Minimum 0.0001 (smaller rounds to zero per PM docs).
    """
    if schedule == FeeSchedule.NONE:
        return 0.0
    params = _SCHEDULE_PARAMS[schedule]
    pq = price * (1.0 - price)
    fee = size_usd * params.fee_rate * pq**params.exponent
    if fee < 0.0001:
        return 0.0
    return round(fee, 4)
