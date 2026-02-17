"""Sweep engine — vectorized parameter grid over signal table.

Iterates over all combinations of (min_traders, agreement_pct, direction,
entry_price_band, sizing_strategy) from a SweepConfig. For each combo:
filter to matching signal rows, apply sizing, compute daily P&L + metrics.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import polars as pl

from strategies.consistency_copy.backtester.metrics import compute_metrics
from strategies.consistency_copy.backtester.sizing import apply_sizing


@dataclass
class SweepConfig:
    """Parameter grid specification for the sweep engine."""

    min_traders_values: list[int]
    agreement_pct_values: list[float]
    direction_values: list[str]  # "YES-only", "NO-only", "both"
    entry_price_bands: list[tuple[float, float]]  # e.g. [(0.05, 0.95)]
    sizing_strategies: list[str]  # "fixed", "agreement_weighted", "kelly", "edge_weighted"
    min_bets: int = 20


def _select_signal_rows(
    st: pl.DataFrame,
    min_traders: int,
    agreement_pct: float,
) -> pl.DataFrame:
    """Filter signal table to rows meeting thresholds, then take first per market.

    For each (min_traders, agreement_pct) combo, find the FIRST row per market
    where the threshold is crossed (first time n_traders >= min_t AND
    agreement_frac >= min_agree). This simulates: "the signal fires the moment
    enough traders agree."
    """
    qualified = st.filter(
        (pl.col("n_traders") >= min_traders) & (pl.col("agreement_frac") >= agreement_pct)
    )
    # Take the first qualifying row per market (lowest arrival_idx)
    return qualified.sort("arrival_idx").group_by("condition_id").first()


def run_sweep(
    signal_table: pl.DataFrame,
    config: SweepConfig,
    base_bet: float = 1.0,
    fee_pct: float = 0.02,
) -> pl.DataFrame:
    """Run a full parameter sweep over the signal table.

    Parameters
    ----------
    signal_table
        DataFrame with columns from build_signal_table: condition_id, arrival_idx,
        trigger_time, resolved_at, resolution_value, n_traders, n_yes, n_no,
        agreement_frac, signal_direction, trigger_entry_price, avg_pool_entry,
        trader, mvf.
    config
        SweepConfig with parameter grid values.
    base_bet
        Base bet amount for sizing.
    fee_pct
        Fee as a fraction of the smaller side.

    Returns
    -------
    pl.DataFrame
        One row per parameter combination, with columns for all params + all
        metric keys from compute_metrics.
    """
    results: list[dict] = []

    combos = itertools.product(
        config.min_traders_values,
        config.agreement_pct_values,
        config.direction_values,
        config.entry_price_bands,
    )

    for min_t, agree, direction, (price_lo, price_hi) in combos:
        # Step 1: select signal rows (first qualifying row per market)
        bets = _select_signal_rows(signal_table, min_t, agree)

        # Step 2: direction filter
        if direction == "YES-only":
            bets = bets.filter(pl.col("signal_direction") == "YES")
        elif direction == "NO-only":
            bets = bets.filter(pl.col("signal_direction") == "NO")
        # "both" -> no filter

        # Step 3: entry price band filter
        bets = bets.filter(
            (pl.col("trigger_entry_price") >= price_lo)
            & (pl.col("trigger_entry_price") <= price_hi)
        )

        # Step 4: skip if fewer than min_bets
        if bets.height < config.min_bets:
            continue

        # Step 5: compute "won" column
        # won = (YES signal AND resolution=1) OR (NO signal AND resolution=0)
        bets = bets.with_columns(
            (
                ((pl.col("signal_direction") == "YES") & (pl.col("resolution_value") == 1))
                | ((pl.col("signal_direction") == "NO") & (pl.col("resolution_value") == 0))
            ).alias("won")
        )

        # Step 6: for each sizing strategy, apply sizing -> daily PnL -> metrics
        for sizing in config.sizing_strategies:
            sized = apply_sizing(
                bets,
                strategy=sizing,  # type: ignore[arg-type]
                base_bet=base_bet,
                fee_pct=fee_pct,
            )

            # Build daily P&L: group by resolved_at date
            daily_pnl = (
                sized.with_columns(pl.col("resolved_at").cast(pl.Date).alias("resolved_date"))
                .group_by("resolved_date")
                .agg(
                    pl.col("bet_pnl").sum().alias("daily_pnl"),
                    pl.len().alias("n_bets"),
                    pl.col("won").sum().cast(pl.Int64).alias("n_wins"),
                )
                .sort("resolved_date")
            )

            metrics = compute_metrics(daily_pnl)

            # Step 7: collect params + metrics
            row = {
                "min_traders": min_t,
                "agreement_pct": agree,
                "direction": direction,
                "price_band_lo": price_lo,
                "price_band_hi": price_hi,
                "sizing": sizing,
                **metrics,
            }
            results.append(row)

    if not results:
        # Return an empty DataFrame with the expected schema
        return pl.DataFrame(
            schema={
                "min_traders": pl.Int64,
                "agreement_pct": pl.Float64,
                "direction": pl.Utf8,
                "price_band_lo": pl.Float64,
                "price_band_hi": pl.Float64,
                "sizing": pl.Utf8,
            }
        )

    return pl.DataFrame(results)
