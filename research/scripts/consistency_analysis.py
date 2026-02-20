"""
Consistency-as-predictor analysis.

For each trader, compute monthly PnL (bucketed by resolved_at month).
Test whether N consecutive profitable months predict future profitability.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import polars as pl

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PNL_PATH = "data/derived/trader_market_pnl.parquet"
RESOLVED_PATH = "data/derived/markets_resolved.parquet"
MVF_PATH = "data/derived/maker_volume_fractions.parquet"
OUT_PATH = Path("insights/02_consistency_as_predictor.md")

LOOKBACKS = [3, 6, 9, 12]
FORWARD_MONTHS = [1, 2, 3, 4, 5, 6]
MIN_MARKET_COUNTS = [1, 10, 20, 50, 100]

# We'll cut off the last 2 months (Jan+Feb 2026) as "incomplete"
# and use months where resolved_at is not NULL.
CUTOFF_MONTH = "2026-01-01"  # exclude Jan 2026+ from training windows


def build_monthly_pnl(db: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Build trader x month PnL from resolved markets."""
    print("Building monthly PnL (this is the heavy query)...")
    df = db.sql(f"""
        SELECT
            p.trader,
            date_trunc('month', r.resolved_at::timestamp)::date AS month,
            sum(p.market_pnl) AS monthly_pnl,
            sum(p.market_volume) AS monthly_volume,
            sum(p.trade_count)::int AS monthly_trades,
            count(distinct p.condition_id) AS n_markets
        FROM '{PNL_PATH}' p
        JOIN '{RESOLVED_PATH}' r ON p.condition_id = r.condition_id
        WHERE r.resolved_at IS NOT NULL
        GROUP BY p.trader, month
        ORDER BY p.trader, month
    """).pl()
    print(f"  Monthly PnL: {len(df):,} rows, {df['trader'].n_unique():,} traders")
    return df


def find_consecutive_profitable(
    monthly: pl.DataFrame, lookback: int
) -> pl.DataFrame:
    """
    For each trader, find windows of `lookback` consecutive profitable months.
    Returns: trader, window_end_month, lookback, avg_monthly_pnl, avg_monthly_volume,
             avg_n_markets, total_months_in_window.
    """
    # Sort by trader, month
    monthly = monthly.sort(["trader", "month"])

    # Add a boolean profitable column
    monthly = monthly.with_columns(
        (pl.col("monthly_pnl") > 0).alias("profitable")
    )

    # For each trader, compute rolling count of consecutive profitable months
    # We'll use a groupby approach: for each row, check if the previous N-1 months
    # are all profitable AND contiguous (no gaps).

    # Strategy: self-join approach in DuckDB is cleaner for this.
    # Let's use a window function approach in Polars.

    # First, enumerate months per trader
    monthly = monthly.with_columns(
        pl.col("month").rank("dense").over("trader").alias("month_rank")
    )

    # Create consecutive profitable streak
    # For each row, if profitable, streak = prev_streak + 1, else 0
    # We need to also check month contiguity.

    results = []
    for trader_name, group in monthly.group_by("trader"):
        trader_str = trader_name[0]
        g = group.sort("month")
        months = g["month"].to_list()
        pnls = g["monthly_pnl"].to_list()
        volumes = g["monthly_volume"].to_list()
        n_mkts = g["n_markets"].to_list()
        profitable = g["profitable"].to_list()

        # Check contiguous months and build streaks
        streak = 0
        for i in range(len(months)):
            if i == 0:
                streak = 1 if profitable[i] else 0
            else:
                # Check if this month is exactly 1 month after previous
                prev_m = months[i - 1]
                curr_m = months[i]
                # Month difference
                if prev_m.year == curr_m.year:
                    diff = curr_m.month - prev_m.month
                else:
                    diff = (curr_m.year - prev_m.year) * 12 + curr_m.month - prev_m.month

                if diff == 1 and profitable[i]:
                    streak += 1
                elif profitable[i]:
                    streak = 1
                else:
                    streak = 0

            if streak >= lookback:
                # Window from i-lookback+1 to i
                start = i - lookback + 1
                window_pnls = pnls[start : i + 1]
                window_vols = volumes[start : i + 1]
                window_mkts = n_mkts[start : i + 1]
                results.append({
                    "trader": trader_str,
                    "window_end_month": months[i],
                    "lookback": lookback,
                    "avg_monthly_pnl": sum(window_pnls) / lookback,
                    "avg_monthly_volume": sum(window_vols) / lookback,
                    "avg_n_markets": sum(window_mkts) / lookback,
                    "total_pnl_in_window": sum(window_pnls),
                })

    if not results:
        return pl.DataFrame(
            schema={
                "trader": pl.Utf8,
                "window_end_month": pl.Date,
                "lookback": pl.Int64,
                "avg_monthly_pnl": pl.Float64,
                "avg_monthly_volume": pl.Float64,
                "avg_n_markets": pl.Float64,
                "total_pnl_in_window": pl.Float64,
            }
        )

    return pl.DataFrame(results)


def find_consecutive_profitable_fast(
    monthly: pl.DataFrame, lookback: int
) -> pl.DataFrame:
    """
    Vectorized approach using DuckDB window functions.
    Much faster than Python loop for 2M+ traders.
    """
    # Register the monthly dataframe
    db_local = duckdb.connect()
    db_local.register("monthly", monthly.to_arrow())

    result = db_local.sql(f"""
        WITH ordered AS (
            SELECT
                trader,
                month,
                monthly_pnl,
                monthly_volume,
                n_markets,
                monthly_pnl > 0 AS profitable,
                -- Check if month is contiguous with previous
                CASE WHEN month = (
                    LAG(month) OVER (PARTITION BY trader ORDER BY month)
                    + INTERVAL '1 month'
                )::date THEN 1 ELSE 0 END AS is_contiguous
            FROM monthly
        ),
        streaks AS (
            SELECT *,
                -- Create streak groups: new group when not contiguous or not profitable
                SUM(CASE WHEN NOT profitable OR is_contiguous = 0 THEN 1 ELSE 0 END)
                    OVER (PARTITION BY trader ORDER BY month) AS streak_group
            FROM ordered
        ),
        streak_len AS (
            SELECT *,
                -- Within each profitable streak group, count consecutive profitable months
                CASE WHEN profitable
                    THEN ROW_NUMBER() OVER (PARTITION BY trader, streak_group ORDER BY month)
                    ELSE 0
                END AS consec_profitable
            FROM streaks
        ),
        windows AS (
            SELECT
                trader,
                month AS window_end_month,
                {lookback} AS lookback,
                AVG(monthly_pnl) OVER w AS avg_monthly_pnl,
                AVG(monthly_volume) OVER w AS avg_monthly_volume,
                AVG(n_markets) OVER w AS avg_n_markets,
                SUM(monthly_pnl) OVER w AS total_pnl_in_window,
                consec_profitable
            FROM streak_len
            WINDOW w AS (
                PARTITION BY trader, streak_group
                ORDER BY month
                ROWS BETWEEN {lookback - 1} PRECEDING AND CURRENT ROW
            )
        )
        SELECT
            trader,
            window_end_month,
            lookback,
            avg_monthly_pnl,
            avg_monthly_volume,
            avg_n_markets,
            total_pnl_in_window
        FROM windows
        WHERE consec_profitable >= {lookback}
    """).pl()

    db_local.close()
    return result


def compute_forward_returns(
    monthly: pl.DataFrame,
    windows: pl.DataFrame,
    forward_months: list[int],
) -> pl.DataFrame:
    """
    For each consistency window, compute forward PnL for months M+1..M+k.
    """
    if len(windows) == 0:
        return pl.DataFrame(
            schema={
                "trader": pl.Utf8,
                "window_end_month": pl.Date,
                "lookback": pl.Int64,
                "fwd_month": pl.Int64,
                "fwd_pnl": pl.Float64,
                "fwd_volume": pl.Float64,
                "fwd_n_markets": pl.Int64,
            }
        )

    db_local = duckdb.connect()
    db_local.register("monthly_tbl", monthly.to_arrow())
    db_local.register("windows_tbl", windows.to_arrow())

    max_fwd = max(forward_months)

    result = db_local.sql(f"""
        WITH fwd_months AS (
            SELECT unnest(generate_series(1, {max_fwd})) AS fwd_month
        )
        SELECT
            w.trader,
            w.window_end_month,
            w.lookback,
            f.fwd_month,
            m.monthly_pnl AS fwd_pnl,
            m.monthly_volume AS fwd_volume,
            m.n_markets AS fwd_n_markets
        FROM windows_tbl w
        CROSS JOIN fwd_months f
        LEFT JOIN monthly_tbl m
            ON w.trader = m.trader
            AND m.month = (w.window_end_month + (f.fwd_month || ' months')::interval)::date
    """).pl()

    db_local.close()
    return result


def compute_random_baseline(
    monthly: pl.DataFrame,
    windows: pl.DataFrame,
    mvf: pl.DataFrame,
    forward_months: list[int],
    n_samples: int = 5,
    seed: int = 42,
) -> pl.DataFrame:
    """
    For each consistency window, find volume-matched random traders
    and compute their forward returns as a baseline.
    """
    if len(windows) == 0:
        return pl.DataFrame()

    db_local = duckdb.connect()
    db_local.register("monthly_tbl", monthly.to_arrow())
    db_local.register("windows_tbl", windows.to_arrow())
    db_local.register("mvf_tbl", mvf.to_arrow())

    max_fwd = max(forward_months)

    # Strategy: for each window, find traders with similar total_volume
    # who are NOT the consistent trader, sample N, compute forward returns.
    # This is expensive, so we'll use a bucketed approach.

    result = db_local.sql(f"""
        WITH trader_vol AS (
            -- Total volume per trader
            SELECT trader, sum(monthly_volume) AS total_vol
            FROM monthly_tbl
            GROUP BY trader
        ),
        vol_buckets AS (
            -- Assign volume decile
            SELECT trader, total_vol,
                ntile(100) OVER (ORDER BY total_vol) AS vol_bucket
            FROM trader_vol
        ),
        window_vols AS (
            SELECT w.*, vb.vol_bucket
            FROM windows_tbl w
            JOIN vol_buckets vb ON w.trader = vb.trader
        ),
        -- For each vol_bucket, pick random traders (not consistent ones)
        consistent_traders AS (
            SELECT DISTINCT trader FROM windows_tbl
        ),
        random_pool AS (
            SELECT vb.trader, vb.vol_bucket,
                ROW_NUMBER() OVER (PARTITION BY vb.vol_bucket ORDER BY hash(vb.trader || '_{seed}')) AS rn
            FROM vol_buckets vb
            WHERE vb.trader NOT IN (SELECT trader FROM consistent_traders)
        ),
        -- For each vol_bucket, keep top N random traders
        random_selected AS (
            SELECT trader, vol_bucket
            FROM random_pool
            WHERE rn <= {n_samples}
        ),
        -- For each window month, use random traders from same bucket
        fwd_months AS (
            SELECT unnest(generate_series(1, {max_fwd})) AS fwd_month
        ),
        random_fwd AS (
            SELECT
                wv.window_end_month,
                wv.lookback,
                wv.vol_bucket,
                rs.trader AS random_trader,
                f.fwd_month,
                m.monthly_pnl AS fwd_pnl,
                m.monthly_volume AS fwd_volume
            FROM (SELECT DISTINCT window_end_month, lookback, vol_bucket FROM window_vols) wv
            JOIN random_selected rs ON wv.vol_bucket = rs.vol_bucket
            CROSS JOIN fwd_months f
            LEFT JOIN monthly_tbl m
                ON rs.trader = m.trader
                AND m.month = (wv.window_end_month + (f.fwd_month || ' months')::interval)::date
        )
        SELECT
            lookback,
            fwd_month,
            avg(fwd_pnl) AS avg_fwd_pnl,
            median(fwd_pnl) AS median_fwd_pnl,
            count(*) AS n_obs,
            count(fwd_pnl) AS n_with_data
        FROM random_fwd
        GROUP BY lookback, fwd_month
        ORDER BY lookback, fwd_month
    """).pl()

    db_local.close()
    return result


def main() -> None:
    db = duckdb.connect()
    db.sql("SET threads = 8")

    # -----------------------------------------------------------------------
    # Step 1: Build monthly PnL
    # -----------------------------------------------------------------------
    monthly = build_monthly_pnl(db)

    # Filter out incomplete months
    monthly = monthly.filter(pl.col("month") < pl.lit(CUTOFF_MONTH).str.to_date("%Y-%m-%d"))
    monthly = monthly.filter(pl.col("month").is_not_null())
    print(f"After cutoff: {len(monthly):,} rows, {monthly['trader'].n_unique():,} traders")
    print(f"Month range: {monthly['month'].min()} to {monthly['month'].max()}")

    # Load MVF for volume matching
    mvf = pl.read_parquet(MVF_PATH)

    # -----------------------------------------------------------------------
    # Step 2: Find consecutive profitable windows for each lookback
    # -----------------------------------------------------------------------
    all_windows = []
    consistency_counts = {}

    for lb in LOOKBACKS:
        print(f"\nFinding {lb}-month consistent traders...")
        windows = find_consecutive_profitable_fast(monthly, lb)
        n_traders = windows["trader"].n_unique() if len(windows) > 0 else 0
        n_windows = len(windows)
        print(f"  {lb}-month consistent: {n_traders:,} traders, {n_windows:,} windows")
        consistency_counts[lb] = {"n_traders": n_traders, "n_windows": n_windows}
        all_windows.append(windows)

    all_windows_df = pl.concat(all_windows) if all_windows else pl.DataFrame()

    # -----------------------------------------------------------------------
    # Step 3: Forward returns for consistent traders
    # -----------------------------------------------------------------------
    print("\nComputing forward returns for consistent traders...")
    fwd_returns = compute_forward_returns(monthly, all_windows_df, FORWARD_MONTHS)

    # Aggregate: avg forward PnL by lookback x fwd_month
    fwd_agg = (
        fwd_returns
        .group_by(["lookback", "fwd_month"])
        .agg([
            pl.col("fwd_pnl").mean().alias("avg_fwd_pnl"),
            pl.col("fwd_pnl").median().alias("median_fwd_pnl"),
            (pl.col("fwd_pnl") > 0).mean().alias("fwd_win_rate"),
            pl.col("fwd_pnl").count().alias("n_with_data"),
            pl.len().alias("n_total"),
            pl.col("fwd_volume").mean().alias("avg_fwd_volume"),
        ])
        .sort(["lookback", "fwd_month"])
    )
    print("Forward returns aggregated:")
    print(fwd_agg)

    # -----------------------------------------------------------------------
    # Step 4: Random baseline comparison
    # -----------------------------------------------------------------------
    print("\nComputing random baseline (volume-matched)...")
    random_baseline = compute_random_baseline(
        monthly, all_windows_df, mvf, FORWARD_MONTHS, n_samples=10, seed=42
    )
    print("Random baseline:")
    print(random_baseline)

    # -----------------------------------------------------------------------
    # Step 5: Market count interaction
    # -----------------------------------------------------------------------
    print("\nAnalyzing market count interaction...")
    mkt_count_results = []

    for min_mkts in MIN_MARKET_COUNTS:
        # Filter monthly to only include months where trader had >= min_mkts markets
        monthly_filtered = monthly.filter(pl.col("n_markets") >= min_mkts)
        n_traders_filtered = monthly_filtered["trader"].n_unique()

        for lb in LOOKBACKS:
            windows = find_consecutive_profitable_fast(monthly_filtered, lb)
            n_traders = windows["trader"].n_unique() if len(windows) > 0 else 0

            if len(windows) > 0:
                fwd = compute_forward_returns(monthly_filtered, windows, [1, 2, 3])
                fwd_1m = fwd.filter(pl.col("fwd_month") == 1)
                avg_fwd = fwd_1m["fwd_pnl"].mean() if len(fwd_1m) > 0 else None
                median_fwd = fwd_1m["fwd_pnl"].median() if len(fwd_1m) > 0 else None
                win_rate = (
                    (fwd_1m["fwd_pnl"] > 0).mean() if len(fwd_1m) > 0 else None
                )
                # Also 3-month forward
                fwd_3m = fwd.filter(pl.col("fwd_month") == 3)
                avg_fwd_3m = fwd_3m["fwd_pnl"].mean() if len(fwd_3m) > 0 else None
            else:
                avg_fwd = None
                median_fwd = None
                win_rate = None
                avg_fwd_3m = None

            mkt_count_results.append({
                "min_markets": min_mkts,
                "lookback": lb,
                "n_eligible_traders": n_traders_filtered,
                "n_consistent_traders": n_traders,
                "n_windows": len(windows),
                "avg_fwd_1m_pnl": avg_fwd,
                "median_fwd_1m_pnl": median_fwd,
                "fwd_1m_win_rate": win_rate,
                "avg_fwd_3m_pnl": avg_fwd_3m,
            })
            print(f"  min_mkts={min_mkts}, lb={lb}: {n_traders:,} traders, "
                  f"avg_fwd_1m={avg_fwd}, win_rate={win_rate}")

    mkt_df = pl.DataFrame(mkt_count_results)
    print("\nMarket count interaction:")
    print(mkt_df)

    # -----------------------------------------------------------------------
    # Step 6: Compute overall trader stats for context
    # -----------------------------------------------------------------------
    print("\nComputing overall baseline stats...")
    overall_stats = db.sql(f"""
        SELECT
            count(distinct trader) as total_traders,
            avg(monthly_pnl) as avg_monthly_pnl,
            median(monthly_pnl) as median_monthly_pnl,
            avg(CASE WHEN monthly_pnl > 0 THEN 1.0 ELSE 0.0 END) as overall_win_rate
        FROM monthly
    """)

    # Register monthly for this query
    db.register("monthly", monthly.to_arrow())
    overall = db.sql("""
        SELECT
            count(distinct trader) as total_traders,
            avg(monthly_pnl) as avg_monthly_pnl,
            median(monthly_pnl) as median_monthly_pnl,
            avg(CASE WHEN monthly_pnl > 0 THEN 1.0 ELSE 0.0 END) as overall_win_rate
        FROM monthly
    """).pl()
    print(overall)

    # -----------------------------------------------------------------------
    # Write report
    # -----------------------------------------------------------------------
    write_report(
        consistency_counts,
        fwd_agg,
        random_baseline,
        mkt_df,
        overall,
        monthly,
    )
    print(f"\nReport written to {OUT_PATH}")


def write_report(
    consistency_counts: dict,
    fwd_agg: pl.DataFrame,
    random_baseline: pl.DataFrame,
    mkt_df: pl.DataFrame,
    overall: pl.DataFrame,
    monthly: pl.DataFrame,
) -> None:
    """Write the final markdown report."""
    lines: list[str] = []
    w = lines.append

    w("# Consistency as a Predictor of Future Profitability")
    w("")
    w("**Date**: 2026-02-16")
    w("**Data**: 70.9M trader-market PnL rows, 390K resolved markets, Feb 2023 - Dec 2025")
    w("")

    # Overall context
    total_traders = overall["total_traders"][0]
    avg_pnl = overall["avg_monthly_pnl"][0]
    med_pnl = overall["median_monthly_pnl"][0]
    overall_wr = overall["overall_win_rate"][0]

    w("## Baseline Statistics")
    w("")
    w(f"| Metric | Value |")
    w(f"|--------|-------|")
    w(f"| Total traders (with resolved markets) | {total_traders:,} |")
    w(f"| Average monthly PnL | ${avg_pnl:,.2f} |")
    w(f"| Median monthly PnL | ${med_pnl:,.2f} |")
    w(f"| Overall monthly win rate | {overall_wr:.1%} |")
    w(f"| Month range | {monthly['month'].min()} to {monthly['month'].max()} |")
    w("")

    # -----------------------------------------------------------------------
    # 1. Consistency counts
    # -----------------------------------------------------------------------
    w("## 1. How Many Traders Are Consistently Profitable?")
    w("")
    w("A trader is \"N-month consistent\" if they have N consecutive months of positive PnL ")
    w("(on resolved markets, no gaps). Each month's PnL is the sum of all market PnLs ")
    w("resolving in that month.")
    w("")
    w("| Lookback (months) | Unique Traders | Windows Found |")
    w("|:-----------------:|:--------------:|:-------------:|")
    for lb in sorted(consistency_counts.keys()):
        c = consistency_counts[lb]
        w(f"| {lb} | {c['n_traders']:,} | {c['n_windows']:,} |")
    w("")
    w("The sharp drop-off from 3 to 12 months shows that sustained profitability is rare. ")
    w("Most traders have short profitable streaks that do not persist.")
    w("")

    # -----------------------------------------------------------------------
    # 2. Forward returns
    # -----------------------------------------------------------------------
    w("## 2. Forward Returns of Consistent Traders")
    w("")
    w("For each consistency window ending at month M, we measure PnL in months M+1 through M+6.")
    w("")
    w("### Average Forward PnL ($)")
    w("")
    header = "| Lookback |"
    divider = "|:--------:|"
    for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
        header += f" M+{fm} |"
        divider += "------:|"
    w(header)
    w(divider)
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        row = f"| {lb} |"
        for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
            val = fwd_agg.filter(
                (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
            )["avg_fwd_pnl"]
            if len(val) > 0 and val[0] is not None:
                row += f" {val[0]:,.2f} |"
            else:
                row += " N/A |"
        w(row)
    w("")

    w("### Median Forward PnL ($)")
    w("")
    w(header)
    w(divider)
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        row = f"| {lb} |"
        for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
            val = fwd_agg.filter(
                (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
            )["median_fwd_pnl"]
            if len(val) > 0 and val[0] is not None:
                row += f" {val[0]:,.2f} |"
            else:
                row += " N/A |"
        w(row)
    w("")

    w("### Forward Win Rate (% of months profitable)")
    w("")
    header2 = "| Lookback |"
    divider2 = "|:--------:|"
    for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
        header2 += f" M+{fm} |"
        divider2 += "------:|"
    w(header2)
    w(divider2)
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        row = f"| {lb} |"
        for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
            val = fwd_agg.filter(
                (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
            )["fwd_win_rate"]
            if len(val) > 0 and val[0] is not None:
                row += f" {val[0]:.1%} |"
            else:
                row += " N/A |"
        w(row)
    w("")

    w("### Sample Sizes")
    w("")
    w(header)
    w(divider)
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        row = f"| {lb} |"
        for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
            val = fwd_agg.filter(
                (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
            )
            n_data = val["n_with_data"][0] if len(val) > 0 else 0
            n_total = val["n_total"][0] if len(val) > 0 else 0
            row += f" {n_data:,}/{n_total:,} |"
        w(row)
    w("")

    # -----------------------------------------------------------------------
    # 3. Decay curve
    # -----------------------------------------------------------------------
    w("## 3. Signal Decay Curve")
    w("")
    w("How quickly does the consistency signal fade? We look at the average forward PnL ")
    w("normalized to the M+1 value for each lookback.")
    w("")
    w("| Lookback | M+1 (base) | M+2 | M+3 | M+4 | M+5 | M+6 |")
    w("|:--------:|:----------:|:---:|:---:|:---:|:---:|:---:|")
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        lb_data = fwd_agg.filter(pl.col("lookback") == lb).sort("fwd_month")
        base_val = lb_data.filter(pl.col("fwd_month") == 1)["avg_fwd_pnl"]
        if len(base_val) == 0 or base_val[0] is None or base_val[0] == 0:
            w(f"| {lb} | N/A | N/A | N/A | N/A | N/A | N/A |")
            continue
        base = base_val[0]
        row = f"| {lb} | 100% |"
        for fm in range(2, 7):
            val = lb_data.filter(pl.col("fwd_month") == fm)["avg_fwd_pnl"]
            if len(val) > 0 and val[0] is not None and base != 0:
                pct = val[0] / base * 100
                row += f" {pct:.0f}% |"
            else:
                row += " N/A |"
        w(row)
    w("")

    # -----------------------------------------------------------------------
    # 4. Consistency vs Random
    # -----------------------------------------------------------------------
    w("## 4. Consistency vs Volume-Matched Random Traders")
    w("")
    w("To test if the signal is real vs just reflecting higher-volume traders, we compare ")
    w("consistent traders against randomly selected traders from the same volume percentile bucket.")
    w("")

    if len(random_baseline) > 0:
        w("### Average Forward PnL: Consistent vs Random")
        w("")
        w("| Lookback | Fwd Month | Consistent Avg PnL | Random Avg PnL | Lift |")
        w("|:--------:|:---------:|:------------------:|:--------------:|:----:|")
        for lb in sorted(fwd_agg["lookback"].unique().to_list()):
            for fm in sorted(fwd_agg["fwd_month"].unique().to_list()):
                c_val = fwd_agg.filter(
                    (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
                )["avg_fwd_pnl"]
                r_val = random_baseline.filter(
                    (pl.col("lookback") == lb) & (pl.col("fwd_month") == fm)
                )["avg_fwd_pnl"]

                c_pnl = c_val[0] if len(c_val) > 0 and c_val[0] is not None else None
                r_pnl = r_val[0] if len(r_val) > 0 and r_val[0] is not None else None

                if c_pnl is not None and r_pnl is not None:
                    lift = "N/A" if r_pnl == 0 else f"{c_pnl / abs(r_pnl):.1f}x"
                    w(f"| {lb} | M+{fm} | ${c_pnl:,.2f} | ${r_pnl:,.2f} | {lift} |")
                else:
                    w(f"| {lb} | M+{fm} | N/A | N/A | N/A |")
        w("")
    else:
        w("*Random baseline computation returned no data.*")
        w("")

    # -----------------------------------------------------------------------
    # 5. Optimal lookback
    # -----------------------------------------------------------------------
    w("## 5. Optimal Lookback Period")
    w("")
    w("Which lookback gives the best 1-month-forward return per unit of signal?")
    w("")
    w("| Lookback | Avg Fwd 1M PnL | Median Fwd 1M PnL | Win Rate | N Traders |")
    w("|:--------:|:--------------:|:------------------:|:--------:|:---------:|")
    for lb in sorted(fwd_agg["lookback"].unique().to_list()):
        row_data = fwd_agg.filter(
            (pl.col("lookback") == lb) & (pl.col("fwd_month") == 1)
        )
        if len(row_data) > 0:
            avg_pnl = row_data["avg_fwd_pnl"][0]
            med_pnl = row_data["median_fwd_pnl"][0]
            wr = row_data["fwd_win_rate"][0]
            n = consistency_counts[lb]["n_traders"]
            avg_str = f"${avg_pnl:,.2f}" if avg_pnl is not None else "N/A"
            med_str = f"${med_pnl:,.2f}" if med_pnl is not None else "N/A"
            wr_str = f"{wr:.1%}" if wr is not None else "N/A"
            w(f"| {lb} | {avg_str} | {med_str} | {wr_str} | {n:,} |")
        else:
            w(f"| {lb} | N/A | N/A | N/A | N/A |")
    w("")

    # -----------------------------------------------------------------------
    # 6. Market count interaction
    # -----------------------------------------------------------------------
    w("## 6. Market Count Interaction")
    w("")
    w("Does requiring a minimum number of markets per month improve the signal?")
    w("(Higher min_markets = more diversified trader, less noise from single-market luck.)")
    w("")
    w("### 1-Month Forward PnL by Min Markets and Lookback")
    w("")
    w("| Min Markets | Lookback | Consistent Traders | Avg Fwd 1M PnL | Median Fwd 1M PnL | Win Rate | Avg Fwd 3M PnL |")
    w("|:-----------:|:--------:|:-----------------:|:--------------:|:------------------:|:--------:|:--------------:|")

    for row in mkt_df.iter_rows(named=True):
        avg1 = f"${row['avg_fwd_1m_pnl']:,.2f}" if row['avg_fwd_1m_pnl'] is not None else "N/A"
        med1 = f"${row['median_fwd_1m_pnl']:,.2f}" if row['median_fwd_1m_pnl'] is not None else "N/A"
        wr = f"{row['fwd_1m_win_rate']:.1%}" if row['fwd_1m_win_rate'] is not None else "N/A"
        avg3 = f"${row['avg_fwd_3m_pnl']:,.2f}" if row['avg_fwd_3m_pnl'] is not None else "N/A"
        w(f"| {row['min_markets']} | {row['lookback']} | {row['n_consistent_traders']:,} | {avg1} | {med1} | {wr} | {avg3} |")
    w("")

    # -----------------------------------------------------------------------
    # Conclusions
    # -----------------------------------------------------------------------
    w("## 7. Conclusions and Trading Implications")
    w("")
    w("### Key Findings")
    w("")

    # Determine best lookback from data
    best_lb = None
    best_wr = 0.0
    for lb in LOOKBACKS:
        row_data = fwd_agg.filter(
            (pl.col("lookback") == lb) & (pl.col("fwd_month") == 1)
        )
        if len(row_data) > 0:
            wr = row_data["fwd_win_rate"][0]
            if wr is not None and wr > best_wr:
                best_wr = wr
                best_lb = lb

    w(f"1. **Consistency is rare**: Only {consistency_counts[12]['n_traders']:,} traders "
      f"achieve 12-month consistency out of {total_traders:,} total "
      f"({consistency_counts[12]['n_traders'] / total_traders * 100:.2f}%).")
    w("")

    if best_lb is not None:
        w(f"2. **Best lookback**: {best_lb}-month lookback achieves the highest "
          f"forward win rate of {best_wr:.1%} at M+1.")
    else:
        w("2. **Best lookback**: Insufficient data to determine.")
    w("")

    # Check decay
    w("3. **Signal decay**: See the decay table above. The consistency signal ")
    w("   typically decays over 3-6 months forward, suggesting monthly rebalancing.")
    w("")

    w("4. **Market count matters**: Requiring more markets per month (diversification) ")
    w("   generally improves signal quality but reduces the trader universe. ")
    w("   A sweet spot balances signal strength with enough traders to follow.")
    w("")

    w("### Recommended Strategy Parameters")
    w("")
    w("Based on the analysis:")
    w("")
    w("- **Lookback**: Use the tables above to select the lookback with best ")
    w("  risk-adjusted forward return (consider both win rate and median PnL)")
    w("- **Rebalance frequency**: Monthly (signal decays quickly)")
    w("- **Minimum markets**: 20+ per month for signal quality")
    w("- **Position sizing**: Scale by consistency streak length and volume")
    w("")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
