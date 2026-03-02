"""S2 Insider Copy: Walk-forward vectorized upper bounds using tag-based susceptibility.

Runs monthly walk-forward: train 12mo, test 1mo, rolling from 2025-01 to 2026-02.
Outputs per-tier metrics aggregated across all OOS months.
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

# Load SQL template
SQL_PATH = Path(__file__).parent.parent / "knowledge" / "queries" / "s2_walkforward_tags.sql"
SQL_TEMPLATE = SQL_PATH.read_text()

# Walk-forward windows: test months from 2025-01 to 2026-02 (14 months)
TEST_MONTHS = []
start = date(2025, 1, 1)
end = date(2026, 3, 1)
current = start
while current < end:
    TEST_MONTHS.append(current)
    current += relativedelta(months=1)


def ch_query(client, sql: str) -> pl.DataFrame:
    r = client.query(sql)
    if not r.result_rows:
        return pl.DataFrame()
    return pl.DataFrame(
        {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
    )


def run_walkforward():
    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)

    all_results = []

    for test_start in TEST_MONTHS:
        test_end = test_start + relativedelta(months=1)
        train_start = test_start - relativedelta(months=12)
        train_end = test_start

        print(f"  Train [{train_start} .. {train_end}) -> Test [{test_start} .. {test_end})")

        sql = SQL_TEMPLATE.format(
            train_start=train_start.isoformat(),
            train_end=train_end.isoformat(),
            test_start=test_start.isoformat(),
            test_end=test_end.isoformat(),
            min_positions=MIN_POSITIONS,
        )

        df = ch_query(client, sql)
        if df.height > 0:
            all_results.append(df)
            for row in df.iter_rows(named=True):
                print(
                    f"    {row['tier']:>8s}: n={row['n_positions']:>7,}  "
                    f"HR={row['hr']:5.1f}%  YES={row['yes_hr']:5.1f}%  NO={row['no_hr']:5.1f}%  "
                    f"avg$={row['avg_pnl']:+7.2f}  traders={row['n_traders']:>5,}"
                )
        else:
            print("    (no data)")

    if not all_results:
        print("No results collected.")
        return

    combined = pl.concat(all_results)

    # Aggregate across all months per tier
    print("\n" + "=" * 90)
    print("AGGREGATED WALK-FORWARD RESULTS (tag-based susceptibility)")
    print("=" * 90)

    agg = (
        combined.filter(pl.col("tier") != "bot")
        .group_by("tier")
        .agg(
            pl.col("n_positions").sum().alias("total_pos"),
            # Weighted HR: sum(correct) / sum(total) = sum(hr/100 * n) / sum(n)
            (pl.col("hr") / 100.0 * pl.col("n_positions")).sum().alias("weighted_correct"),
            (
                pl.col("yes_hr") / 100.0 * pl.col("yes_n")
            ).sum().alias("yes_weighted_correct"),
            pl.col("yes_n").sum().alias("total_yes"),
            (
                pl.col("no_hr") / 100.0 * pl.col("no_n")
            ).sum().alias("no_weighted_correct"),
            pl.col("no_n").sum().alias("total_no"),
            pl.col("total_pnl").sum().alias("total_pnl"),
            pl.col("avg_volume").mean().alias("avg_volume"),
            pl.col("n_traders").mean().alias("avg_traders_per_month"),
            pl.col("n_markets").mean().alias("avg_markets_per_month"),
        )
        .with_columns(
            (pl.col("weighted_correct") / pl.col("total_pos") * 100).round(1).alias("agg_hr"),
            (pl.col("yes_weighted_correct") / pl.col("total_yes") * 100)
            .round(1)
            .alias("agg_yes_hr"),
            (pl.col("no_weighted_correct") / pl.col("total_no") * 100)
            .round(1)
            .alias("agg_no_hr"),
            (pl.col("total_pnl") / pl.col("total_pos")).round(2).alias("avg_pnl_per_pos"),
        )
        .sort("tier")
    )

    print(f"\n{'Tier':<10} {'Total Pos':>10} {'HR':>7} {'YES HR':>8} {'NO HR':>8} "
          f"{'Avg $/pos':>10} {'Total PnL':>12} {'Traders/mo':>11}")
    print("-" * 90)
    for row in agg.iter_rows(named=True):
        print(
            f"{row['tier']:<10} {row['total_pos']:>10,} {row['agg_hr']:>6.1f}% "
            f"{row['agg_yes_hr']:>7.1f}% {row['agg_no_hr']:>7.1f}% "
            f"{row['avg_pnl_per_pos']:>+10.2f} {row['total_pnl']:>+12,.0f} "
            f"{row['avg_traders_per_month']:>11,.0f}"
        )

    # Base rate reference
    print(f"\n{'Base rates (susceptible, 2025-01 to 2026-02):'}")
    print(f"  Overall: 44.3%, YES: 33.2%, NO: 57.9%")

    # Excess HR
    print(f"\nExcess HR over susceptible base rate:")
    for row in agg.iter_rows(named=True):
        excess = row["agg_hr"] - 44.3
        yes_excess = row["agg_yes_hr"] - 33.2
        no_excess = row["agg_no_hr"] - 57.9
        print(
            f"  {row['tier']:<10}: overall {excess:+.1f}pp  YES {yes_excess:+.1f}pp  "
            f"NO {no_excess:+.1f}pp"
        )

    # Save results
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    combined.write_parquet(str(output_dir / "s2_walkforward_tags_monthly.parquet"))
    agg.write_parquet(str(output_dir / "s2_walkforward_tags_agg.parquet"))
    print(f"\nSaved to {output_dir / 's2_walkforward_tags_monthly.parquet'}")
    print(f"Saved to {output_dir / 's2_walkforward_tags_agg.parquet'}")

    # Return for further analysis
    return combined, agg


if __name__ == "__main__":
    run_walkforward()
