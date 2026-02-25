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

    Applies three filters (all optional, backward-compatible):
    1. min_markets — minimum distinct markets traded
    2. min_longshot_yes_frac — minimum fraction of YES buys at price < 0.50
    3. max_no_fraction — maximum fraction of SELL-side (NO) positions

    From insight #14: longshot_yes_fraction > 0.15 is the single strongest
    predictor of holdout copy profitability (Spearman r=+0.578).

    Parameters
    ----------
    min_markets:
        Minimum distinct markets a trader must have traded.
    min_longshot_yes_frac:
        Minimum fraction of YES buys at price < 0.50.
    max_no_fraction:
        Maximum fraction of SELL-side (NO) positions.
    """

    name: str = "pool_traders"

    def __init__(
        self,
        min_markets: int = 50,
        min_longshot_yes_frac: float = 0.0,
        max_no_fraction: float = 1.0,
    ) -> None:
        self._min_markets = min_markets
        self._min_longshot_yes_frac = min_longshot_yes_frac
        self._max_no_fraction = max_no_fraction
        self._pool: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        trades = await backend.query_trades()

        if trades.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        import polars as pl

        lf = trades.lazy()

        # Per-trader aggregates
        trader_stats = (
            lf.group_by("maker")
            .agg(
                pl.col("condition_id").n_unique().alias("n_markets"),
                # Longshot YES: BUY side and price < 0.50
                (
                    (pl.col("side") == "BUY") & (pl.col("price").cast(pl.Float64) < 0.50)
                ).sum().alias("n_longshot_yes"),
                # NO fraction: SELL side count
                (pl.col("side") == "SELL").sum().alias("n_no"),
                pl.len().alias("n_total"),
            )
            .with_columns(
                (pl.col("n_longshot_yes") / pl.col("n_total")).alias("longshot_yes_frac"),
                (pl.col("n_no") / pl.col("n_total")).alias("no_frac"),
            )
        )

        # Apply filters
        filtered = trader_stats.filter(pl.col("n_markets") >= self._min_markets)

        if self._min_longshot_yes_frac > 0:
            filtered = filtered.filter(
                pl.col("longshot_yes_frac") >= self._min_longshot_yes_frac
            )

        if self._max_no_fraction < 1.0:
            filtered = filtered.filter(
                pl.col("no_frac") <= self._max_no_fraction
            )

        result = filtered.collect()
        self._pool = frozenset(result["maker"].to_list())
        logger.info(
            "pool_traders.compute",
            count=len(self._pool),
            min_longshot_yes_frac=self._min_longshot_yes_frac,
            max_no_fraction=self._max_no_fraction,
        )

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — pool is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"pool_traders": self._pool}
