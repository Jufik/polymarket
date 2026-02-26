"""HR Pool feature provider: builds trader pool from hit-rate threshold.

Constructs the pool by:
1. Classification: filter directional positions, remove safe-bets/bots/one-offs
2. Pool selection: HR >= threshold + min markets + monthly consistency

Supports both Polars (offline/backtest) and ClickHouse (live) backends.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl
import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies.protocol import FeatureBackend
from polymarket_pipeline.strategies_impl.hr_pool.config import HRPoolConfig

log = structlog.get_logger()


class HRPoolProvider:
    """Feature provider that computes the HR-based trader pool.

    Exposes ``hr_pool`` feature: ``frozenset[str]`` of trader addresses.
    """

    name: str = "hr_pool"

    def __init__(
        self,
        config: HRPoolConfig,
        *,
        train_start: datetime | None = None,
        train_end: datetime | None = None,
    ) -> None:
        self._cfg = config
        self._train_start = train_start
        self._train_end = train_end
        self._pool: frozenset[str] = frozenset()

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute pool at startup."""
        if hasattr(backend, "query_custom"):
            await self._compute_from_ch(backend)
        else:
            log.warning("hr_pool.compute: no ClickHouse backend, using empty pool")

    async def _compute_from_ch(self, backend: FeatureBackend) -> None:
        """Build pool from ClickHouse _tmp_s1_enriched table."""
        cfg = self._cfg
        ts = self._train_start or datetime(2024, 1, 1)
        te = self._train_end or datetime.utcnow()

        # Trader stats within training window
        query = f"""
        SELECT
            trader,
            avg(correct)    AS hr,
            count()         AS n_markets,
            uniq(month)     AS active_months
        FROM _tmp_s1_enriched
        WHERE position IN ('YES', 'NO')
          AND dir_entry <= {cfg.max_classification_entry}
          AND resolved_at >= '{ts:%Y-%m-%d}'
          AND resolved_at < '{te:%Y-%m-%d}'
        GROUP BY trader
        HAVING n_markets >= {cfg.min_positions}
           AND n_markets <= {cfg.max_positions}
           AND hr >= {cfg.min_hr}
           AND n_markets >= {cfg.min_markets}
        """
        stats_df = await backend.query_custom(query)

        # Monthly good-months filter
        monthly_query = f"""
        SELECT trader, month, avg(correct) AS m_hr, count() AS m_n
        FROM _tmp_s1_enriched
        WHERE position IN ('YES', 'NO')
          AND dir_entry <= {cfg.max_classification_entry}
          AND resolved_at >= '{ts:%Y-%m-%d}'
          AND resolved_at < '{te:%Y-%m-%d}'
        GROUP BY trader, month
        HAVING m_n >= {cfg.monthly_min_bets}
        """
        monthly_df = await backend.query_custom(monthly_query)

        good_months = (
            monthly_df.filter(pl.col("m_hr") >= cfg.monthly_hr)
            .group_by("trader").agg(pl.len().alias("good_months"))
        )
        pool_df = stats_df.join(good_months, on="trader", how="left").with_columns(
            pl.col("good_months").fill_null(0)
        ).filter(pl.col("good_months") >= cfg.min_good_months)

        self._pool = frozenset(pool_df["trader"].to_list())
        log.info(
            "hr_pool.compute",
            pool_size=len(self._pool),
            train_window=f"{ts:%Y-%m-%d}→{te:%Y-%m-%d}",
            min_hr=cfg.min_hr,
        )

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op: pool is refreshed periodically, not per-trade."""
        pass

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic refresh — rebuild pool from backend."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Return pool as feature dict."""
        return {"hr_pool": self._pool}
