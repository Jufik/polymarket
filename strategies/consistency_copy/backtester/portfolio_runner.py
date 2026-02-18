"""Portfolio replication backtester — per-trader holdout evaluation.

Measures each trader's actual and simulated copy PnL individually,
using the same rolling-window framework as the consensus backtester.
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

from strategies.consistency_copy.backtester.config import load_config
from strategies.consistency_copy.backtester.runner import (
    _compute_trader_median_entry,
    _load_data,
    _precompute_entry_prices,
    _precompute_mvf_subsets,
    get_consistent_traders,
)

OUTPUT_DIR = Path("strategies/consistency_copy")
DEFAULT_CONFIG = OUTPUT_DIR / "sweep_config.toml"
PRICE_CACHE_DIR = Path("data/derived/forward_prices")


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


def main(config_path: Path | None = None) -> None:
    """Run per-trader portfolio evaluation across windows and pool configs.

    For each window x pool_config, evaluates every trader in the pool
    individually, producing both actual PnL and simulated copy PnL.
    """
    t0 = time.time()

    config = load_config(config_path or DEFAULT_CONFIG)
    windows = config.generate_windows()

    print(f"[config] Loaded from {config_path or DEFAULT_CONFIG}")
    print(f"[config] {len(windows)} windows, mode=portfolio")

    # Load data (same as consensus runner)
    df_pnl, mvf_df, markets, price_ts = _load_data()

    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    trader_median_entry = _compute_trader_median_entry(df_pnl)

    # Build MVF lookup for per-trader metadata
    mvf_lookup = mvf_df.select(["trader", "mvf"])

    all_rows: list[pl.DataFrame] = []

    for win in windows:
        print(f"\n{'=' * 70}")
        print(f"[window] {win.name}: holdout {win.holdout_start:%Y-%m-%d} to "
              f"{win.holdout_end:%Y-%m-%d}")

        holdout_data = df_pnl.filter(
            (pl.col("resolved_at") >= win.holdout_start)
            & (pl.col("resolved_at") < win.holdout_end)
        )

        if holdout_data.height == 0:
            print("  no holdout data, skipping")
            continue

        # Pre-compute forward prices for all delays (cached on disk)
        delay_prices: dict[float, pl.DataFrame] = {}
        if price_ts is not None:
            PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for delay_s in config.execution_delays:
                cache_path = PRICE_CACHE_DIR / f"{win.name}_delay_{delay_s}.parquet"
                if cache_path.exists():
                    delay_prices[delay_s] = pl.read_parquet(cache_path)
                else:
                    print(f"  computing forward prices delay={delay_s}s...")
                    ep = _precompute_entry_prices(
                        holdout_data, price_ts,
                        execution_delay_s=delay_s,
                        max_price_delay_s=config.max_price_delay_s,
                    )
                    ep.write_parquet(cache_path)
                    delay_prices[delay_s] = ep

        for n_months in config.consistency_months:
            for min_mkts in config.min_markets:
                skilled = get_consistent_traders(
                    df_pnl, win.train_start, win.train_end, n_months, min_mkts
                )
                if len(skilled) < 10:
                    continue

                for band_name in config.mvf_bands:
                    pool_base = skilled & mvf_subsets[band_name]
                    if len(pool_base) < 5:
                        continue

                    for max_entry in config.max_median_entry_price:
                        eligible = set(
                            trader_median_entry.filter(
                                pl.col("median_entry") <= max_entry
                            )["trader"].to_list()
                        )
                        pool = pool_base & eligible
                        if len(pool) < 5:
                            continue

                        # Actual PnL (delay-independent, compute once)
                        actual = evaluate_traders_actual(holdout_data, pool)

                        if actual.height == 0:
                            continue

                        # Add trader metadata
                        actual = actual.join(
                            trader_median_entry, on="trader", how="left"
                        ).join(
                            mvf_lookup, on="trader", how="left"
                        )

                        # Copy PnL for each delay
                        for delay_s in config.execution_delays:
                            ep = delay_prices.get(delay_s)
                            copy = evaluate_traders_copy(
                                holdout_data, pool,
                                entry_prices=ep,
                                base_bet=config.base_bet,
                            )

                            # Join actual + copy
                            combined = actual.join(copy, on="trader", how="left")

                            # Add pool/window metadata
                            combined = combined.with_columns(
                                pl.lit(win.name).alias("window"),
                                pl.lit(win.is_test).alias("is_test"),
                                pl.lit(n_months).alias("consistency_months"),
                                pl.lit(min_mkts).alias("min_markets"),
                                pl.lit(band_name).alias("mvf_band"),
                                pl.lit(max_entry).alias("max_median_entry"),
                                pl.lit(delay_s).alias("execution_delay"),
                                pl.lit(len(pool)).alias("pool_size"),
                            )

                            all_rows.append(combined)

                        # Progress
                        n_profitable = int(actual.filter(
                            pl.col("actual_pnl") > 0
                        ).height)
                        pct = n_profitable / actual.height * 100
                        med_pnl = actual["actual_pnl"].median()
                        print(
                            f"  m={n_months} mkts={min_mkts} mvf={band_name} "
                            f"entry<={max_entry} pool={len(pool)}: "
                            f"{pct:.0f}% profitable, "
                            f"median=${med_pnl:.2f}"
                        )

    if not all_rows:
        print("\n[done] No results.")
        return

    result = pl.concat(all_rows, how="diagonal_relaxed")
    out_path = OUTPUT_DIR / "portfolio_results.parquet"
    result.write_parquet(out_path)
    print(f"\n[save] {result.height:,} rows -> {out_path}")

    elapsed = time.time() - t0
    print(f"[done] Finished in {elapsed:.1f}s")
