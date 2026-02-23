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

    async def get_features(self, key: str) -> Any:
        """Return a feature value by *key*, or ``None``."""
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

    async def on_market_update(self, update: Any, ctx: StrategyContext) -> list[TradeIntent] | None:
        """React to a market-level update (price change, metadata, etc.)."""
        ...

    async def on_timer(self, now: float, ctx: StrategyContext) -> list[TradeIntent] | None:
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

    def compute_signals(self, trades: pl.LazyFrame, markets: pl.LazyFrame) -> pl.DataFrame:
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


# ---------------------------------------------------------------------------
# FeatureBackend — data access layer for feature providers
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureBackend(Protocol):
    """Abstraction over Polars (backtest) vs ClickHouse (live) for batch queries."""

    async def query_trades(self, condition_ids: list[str] | None = None) -> pl.DataFrame:
        """Return trades, optionally filtered by condition IDs."""
        ...

    async def query_markets(self) -> pl.DataFrame:
        """Return market metadata."""
        ...

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary query (SQL for CH, Polars expression for in-memory)."""
        ...


# ---------------------------------------------------------------------------
# FeatureProvider — independent feature computation unit
# ---------------------------------------------------------------------------


@runtime_checkable
class FeatureProvider(Protocol):
    """Independent computation that feeds features into StrategyContext.

    Lifecycle: compute() at startup, on_trade() per event (hot path),
    refresh() periodically for expensive recomputation.
    """

    name: str

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute features at startup."""
        ...

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """O(1) streaming update — hot path, in-memory only."""
        ...

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic expensive recomputation (e.g. every 15 min)."""
        ...

    def get_features(self) -> dict[str, Any]:
        """Return current feature values for context injection."""
        ...
