"""Dict-backed StrategyContext for backtest and paper-dev modes.

Zero external dependencies — all state is held in plain dicts.
Mutation methods (``set_*``) are synchronous and intended for runners,
not strategies.
"""

from __future__ import annotations

from typing import Any

from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
)


class InMemoryContext:
    """In-memory :class:`~polymarket_pipeline.strategies.protocol.StrategyContext`.

    Satisfies the ``StrategyContext`` protocol structurally (duck typing).
    All reads are ``async`` (protocol requirement); all writes are plain sync
    helpers used by the backtest runner to advance simulated state.
    """

    __slots__ = ("_features", "_markets", "_orderbooks", "_positions", "_time")

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._markets: dict[str, MarketInfo] = {}
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
        self._features: dict[str, Any] = {}
        self._time: float = 0.0

    # ------------------------------------------------------------------
    # Protocol methods (async, read-only)
    # ------------------------------------------------------------------

    async def get_position(self, condition_id: str) -> Position | None:
        """Return the current position for *condition_id*, or ``None``."""
        return self._positions.get(condition_id)

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        """Return market metadata for *condition_id*, or ``None``."""
        return self._markets.get(condition_id)

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        """Return the latest order-book snapshot, or ``None``."""
        return self._orderbooks.get(condition_id)

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        """Return the last known price for *outcome* in *condition_id*."""
        market = self._markets.get(condition_id)
        if market is None:
            return None
        if outcome == "YES":
            return market.yes_price
        if outcome == "NO":
            return market.no_price
        return None

    async def now(self) -> float:
        """Return the current simulated timestamp (epoch seconds)."""
        return self._time

    async def get_features(self, key: str) -> Any:
        """Return a feature value by *key*, or ``None``."""
        return self._features.get(key)

    # ------------------------------------------------------------------
    # Mutation methods (sync, used by runners)
    # ------------------------------------------------------------------

    def set_position(self, condition_id: str, position: Position) -> None:
        """Store *position* for *condition_id*."""
        self._positions[condition_id] = position

    def set_market(self, condition_id: str, market: MarketInfo) -> None:
        """Store *market* metadata for *condition_id*."""
        self._markets[condition_id] = market

    def set_time(self, t: float) -> None:
        """Advance simulated clock to *t*."""
        self._time = t

    def set_orderbook(self, condition_id: str, ob: OrderbookSnapshot) -> None:
        """Store order-book snapshot for *condition_id*."""
        self._orderbooks[condition_id] = ob

    def update_features(self, features: dict[str, Any]) -> None:
        """Merge *features* into the feature store."""
        self._features.update(features)
