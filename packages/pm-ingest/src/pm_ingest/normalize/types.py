"""Normalization pipeline types — Decode -> Enrich -> Validate.

These frozen dataclasses form the foundation shared by all normalizers
and the enrichment/validation stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pm_core.types import Source

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ResolutionOutcome(StrEnum):
    """Canonical market resolution outcome."""

    YES = "YES"
    NO = "NO"
    VOIDED = "VOIDED"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecodedTrade:
    """Raw decoded fields before enrichment.

    WS sources provide pre-computed ``price``/``size``; on-chain sources
    provide raw ``maker_amount``/``taker_amount`` (1e6-scaled USDC).
    """

    asset_id: str
    maker_asset_id: str | None
    taker_asset_id: str | None
    price: Decimal | None
    size: Decimal | None
    maker_amount: Decimal | None
    taker_amount: Decimal | None
    fee_raw: Decimal
    maker: str | None
    taker: str | None
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None
    timestamp: datetime
    source: Source
    is_backfill: bool


@dataclass(frozen=True, slots=True)
class Rejection:
    """Structured rejection for metrics and debugging.

    Created when a decoded trade fails enrichment or validation
    (e.g. taker dedup, unknown asset, price out of range).
    """

    reason: str
    asset_id: str
    source: Source
    timestamp: datetime
    details: str = ""


@dataclass(frozen=True, slots=True)
class MarketResolution:
    """Canonical resolution event from any source (RPC, CLOB WS, CLOB API)."""

    condition_id: str
    outcome: ResolutionOutcome
    payout_per_token: float
    source: str
    timestamp: float
    settled_price_raw: int | None = field(default=None)
