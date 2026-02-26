"""Feature providers for the consensus-copy strategy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class SkilledTradersProvider:
    """Computes and maintains the set of skilled trader addresses.

    Two modes of operation:

    **Consistency mode** (when ``pnl_df``, ``resolved_df``, ``mvf_df`` are provided):
    Applies the full 5-filter research pipeline via
    ``filter_consistent_traders()``. This matches the research backtester's
    ``get_consistent_traders()`` + MVF + median entry.

    **Legacy mode** (when DataFrames not provided):
    Falls back to the simple market-count filter (``n_unique(condition_id) >= min_trades``).
    This preserves backward compatibility with existing tests and configs.

    Parameters
    ----------
    min_trades:
        Legacy mode: minimum distinct markets to qualify.
    pnl_df:
        Pre-loaded ``trader_market_pnl`` DataFrame. Enables consistency mode.
    resolved_df:
        Pre-loaded ``markets_resolved`` DataFrame.
    mvf_df:
        Pre-loaded ``maker_volume_fractions`` DataFrame.
    train_start:
        Training window start (consistency mode).
    train_end:
        Training window end (consistency mode).
    min_periods:
        Minimum profitable months (default: 6, from research top config).
    min_markets:
        Minimum total markets (default: 10, from research top config).
    max_mvf:
        Maximum MVF (default: 0.10 = pure_taker band).
    max_median_entry:
        Maximum median directional entry (default: 0.90).
    """

    name: str = "skilled_traders"

    def __init__(
        self,
        min_trades: int = 50,
        *,
        pnl_df: pl.DataFrame | None = None,
        resolved_df: pl.DataFrame | None = None,
        mvf_df: pl.DataFrame | None = None,
        train_start: datetime | None = None,
        train_end: datetime | None = None,
        min_periods: int = 6,
        min_markets: int = 10,
        max_mvf: float = 0.10,
        max_median_entry: float = 0.90,
    ) -> None:
        self._min_trades = min_trades
        self._pnl_df = pnl_df
        self._resolved_df = resolved_df
        self._mvf_df = mvf_df
        self._train_start = train_start
        self._train_end = train_end
        self._min_periods = min_periods
        self._min_markets = min_markets
        self._max_mvf = max_mvf
        self._max_median_entry = max_median_entry
        self._skilled: frozenset[str] = frozenset()

    @property
    def _use_consistency(self) -> bool:
        return (
            self._pnl_df is not None
            and self._resolved_df is not None
            and self._mvf_df is not None
            and self._train_start is not None
            and self._train_end is not None
        )

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute the skilled traders set."""
        if self._use_consistency:
            self._compute_consistent()
        else:
            await self._compute_legacy(backend)

    def _compute_consistent(self) -> None:
        """Full 5-filter consistency pipeline."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._pnl_df is not None  # noqa: S101
        assert self._resolved_df is not None  # noqa: S101
        assert self._mvf_df is not None  # noqa: S101
        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        self._skilled = filter_consistent_traders(
            pnl=self._pnl_df,
            resolved=self._resolved_df,
            mvf=self._mvf_df,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )
        logger.info("skilled_traders.consistency_mode", count=len(self._skilled))

    async def _compute_legacy(self, backend: FeatureBackend) -> None:
        """Simple market-count filter (backward compat).

        When a ClickHouse backend is detected, the aggregation is pushed
        to the server to avoid loading 438M+ raw rows into Python memory.
        """
        if getattr(backend, "supports_sql", False) is True:
            await self._compute_legacy_ch(backend)
        else:
            await self._compute_legacy_polars(backend)

    async def _compute_legacy_ch(self, backend: Any) -> None:
        """Push legacy trader aggregation to ClickHouse."""
        result = await backend.query_custom(f"""
            SELECT
                maker,
                uniq(condition_id) AS n_markets
            FROM trades_raw FINAL
            WHERE maker IS NOT NULL AND maker != ''
            GROUP BY maker
            HAVING n_markets >= {self._min_trades}
        """)

        if result.is_empty():
            self._skilled = frozenset()
            logger.info("skilled_traders.compute", count=0)
            return

        self._skilled = frozenset(result["maker"].to_list())
        logger.info("skilled_traders.legacy_mode", count=len(self._skilled))

    async def _compute_legacy_polars(self, backend: FeatureBackend) -> None:
        """Fallback: aggregate in Polars (offline/backtest only)."""
        trades = await backend.query_trades()

        if trades.is_empty():
            self._skilled = frozenset()
            logger.info("skilled_traders.compute", count=0)
            return

        trader_counts = (
            trades.lazy()
            .group_by("maker")
            .agg(pl.col("condition_id").n_unique().alias("n_markets"))
            .filter(pl.col("n_markets") >= self._min_trades)
            .collect()
        )

        self._skilled = frozenset(trader_counts["maker"].to_list())
        logger.info("skilled_traders.legacy_mode", count=len(self._skilled))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — skilled set is refreshed periodically, not per-trade."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the skilled set.

        If the backend has CH-specific query methods (``query_consistency_pnl``,
        ``query_resolved_markets``, ``query_mvf``), use them to fetch fresh data
        from ClickHouse derived views. Otherwise fall back to ``compute()``.
        """
        if (
            self._use_consistency
            and hasattr(backend, "query_consistency_pnl")
            and hasattr(backend, "query_resolved_markets")
            and hasattr(backend, "query_mvf")
        ):
            await self._refresh_from_ch(backend)
        else:
            await self.compute(backend)

    async def _refresh_from_ch(self, backend: Any) -> None:
        """Refresh using ClickHouse derived views."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        pnl = await backend.query_consistency_pnl()
        resolved = await backend.query_resolved_markets()
        mvf = await backend.query_mvf()

        self._skilled = filter_consistent_traders(
            pnl=pnl,
            resolved=resolved,
            mvf=mvf,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )
        logger.info("skilled_traders.ch_refresh", count=len(self._skilled))

    def get_features(self) -> dict[str, Any]:
        """Return ``{"skilled_traders": frozenset[str]}``."""
        return {"skilled_traders": self._skilled}


def load_skilled_provider(
    *,
    data_dir: str | Path,
    train_start: str,
    train_end: str,
    min_periods: int = 6,
    min_markets: int = 10,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.90,
) -> SkilledTradersProvider:
    """Factory that loads derived parquet files and creates a consistency-mode provider.

    Parameters
    ----------
    data_dir:
        Directory containing ``trader_market_pnl.parquet``,
        ``markets_resolved.parquet``, and ``maker_volume_fractions.parquet``.
    train_start:
        ISO date string for training window start (e.g. ``"2023-01-01"``).
    train_end:
        ISO date string for training window end (e.g. ``"2026-02-01"``).
    """
    d = Path(data_dir)

    pnl_df = pl.read_parquet(d / "trader_market_pnl.parquet")
    resolved_df = pl.read_parquet(d / "markets_resolved.parquet")
    mvf_df = pl.read_parquet(d / "maker_volume_fractions.parquet")

    ts = datetime.fromisoformat(train_start).replace(tzinfo=timezone.utc)
    te = datetime.fromisoformat(train_end).replace(tzinfo=timezone.utc)

    return SkilledTradersProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=ts,
        train_end=te,
        min_periods=min_periods,
        min_markets=min_markets,
        max_mvf=max_mvf,
        max_median_entry=max_median_entry,
    )
