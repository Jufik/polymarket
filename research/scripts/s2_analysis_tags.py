"""S2 Insider Copy: Consensus and category analysis using tag-based susceptibility.

Runs across all OOS months (2025-01 to 2026-02), strict tier only.
Reports consensus bucket HR/PnL and category breakdown.
"""

import clickhouse_connect
import polars as pl
from pathlib import Path
from datetime import date
from dateutil.relativedelta import relativedelta

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
MIN_POSITIONS = 20

SQL_DIR = Path(__file__).parent.parent / "knowledge" / "queries"
CONSENSUS_SQL = (SQL_DIR / "s2_consensus_tags.sql").read_text()
CATEGORY_SQL = (SQL_DIR / "s2_category_tags.sql").read_text()

TEST_MONTHS = []
start = date(2025, 1, 1)
end = date(2026, 3, 1)
current = start
while current < end:
    TEST_MONTHS.append(current)
    current += relativedelta(months=1)

# Strict tier params
STRICT_PARAMS = {
    "min_positions": 20,
    "min_train_hr": 0.75,
    "max_train_hr": 0.99,
    "min_high_pct": 0.20,
}


def ch_query(client, sql: str) -> pl.DataFrame:
    r = client.query(sql)
    if not r.result_rows:
        return pl.DataFrame()
    return pl.DataFrame(
        {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
    )


def run_consensus_analysis(client):
    print("=" * 80)
    print("CONSENSUS ANALYSIS (strict tier, tag-based)")
    print("=" * 80)

    all_results = []
    for test_start in TEST_MONTHS:
        test_end = test_start + relativedelta(months=1)
        train_start = test_start - relativedelta(months=12)
        train_end = test_start

        sql = CONSENSUS_SQL.format(
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            **STRICT_PARAMS,
        )
        df = ch_query(client, sql)
        if df.height > 0:
            df = df.with_columns(pl.lit(test_start.isoformat()).alias("test_month"))
            all_results.append(df)

    if not all_results:
        print("No results.")
        return

    combined = pl.concat(all_results)

    # Aggregate across months
    agg = (
        combined.group_by("consensus_bucket")
        .agg(
            pl.col("n_market_sides").sum().alias("total_sides"),
            (pl.col("hr") / 100.0 * pl.col("n_market_sides")).sum().alias("weighted_correct"),
            (pl.col("avg_pnl_per_pos") * pl.col("n_market_sides")).sum().alias("weighted_pnl"),
        )
        .with_columns(
            (pl.col("weighted_correct") / pl.col("total_sides") * 100)
            .round(1)
            .alias("agg_hr"),
            (pl.col("weighted_pnl") / pl.col("total_sides")).round(2).alias("agg_avg_pnl"),
        )
        .sort("consensus_bucket")
    )

    print(
        f"\n{'Bucket':<10} {'Sides':>8} {'HR':>7} {'Avg $/pos':>10}"
    )
    print("-" * 40)
    for row in agg.iter_rows(named=True):
        print(
            f"{row['consensus_bucket']:<10} {row['total_sides']:>8,} "
            f"{row['agg_hr']:>6.1f}% {row['agg_avg_pnl']:>+10.2f}"
        )

    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    combined.write_parquet(str(output_dir / "s2_consensus_tags.parquet"))
    return combined


def run_category_analysis(client):
    print("\n" + "=" * 80)
    print("CATEGORY ANALYSIS (strict tier, tag-based)")
    print("=" * 80)

    all_results = []
    for test_start in TEST_MONTHS:
        test_end = test_start + relativedelta(months=1)
        train_start = test_start - relativedelta(months=12)
        train_end = test_start

        sql = CATEGORY_SQL.format(
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            **STRICT_PARAMS,
        )
        df = ch_query(client, sql)
        if df.height > 0:
            df = df.with_columns(pl.lit(test_start.isoformat()).alias("test_month"))
            all_results.append(df)

    if not all_results:
        print("No results.")
        return

    combined = pl.concat(all_results)

    # Aggregate
    agg = (
        combined.group_by("category")
        .agg(
            pl.col("n_positions").sum().alias("total_pos"),
            (pl.col("hr") / 100.0 * pl.col("n_positions")).sum().alias("weighted_correct"),
            (pl.col("yes_hr") / 100.0 * pl.col("n_positions")).sum().alias("yes_wc"),
            (pl.col("no_hr") / 100.0 * pl.col("n_positions")).sum().alias("no_wc"),
            pl.col("total_pnl").sum().alias("total_pnl"),
            pl.col("n_markets").mean().alias("avg_markets_per_month"),
        )
        .with_columns(
            (pl.col("weighted_correct") / pl.col("total_pos") * 100)
            .round(1)
            .alias("agg_hr"),
            (pl.col("total_pnl") / pl.col("total_pos")).round(2).alias("avg_pnl_per_pos"),
        )
        .sort("total_pos", descending=True)
    )

    print(
        f"\n{'Category':<12} {'Total Pos':>10} {'HR':>7} {'Avg $/pos':>10} "
        f"{'Total PnL':>12} {'Markets/mo':>11}"
    )
    print("-" * 70)
    for row in agg.iter_rows(named=True):
        print(
            f"{row['category']:<12} {row['total_pos']:>10,} {row['agg_hr']:>6.1f}% "
            f"{row['avg_pnl_per_pos']:>+10.2f} {row['total_pnl']:>+12,.0f} "
            f"{row['avg_markets_per_month']:>11,.0f}"
        )

    output_dir = Path(__file__).parent.parent / "output"
    combined.write_parquet(str(output_dir / "s2_category_tags.parquet"))
    return combined


def run_direction_analysis(client):
    """Direction breakdown (YES vs NO) for strict tier from the walk-forward data."""
    print("\n" + "=" * 80)
    print("DIRECTION ANALYSIS (strict tier, tag-based)")
    print("=" * 80)

    output_dir = Path(__file__).parent.parent / "output"
    monthly = pl.read_parquet(str(output_dir / "s2_walkforward_tags_monthly.parquet"))

    strict = monthly.filter(pl.col("tier") == "strict")

    total_yes = strict["yes_n"].sum()
    total_no = strict["no_n"].sum()
    yes_correct = (strict["yes_hr"] / 100.0 * strict["yes_n"]).sum()
    no_correct = (strict["no_hr"] / 100.0 * strict["no_n"]).sum()
    total_pnl = strict["total_pnl"].sum()

    yes_hr = yes_correct / total_yes * 100 if total_yes > 0 else 0
    no_hr = no_correct / total_no * 100 if total_no > 0 else 0

    print(f"\nDirection  {'Count':>10} {'HR':>7} {'Excess':>8} {'Base':>6}")
    print("-" * 45)
    print(
        f"YES        {total_yes:>10,} {yes_hr:>6.1f}% {yes_hr - 33.2:>+7.1f}pp  33.2%"
    )
    print(
        f"NO         {total_no:>10,} {no_hr:>6.1f}% {no_hr - 57.9:>+7.1f}pp  57.9%"
    )
    print(
        f"Combined   {total_yes + total_no:>10,} "
        f"{(yes_correct + no_correct) / (total_yes + total_no) * 100:>6.1f}% "
        f"{(yes_correct + no_correct) / (total_yes + total_no) * 100 - 44.3:>+7.1f}pp  44.3%"
    )
    print(f"\nTotal PnL: ${total_pnl:+,.0f}")


if __name__ == "__main__":
    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    run_direction_analysis(client)
    run_consensus_analysis(client)
    run_category_analysis(client)
