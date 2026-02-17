"""Bet sizing module — compute bet sizes and per-bet P&L.

Supports four sizing strategies: fixed, agreement_weighted, kelly, edge_weighted.
Each adds columns (bet_size, fee, bet_pnl) to the input bets DataFrame.
"""

from __future__ import annotations

from typing import Literal

import polars as pl


def apply_sizing(
    bets: pl.DataFrame,
    strategy: Literal["fixed", "kelly", "agreement_weighted", "edge_weighted"] = "fixed",
    base_bet: float = 1.0,
    fee_pct: float = 0.02,
    kelly_cap: float = 0.25,
    rolling_hr: float | None = None,
) -> pl.DataFrame:
    """Apply a bet sizing strategy and compute per-bet P&L.

    Parameters
    ----------
    bets
        DataFrame with columns: agreement_frac, trigger_entry_price,
        signal_direction, won (bool), resolution_value.
    strategy
        One of "fixed", "kelly", "agreement_weighted", "edge_weighted".
    base_bet
        Base bet amount (default 1.0).
    fee_pct
        Fee as a fraction of the smaller side (default 0.02 = 2%).
    kelly_cap
        Maximum Kelly fraction (default 0.25).
    rolling_hr
        Win probability estimate. If None, uses bets["won"].mean().

    Returns
    -------
    pl.DataFrame
        Input DataFrame with added columns: bet_size, fee, bet_pnl.
    """
    p = rolling_hr if rolling_hr is not None else bets["won"].mean()

    price = pl.col("trigger_entry_price")

    if strategy == "fixed":
        df = bets.with_columns(pl.lit(base_bet).alias("bet_size"))

    elif strategy == "agreement_weighted":
        df = bets.with_columns(
            (pl.lit(base_bet) * (pl.col("agreement_frac") - 0.5) * 2.0).alias("bet_size")
        )

    elif strategy == "kelly":
        # b = (1 - price) / price  (decimal odds minus 1)
        # f* = (p*b - q) / b, clamped to [0, kelly_cap]
        # bet_size = f* * base_bet
        q = 1.0 - p
        b_expr = (1.0 - price) / price
        f_star = (pl.lit(p) * b_expr - pl.lit(q)) / b_expr
        f_clamped = f_star.clip(0.0, kelly_cap)
        df = bets.with_columns((f_clamped * pl.lit(base_bet)).alias("bet_size"))

    elif strategy == "edge_weighted":
        # edge = p*b - q
        # bet_size = max(0, edge) * base_bet
        q = 1.0 - p
        b_expr = (1.0 - price) / price
        edge = pl.lit(p) * b_expr - pl.lit(q)
        df = bets.with_columns((edge.clip(0.0, None) * pl.lit(base_bet)).alias("bet_size"))

    else:
        msg = (
            f"Unknown strategy: {strategy!r}. "
            "Must be one of: fixed, kelly, agreement_weighted, edge_weighted."
        )
        raise ValueError(msg)

    # Fee = fee_pct * min(price, 1 - price) * bet_size
    df = df.with_columns(
        (pl.lit(fee_pct) * pl.min_horizontal(price, 1.0 - price) * pl.col("bet_size")).alias("fee")
    )

    # P&L:
    #   won=True:  bet_size * (1 - price) / price - fee
    #   won=False: -bet_size - fee
    df = df.with_columns(
        pl.when(pl.col("won"))
        .then(pl.col("bet_size") * (1.0 - price) / price - pl.col("fee"))
        .otherwise(-pl.col("bet_size") - pl.col("fee"))
        .alias("bet_pnl")
    )

    return df
