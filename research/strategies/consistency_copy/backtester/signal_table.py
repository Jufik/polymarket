"""Signal table builder — one row per (market, Nth skilled trader arrival).

The signal table is the core data structure for the consensus-copy backtester.
All parameter sweeps are pure filter+aggregate on this table.
"""

from __future__ import annotations

import polars as pl


def _infer_bet_yes_from_tokens(net_yes_tokens: pl.Expr) -> pl.Expr:
    """Determine whether a trader bet YES from their net YES-token position.

    Uses actual trade direction (token_index=0 is YES side), not outcomes.
    Positive net_yes_tokens means the trader was net long YES → bet YES.
    """
    return net_yes_tokens > 0


def _infer_bet_yes_from_pnl(market_pnl: pl.Expr, resolution_value: pl.Expr) -> pl.Expr:
    """DEPRECATED: Infer bet direction from PnL + outcome (tautological).

    This creates a circular relationship: direction is derived from the outcome,
    leading to 100%/0% hit rates for YES-only/NO-only strategies.
    Kept as fallback for data without net_yes_tokens.
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

    # Infer bet direction from actual trade data (non-tautological)
    if "net_yes_tokens" in df.columns:
        df = df.with_columns(
            _infer_bet_yes_from_tokens(pl.col("net_yes_tokens")).alias("bet_yes")
        )
    else:
        import warnings
        warnings.warn(
            "net_yes_tokens not found in PnL data — falling back to PnL-based "
            "direction inference (tautological). Re-run: "
            "uv run python scripts/build_data.py --step derived",
            stacklevel=2,
        )
        df = df.with_columns(
            _infer_bet_yes_from_pnl(pl.col("market_pnl"), pl.col("resolution_value"))
            .alias("bet_yes")
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
    # If wavg_yes_entry_price is available, compute cumulative avg.
    # Otherwise, set avg_pool_entry and trigger_entry_price to null
    # (they will be replaced by forward market prices).
    has_entry_price = "wavg_yes_entry_price" in df.columns

    if has_entry_price:
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

        # trigger_entry_price = price to enter the SIGNALED direction:
        # YES signal → YES price, NO signal → 1 - YES price
        df = df.with_columns([
            pl.col("first_trade").alias("trigger_time"),
            pl.when(pl.col("signal_direction") == "YES")
            .then(pl.col("wavg_yes_entry_price"))
            .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
            .alias("trigger_entry_price"),
        ])
    else:
        # No trader entry price available — use null placeholders.
        # Forward pricing will overwrite trigger_entry_price with market prices.
        df = df.with_columns([
            pl.col("first_trade").alias("trigger_time"),
            pl.lit(None).cast(pl.Float64).alias("trigger_entry_price"),
            pl.lit(None).cast(pl.Float64).alias("avg_pool_entry"),
        ])

    # Join MVF data
    df = df.join(mvf_data, on="trader", how="left")

    # Select output columns in the specified order
    out_cols = [
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
    ]
    # Include yes_won if available (for non-tautological win determination)
    if "yes_won" in df.columns:
        out_cols.append("yes_won")
    return df.select(out_cols)
