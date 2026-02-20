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

from strategies.consistency_copy.backtester.config import load_config
from strategies.consistency_copy.backtester.signal_table import build_signal_table
from strategies.consistency_copy.backtester.sweep import SweepConfig, run_sweep

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("data/derived")
PRICE_CACHE_DIR = DATA_DIR / "forward_prices"
OUTPUT_DIR = Path("strategies/consistency_copy")
DEFAULT_CONFIG = OUTPUT_DIR / "sweep_config.toml"


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
      all             — every trader
      pure_taker      — mvf < 0.10
      informed_taker  — mvf < 0.30 (synthesis recommendation)
      mixed           — 0.10 <= mvf <= 0.50
      maker_dominant  — mvf > 0.50
    """
    all_traders = set(mvf["trader"].to_list())
    return {
        "all": all_traders,
        "pure_taker": set(
            mvf.filter(pl.col("mvf") < 0.10)["trader"].to_list()
        ),
        "informed_taker": set(
            mvf.filter(pl.col("mvf") < 0.30)["trader"].to_list()
        ),
        "mixed": set(
            mvf.filter((pl.col("mvf") >= 0.10) & (pl.col("mvf") <= 0.50))["trader"].to_list()
        ),
        "maker_dominant": set(
            mvf.filter(pl.col("mvf") > 0.50)["trader"].to_list()
        ),
    }


def _compute_trader_median_entry(df_pnl: pl.DataFrame) -> pl.DataFrame:
    """Compute per-trader median directional entry price.

    For each (trader, market), determines the trader's direction from
    net_yes_tokens and computes the directional entry price:
      YES → wavg_yes_entry_price
      NO  → 1 - wavg_yes_entry_price

    Then groups by trader and takes the median across all markets.

    Returns
    -------
    pl.DataFrame
        Columns: trader, median_entry.
    """
    df = df_pnl.select(["trader", "condition_id", "net_yes_tokens", "wavg_yes_entry_price"])

    # Directional entry: YES-side price if net long YES, NO-side price otherwise
    df = df.with_columns(
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("directional_entry")
    )

    return df.group_by("trader").agg(
        pl.col("directional_entry").median().alias("median_entry")
    )


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


def _compute_stability_ranking(
    all_results: pl.DataFrame,
    top_n: int = 50,
    min_windows: int = 2,
) -> list[dict]:
    """Group results across windows and rank by stability + average Sharpe.

    Splits dev/test windows, ranks on dev only, and looks up test results
    for each top config.

    Parameters
    ----------
    all_results
        Concatenated sweep results with window and pool columns.
    top_n
        Number of top configs to return.
    min_windows
        Minimum number of dev windows a config must appear in.

    Returns
    -------
    list[dict]
        Top configs as list of dicts with nested ``config``, ``dev``, ``test`` keys,
        sorted by avg_sharpe descending.
    """
    if all_results.height == 0:
        return []

    config_cols = [
        "consistency_months",
        "min_markets",
        "mvf_band",
        "max_median_entry",
        "min_traders",
        "agreement_pct",
        "direction",
        "price_band_lo",
        "price_band_hi",
        "sizing",
        "execution_delay",
    ]
    available = set(all_results.columns)
    config_cols = [c for c in config_cols if c in available]

    # Split dev/test
    if "is_test" in all_results.columns:
        dev = all_results.filter(~pl.col("is_test"))
        test = all_results.filter(pl.col("is_test"))
    else:
        dev = all_results
        test = pl.DataFrame()

    if dev.height == 0:
        return []

    n_actual_windows = dev["window"].n_unique()
    min_win = min(n_actual_windows, min_windows)

    # Metrics to aggregate
    agg_exprs = [
        pl.col("sharpe").mean().alias("avg_sharpe"),
        pl.col("sharpe").std().alias("std_sharpe"),
        pl.col("total_pnl").mean().alias("avg_pnl"),
        pl.col("hit_rate").mean().alias("avg_hit_rate"),
        pl.col("window").n_unique().alias("n_windows"),
        pl.col("pool_size").mean().alias("avg_pool_size"),
    ]
    if "excess_hr" in dev.columns:
        agg_exprs.append(pl.col("excess_hr").mean().alias("avg_excess_hr"))
    if "base_adjusted_sharpe" in dev.columns:
        agg_exprs.append(pl.col("base_adjusted_sharpe").mean().alias("avg_base_adj_sharpe"))

    grouped = dev.group_by(config_cols).agg(agg_exprs)
    stable = grouped.filter(pl.col("n_windows") >= min_win)
    top = stable.sort("avg_sharpe", descending=True).head(top_n)

    # Build nested output with test lookup
    metric_keys = [c for c in top.columns if c not in config_cols]
    top_list = []
    for row in top.to_dicts():
        entry = {
            "config": {k: row[k] for k in config_cols if k in row},
            "dev": {k: row[k] for k in metric_keys if k in row},
            "test": {},
        }

        if test.height > 0:
            mask = pl.lit(True)
            for col in config_cols:
                if col in test.columns and col in row:
                    mask = mask & (pl.col(col) == row[col])
            test_rows = test.filter(mask)
            if test_rows.height > 0:
                t = {
                    "sharpe": float(test_rows["sharpe"].mean()),
                    "hit_rate": float(test_rows["hit_rate"].mean()),
                    "total_pnl": float(test_rows["total_pnl"].sum()),
                    "total_bets": int(test_rows["total_bets"].sum()),
                }
                if "excess_hr" in test_rows.columns:
                    t["excess_hr"] = float(test_rows["excess_hr"].mean())
                if "base_adjusted_sharpe" in test_rows.columns:
                    t["base_adjusted_sharpe"] = float(test_rows["base_adjusted_sharpe"].mean())
                entry["test"] = t

        top_list.append(entry)

    return top_list


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _precompute_entry_prices(
    holdout_data: pl.DataFrame,
    price_ts: pl.DataFrame,
    execution_delay_s: float = 0.0,
    max_price_delay_s: float = 3600.0,
) -> pl.DataFrame:
    """Pre-compute market YES prices at the next trade AFTER each trader's entry (t+dt).

    Uses a FORWARD asof join: for each (condition_id, trader, first_trade),
    find the first price record at or after first_trade + execution_delay_s.
    This simulates the realistic copy entry price — the next available trade
    after the signal fires.

    Parameters
    ----------
    holdout_data
        Per-trader per-market PnL with first_trade column.
    price_ts
        Price timeseries: condition_id, timestamp (float), yes_price.
    execution_delay_s
        Additional delay in seconds after trigger time (simulates detection +
        order placement latency). Default 0 = next available price.
    max_price_delay_s
        Maximum seconds between trigger and next trade. Prices beyond this
        are treated as unavailable (falls back to trader entry price).
        Default 3600 = 1 hour.

    Returns
    -------
    pl.DataFrame
        Columns: condition_id, trader, first_trade, market_yes_price, price_dt_s.
        price_dt_s = seconds between trigger and the matched price (for diagnostics).
    """
    # Get unique (condition_id, trader, first_trade) from holdout data
    entries = holdout_data.select(
        ["condition_id", "trader", "first_trade"]
    ).unique()

    # Convert first_trade to float timestamp + execution delay for asof join
    entries = entries.with_columns(
        (pl.col("first_trade").dt.epoch("s").cast(pl.Float64) + execution_delay_s)
        .alias("trigger_ts")
    ).sort(["condition_id", "trigger_ts"])

    # Sort price timeseries
    prices = price_ts.sort(["condition_id", "timestamp"])

    # Forward asof join: for each entry, find the first price >= trigger_ts
    joined = entries.join_asof(
        prices,
        left_on="trigger_ts",
        right_on="timestamp",
        by="condition_id",
        strategy="forward",
    )

    # Compute dt (seconds between trigger and matched price)
    joined = joined.with_columns(
        (pl.col("timestamp") - pl.col("trigger_ts")).alias("price_dt_s")
    )

    # Null out prices where the delay exceeds max_price_delay_s
    if max_price_delay_s > 0:
        joined = joined.with_columns(
            pl.when(
                pl.col("yes_price").is_not_null()
                & (pl.col("price_dt_s") <= max_price_delay_s)
            )
            .then(pl.col("yes_price"))
            .otherwise(pl.lit(None).cast(pl.Float64))
            .alias("yes_price")
        )

    return joined.select([
        "condition_id",
        "trader",
        "first_trade",
        pl.col("yes_price").alias("market_yes_price"),
        "price_dt_s",
    ])


def _apply_precomputed_prices(
    signal_table: pl.DataFrame,
    entry_prices: pl.DataFrame,
) -> pl.DataFrame:
    """Replace trigger_entry_price with forward market prices using pre-computed lookup.

    For YES signals: entry = market_yes_price (next available price after trigger)
    For NO signals: entry = 1 - market_yes_price
    """
    # Join on (condition_id, trader, first_trade = trigger_time)
    joined = signal_table.join(
        entry_prices.drop("price_dt_s"),
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


def _print_price_diagnostics(entry_prices: pl.DataFrame, win_name: str) -> None:
    """Print diagnostics about forward price estimation quality."""
    total = entry_prices.height
    has_price = entry_prices["market_yes_price"].is_not_null().sum()
    coverage = has_price / total if total > 0 else 0.0

    # dt stats (only where we got a price)
    dt_valid = entry_prices.filter(
        pl.col("price_dt_s").is_not_null() & pl.col("market_yes_price").is_not_null()
    )

    if dt_valid.height > 0:
        dt_series = dt_valid["price_dt_s"]
        p50 = dt_series.median()
        p95 = dt_series.quantile(0.95)
        p99 = dt_series.quantile(0.99)
        mean_dt = dt_series.mean()
        print(
            f"[prices] {win_name} diagnostics: "
            f"coverage={coverage:.1%} ({has_price:,}/{total:,}), "
            f"dt: mean={mean_dt:.0f}s, p50={p50:.0f}s, p95={p95:.0f}s, p99={p99:.0f}s"
        )
    else:
        print(f"[prices] {win_name} diagnostics: coverage={coverage:.1%} ({has_price:,}/{total:,})")


def main(config_path: Path | None = None) -> None:
    """Run the full parameter sweep across windows and trader pool configs.

    Uses FORWARD asof join for entry prices (t+dt): the next available trade
    price after the signal fires, simulating realistic copy-trade execution.

    Parameters
    ----------
    config_path
        Path to a TOML config file.  Falls back to ``DEFAULT_CONFIG`` when
        ``None``.
    """
    t0 = time.time()

    # Load config
    config = load_config(config_path or DEFAULT_CONFIG)
    windows = config.generate_windows()
    sweep_cfg = config.to_sweep_config()

    print(f"[config] Loaded from {config_path or DEFAULT_CONFIG}")
    print(f"[config] {len(windows)} windows ({sum(1 for w in windows if not w.is_test)} dev, "
          f"{sum(1 for w in windows if w.is_test)} test)")
    print(f"[config] Sizing: {config.sizing_strategies}")
    print(f"[config] Base rate adjustment: {config.base_rate_adjustment}")

    # Step 1: Load data
    df_pnl, mvf_df, markets, price_ts = _load_data()

    # Step 2: Pre-compute MVF subsets and trader median entry prices
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    for band, traders in mvf_subsets.items():
        print(f"[mvf] {band}: {len(traders):,} traders")

    trader_median_entry = _compute_trader_median_entry(df_pnl)
    print(f"[entry] Computed median directional entry for {trader_median_entry.height:,} traders")

    print(f"\n[config] Forward pricing: delays={config.execution_delays}s, "
          f"max_dt={config.max_price_delay_s}s, base_bet=${config.base_bet:.0f}")

    all_results: list[pl.DataFrame] = []

    # Step 3: For each rolling window
    for win in windows:
        win_name = win.name
        train_start = win.train_start
        train_end = win.train_end
        holdout_start = win.holdout_start
        holdout_end = win.holdout_end

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

        # Step 3b: Pre-compute FORWARD entry prices for ALL delays (once per window)
        # Uses disk cache: data/derived/forward_prices/<window>_delay_<d>.parquet
        delay_prices: dict[float, pl.DataFrame] = {}
        if price_ts is not None:
            PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for delay_s in config.execution_delays:
                cache_path = PRICE_CACHE_DIR / f"{win_name}_delay_{delay_s}.parquet"
                if cache_path.exists():
                    ep = pl.read_parquet(cache_path)
                    print(f"[prices] Loaded cached {win_name}/delay={delay_s}s ({ep.height:,} rows)")
                    _print_price_diagnostics(ep, f"{win_name}/delay={delay_s}s")
                else:
                    print(f"[prices] Pre-computing forward prices for {win_name} delay={delay_s}s...")
                    t_prices = time.time()
                    ep = _precompute_entry_prices(
                        holdout_data,
                        price_ts,
                        execution_delay_s=delay_s,
                        max_price_delay_s=config.max_price_delay_s,
                    )
                    ep.write_parquet(cache_path)
                    elapsed_p = time.time() - t_prices
                    _print_price_diagnostics(ep, f"{win_name}/delay={delay_s}s")
                    print(f"[prices] Done in {elapsed_p:.1f}s (cached)")
                delay_prices[delay_s] = ep

        # Step 3c: Compute per-window base rates from holdout markets
        base_rates: dict[str, float] | None = None
        if config.base_rate_adjustment:
            holdout_mkts = markets.filter(
                (pl.col("resolved_at") >= holdout_start)
                & (pl.col("resolved_at") < holdout_end)
            )
            if holdout_mkts.height > 0 and "yes_won" in holdout_mkts.columns:
                yes_rate = float(holdout_mkts["yes_won"].mean())
                base_rates = {"YES": yes_rate, "NO": 1.0 - yes_rate}
                print(f"[base_rate] {win_name}: YES={yes_rate:.3f}, NO={1-yes_rate:.3f}")

        # Step 4: Nested loops over pool configs
        for n_months in config.consistency_months:
            for min_mkts in config.min_markets:
                # Get consistent traders for this training window
                skilled = get_consistent_traders(
                    df_pnl, train_start, train_end, n_months, min_mkts
                )

                if len(skilled) < 10:
                    continue

                for band_name in config.mvf_bands:
                    # Intersect with MVF subset
                    pool_base = skilled & mvf_subsets[band_name]

                    if len(pool_base) < 5:
                        continue

                    for max_entry in config.max_median_entry_price:
                        # Filter pool by median directional entry price
                        eligible = set(
                            trader_median_entry.filter(
                                pl.col("median_entry") <= max_entry
                            )["trader"].to_list()
                        )
                        pool = pool_base & eligible

                        if len(pool) < 5:
                            continue

                        # Step 5: Build signal table per pool config
                        signal_table = build_signal_table(holdout_data, pool, mvf_df)

                        if signal_table.height < 20:
                            continue

                        # Step 5b: For each delay, apply prices and sweep
                        # delay=-1 is a sentinel meaning "use signal price"
                        delays_to_run = list(delay_prices.keys())
                        if config.signal_price:
                            delays_to_run.append(-1.0)
                        if not delays_to_run:
                            continue  # nothing to run

                        for delay_s in delays_to_run:
                            # Apply precomputed forward prices for this delay
                            if delay_s in delay_prices:
                                st_priced = _apply_precomputed_prices(
                                    signal_table, delay_prices[delay_s]
                                )
                            else:
                                st_priced = signal_table

                            # Signal price mode (delay=-1): skip price_band filtering
                            effective_cfg = sweep_cfg
                            if delay_s == -1.0:
                                effective_cfg = SweepConfig(
                                    min_traders_values=sweep_cfg.min_traders_values,
                                    agreement_pct_values=sweep_cfg.agreement_pct_values,
                                    direction_values=sweep_cfg.direction_values,
                                    entry_price_bands=[(0.0, 1.0)],
                                    sizing_strategies=sweep_cfg.sizing_strategies,
                                    min_bets=sweep_cfg.min_bets,
                                )

                            # Step 6: Run sweep
                            sweep_results = run_sweep(
                                st_priced, effective_cfg,
                                base_bet=config.base_bet, fee_pct=config.fee_pct,
                                base_rates=base_rates,
                            )

                            if sweep_results.height == 0:
                                continue

                            # Step 7: Add pool params + delay + entry filter + is_test
                            sweep_results = sweep_results.with_columns(
                                pl.lit(win_name).alias("window"),
                                pl.lit(n_months).alias("consistency_months"),
                                pl.lit(min_mkts).alias("min_markets"),
                                pl.lit(band_name).alias("mvf_band"),
                                pl.lit(len(pool)).alias("pool_size"),
                                pl.lit(max_entry).alias("max_median_entry"),
                                pl.lit(delay_s).alias("execution_delay"),
                                pl.lit(win.is_test).alias("is_test"),
                            )

                            all_results.append(sweep_results)

                        # Progress line (summary across delays)
                        if all_results:
                            last = all_results[-1]
                            best_hr = last["hit_rate"].max()
                            best_pnl = last["total_pnl"].max()
                            n_traders_sig = int(last["n_traders_in_signal"].max())
                            print(
                                f"  months={n_months} mkts={min_mkts} mvf={band_name} "
                                f"entry<={max_entry} pool={len(pool)}: "
                                f"{last.height} configs/delay, {n_traders_sig} traders, "
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

    top_configs = _compute_stability_ranking(
        combined, top_n=config.top_n, min_windows=config.min_windows
    )

    out_json = OUTPUT_DIR / "top_configs.json"
    out_json.write_text(json.dumps(top_configs, indent=2, default=str))
    print(f"[save] Wrote {out_json} ({len(top_configs)} configs)")

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")
