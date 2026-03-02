"""S2 FeatureProvider — loads and refreshes qualified trader pool from CH."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from research.strategies.s2_hitrate_copy import S2HitRateCopy

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class S2Provider:
    """Qualified trader pool provider."""

    name: str = "s2_provider"

    def __init__(
        self,
        min_positions: int = 30,
        min_excess_hr: float = 0.10,
        recency_months: int = 6,
        direction: str = "BOTH",
    ) -> None:
        self._min_positions = min_positions
        self._min_excess_hr = min_excess_hr
        self._recency_months = recency_months
        self._direction = direction
        self._qualified: set[str] = set()

    async def compute(self, backend: FeatureBackend) -> None:
        """Initial pool computation from CH."""
        await self._load_pool(backend)

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic pool refresh."""
        await self._load_pool(backend)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No per-trade action needed — pool is static between refreshes."""

    def get_features(self) -> dict[str, Any]:
        return {
            "pool_traders": frozenset(self._qualified),
            "pool_size": len(self._qualified),
        }

    async def _load_pool(self, backend: FeatureBackend) -> None:
        sql = S2HitRateCopy.qualified_traders_query(
            min_positions=self._min_positions,
            min_excess_hr=self._min_excess_hr,
            recency_months=self._recency_months,
            direction=self._direction,
        )
        df = await backend.query_custom(sql)
        self._qualified = set(df["trader"].to_list()) if len(df) > 0 else set()
        logger.info("s2_provider.pool_loaded", size=len(self._qualified))
