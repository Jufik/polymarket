"""S1 Hit-Rate Copy-Trading: Rolling Walk-Forward Parameter Sweep.

Reads per-trader-per-month resolved positions from parquet and runs
a full grid sweep over:
  - lookback_months: [3, 6, 9, 12]
  - min_hit_rate: [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
  - min_positions: [5, 10, 20, 50]

For each combo, rolls monthly: train on [t-lookback, t), test on [t, t+1month).
Outputs a parquet file with one row per (params, test_month).

Usage:
    uv run python research/scripts/s1_parameter_sweep.py
"""

from __future__ import annotations

import time
from pathlib import Path

import polars as pl

DERIVED = Path("data/derived")
OUTPUT = Path("research/output")
OUTPUT.mkdir(parents=True, exist_ok=True)

# --- Parameters to sweep ---
LOOKBACK_MONTHS = [3, 6, 9, 12]
MIN_HIT_RATES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
MIN_POSITIONS = [5, 10, 20, 50]

# Test months: start from 2024-07 (enough history for 12-month lookback from 2023-07)
# through 2026-01 (last full month before current)
TEST_MONTHS = [
    "2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12",
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02",
]


def month_offset(month_str: str, offset: int) -> str:
    """Shift a 'YYYY-MM' string by offset months."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    m += offset
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def run_sweep() -> pl.DataFrame:
    """Run the full parameter sweep and return results DataFrame."""
    print("Loading data...")
    t0 = time.time()
    df = pl.read_parquet(str(DERIVED / "trader_monthly_resolved.parquet"))

    # Also load MVF for optional MVF filter sweep
    vol = pl.read_parquet(str(DERIVED / "trader_volumes.parquet"))
    vol = vol.with_columns(
        (pl.col("maker_vol") / (pl.col("maker_vol") + pl.col("taker_vol")))
        .alias("mvf")
    ).filter(
        (pl.col("maker_vol") + pl.col("taker_vol")) > 0
    ).select(["trader", "mvf"])

    print(f"  Loaded {len(df):,} trader-months, {len(vol):,} trader MVFs in {time.time()-t0:.1f}s")

    # Pre-sort for faster filtering
    df = df.sort(["trader", "res_month"])

    results: list[dict] = []
    total_combos = len(LOOKBACK_MONTHS) * len(MIN_HIT_RATES) * len(MIN_POSITIONS) * len(TEST_MONTHS)
    done = 0
    t_start = time.time()

    for lookback in LOOKBACK_MONTHS:
        for test_month in TEST_MONTHS:
            # Compute training window
            train_start = month_offset(test_month, -lookback)
            train_end = test_month  # exclusive

            # Slice training data: months in [train_start, train_end)
            train_df = df.filter(
                (pl.col("res_month") >= train_start) & (pl.col("res_month") < train_end)
            )

            # Aggregate per-trader over the training window
            train_agg = train_df.group_by("trader").agg(
                pl.col("n_positions").sum().alias("train_n"),
                pl.col("wins").sum().alias("train_wins"),
                pl.col("total_pnl").sum().alias("train_pnl"),
            ).with_columns(
                (pl.col("train_wins") / pl.col("train_n")).alias("train_hr")
            )

            # Slice test data: just the test month
            test_df = df.filter(pl.col("res_month") == test_month)

            if len(test_df) == 0:
                # No test data for this month
                for min_hr in MIN_HIT_RATES:
                    for min_pos in MIN_POSITIONS:
                        done += 1
                continue

            # Test aggregated per-trader
            test_agg = test_df.group_by("trader").agg(
                pl.col("n_positions").sum().alias("test_n"),
                pl.col("wins").sum().alias("test_wins"),
                pl.col("total_pnl").sum().alias("test_pnl"),
            ).with_columns(
                (pl.col("test_wins") / pl.col("test_n")).alias("test_hr")
            )

            # Join train and test
            joined = train_agg.join(test_agg, on="trader", how="inner")

            if len(joined) == 0:
                for min_hr in MIN_HIT_RATES:
                    for min_pos in MIN_POSITIONS:
                        done += 1
                continue

            # Now sweep min_hr and min_pos on the joined data
            for min_hr in MIN_HIT_RATES:
                for min_pos in MIN_POSITIONS:
                    # Filter qualifying traders
                    qualified = joined.filter(
                        (pl.col("train_hr") >= min_hr) & (pl.col("train_n") >= min_pos)
                    )

                    n_traders = len(qualified)
                    if n_traders == 0:
                        results.append({
                            "lookback": lookback,
                            "min_hr": min_hr,
                            "min_pos": min_pos,
                            "test_month": test_month,
                            "n_traders": 0,
                            "oos_positions": 0,
                            "oos_wins": 0,
                            "oos_hr": None,
                            "oos_total_pnl": 0.0,
                            "oos_avg_pnl": None,
                            "train_mean_hr": None,
                            "train_mean_n": None,
                            "decay": None,
                        })
                    else:
                        oos_pos = int(qualified["test_n"].sum())
                        oos_wins = int(qualified["test_wins"].sum())
                        oos_pnl = float(qualified["test_pnl"].sum())
                        oos_hr = oos_wins / oos_pos if oos_pos > 0 else None
                        oos_avg_pnl = oos_pnl / oos_pos if oos_pos > 0 else None
                        train_mean_hr = float(qualified["train_hr"].mean())
                        train_mean_n = float(qualified["train_n"].mean())
                        decay = (oos_hr - train_mean_hr) if oos_hr is not None else None

                        results.append({
                            "lookback": lookback,
                            "min_hr": min_hr,
                            "min_pos": min_pos,
                            "test_month": test_month,
                            "n_traders": n_traders,
                            "oos_positions": oos_pos,
                            "oos_wins": oos_wins,
                            "oos_hr": oos_hr,
                            "oos_total_pnl": oos_pnl,
                            "oos_avg_pnl": oos_avg_pnl,
                            "train_mean_hr": train_mean_hr,
                            "train_mean_n": train_mean_n,
                            "decay": decay,
                        })

                    done += 1

            # Progress
            if done % 500 == 0 or done == total_combos:
                elapsed = time.time() - t_start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total_combos - done) / rate if rate > 0 else 0
                print(f"  [{done:,}/{total_combos:,}] {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining")

    result_df = pl.DataFrame(results)
    print(f"\nSweep complete: {len(result_df):,} result rows in {time.time()-t_start:.1f}s")
    return result_df


def print_summary(result_df: pl.DataFrame) -> None:
    """Print a summary of the best parameter combinations."""
    # Filter to rows with actual data
    valid = result_df.filter(pl.col("oos_positions") > 0)

    # Aggregate across test months per parameter combo
    summary = valid.group_by(["lookback", "min_hr", "min_pos"]).agg(
        pl.col("n_traders").mean().alias("avg_traders"),
        pl.col("oos_positions").sum().alias("total_oos_pos"),
        pl.col("oos_wins").sum().alias("total_oos_wins"),
        pl.col("oos_total_pnl").sum().alias("total_oos_pnl"),
        pl.col("oos_avg_pnl").mean().alias("mean_oos_avg_pnl"),
        pl.col("oos_hr").mean().alias("mean_oos_hr"),
        pl.col("train_mean_hr").mean().alias("mean_train_hr"),
        pl.col("decay").mean().alias("mean_decay"),
        pl.len().alias("n_months"),
    ).with_columns(
        (pl.col("total_oos_pnl") / pl.col("total_oos_pos")).alias("overall_pnl_per_pos"),
        (pl.col("total_oos_wins") / pl.col("total_oos_pos")).alias("overall_oos_hr"),
    ).sort("overall_pnl_per_pos", descending=True)

    print("\n=== TOP 20 PARAMETER COMBINATIONS (by PnL/position) ===")
    print(f"{'LB':>3} {'min_hr':>7} {'min_p':>6} {'traders':>8} {'OOS_pos':>10} "
          f"{'OOS_HR':>8} {'train_HR':>9} {'decay':>7} {'PnL/pos':>10} {'tot_PnL':>15}")
    for row in summary.head(20).iter_rows(named=True):
        print(f"{row['lookback']:>3} {row['min_hr']:>7.2f} {row['min_pos']:>6} "
              f"{row['avg_traders']:>8.0f} {row['total_oos_pos']:>10,} "
              f"{row['overall_oos_hr']:>8.4f} {row['mean_train_hr']:>9.4f} "
              f"{row['mean_decay']:>+7.3f} {row['overall_pnl_per_pos']:>10.2f} "
              f"{row['total_oos_pnl']:>15,.0f}")

    # Also show the high-HR regime the user is interested in
    print("\n=== HR >= 0.70 REGIME ===")
    high_hr = summary.filter(pl.col("min_hr") >= 0.70).sort("overall_pnl_per_pos", descending=True)
    for row in high_hr.head(15).iter_rows(named=True):
        print(f"LB={row['lookback']:>2} min_hr={row['min_hr']:.2f} min_pos={row['min_pos']:>3} | "
              f"traders={row['avg_traders']:>6.0f} OOS_pos={row['total_oos_pos']:>8,} "
              f"OOS_HR={row['overall_oos_hr']:.4f} PnL/pos=${row['overall_pnl_per_pos']:>8.2f} "
              f"tot=${row['total_oos_pnl']:>12,.0f}")

    return summary


if __name__ == "__main__":
    result_df = run_sweep()

    # Save raw results
    out_path = OUTPUT / "s1_sweep_results.parquet"
    result_df.write_parquet(str(out_path))
    print(f"\nRaw results saved to {out_path}")

    summary = print_summary(result_df)

    # Save summary
    sum_path = OUTPUT / "s1_sweep_summary.parquet"
    summary.write_parquet(str(sum_path))
    print(f"Summary saved to {sum_path}")
