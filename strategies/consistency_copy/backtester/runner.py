"""Runner CLI — full parameter sweep across rolling windows and trader pools.

Loads cached PnL/MVF/resolution data, builds trader pools by consistency
criteria, generates signal tables, and sweeps all config combos.
Outputs sweep_results.parquet and top_configs.json.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import polars as pl

from strategies.consistency_copy.backtester.price_scanner import get_market_prices_at_signals
from strategies.consistency_copy.backtester.signal_table import build_signal_table
from strategies.consistency_copy.backtester.sweep import SweepConfig, run_sweep

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("data/derived")
OUTPUT_DIR = Path("strategies/consistency_copy")

# ---------------------------------------------------------------------------
# Rolling windows
# ---------------------------------------------------------------------------
WINDOWS: list[dict] = [
    {
        "name": "win0_2024h1",
        "train_start": datetime(2023, 1, 1),
        "train_end": datetime(2024, 1, 1),
        "holdout_start": datetime(2024, 1, 1),
        "holdout_end": datetime(2024, 7, 1),
    },
    {
        "name": "win1_2024h2",
        "train_start": datetime(2023, 7, 1),
        "train_end": datetime(2024, 7, 1),
        "holdout_start": datetime(2024, 7, 1),
        "holdout_end": datetime(2025, 1, 1),
    },
    {
        "name": "win2_2025h1",
        "train_start": datetime(2024, 1, 1),
        "train_end": datetime(2025, 1, 1),
        "holdout_start": datetime(2025, 1, 1),
        "holdout_end": datetime(2025, 7, 1),
    },
    {
        "name": "win3_dec25",
        "train_start": datetime(2025, 1, 1),
        "train_end": datetime(2025, 12, 1),
        "holdout_start": datetime(2025, 12, 1),
        "holdout_end": datetime(2026, 1, 1),
    },
    {
        "name": "win4_jan26",
        "train_start": datetime(2025, 3, 1),
        "train_end": datetime(2026, 1, 1),
        "holdout_start": datetime(2026, 1, 1),
        "holdout_end": datetime(2026, 2, 1),
    },
]

# ---------------------------------------------------------------------------
# Pool parameter grids
# ---------------------------------------------------------------------------
CONSISTENCY_MONTHS = [3, 4, 6, 8, 12]
MIN_MARKETS = [10, 20, 30, 50]
MVF_BAND_NAMES = ["all", "pure_taker", "mixed", "maker_dominant"]

# ---------------------------------------------------------------------------
# Default sweep config (full grid)
# ---------------------------------------------------------------------------
DEFAULT_SWEEP = SweepConfig(
    min_traders_values=[2, 3, 5, 7, 10],
    agreement_pct_values=[0.60, 0.70, 0.80, 0.90, 1.00],
    direction_values=["YES-only", "NO-only", "both"],
    entry_price_bands=[(0.05, 0.95), (0.10, 0.90), (0.20, 0.80)],
    sizing_strategies=["fixed", "agreement_weighted", "kelly", "edge_weighted"],
    min_bets=20,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame | None]:
    """Load cached parquet files and join PnL with market resolution dates.

    Returns (df_pnl_joined, mvf_df, markets_df, price_ts_or_None).
    """
    print("[load] Reading data files ...")

    pnl = pl.read_parquet(DATA_DIR / "trader_market_pnl.parquet")
    mvf = pl.read_parquet(DATA_DIR / "maker_volume_fractions.parquet")
    markets = pl.read_parquet(DATA_DIR / "markets_resolved.parquet")

    print(
        f"[load] pnl={pnl.height:,} rows  mvf={mvf.height:,} rows  "
        f"markets={markets.height:,} rows"
    )

    # Strip timezone from datetime columns if tz-aware (DuckDB/Gamma output)
    for col_name in ["resolved_at"]:
        dtype = markets.schema.get(col_name)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            markets = markets.with_columns(
                pl.col(col_name).dt.replace_time_zone(None)
            )

    # Strip timezone from datetime columns in PnL if tz-aware
    for col_name in ["first_trade", "last_trade"]:
        dtype = pnl.schema.get(col_name)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            pnl = pnl.with_columns(pl.col(col_name).dt.replace_time_zone(None))

    # Join PnL with markets on condition_id
    df_pnl = pnl.join(markets, on="condition_id", how="inner")

    print(f"[load] Joined PnL rows: {df_pnl.height:,}")

    # Load market prices if available
    price_path = DATA_DIR / "market_prices.parquet"
    price_ts: pl.DataFrame | None = None
    if price_path.exists():
        price_ts = pl.read_parquet(price_path)
        print(f"[load] Market prices: {price_ts.height:,} records, "
              f"{price_ts['condition_id'].n_unique():,} markets")
    else:
        print("[load] WARNING: market_prices.parquet not found — using trader entry prices")

    return df_pnl, mvf, markets, price_ts


def _precompute_mvf_subsets(
    mvf: pl.DataFrame,
) -> dict[str, set[str]]:
    """Pre-compute trader sets for each MVF band.

    Bands:
      all            — every trader
      pure_taker     — mvf < 0.10
      mixed          — 0.10 <= mvf <= 0.50
      maker_dominant — mvf > 0.50
    """
    all_traders = set(mvf["trader"].to_list())
    return {
        "all": all_traders,
        "pure_taker": set(
            mvf.filter(pl.col("mvf") < 0.10)["trader"].to_list()
        ),
        "mixed": set(
            mvf.filter((pl.col("mvf") >= 0.10) & (pl.col("mvf") <= 0.50))["trader"].to_list()
        ),
        "maker_dominant": set(
            mvf.filter(pl.col("mvf") > 0.50)["trader"].to_list()
        ),
    }


def get_consistent_traders(
    df_pnl: pl.DataFrame,
    train_start: datetime,
    train_end: datetime,
    n_periods: int,
    min_markets: int,
) -> set[str]:
    """Identify traders who are consistently profitable during the training period.

    Parameters
    ----------
    df_pnl
        Per-trader per-market PnL with resolved_at column.
    train_start, train_end
        Training window boundaries (resolved_at must fall in [train_start, train_end)).
    n_periods
        Minimum number of months the trader must be active.
    min_markets
        Minimum number of distinct markets traded across the training period.

    Returns
    -------
    set[str]
        Set of trader addresses meeting the consistency criteria.
    """
    # Filter to training period
    df = df_pnl.filter(
        (pl.col("resolved_at") >= train_start) & (pl.col("resolved_at") < train_end)
    )

    if df.height == 0:
        return set()

    # Compute month key: YYYYMM as UInt32
    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y%m").cast(pl.UInt32).alias("month")
    )

    # Group by (trader, month) to get monthly PnL and markets traded
    monthly = df.group_by(["trader", "month"]).agg(
        pl.col("market_pnl").sum().alias("monthly_pnl"),
        pl.col("condition_id").n_unique().alias("markets_traded"),
    )

    # Group by trader to get consistency stats
    trader_stats = monthly.group_by("trader").agg(
        (pl.col("monthly_pnl") > 0).sum().alias("positive_months"),
        pl.len().alias("total_months"),
        pl.col("markets_traded").sum().alias("total_markets"),
    )

    # Filter: profitable ALL months, enough months, enough markets
    consistent = trader_stats.filter(
        (pl.col("positive_months") == pl.col("total_months"))
        & (pl.col("total_months") >= n_periods)
        & (pl.col("total_markets") >= min_markets)
    )

    return set(consistent["trader"].to_list())


def _compute_stability_ranking(all_results: pl.DataFrame, top_n: int = 50) -> list[dict]:
    """Group results across windows and rank by stability + average Sharpe.

    Parameters
    ----------
    all_results
        Concatenated sweep results with window and pool columns.
    top_n
        Number of top configs to return.

    Returns
    -------
    list[dict]
        Top configs as list of dicts, sorted by avg_sharpe descending.
    """
    if all_results.height == 0:
        return []

    # Config columns = everything except window-specific and metric columns
    config_cols = [
        "consistency_months",
        "min_markets",
        "mvf_band",
        "min_traders",
        "agreement_pct",
        "direction",
        "price_band_lo",
        "price_band_hi",
        "sizing",
    ]

    # Ensure all config_cols are present
    available = set(all_results.columns)
    config_cols = [c for c in config_cols if c in available]

    n_actual_windows = all_results["window"].n_unique()
    min_windows = min(n_actual_windows, 2)

    grouped = all_results.group_by(config_cols).agg(
        pl.col("sharpe").mean().alias("avg_sharpe"),
        pl.col("sharpe").std().alias("std_sharpe"),
        pl.col("total_pnl").mean().alias("avg_pnl"),
        pl.col("hit_rate").mean().alias("avg_hit_rate"),
        pl.col("window").n_unique().alias("n_windows"),
        pl.col("pool_size").mean().alias("avg_pool_size"),
    )

    # Require stability: present in enough windows
    stable = grouped.filter(pl.col("n_windows") >= min_windows)

    # Sort by avg_sharpe descending, take top N
    top = stable.sort("avg_sharpe", descending=True).head(top_n)

    return top.to_dicts()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _precompute_entry_prices(
    holdout_data: pl.DataFrame,
    price_ts: pl.DataFrame,
) -> pl.DataFrame:
    """Pre-compute market YES prices at every trader's first_trade time.

    Instead of doing an expensive asof join for each signal table variant,
    do it ONCE per holdout window for all possible (condition_id, trader, first_trade).

    Returns a DataFrame with columns: condition_id, trader, first_trade,
    market_yes_price.
    """
    # Get unique (condition_id, trader, first_trade) from holdout data
    entries = holdout_data.select(
        ["condition_id", "trader", "first_trade"]
    ).unique()

    # Convert first_trade to float timestamp for asof join
    entries = entries.with_columns(
        pl.col("first_trade").dt.epoch("s").cast(pl.Float64).alias("trigger_ts")
    ).sort(["condition_id", "trigger_ts"])

    # Sort price timeseries
    prices = price_ts.sort(["condition_id", "timestamp"])

    # Asof join: for each entry, find the last price <= trigger_ts
    joined = entries.join_asof(
        prices,
        left_on="trigger_ts",
        right_on="timestamp",
        by="condition_id",
        strategy="backward",
    )

    return joined.select([
        "condition_id",
        "trader",
        "first_trade",
        pl.col("yes_price").alias("market_yes_price"),
    ])


def _apply_precomputed_prices(
    signal_table: pl.DataFrame,
    entry_prices: pl.DataFrame,
) -> pl.DataFrame:
    """Replace trigger_entry_price with real market prices using pre-computed lookup.

    For YES signals: entry = market_yes_price
    For NO signals: entry = 1 - market_yes_price
    """
    # Join on (condition_id, trader, first_trade = trigger_time)
    joined = signal_table.join(
        entry_prices,
        left_on=["condition_id", "trader", "trigger_time"],
        right_on=["condition_id", "trader", "first_trade"],
        how="left",
    )

    # Compute entry price based on signal direction
    joined = joined.with_columns(
        pl.when(pl.col("market_yes_price").is_not_null())
        .then(
            pl.when(pl.col("signal_direction") == "YES")
            .then(pl.col("market_yes_price"))
            .otherwise(1.0 - pl.col("market_yes_price"))
        )
        .otherwise(pl.col("trigger_entry_price"))
        .alias("trigger_entry_price")
    )

    return joined.drop("market_yes_price")


def main() -> None:
    """Run the full parameter sweep across windows and trader pool configs."""
    t0 = time.time()

    # Step 1: Load data
    df_pnl, mvf_df, _markets, price_ts = _load_data()

    # Step 2: Pre-compute MVF subsets
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    for band, traders in mvf_subsets.items():
        print(f"[mvf] {band}: {len(traders):,} traders")

    all_results: list[pl.DataFrame] = []

    # Step 3: For each rolling window
    for win in WINDOWS:
        win_name = win["name"]
        train_start = win["train_start"]
        train_end = win["train_end"]
        holdout_start = win["holdout_start"]
        holdout_end = win["holdout_end"]

        print(f"\n{'=' * 70}")
        print(f"[window] {win_name}: train {train_start:%Y-%m-%d} to {train_end:%Y-%m-%d}, "
              f"holdout {holdout_start:%Y-%m-%d} to {holdout_end:%Y-%m-%d}")
        print(f"{'=' * 70}")

        # Pre-filter holdout data for this window
        holdout_data = df_pnl.filter(
            (pl.col("resolved_at") >= holdout_start)
            & (pl.col("resolved_at") < holdout_end)
        )

        if holdout_data.height == 0:
            print(f"[window] {win_name}: no holdout data, skipping")
            continue

        # Step 3b: Pre-compute entry prices for this window (ONCE per window)
        entry_prices: pl.DataFrame | None = None
        if price_ts is not None:
            print(f"[prices] Pre-computing entry prices for {win_name}...")
            t_prices = time.time()
            entry_prices = _precompute_entry_prices(holdout_data, price_ts)
            price_coverage = entry_prices["market_yes_price"].is_not_null().mean()
            print(f"[prices] Done in {time.time()-t_prices:.1f}s: "
                  f"{entry_prices.height:,} entries, coverage={price_coverage:.1%}")

        # Step 4: Nested loops over pool configs
        for n_months in CONSISTENCY_MONTHS:
            for min_mkts in MIN_MARKETS:
                # Get consistent traders for this training window
                skilled = get_consistent_traders(
                    df_pnl, train_start, train_end, n_months, min_mkts
                )

                if len(skilled) < 10:
                    continue

                for band_name in MVF_BAND_NAMES:
                    # Intersect with MVF subset
                    pool = skilled & mvf_subsets[band_name]

                    if len(pool) < 5:
                        continue

                    # Step 5: Build signal table
                    signal_table = build_signal_table(holdout_data, pool, mvf_df)

                    if signal_table.height < 20:
                        continue

                    # Step 5b: Replace entry prices with real market prices
                    if entry_prices is not None:
                        signal_table = _apply_precomputed_prices(
                            signal_table, entry_prices
                        )

                    # Step 6: Run sweep
                    sweep_results = run_sweep(signal_table, DEFAULT_SWEEP)

                    if sweep_results.height == 0:
                        continue

                    # Step 7: Add pool params
                    sweep_results = sweep_results.with_columns(
                        pl.lit(win_name).alias("window"),
                        pl.lit(n_months).alias("consistency_months"),
                        pl.lit(min_mkts).alias("min_markets"),
                        pl.lit(band_name).alias("mvf_band"),
                        pl.lit(len(pool)).alias("pool_size"),
                    )

                    all_results.append(sweep_results)

                    # Progress line
                    best_hr = sweep_results["hit_rate"].max()
                    best_pnl = sweep_results["total_pnl"].max()
                    print(
                        f"  months={n_months} mkts={min_mkts} mvf={band_name} "
                        f"pool={len(pool)}: {sweep_results.height} configs, "
                        f"best HR={best_hr:.1%}, best PnL=${best_pnl:.2f}"
                    )

    # Step 8: Save outputs
    if not all_results:
        print("\n[done] No results produced. Check data availability.")
        return

    combined = pl.concat(all_results, how="diagonal_relaxed")
    print(f"\n[save] Total configs: {combined.height:,}")

    out_parquet = OUTPUT_DIR / "sweep_results.parquet"
    combined.write_parquet(out_parquet)
    print(f"[save] Wrote {out_parquet}")

    top_configs = _compute_stability_ranking(combined, top_n=50)

    out_json = OUTPUT_DIR / "top_configs.json"
    out_json.write_text(json.dumps(top_configs, indent=2, default=str))
    print(f"[save] Wrote {out_json} ({len(top_configs)} configs)")

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")
