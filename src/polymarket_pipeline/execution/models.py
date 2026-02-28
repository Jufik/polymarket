"""Execution data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Position:
    """Current position in a market."""

    condition_id: str
    asset_id: str
    side: str  # "BUY" (long) or "SELL" (short)
    size: float  # number of tokens held
    avg_entry: float  # average entry price
    cost_basis: float  # total USD invested
    unrealized_pnl: float  # based on last_price
    last_price: float
    realized_pnl: float = 0.0  # cumulative realized from sells
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FillRecord:
    """A recorded fill from execution."""

    intent_id: str
    strategy: str
    condition_id: str
    asset_id: str
    side: str
    outcome: str
    price: float
    size_usd: float
    fee_usd: float
    filled_at: datetime
