"""Portfolio replication backtester — per-trader holdout evaluation.

Measures each trader's actual and simulated copy PnL individually,
using the same rolling-window framework as the consensus backtester.
"""

from __future__ import annotations

import polars as pl


def evaluate_traders_actual(
    holdout_pnl: pl.DataFrame,
    pool: set[str],
) -> pl.DataFrame:
    """Compute actual PnL stats for each trader in the pool.

    Parameters
    ----------
    holdout_pnl
        Per-trader per-market PnL filtered to the holdout window.
        Must have columns: trader, condition_id, market_pnl, market_volume.
    pool
        Set of trader addresses in the current pool.

    Returns
    -------
    pl.DataFrame
        One row per trader: trader, actual_pnl, actual_n_markets,
        actual_wins, actual_win_rate, actual_volume.
    """
    df = holdout_pnl.filter(pl.col("trader").is_in(list(pool)))

    return df.group_by("trader").agg(
        pl.col("market_pnl").sum().alias("actual_pnl"),
        pl.len().alias("actual_n_markets"),
        (pl.col("market_pnl") > 0).sum().alias("actual_wins"),
        pl.col("market_volume").sum().alias("actual_volume"),
    ).with_columns(
        (pl.col("actual_wins").cast(pl.Float64) / pl.col("actual_n_markets"))
        .alias("actual_win_rate"),
    )


def evaluate_traders_copy(
    holdout_pnl: pl.DataFrame,
    pool: set[str],
    entry_prices: pl.DataFrame | None,
    base_bet: float = 100.0,
) -> pl.DataFrame:
    """Compute simulated copy PnL for each trader in the pool.

    For each (trader, market), determines direction from net_yes_tokens,
    looks up the forward-priced entry (or falls back to trader's own entry),
    and computes binary bet PnL.

    Parameters
    ----------
    holdout_pnl
        Per-trader per-market PnL with columns: trader, condition_id,
        net_yes_tokens, wavg_yes_entry_price, first_trade, yes_won.
    pool
        Set of trader addresses in the current pool.
    entry_prices
        Pre-computed forward prices (from _precompute_entry_prices).
        Columns: condition_id, trader, first_trade, market_yes_price.
        If None, uses trader's own wavg_yes_entry_price.
    base_bet
        Fixed bet size per position.

    Returns
    -------
    pl.DataFrame
        One row per trader: trader, copy_pnl, copy_n_markets, copy_wins,
        copy_win_rate.
    """
    df = holdout_pnl.filter(pl.col("trader").is_in(list(pool)))

    # Direction from actual trade data
    df = df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes")
    )

    # Entry price: use forward price if available, else trader's own
    if entry_prices is not None:
        df = df.join(
            entry_prices.select(["condition_id", "trader", "first_trade", "market_yes_price"]),
            on=["condition_id", "trader", "first_trade"],
            how="left",
        )
        # Directional entry: YES -> yes_price, NO -> 1 - yes_price
        df = df.with_columns(
            pl.when(pl.col("market_yes_price").is_not_null())
            .then(
                pl.when(pl.col("bet_yes"))
                .then(pl.col("market_yes_price"))
                .otherwise(1.0 - pl.col("market_yes_price"))
            )
            .otherwise(
                pl.when(pl.col("bet_yes"))
                .then(pl.col("wavg_yes_entry_price"))
                .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
            )
            .alias("copy_entry")
        )
    else:
        df = df.with_columns(
            pl.when(pl.col("bet_yes"))
            .then(pl.col("wavg_yes_entry_price"))
            .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
            .alias("copy_entry")
        )

    # Outcome: did the copy bet win?
    df = df.with_columns(
        (
            (pl.col("bet_yes") & pl.col("yes_won"))
            | (~pl.col("bet_yes") & ~pl.col("yes_won"))
        ).alias("copy_won")
    )

    # PnL: won -> base_bet * (1-entry)/entry, lost -> -base_bet
    df = df.with_columns(
        pl.when(pl.col("copy_won"))
        .then(pl.lit(base_bet) * (1.0 - pl.col("copy_entry")) / pl.col("copy_entry"))
        .otherwise(pl.lit(-base_bet))
        .alias("position_pnl")
    )

    return df.group_by("trader").agg(
        pl.col("position_pnl").sum().alias("copy_pnl"),
        pl.len().alias("copy_n_markets"),
        pl.col("copy_won").sum().alias("copy_wins"),
    ).with_columns(
        (pl.col("copy_wins").cast(pl.Float64) / pl.col("copy_n_markets"))
        .alias("copy_win_rate"),
    )
