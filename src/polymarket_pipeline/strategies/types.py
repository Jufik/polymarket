"""Core types for the strategy execution framework.

These frozen dataclasses are the lingua franca used by protocols, contexts,
executors, and runners throughout the strategy framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ExecutionMode(StrEnum):
    """Execution mode determines how intents are routed and filled."""

    VECTORIZED = "vectorized"
    REPLAY = "replay"
    PAPER_DEV = "paper_dev"
    PAPER_PROD = "paper_prod"
    LIVE = "live"


class FillStatus(StrEnum):
    """Outcome status of a fill attempt."""

    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeIntent:
    """A strategy's desire to trade — not yet executed.

    Produced by strategy logic, consumed by executors.
    """

    strategy: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    size_usd: float
    urgency: Literal["immediate", "patient"]
    max_price: float | None
    reason: str
    signal_time: float
    asset_id: str | None = None


@dataclass(frozen=True)
class Position:
    """Current position in a single market for a single strategy."""

    condition_id: str
    strategy: str
    qty_yes: float = 0.0
    qty_no: float = 0.0
    avg_entry_yes: float = 0.0
    avg_entry_no: float = 0.0
    cost_basis: float = 0.0
    realized_pnl: float = 0.0

    @property
    def avg_entry_price(self) -> float:
        """Backward compat: returns YES avg if holding YES, else NO avg."""
        if self.qty_yes > 0:
            return self.avg_entry_yes
        if self.qty_no > 0:
            return self.avg_entry_no
        return 0.0


@dataclass(frozen=True)
class MarketInfo:
    """Snapshot of market metadata and current prices."""

    condition_id: str
    question: str
    active: bool
    yes_price: float | None = None
    no_price: float | None = None
    event_id: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class OrderbookSnapshot:
    """Point-in-time view of a market's order book."""

    condition_id: str
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float
    timestamp: float

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class Fill:
    """Result of executing a TradeIntent — what actually happened."""

    intent_id: str
    strategy: str
    condition_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["YES", "NO"]
    filled_price: float
    filled_size_usd: float
    fee_usd: float
    status: FillStatus
    filled_at: float
    error: str | None = None
    order_id: str | None = None
