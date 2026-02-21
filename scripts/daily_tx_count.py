"""Count daily transactions from compact parquet files since early 2026."""

import polars as pl
from datetime import datetime, timezone

# Scan all compact parquet files lazily
lf = pl.scan_parquet("data/compact/compact_*.parquet")

# Build a timezone-aware literal that matches the ns/UTC column
cutoff = pl.lit(
    datetime(2026, 1, 1, tzinfo=timezone.utc)
).cast(pl.Datetime("ns", "UTC"))

# Filter to 2026 data, group by date, count transactions per day
result = (
    lf
    .filter(pl.col("timestamp") >= cutoff)
    .with_columns(pl.col("timestamp").dt.date().alias("date"))
    .group_by("date")
    .agg(
        pl.len().alias("total_rows"),
    )
    .sort("date")
    .collect()
)

sep = "=" * 60
dash = "-" * 14

print("Daily transaction counts since 2026-01-01")
print("(raw rows from compact parquet files, includes ~40.5% taker duplicates)")
print(sep)
print(f"{'Date':<14} {'Transactions':>14}")
print(f"{dash} {dash}")

for row in result.iter_rows(named=True):
    print(f"{row['date']}   {row['total_rows']:>12,}")

print(f"{dash} {dash}")
print(f"{'TOTAL':<14} {result['total_rows'].sum():>12,}")
print(f"{'Days':<14} {len(result):>12}")
print(f"{'Avg/day':<14} {result['total_rows'].mean():>12,.0f}")
print()

# Also show the date range in the full dataset
date_range = (
    lf
    .select(
        pl.col("timestamp").min().alias("min_ts"),
        pl.col("timestamp").max().alias("max_ts"),
    )
    .collect()
)
print(f"Full dataset range: {date_range['min_ts'][0]} to {date_range['max_ts'][0]}")
print()

# Deduplicated estimate (approx 59.5% of raw rows are unique trades)
dedup_factor = 0.595
total_raw = result["total_rows"].sum()
print(f"Estimated unique trades (after ~40.5% taker dedup): ~{int(total_raw * dedup_factor):,}")
