"""Feature providers for the proportional-copy strategy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class GradedPoolProvider:
    """Computes and maintains the graded trader pool for proportional copy.

    A trader qualifies if they have traded in at least ``min_markets``
    distinct markets. In production, this provider would additionally
    filter by consistency months, MVF, and longshot_yes_fraction — but
    those metrics require the derived ``trader_market_pnl`` table.

    For the initial implementation, the pool is seeded from the
    ``pool_traders`` config (pre-computed offline) and this provider
    validates they remain active. Future versions will compute grades
    from the ClickHouse backend directly.

    Parameters
    ----------
    min_markets:
        Minimum distinct markets a trader must have traded.
    """

    name: str = "pool_traders"

    def __init__(self, min_markets: int = 50) -> None:
        self._min_markets = min_markets
        self._pool: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        trades = await backend.query_trades()

        if trades.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        import polars as pl

        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_markets)
            .collect()
        )

        self._pool = frozenset(trader_counts["maker"].to_list())
        logger.info("pool_traders.compute", count=len(self._pool))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — pool is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"pool_traders": self._pool}
