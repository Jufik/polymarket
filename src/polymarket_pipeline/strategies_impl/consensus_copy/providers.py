"""Feature providers for the consensus-copy strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class SkilledTradersProvider:
    """Computes and maintains the set of skilled trader addresses.

    A trader is "skilled" if they have at least *min_trades* distinct trades
    across different markets. The skilled set is recomputed periodically via
    ``refresh()``; ``on_trade()`` is a no-op because the set changes slowly.

    Parameters
    ----------
    min_trades:
        Minimum number of unique market trades to qualify as skilled.
    """

    name: str = "skilled_traders"

    def __init__(self, min_trades: int = 50) -> None:
        self._min_trades = min_trades
        self._skilled: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute the skilled traders set from historical trades."""
        trades = await backend.query_trades()

        if trades.is_empty():
            self._skilled = frozenset()
            logger.info("skilled_traders.compute", count=0)
            return

        import polars as pl

        # Count distinct markets per trader
        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_trades)
            .collect()
        )

        self._skilled = frozenset(trader_counts["maker"].to_list())
        logger.info("skilled_traders.compute", count=len(self._skilled))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — skilled set is refreshed periodically, not per-trade."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the skilled set."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Return ``{"skilled_traders": frozenset[str]}``."""
        return {"skilled_traders": self._skilled}
