"""S2 Insider Copy: Three enhancements walk-forward analysis.

Enhancement 1: Entry Price Filter -- filter by insider's avg_yes_price
Enhancement 2: Feature Weight Optimization -- data-driven feature weights
Enhancement 3: Market Volume at Entry Time -- liquidity filter (no look-ahead)

APPROACH: Pull ALL enriched positions once per month (1 CH query per month),
then compute volume-at-entry via a batch temp table, then slice everything
in Polars. Much faster than running separate CH queries per filter.

Usage:
    PYTHONUNBUFFERED=1 uv run python research/scripts/s2_enhancements.py
"""

from __future__ import annotations

import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path

import clickhouse_connect
import numpy as np
import polars as pl
from dateutil.relativedelta import relativedelta

warnings.filterwarnings("ignore", category=DeprecationWarning)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
MIN_POSITIONS = 20

SQL_DIR = Path(__file__).parent.parent / "knowledge" / "queries"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Walk-forward windows: test months from 2025-01 to 2026-02 (14 months)
TEST_MONTHS: list[date] = []
_start = date(2025, 1, 1)
_end = date(2026, 3, 1)
_current = _start
while _current < _end:
    TEST_MONTHS.append(_current)
    _current += relativedelta(months=1)

# Base rates for susceptible markets (tag-based)
BASE_RATE_OVERALL = 44.3
BASE_RATE_YES = 33.2
BASE_RATE_NO = 57.9


def ch_query(client, sql: str) -> pl.DataFrame:
    r = client.query(sql)
    if not r.result_rows:
        return pl.DataFrame()
    return pl.DataFrame(
        {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
    )


def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text()


# =========================================================================== #
# Phase 1: Pull all enriched positions (1 query per month)
# =========================================================================== #


def pull_enriched_positions(client) -> pl.DataFrame:
    """Pull enriched test positions for all 14 months. One CH query per month."""
    print("\nPhase 1: Pulling enriched positions (14 months)...")
    sql_template = load_sql("s2_enriched_positions.sql")

    all_dfs: list[pl.DataFrame] = []
    for test_start in TEST_MONTHS:
        t0 = time.time()
        test_end = test_start + relativedelta(months=1)
        train_start = test_start - relativedelta(months=12)

        sql = sql_template.format(
            train_start=train_start.isoformat(),
            train_end=test_start.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            min_positions=MIN_POSITIONS,
        )
        df = ch_query(client, sql)
        if df.height > 0:
            df = df.with_columns(pl.lit(test_start.isoformat()).alias("test_month"))
            all_dfs.append(df)
        elapsed = time.time() - t0
        print(f"  {test_start}: {df.height:>7,} rows ({elapsed:.1f}s)")

    if not all_dfs:
        print("  No data collected!")
        return pl.DataFrame()

    combined = pl.concat(all_dfs)
    combined.write_parquet(str(OUTPUT_DIR / "s2_enriched_positions.parquet"))
    print(f"  Total: {combined.height:,} rows across {len(all_dfs)} months")
    print(f"  Saved to {OUTPUT_DIR / 's2_enriched_positions.parquet'}")
    return combined


# =========================================================================== #
# Phase 2: Compute volume-at-entry via batch temp table
# =========================================================================== #


def compute_volume_at_entry(client, positions: pl.DataFrame) -> pl.DataFrame:
    """Compute cumulative market volume at entry time. No look-ahead.

    For each unique (condition_id), find the earliest insider entry (first_trade),
    then sum trades_raw volume before that time.
    """
    print("\nPhase 2: Computing volume-at-entry (no look-ahead)...")

    # Get unique (condition_id, min_first_trade) pairs across all months
    # We need per-month earliest entry to avoid cross-month leakage
    entries = (
        positions.group_by(["condition_id", "test_month"])
        .agg(pl.col("first_trade").min().alias("entry_time"))
    )
    print(f"  Unique (market, month) pairs: {entries.height:,}")

    # Create temp table
    client.command("DROP TABLE IF EXISTS _tmp_s2_entry_times")
    client.command("""
        CREATE TABLE _tmp_s2_entry_times (
            condition_id String,
            entry_time DateTime64(3)
        ) ENGINE = Memory
    """)

    # Insert entries in batches
    batch_size = 10000
    condition_ids = entries["condition_id"].to_list()
    entry_times = entries["entry_time"].to_list()

    for i in range(0, len(condition_ids), batch_size):
        batch_cids = condition_ids[i:i + batch_size]
        batch_times = entry_times[i:i + batch_size]
        # Convert times to proper format
        rows = []
        for cid, et in zip(batch_cids, batch_times):
            if et is None:
                continue
            # Handle different types
            if isinstance(et, datetime):
                ts = et.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            else:
                ts = str(et)
            rows.append((cid, ts))
        if rows:
            client.insert(
                "_tmp_s2_entry_times",
                rows,
                column_names=["condition_id", "entry_time"],
            )

    count = client.query("SELECT count() FROM _tmp_s2_entry_times").result_rows[0][0]
    print(f"  Inserted {count:,} entry times into temp table")

    # Now compute volume at entry
    print("  Computing cumulative volume per market (this may take a while)...")
    t0 = time.time()
    vol_sql = load_sql("s2_volume_at_entry_batch.sql")
    vol_df = ch_query(client, vol_sql)
    elapsed = time.time() - t0
    print(f"  Volume computation took {elapsed:.1f}s, {vol_df.height:,} rows")

    # Clean up
    client.command("DROP TABLE IF EXISTS _tmp_s2_entry_times")

    if vol_df.height == 0:
        return positions.with_columns(pl.lit(0.0).alias("volume_at_entry"))

    # Join volume back to positions
    # vol_df has one row per condition_id (the min entry time per market)
    # We need to join on condition_id
    vol_df = vol_df.select([
        pl.col("condition_id"),
        pl.col("volume_at_entry").cast(pl.Float64),
    ])

    result = positions.join(vol_df, on="condition_id", how="left")
    result = result.with_columns(
        pl.col("volume_at_entry").fill_null(0.0)
    )

    # Save
    result.write_parquet(str(OUTPUT_DIR / "s2_positions_with_volume.parquet"))
    print(f"  Volume stats: mean=${result['volume_at_entry'].mean():,.0f}, "
          f"median=${result['volume_at_entry'].median():,.0f}")
    print(f"  Saved to {OUTPUT_DIR / 's2_positions_with_volume.parquet'}")

    return result


# =========================================================================== #
# Enhancement 1: Entry Price Filter (Polars slicing)
# =========================================================================== #

PRICE_FILTERS: list[tuple[str, pl.Expr | None]] = [
    ("no_filter", None),
    ("lt_0.85", pl.col("avg_yes_price") < 0.85),
    ("lt_0.75", pl.col("avg_yes_price") < 0.75),
    ("lt_0.65", pl.col("avg_yes_price") < 0.65),
    ("0.20_to_0.80", (pl.col("avg_yes_price") >= 0.20) & (pl.col("avg_yes_price") <= 0.80)),
    ("0.30_to_0.70", (pl.col("avg_yes_price") >= 0.30) & (pl.col("avg_yes_price") <= 0.70)),
]


def compute_metrics(df: pl.DataFrame) -> dict:
    """Compute aggregate metrics from a positions DataFrame."""
    n = df.height
    if n == 0:
        return {
            "n_pos": 0, "hr": 0, "yes_hr": 0, "no_hr": 0,
            "avg_pnl": 0, "total_pnl": 0, "excess_hr": 0,
            "n_traders": 0, "n_markets": 0,
        }

    correct = df["correct"].sum()
    hr = correct / n * 100

    yes_df = df.filter(pl.col("position") == "YES")
    no_df = df.filter(pl.col("position") == "NO")
    yes_hr = yes_df["correct"].sum() / yes_df.height * 100 if yes_df.height > 0 else 0
    no_hr = no_df["correct"].sum() / no_df.height * 100 if no_df.height > 0 else 0

    total_pnl = df["realized_pnl"].sum()
    avg_pnl = total_pnl / n

    return {
        "n_pos": n,
        "hr": hr,
        "yes_hr": yes_hr,
        "no_hr": no_hr,
        "yes_n": yes_df.height,
        "no_n": no_df.height,
        "avg_pnl": avg_pnl,
        "total_pnl": total_pnl,
        "excess_hr": hr - BASE_RATE_OVERALL,
        "n_traders": df["trader"].n_unique(),
        "n_markets": df["condition_id"].n_unique(),
    }


def run_entry_price_filter(strict: pl.DataFrame) -> list[dict]:
    """Enhancement 1: Entry price filtering in Polars."""
    print("\n" + "=" * 100)
    print("ENHANCEMENT 1: ENTRY PRICE FILTER (strict tier)")
    print("=" * 100)

    rows = []
    has_price = strict.filter(pl.col("avg_yes_price").is_not_null())

    for label, expr in PRICE_FILTERS:
        if expr is None:
            subset = has_price
        else:
            subset = has_price.filter(expr)

        m = compute_metrics(subset)
        m["config"] = label
        m["source"] = "E1:Price"

        # Extra stats
        if subset.height > 0 and "avg_yes_price" in subset.columns:
            m["mean_entry_price"] = subset["avg_yes_price"].mean()
            m["median_entry_price"] = subset["avg_yes_price"].median()
        else:
            m["mean_entry_price"] = 0
            m["median_entry_price"] = 0

        rows.append(m)
        print(
            f"  {label:<16} n={m['n_pos']:>8,}  HR={m['hr']:5.1f}%  "
            f"YES={m['yes_hr']:5.1f}%  NO={m['no_hr']:5.1f}%  "
            f"$/pos={m['avg_pnl']:+7.2f}  PnL=${m['total_pnl']:+,.0f}  "
            f"entry={m.get('mean_entry_price', 0):.3f}"
        )

    return rows


# =========================================================================== #
# Enhancement 2: Feature Weight Optimization (Polars + numpy)
# =========================================================================== #


def run_feature_weights(all_positions: pl.DataFrame) -> tuple[dict[str, float], list[dict]]:
    """Enhancement 2: Compute per-feature correlations with OOS correctness."""
    print("\n" + "=" * 100)
    print("ENHANCEMENT 2: FEATURE WEIGHT OPTIMIZATION")
    print("=" * 100)

    feature_cols = [
        "f1_hr_excess",
        "f2_conviction_raw",
        "f3_selectivity_raw",
        "f4_markets_per_month",
        "f5_timing_raw",
        "f6_susceptibility",
    ]
    feature_labels = [
        "F1: HR Excess",
        "F2: Conviction",
        "F3: Selectivity",
        "F4: Anomaly (mkt/mo)",
        "F5: Timing Edge",
        "F6: Susceptibility",
    ]

    # Per-month correlations for ALL tiers (train_hr >= 0.55)
    month_corrs: dict[str, list[float]] = {f: [] for f in feature_cols}
    months = all_positions["test_month"].unique().sort().to_list()

    for month in months:
        month_df = all_positions.filter(pl.col("test_month") == month)
        if month_df.height < 30:
            continue

        correct_arr = month_df["correct"].cast(pl.Float64).to_numpy()
        for feat in feature_cols:
            feat_arr = month_df[feat].cast(pl.Float64).to_numpy()
            mask = np.isfinite(feat_arr) & np.isfinite(correct_arr)
            if mask.sum() < 30:
                continue
            corr = np.corrcoef(feat_arr[mask], correct_arr[mask])[0, 1]
            if np.isfinite(corr):
                month_corrs[feat].append(corr)

    # Compute average correlation per feature
    print(f"\n  {'Feature':<25} {'Avg Corr':>9} {'Std':>7} {'N months':>9}")
    print("  " + "-" * 55)

    avg_corrs: dict[str, float] = {}
    for feat, label in zip(feature_cols, feature_labels):
        corrs = month_corrs[feat]
        if corrs:
            avg_c = float(np.mean(corrs))
            std_c = float(np.std(corrs))
            avg_corrs[feat] = avg_c
            print(f"  {label:<25} {avg_c:>+9.4f} {std_c:>7.4f} {len(corrs):>9}")
        else:
            avg_corrs[feat] = 0.0
            print(f"  {label:<25} {'N/A':>9} {'N/A':>7} {0:>9}")

    # Data-driven weights: proportional to max(corr, 0)
    positive_corrs = {k: max(v, 0.0) for k, v in avg_corrs.items()}
    total = sum(positive_corrs.values())
    if total > 0:
        data_weights = {k: v / total for k, v in positive_corrs.items()}
    else:
        data_weights = {k: 1 / 6 for k in feature_cols}
    equal_weights = {k: 1 / 6 for k in feature_cols}

    print(f"\n  {'Feature':<25} {'Equal':>7} {'Data-Driven':>12}")
    print("  " + "-" * 48)
    for feat, label in zip(feature_cols, feature_labels):
        print(f"  {label:<25} {equal_weights[feat]:>7.3f} {data_weights[feat]:>12.3f}")

    # Now test: score each trader with equal vs data-driven weights,
    # then compare HR at different score thresholds
    strict = all_positions.filter(
        (pl.col("train_hr") >= 0.75)
        & (pl.col("train_hr") < 0.99)
        & (pl.col("high_pct") >= 0.20)
    )

    # For feature weighting, we need percentile-ranked features per month
    # But since the strict tier already filters on train_hr, the feature weights
    # mainly affect HOW we rank within the strict pool
    # The real value is understanding which features carry predictive power

    print("\n  Strict tier (all data, no score thresholding):")
    m = compute_metrics(strict)
    print(
        f"    n={m['n_pos']:>8,}  HR={m['hr']:5.1f}%  $/pos={m['avg_pnl']:+7.2f}  "
        f"excess={m['excess_hr']:+.1f}pp"
    )

    # Test score-based filtering: top-half vs bottom-half by composite score
    if strict.height > 100:
        # Compute percentile rank per month for each feature
        scored_dfs = []
        for month in strict["test_month"].unique().sort().to_list():
            month_df = strict.filter(pl.col("test_month") == month)
            if month_df.height < 10:
                scored_dfs.append(month_df.with_columns(
                    pl.lit(0.5).alias("equal_score"),
                    pl.lit(0.5).alias("data_score"),
                ))
                continue

            # Rank each feature to percentile [0,1]
            ranked = month_df
            eq_score = pl.lit(0.0)
            dd_score = pl.lit(0.0)
            for feat in feature_cols:
                rank_col = f"_rank_{feat}"
                ranked = ranked.with_columns(
                    pl.col(feat).rank().over(pl.lit(1)).alias(rank_col)
                )
                n = ranked.height
                eq_score = eq_score + pl.col(rank_col) / n * equal_weights[feat]
                dd_score = dd_score + pl.col(rank_col) / n * data_weights[feat]

            ranked = ranked.with_columns(
                eq_score.alias("equal_score"),
                dd_score.alias("data_score"),
            )
            # Drop temp rank columns
            ranked = ranked.drop([f"_rank_{f}" for f in feature_cols])
            scored_dfs.append(ranked)

        scored = pl.concat(scored_dfs)

        # Compare top-half vs bottom-half for each scoring method
        print("\n  Score-based filtering (top-half vs bottom-half):")
        for score_col, label in [("equal_score", "Equal-Weight"), ("data_score", "Data-Driven")]:
            median_score = scored[score_col].median()
            top = scored.filter(pl.col(score_col) >= median_score)
            bot = scored.filter(pl.col(score_col) < median_score)
            m_top = compute_metrics(top)
            m_bot = compute_metrics(bot)
            print(
                f"    {label:<14} Top-half: n={m_top['n_pos']:>7,} HR={m_top['hr']:5.1f}% "
                f"$/pos={m_top['avg_pnl']:+7.2f} | "
                f"Bot-half: n={m_bot['n_pos']:>7,} HR={m_bot['hr']:5.1f}% "
                f"$/pos={m_bot['avg_pnl']:+7.2f}"
            )

            # Top-third
            p67 = scored[score_col].quantile(0.67)
            top_third = scored.filter(pl.col(score_col) >= p67)
            m_t3 = compute_metrics(top_third)
            print(
                f"    {label:<14} Top-33%:  n={m_t3['n_pos']:>7,} HR={m_t3['hr']:5.1f}% "
                f"$/pos={m_t3['avg_pnl']:+7.2f} excess={m_t3['excess_hr']:+.1f}pp"
            )

    rows = []
    m["config"] = "strict_all"
    m["source"] = "E2:Features"
    rows.append(m)

    return data_weights, rows


# =========================================================================== #
# Enhancement 3: Market Volume at Entry Time (Polars slicing)
# =========================================================================== #

VOLUME_FILTERS: list[tuple[str, pl.Expr | None]] = [
    ("no_filter", None),
    ("vol_ge_500", pl.col("volume_at_entry") >= 500),
    ("vol_ge_1000", pl.col("volume_at_entry") >= 1000),
    ("vol_ge_5000", pl.col("volume_at_entry") >= 5000),
    ("vol_ge_10000", pl.col("volume_at_entry") >= 10000),
]


def run_volume_at_entry(strict: pl.DataFrame) -> list[dict]:
    """Enhancement 3: Volume-at-entry filtering in Polars."""
    print("\n" + "=" * 100)
    print("ENHANCEMENT 3: MARKET VOLUME AT ENTRY TIME (NO LOOK-AHEAD)")
    print("=" * 100)

    rows = []
    for label, expr in VOLUME_FILTERS:
        if expr is None:
            subset = strict
        else:
            subset = strict.filter(expr)

        m = compute_metrics(subset)
        m["config"] = label
        m["source"] = "E3:Volume"

        if subset.height > 0 and "volume_at_entry" in subset.columns:
            m["avg_vol_at_entry"] = subset["volume_at_entry"].mean()
            m["median_vol_at_entry"] = subset["volume_at_entry"].median()
        else:
            m["avg_vol_at_entry"] = 0
            m["median_vol_at_entry"] = 0

        rows.append(m)
        print(
            f"  {label:<16} n={m['n_pos']:>8,}  HR={m['hr']:5.1f}%  "
            f"YES={m['yes_hr']:5.1f}%  NO={m['no_hr']:5.1f}%  "
            f"$/pos={m['avg_pnl']:+7.2f}  PnL=${m['total_pnl']:+,.0f}  "
            f"vol_at_entry=${m.get('avg_vol_at_entry', 0):,.0f}"
        )

    return rows


# =========================================================================== #
# Combined: Best of each enhancement
# =========================================================================== #

COMBINED_COMBOS: list[tuple[str, pl.Expr | None, pl.Expr | None]] = [
    ("baseline", None, None),
    ("price_lt_0.85", pl.col("avg_yes_price") < 0.85, None),
    ("price_lt_0.75", pl.col("avg_yes_price") < 0.75, None),
    ("vol_ge_1000", None, pl.col("volume_at_entry") >= 1000),
    ("vol_ge_5000", None, pl.col("volume_at_entry") >= 5000),
    ("price_lt_0.85+vol_ge_1000",
     pl.col("avg_yes_price") < 0.85,
     pl.col("volume_at_entry") >= 1000),
    ("price_lt_0.75+vol_ge_1000",
     pl.col("avg_yes_price") < 0.75,
     pl.col("volume_at_entry") >= 1000),
    ("price_lt_0.85+vol_ge_5000",
     pl.col("avg_yes_price") < 0.85,
     pl.col("volume_at_entry") >= 5000),
    ("price_0.20_0.80+vol_ge_1000",
     (pl.col("avg_yes_price") >= 0.20) & (pl.col("avg_yes_price") <= 0.80),
     pl.col("volume_at_entry") >= 1000),
]


def run_combined(strict: pl.DataFrame) -> list[dict]:
    """Run all combo filters."""
    print("\n" + "=" * 100)
    print("COMBINED ENHANCEMENTS: ENTRY PRICE + VOLUME AT ENTRY")
    print("=" * 100)

    has_price = strict.filter(pl.col("avg_yes_price").is_not_null())
    rows = []

    for label, price_expr, vol_expr in COMBINED_COMBOS:
        subset = has_price
        if price_expr is not None:
            subset = subset.filter(price_expr)
        if vol_expr is not None:
            subset = subset.filter(vol_expr)

        m = compute_metrics(subset)
        m["config"] = label
        m["source"] = "Combined"
        rows.append(m)

        print(
            f"  {label:<35} n={m['n_pos']:>8,}  HR={m['hr']:5.1f}%  "
            f"$/pos={m['avg_pnl']:+7.2f}  PnL=${m['total_pnl']:+,.0f}  "
            f"excess={m['excess_hr']:+.1f}pp"
        )

    return rows


# =========================================================================== #
# Summary Table
# =========================================================================== #


def print_comparison_table(all_rows: list[dict]) -> None:
    """Print final comparison across all enhancements."""
    print("\n" + "=" * 120)
    print("FINAL COMPARISON TABLE: BASELINE vs ENHANCEMENTS (STRICT TIER)")
    print("=" * 120)
    print(f"  > [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.")
    print(f"  > Base rates (susceptible): Overall {BASE_RATE_OVERALL}%, "
          f"YES {BASE_RATE_YES}%, NO {BASE_RATE_NO}%\n")

    print(
        f"  {'Source':<12} {'Config':<36} {'N Pos':>8} {'HR':>7} "
        f"{'+Base':>7} {'YES HR':>8} {'NO HR':>8} {'$/pos':>8} {'Total PnL':>12}"
    )
    print("  " + "-" * 110)

    # Sort by source then by PnL descending
    for r in sorted(all_rows, key=lambda x: (x.get("source", ""), -x.get("total_pnl", 0))):
        print(
            f"  {r.get('source', '?'):<12} {r['config']:<36} {r['n_pos']:>8,} {r['hr']:>6.1f}% "
            f"{r['excess_hr']:>+6.1f}pp {r['yes_hr']:>7.1f}% {r['no_hr']:>7.1f}% "
            f"{r['avg_pnl']:>+8.2f} {r['total_pnl']:>+12,.0f}"
        )

    # Find best by total PnL
    valid = [r for r in all_rows if r["n_pos"] > 0]
    if not valid:
        return

    print("\n  --- RECOMMENDATIONS ---")

    # Best total PnL
    best_pnl = max(valid, key=lambda x: x["total_pnl"])
    print(f"\n  Best Total PnL: {best_pnl['source']} / {best_pnl['config']}")
    print(f"    ${best_pnl['total_pnl']:+,.0f} | HR {best_pnl['hr']:.1f}% | "
          f"n={best_pnl['n_pos']:,}")

    # Best PnL/pos (with min 50K positions)
    viable = [r for r in valid if r["n_pos"] >= 50_000]
    if viable:
        best_per_pos = max(viable, key=lambda x: x["avg_pnl"])
        print(f"\n  Best $/pos (n>=50K): {best_per_pos['source']} / {best_per_pos['config']}")
        print(f"    ${best_per_pos['avg_pnl']:+.2f}/pos | HR {best_per_pos['hr']:.1f}% | "
              f"n={best_per_pos['n_pos']:,}")

    # Best HR (with min 50K positions)
    if viable:
        best_hr = max(viable, key=lambda x: x["hr"])
        print(f"\n  Best HR (n>=50K): {best_hr['source']} / {best_hr['config']}")
        print(f"    HR {best_hr['hr']:.1f}% ({best_hr['excess_hr']:+.1f}pp excess) | "
              f"n={best_hr['n_pos']:,}")

    # Recommended for tick-by-tick
    # Best balance: maximize excess_hr * avg_pnl * sqrt(n_pos) as "edge quality"
    for r in valid:
        r["edge_quality"] = max(r["excess_hr"], 0) * max(r["avg_pnl"], 0) * (r["n_pos"] ** 0.5)
    best_eq = max(valid, key=lambda x: x.get("edge_quality", 0))
    print(f"\n  Best Edge Quality: {best_eq['source']} / {best_eq['config']}")
    print(f"    HR {best_eq['hr']:.1f}% ({best_eq['excess_hr']:+.1f}pp) | "
          f"$/pos {best_eq['avg_pnl']:+.2f} | n={best_eq['n_pos']:,}")
    realistic_low = best_eq["hr"] - 40
    realistic_high = best_eq["hr"] - 20
    print(f"    Realistic tick-by-tick range: {realistic_low:.1f}% to {realistic_high:.1f}%")


# =========================================================================== #
# Main
# =========================================================================== #


def main():
    t_start = time.time()
    print("S2 Insider Copy -- Three Enhancements Walk-Forward Analysis")
    print(f"Test months: {TEST_MONTHS[0]} to {TEST_MONTHS[-1]} ({len(TEST_MONTHS)} months)")
    print(f"ClickHouse: {CH_HOST}:{CH_PORT}/{CH_DB}")

    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    # Phase 1: Pull all enriched positions
    positions = pull_enriched_positions(client)
    if positions.height == 0:
        print("FATAL: No positions collected. Exiting.")
        sys.exit(1)

    # Phase 2: Compute volume-at-entry
    positions = compute_volume_at_entry(client, positions)

    # Filter to strict tier
    strict = positions.filter(
        (pl.col("train_hr") >= 0.75)
        & (pl.col("train_hr") < 0.99)
        & (pl.col("high_pct") >= 0.20)
    )
    print(f"\nStrict tier: {strict.height:,} positions "
          f"({strict['trader'].n_unique():,} traders, "
          f"{strict['condition_id'].n_unique():,} markets)")

    # Run all enhancements
    all_rows: list[dict] = []

    price_rows = run_entry_price_filter(strict)
    all_rows.extend(price_rows)

    data_weights, feat_rows = run_feature_weights(positions)
    all_rows.extend(feat_rows)

    vol_rows = run_volume_at_entry(strict)
    all_rows.extend(vol_rows)

    combo_rows = run_combined(strict)
    all_rows.extend(combo_rows)

    # Summary
    print_comparison_table(all_rows)

    elapsed = time.time() - t_start
    print(f"\n\nTotal runtime: {elapsed:.0f}s ({elapsed / 60:.1f}min)")
    print(f"\nOutput files in {OUTPUT_DIR}:")
    for p in sorted(OUTPUT_DIR.glob("s2_*")):
        size = p.stat().st_size
        print(f"  {p.name:<45s} {size / 1024:>8.1f} KB")


if __name__ == "__main__":
    main()
