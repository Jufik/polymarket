"""Protocol definitions for the strategy execution framework.

These runtime-checkable protocols define the structural contracts that strategies,
contexts, and executors must satisfy.  They intentionally avoid concrete base classes
so that implementations stay decoupled and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import polars as pl

from polymarket_pipeline.strategies.types import (
    MarketInfo,
    OrderbookSnapshot,
    Position,
    TradeIntent,
)

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade


# ---------------------------------------------------------------------------
# StrategyContext — read-only view of the world available to strategies
# ---------------------------------------------------------------------------


@runtime_checkable
class StrategyContext(Protocol):
    """Read-only runtime context supplied to every strategy callback.

    Provides access to positions, market metadata, order-book snapshots,
    prices, and the current wall-clock time.
    """

    async def get_position(self, condition_id: str) -> Position | None:
        """Return the current position for *condition_id*, or ``None``."""
        ...

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        """Return market metadata for *condition_id*, or ``None``."""
        ...

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        """Return the latest order-book snapshot, or ``None``."""
        ...

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        """Return the last known price for *outcome* in *condition_id*."""
        ...

    async def now(self) -> float:
        """Return the current timestamp (epoch seconds)."""
        ...


# ---------------------------------------------------------------------------
# Strategy — event-driven (live / replay) strategy interface
# ---------------------------------------------------------------------------


@runtime_checkable
class Strategy(Protocol):
    """Event-driven strategy interface.

    Concrete strategies implement one or more of the ``on_*`` callbacks.
    The runner invokes them when the corresponding event arrives.
    """

    name: str

    async def on_trade(
        self, trade: NormalizedTrade, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to a new trade arriving on the feed."""
        ...

    async def on_market_update(
        self, update: Any, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """React to a market-level update (price change, metadata, etc.)."""
        ...

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        """Periodic callback fired by the runner's timer loop."""
        ...


# ---------------------------------------------------------------------------
# VectorizedStrategy — batch / backtest strategy interface
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorizedStrategy(Protocol):
    """Batch strategy interface for vectorized backtesting.

    Operates on full Polars DataFrames rather than individual events.
    """

    def compute_signals(
        self, trades: pl.LazyFrame, markets: pl.LazyFrame
    ) -> pl.DataFrame:
        """Compute strategy signals over the provided frames.

        Returns a DataFrame with at minimum a ``signal`` column.
        """
        ...


# ---------------------------------------------------------------------------
# Executor — intent-to-fill bridge
# ---------------------------------------------------------------------------


@runtime_checkable
class Executor(Protocol):
    """Translates a :class:`TradeIntent` into an actual order / fill."""

    async def execute(self, intent: TradeIntent) -> Any:
        """Submit *intent* and return execution details."""
        ...
