"""Feature providers for the proportional-copy strategy.

Two modes:

**Consistency mode** (preferred): Uses pre-loaded PnL/resolved/MVF DataFrames
to run the full research pipeline — ``filter_consistent_traders()`` from the
shared consistency module, followed by longshot YES grading and NO fraction cap.

**Legacy mode**: Simple market-count + trade-stat filters from ``query_trades()``.
Preserves backward compat with existing tests and minimal configs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------


def _compute_grading_stats(
    pnl: pl.DataFrame,
    resolved: pl.DataFrame,
    traders: frozenset[str],
    train_start: datetime,
    train_end: datetime,
) -> pl.DataFrame:
    """Compute per-trader longshot_yes_frac and no_frac for grading.

    Uses training-period PnL data for traders that already passed the
    consistency filter.

    Returns
    -------
    pl.DataFrame
        Columns: trader, n_total, longshot_yes_frac, no_frac.
    """
    df = (
        pnl.lazy()
        .filter(pl.col("trader").is_in(list(traders)))
        .join(
            resolved.lazy().select("condition_id", "resolved_at"),
            on="condition_id",
            how="inner",
        )
        .filter(
            (pl.col("resolved_at") >= train_start)
            & (pl.col("resolved_at") < train_end)
        )
    )

    stats = df.group_by("trader").agg(
        pl.len().alias("n_total"),
        # Longshot YES: long YES position AND entry price < 0.50
        (
            (pl.col("net_yes_tokens") > 0)
            & (pl.col("wavg_yes_entry_price") < 0.50)
        )
        .sum()
        .alias("n_longshot_yes"),
        # NO positions: net short YES (= betting NO)
        (pl.col("net_yes_tokens") <= 0).sum().alias("n_no"),
    ).with_columns(
        (pl.col("n_longshot_yes") / pl.col("n_total")).alias("longshot_yes_frac"),
        (pl.col("n_no") / pl.col("n_total")).alias("no_frac"),
    )

    return stats.collect()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GradedPoolProvider:
    """Computes and maintains the graded trader pool for proportional copy.

    **Consistency mode** (when ``pnl_df``, ``resolved_df``, ``mvf_df`` provided):

    1. ``filter_consistent_traders()`` — shared 5-filter pipeline
       (profitable every month, enough months/markets, MVF, median entry).
    2. Longshot YES grading — ``min_longshot_yes_frac`` (Spearman r=+0.578).
    3. NO fraction cap — ``max_no_fraction`` (>0.60 is anti-predictive).

    **Legacy mode** (backward compat):
    Simple trade-count + optional grading from ``query_trades()``.

    Parameters
    ----------
    min_markets:
        Legacy mode: minimum distinct markets to qualify.
    pnl_df, resolved_df, mvf_df:
        Pre-loaded DataFrames for consistency mode.
    train_start, train_end:
        Training window boundaries.
    min_periods:
        Minimum consecutive profitable months (default: 9).
    min_consistency_markets:
        Minimum total markets in consistency filter (default: 20).
    max_mvf:
        Maximum MVF — pure_taker band (default: 0.10).
    max_median_entry:
        Maximum median directional entry price (default: 0.80).
    min_longshot_yes_frac:
        Minimum fraction of YES buys at < $0.50 (default: 0.15).
    max_no_fraction:
        Maximum fraction of NO positions (default: 0.60).
    """

    name: str = "pool_traders"

    def __init__(
        self,
        min_markets: int = 50,
        *,
        pnl_df: pl.DataFrame | None = None,
        resolved_df: pl.DataFrame | None = None,
        mvf_df: pl.DataFrame | None = None,
        train_start: datetime | None = None,
        train_end: datetime | None = None,
        min_periods: int = 9,
        min_consistency_markets: int = 20,
        max_mvf: float = 0.10,
        max_median_entry: float = 0.80,
        min_longshot_yes_frac: float = 0.0,
        max_no_fraction: float = 1.0,
    ) -> None:
        self._min_markets = min_markets
        self._pnl_df = pnl_df
        self._resolved_df = resolved_df
        self._mvf_df = mvf_df
        self._train_start = train_start
        self._train_end = train_end
        self._min_periods = min_periods
        self._min_consistency_markets = min_consistency_markets
        self._max_mvf = max_mvf
        self._max_median_entry = max_median_entry
        self._min_longshot_yes_frac = min_longshot_yes_frac
        self._max_no_fraction = max_no_fraction
        self._pool: frozenset[str] = frozenset()

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
        """Batch compute the graded pool."""
        if self._use_consistency:
            self._compute_graded()
        else:
            await self._compute_legacy(backend)

    def _compute_graded(self) -> None:
        """Full consistency + longshot grading pipeline."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._pnl_df is not None  # noqa: S101
        assert self._resolved_df is not None  # noqa: S101
        assert self._mvf_df is not None  # noqa: S101
        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        # Step 1: Base consistency filter (shared with S3)
        base_pool = filter_consistent_traders(
            pnl=self._pnl_df,
            resolved=self._resolved_df,
            mvf=self._mvf_df,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_consistency_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )
        logger.info("pool_traders.consistency_base", count=len(base_pool))

        if not base_pool:
            self._pool = frozenset()
            return

        # Step 2: Compute grading stats for the consistent pool
        grading = _compute_grading_stats(
            self._pnl_df,
            self._resolved_df,
            base_pool,
            self._train_start,
            self._train_end,
        )

        # Step 3: Apply longshot YES filter
        if self._min_longshot_yes_frac > 0:
            grading = grading.filter(
                pl.col("longshot_yes_frac") >= self._min_longshot_yes_frac
            )

        # Step 4: Apply NO fraction cap
        if self._max_no_fraction < 1.0:
            grading = grading.filter(pl.col("no_frac") <= self._max_no_fraction)

        self._pool = frozenset(grading["trader"].to_list())
        logger.info(
            "pool_traders.graded",
            count=len(self._pool),
            base_count=len(base_pool),
            min_longshot_yes_frac=self._min_longshot_yes_frac,
            max_no_fraction=self._max_no_fraction,
        )

    async def _compute_legacy(self, backend: FeatureBackend) -> None:
        """Simple market-count + optional grading from trade stats.

        When a ClickHouse backend is detected (``query_custom`` method),
        the aggregation is pushed to the server to avoid loading 438M+
        raw rows into Python memory.
        """
        if getattr(backend, "supports_sql", False) is True:
            await self._compute_legacy_ch(backend)
        else:
            await self._compute_legacy_polars(backend)

    async def _compute_legacy_ch(self, backend: Any) -> None:
        """Push legacy pool aggregation to ClickHouse."""
        result = await backend.query_custom("""
            SELECT
                maker,
                uniq(condition_id) AS n_markets,
                countIf(side = 'BUY' AND toFloat64(price) < 0.50) AS n_longshot_yes,
                countIf(side = 'SELL') AS n_no,
                count() AS n_total
            FROM trades_raw FINAL
            WHERE maker IS NOT NULL AND maker != ''
            GROUP BY maker
        """)

        if result.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        self._apply_legacy_filters(result)

    async def _compute_legacy_polars(self, backend: FeatureBackend) -> None:
        """Fallback: aggregate in Polars (offline/backtest only)."""
        trades = await backend.query_trades()

        if trades.is_empty():
            self._pool = frozenset()
            logger.info("pool_traders.compute", count=0)
            return

        lf = trades.lazy()

        result = (
            lf.group_by("maker")
            .agg(
                pl.col("condition_id").n_unique().alias("n_markets"),
                (
                    (pl.col("side") == "BUY")
                    & (pl.col("price").cast(pl.Float64) < 0.50)
                )
                .sum()
                .alias("n_longshot_yes"),
                (pl.col("side") == "SELL").sum().alias("n_no"),
                pl.len().alias("n_total"),
            )
            .collect()
        )

        self._apply_legacy_filters(result)

    def _apply_legacy_filters(self, stats: pl.DataFrame) -> None:
        """Apply min_markets, longshot_yes_frac, and no_frac filters."""
        for col in ("n_markets", "n_longshot_yes", "n_no", "n_total"):
            if col in stats.columns:
                stats = stats.with_columns(pl.col(col).cast(pl.Float64))

        stats = stats.with_columns(
            (pl.col("n_longshot_yes") / pl.col("n_total").clip(lower_bound=1)).alias(
                "longshot_yes_frac"
            ),
            (pl.col("n_no") / pl.col("n_total").clip(lower_bound=1)).alias("no_frac"),
        )

        filtered = stats.filter(pl.col("n_markets") >= self._min_markets)

        if self._min_longshot_yes_frac > 0:
            filtered = filtered.filter(
                pl.col("longshot_yes_frac") >= self._min_longshot_yes_frac
            )

        if self._max_no_fraction < 1.0:
            filtered = filtered.filter(pl.col("no_frac") <= self._max_no_fraction)

        self._pool = frozenset(filtered["maker"].to_list())
        logger.info(
            "pool_traders.legacy_mode",
            count=len(self._pool),
            min_longshot_yes_frac=self._min_longshot_yes_frac,
            max_no_fraction=self._max_no_fraction,
        )

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — pool is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the pool.

        If the backend has CH-specific query methods, use them to fetch fresh
        data from ClickHouse derived views.  Otherwise fall back to ``compute()``.
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
        """Refresh from ClickHouse derived views."""
        from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
            filter_consistent_traders,
        )

        assert self._train_start is not None  # noqa: S101
        assert self._train_end is not None  # noqa: S101

        pnl = await backend.query_consistency_pnl()
        resolved = await backend.query_resolved_markets()
        mvf = await backend.query_mvf()

        base_pool = filter_consistent_traders(
            pnl=pnl,
            resolved=resolved,
            mvf=mvf,
            train_start=self._train_start,
            train_end=self._train_end,
            min_periods=self._min_periods,
            min_markets=self._min_consistency_markets,
            max_mvf=self._max_mvf,
            max_median_entry=self._max_median_entry,
        )

        if not base_pool:
            self._pool = frozenset()
            logger.info("pool_traders.ch_refresh", count=0)
            return

        grading = _compute_grading_stats(
            pnl,
            resolved,
            base_pool,
            self._train_start,
            self._train_end,
        )

        if self._min_longshot_yes_frac > 0:
            grading = grading.filter(
                pl.col("longshot_yes_frac") >= self._min_longshot_yes_frac
            )

        if self._max_no_fraction < 1.0:
            grading = grading.filter(pl.col("no_frac") <= self._max_no_fraction)

        self._pool = frozenset(grading["trader"].to_list())
        logger.info("pool_traders.ch_refresh", count=len(self._pool))

    def get_features(self) -> dict[str, Any]:
        """Return ``{"pool_traders": frozenset[str]}``."""
        return {"pool_traders": self._pool}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def load_graded_pool_provider(
    *,
    data_dir: str | Path,
    train_start: str,
    train_end: str,
    min_periods: int = 9,
    min_consistency_markets: int = 20,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.80,
    min_longshot_yes_frac: float = 0.15,
    max_no_fraction: float = 0.60,
) -> GradedPoolProvider:
    """Factory that loads parquet files and creates a consistency-mode provider.

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

    ts = datetime.fromisoformat(train_start).replace(tzinfo=UTC)
    te = datetime.fromisoformat(train_end).replace(tzinfo=UTC)

    return GradedPoolProvider(
        pnl_df=pnl_df,
        resolved_df=resolved_df,
        mvf_df=mvf_df,
        train_start=ts,
        train_end=te,
        min_periods=min_periods,
        min_consistency_markets=min_consistency_markets,
        max_mvf=max_mvf,
        max_median_entry=max_median_entry,
        min_longshot_yes_frac=min_longshot_yes_frac,
        max_no_fraction=max_no_fraction,
    )
