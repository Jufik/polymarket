"""Core ledger record type -- frozen, immutable, one per intent lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pm_strategy.types import FillStatus


@dataclass(frozen=True)
class LedgerRecord:
    """Single intent-to-outcome record spanning the full lifecycle.

    Constructed once after a fill, then optionally enriched with resolution data
    when the market resolves.
    """

    # -- Signal phase --
    record_id: str
    signal_time: float
    strategy: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    intent_size_usd: float
    max_price: float | None
    reason: str
    asset_id: str | None

    # -- Fill phase --
    fill_status: FillStatus
    fill_price: float
    fill_size_usd: float
    fill_fee_usd: float
    fill_latency_ms: float  # (filled_at - signal_time) * 1000

    # -- Resolution phase (nullable until market resolves) --
    resolution: Literal["YES", "NO"] | None = None
    pnl_gross: float | None = None
    pnl_net: float | None = None
    hold_duration_s: float | None = None
    resolved_at: float | None = None
