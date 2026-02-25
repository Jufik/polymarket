"""Consistency-based trader filtering — production mirror of research logic.

Applies the same 5-filter pipeline as the research backtester's
``get_consistent_traders()`` + MVF band + median entry price.

All filters have relaxed defaults so callers can opt into strictness
incrementally.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


def filter_consistent_traders(
    *,
    pnl: pl.DataFrame,
    resolved: pl.DataFrame,
    mvf: pl.DataFrame,
    train_start: datetime,
    train_end: datetime,
    min_periods: int = 6,
    min_markets: int = 10,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.90,
) -> frozenset[str]:
    """Return traders passing all five consistency filters.

    Parameters
    ----------
    pnl:
        ``trader_market_pnl`` table. Needs: ``trader``, ``condition_id``,
        ``market_pnl``, ``net_yes_tokens``, ``wavg_yes_entry_price``.
    resolved:
        ``markets_resolved`` table. Needs: ``condition_id``, ``resolved_at``.
    mvf:
        ``maker_volume_fractions`` table. Needs: ``trader``, ``mvf``.
    train_start:
        Training window start (inclusive).
    train_end:
        Training window end (exclusive).
    min_periods:
        Minimum number of distinct profitable months.
    min_markets:
        Minimum total distinct markets across training.
    max_mvf:
        Maximum maker volume fraction (pure_taker = 0.10).
    max_median_entry:
        Maximum median directional entry price.

    Returns
    -------
    frozenset[str]
        Set of qualifying trader addresses.
    """
    # --- Step 1: Join PnL with resolution dates, filter to training window ---
    df = pnl.lazy().join(
        resolved.lazy().select("condition_id", "resolved_at"),
        on="condition_id",
        how="inner",
    ).filter(
        (pl.col("resolved_at") >= train_start)
        & (pl.col("resolved_at") < train_end)
    )

    # --- Step 2: Monthly aggregation ---
    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y%m").cast(pl.UInt32).alias("month")
    )

    monthly = df.group_by(["trader", "month"]).agg(
        pl.col("market_pnl").sum().alias("monthly_pnl"),
        pl.col("condition_id").n_unique().alias("markets_traded"),
    )

    # --- Step 3: Trader-level consistency stats ---
    trader_stats = monthly.group_by("trader").agg(
        (pl.col("monthly_pnl") > 0).sum().alias("positive_months"),
        pl.len().alias("total_months"),
        pl.col("markets_traded").sum().alias("total_markets"),
    )

    # --- Filter 1+2+3: profitable every month, enough months, enough markets ---
    consistent = trader_stats.filter(
        (pl.col("positive_months") == pl.col("total_months"))
        & (pl.col("total_months") >= min_periods)
        & (pl.col("total_markets") >= min_markets)
    ).collect()

    traders = set(consistent["trader"].to_list())
    logger.info(
        "consistency.base_filter",
        n_consistent=len(traders),
        min_periods=min_periods,
        min_markets=min_markets,
    )

    if not traders:
        return frozenset()

    # --- Filter 4: MVF band ---
    if max_mvf < 1.0:
        mvf_pass = set(
            mvf.filter(pl.col("mvf") <= max_mvf)["trader"].to_list()
        )
        traders &= mvf_pass
        logger.info("consistency.mvf_filter", remaining=len(traders), max_mvf=max_mvf)

    if not traders:
        return frozenset()

    # --- Filter 5: Median directional entry price ---
    if max_median_entry < 1.0:
        # Directional entry: if net long YES, use wavg_yes_entry; else 1 - wavg_yes_entry
        trader_entries = (
            df.filter(pl.col("trader").is_in(list(traders)))
            .with_columns(
                pl.when(pl.col("net_yes_tokens") > 0)
                .then(pl.col("wavg_yes_entry_price"))
                .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
                .alias("directional_entry")
            )
            .group_by("trader")
            .agg(pl.col("directional_entry").median().alias("median_entry"))
            .filter(pl.col("median_entry") <= max_median_entry)
            .collect()
        )
        entry_pass = set(trader_entries["trader"].to_list())
        traders &= entry_pass
        logger.info(
            "consistency.entry_filter",
            remaining=len(traders),
            max_median_entry=max_median_entry,
        )

    return frozenset(traders)
