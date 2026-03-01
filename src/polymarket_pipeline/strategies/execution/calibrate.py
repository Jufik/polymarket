"""Spread calibration from historical trade data.

Estimates effective bid-ask spreads per market from consecutive trade price
changes. No historical orderbook data is required.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

import polars as pl

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade


def calibrate_spreads(
    trades: pl.DataFrame | list[NormalizedTrade],
    *,
    min_trades_per_market: int = 20,
    method: Literal["median_abs_change", "roll"] = "median_abs_change",
) -> dict[str, float]:
    """Estimate per-market half-spread from consecutive trade price changes.

    Parameters
    ----------
    trades:
        Either a Polars DataFrame with ``condition_id`` and ``price`` columns,
        or a list of NormalizedTrade objects. Trades should be sorted by time
        within each market.
    min_trades_per_market:
        Markets with fewer trades are excluded (the simulator falls back to
        the config default for these).
    method:
        - ``median_abs_change``: ``half_spread = median(|p_t - p_{t-1}|)``
          Simple, robust to outliers.
        - ``roll``: ``half_spread = sqrt(-cov(dp_t, dp_{t-1}))``
          Roll (1984) estimator. More theoretically grounded but sensitive
          to autocorrelation.

    Returns
    -------
    dict mapping condition_id to estimated half-spread.
    """
    df = _to_dataframe(trades)
    if df.is_empty():
        return {}

    df = df.select(["condition_id", "price"]).sort("condition_id")

    # Compute price changes within each market
    df = df.with_columns(
        pl.col("price")
        .diff()
        .over("condition_id")
        .alias("dp"),
    )
    # Drop first row per group (null diff)
    df = df.drop_nulls("dp")

    if method == "median_abs_change":
        return _median_abs_change(df, min_trades_per_market)
    elif method == "roll":
        return _roll_estimator(df, min_trades_per_market)
    else:
        msg = f"Unknown method: {method}"
        raise ValueError(msg)


def calibrate_volumes(
    trades: pl.DataFrame | list[NormalizedTrade],
) -> dict[str, float]:
    """Compute per-market cumulative USD volume.

    Parameters
    ----------
    trades:
        Either a Polars DataFrame with ``condition_id`` and ``amount_usd``
        columns, or a list of NormalizedTrade objects.

    Returns
    -------
    dict mapping condition_id to total USD volume.
    """
    df = _to_dataframe(trades)
    if df.is_empty():
        return {}

    result = (
        df.group_by("condition_id")
        .agg(pl.col("amount_usd").cast(pl.Float64).sum().alias("volume"))
    )
    cids = result["condition_id"].to_list()
    vols = result["volume"].to_list()
    return dict(zip(cids, vols, strict=True))


def _median_abs_change(
    df: pl.DataFrame, min_trades: int
) -> dict[str, float]:
    """half_spread = median(|dp|) per market."""
    result = (
        df.group_by("condition_id")
        .agg(
            pl.col("dp").abs().median().alias("half_spread"),
            pl.col("dp").count().alias("n"),
        )
        .filter(pl.col("n") >= min_trades)
        .filter(pl.col("half_spread").is_not_null())
        .filter(pl.col("half_spread") > 0)
    )
    cids = result["condition_id"].to_list()
    spreads = result["half_spread"].to_list()
    return dict(zip(cids, spreads, strict=True))


def _roll_estimator(
    df: pl.DataFrame, min_trades: int
) -> dict[str, float]:
    """Roll (1984) estimator: half_spread = sqrt(-cov(dp_t, dp_{t-1})).

    If the serial covariance is positive (no bid-ask bounce), returns 0.
    """
    # Add lagged price change
    df = df.with_columns(
        pl.col("dp")
        .shift(1)
        .over("condition_id")
        .alias("dp_lag"),
    )
    df = df.drop_nulls("dp_lag")

    results: dict[str, float] = {}

    for group_name, group_df in df.group_by("condition_id"):
        cid = group_name[0] if isinstance(group_name, tuple) else group_name
        if len(group_df) < min_trades:
            continue

        dp = group_df["dp"].to_list()
        dp_lag = group_df["dp_lag"].to_list()
        n = len(dp)
        if n < 2:
            continue

        mean_dp = sum(dp) / n
        mean_lag = sum(dp_lag) / n
        cov = sum(
            (a - mean_dp) * (b - mean_lag) for a, b in zip(dp, dp_lag, strict=True)
        ) / (n - 1)

        if cov >= 0:
            # No bid-ask bounce detected
            continue

        half_spread = math.sqrt(-cov)
        if half_spread > 0:
            results[str(cid)] = half_spread

    return results


def _to_dataframe(
    trades: pl.DataFrame | list[NormalizedTrade],
) -> pl.DataFrame:
    """Convert trade input to a Polars DataFrame."""
    if isinstance(trades, pl.DataFrame):
        return trades

    if not trades:
        return pl.DataFrame()

    rows = []
    for t in trades:
        rows.append(
            {
                "condition_id": t.condition_id,
                "price": float(t.price),
                "amount_usd": float(t.amount_usd),
            }
        )
    return pl.DataFrame(rows)
