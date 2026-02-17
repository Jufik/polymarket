"""Signal table builder — one row per (market, Nth skilled trader arrival).

The signal table is the core data structure for the consensus-copy backtester.
All parameter sweeps are pure filter+aggregate on this table.
"""

from __future__ import annotations

import polars as pl


def _infer_bet_yes(market_pnl: pl.Expr, resolution_value: pl.Expr) -> pl.Expr:
    """Infer whether a trader bet YES.

    A trader bet YES if:
      - (market_pnl > 0 AND resolution_value == 1)  — won on a YES-resolved market
      - (market_pnl < 0 AND resolution_value == 0)  — lost on a NO-resolved market
    Otherwise they bet NO.
    """
    return (
        ((market_pnl > 0) & (resolution_value == 1))
        | ((market_pnl < 0) & (resolution_value == 0))
    )


def build_signal_table(
    df_pnl: pl.DataFrame,
    skilled_traders: set[str],
    mvf_data: pl.DataFrame,
) -> pl.DataFrame:
    """Build the signal table: one row per (market, Nth skilled trader arrival).

    Parameters
    ----------
    df_pnl
        Per-trader per-market PnL with columns: trader, condition_id,
        resolution_value, market_pnl, wavg_yes_entry_price, first_trade,
        last_trade, resolved_at.
    skilled_traders
        Set of trader addresses considered skilled.
    mvf_data
        DataFrame with columns (trader, mvf).

    Returns
    -------
    pl.DataFrame
        Signal table with columns: condition_id, arrival_idx, trigger_time,
        resolved_at, resolution_value, n_traders, n_yes, n_no, agreement_frac,
        signal_direction, trigger_entry_price, avg_pool_entry, trader, mvf.
    """
    # Filter to skilled traders only
    df = df_pnl.filter(pl.col("trader").is_in(list(skilled_traders)))

    # Infer bet direction
    df = df.with_columns(
        _infer_bet_yes(pl.col("market_pnl"), pl.col("resolution_value")).alias("bet_yes")
    )

    # Sort by first_trade within each market to establish arrival order
    df = df.sort(["condition_id", "first_trade"])

    # Compute arrival_idx (1-based) within each market
    df = df.with_columns(
        pl.col("first_trade").cum_count().over("condition_id").alias("arrival_idx")
    )

    # Cumulative counts of YES and NO bettors per market
    df = df.with_columns([
        pl.col("bet_yes").cast(pl.Int64).cum_sum().over("condition_id").alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).cum_sum().over("condition_id").alias("n_no"),
    ])

    # n_traders = arrival_idx (since it's 1-based cumulative count)
    df = df.with_columns(
        pl.col("arrival_idx").alias("n_traders"),
    )

    # agreement_frac = max(n_yes, n_no) / n_traders
    df = df.with_columns(
        (
            pl.max_horizontal("n_yes", "n_no").cast(pl.Float64)
            / pl.col("n_traders").cast(pl.Float64)
        ).alias("agreement_frac"),
    )

    # signal_direction: YES if n_yes >= n_no (ties go to YES), else NO
    df = df.with_columns(
        pl.when(pl.col("n_yes") >= pl.col("n_no"))
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("signal_direction"),
    )

    # Running average of wavg_yes_entry_price for YES bettors only.
    # For each row, avg_pool_entry = mean of wavg_yes_entry_price where
    # bet_yes is True, considering only traders up to this arrival.
    # We compute this as cumulative sum / cumulative count of YES prices.
    df = df.with_columns([
        (pl.col("wavg_yes_entry_price") * pl.col("bet_yes").cast(pl.Float64))
        .cum_sum()
        .over("condition_id")
        .alias("_cum_yes_price"),
        pl.col("bet_yes")
        .cast(pl.Int64)
        .cum_sum()
        .over("condition_id")
        .alias("_cum_yes_count"),
    ])

    df = df.with_columns(
        pl.when(pl.col("_cum_yes_count") > 0)
        .then(pl.col("_cum_yes_price") / pl.col("_cum_yes_count").cast(pl.Float64))
        .otherwise(pl.lit(None).cast(pl.Float64))
        .alias("avg_pool_entry"),
    )

    # Rename columns for output
    df = df.with_columns([
        pl.col("first_trade").alias("trigger_time"),
        pl.col("wavg_yes_entry_price").alias("trigger_entry_price"),
    ])

    # Join MVF data
    df = df.join(mvf_data, on="trader", how="left")

    # Select output columns in the specified order
    return df.select([
        "condition_id",
        "arrival_idx",
        "trigger_time",
        "resolved_at",
        "resolution_value",
        "n_traders",
        "n_yes",
        "n_no",
        "agreement_frac",
        "signal_direction",
        "trigger_entry_price",
        "avg_pool_entry",
        "trader",
        "mvf",
    ])
