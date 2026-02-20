"""Research: what predicts fast resolution in S2 "Will" binary markets?

If we can filter to fast-resolving markets, capital recycles faster → more bets/month.
Current median lockup: 6.5 days. Target: <3 days.

Dimensions:
1. Category (crypto daily vs politics monthly)
2. YES price level (deeper longshot = faster?)
3. Question keywords ("today", "this week", "before", "by")
4. Market volume / liquidity
5. Market duration (end_date - creation)
6. Time-of-week patterns
7. Whether neg_risk affects resolution speed

Usage:
    PYTHONPATH=. uv run python scripts/s2_rotation_research.py
"""

from __future__ import annotations

from datetime import datetime

import polars as pl


def main():
    # Load data
    markets = pl.read_parquet("data/metadata/markets.parquet").select(
        "condition_id", "question", "category", "neg_risk", "end_date_iso"
    )
    resolved = pl.read_parquet("data/derived/markets_resolved.parquet")
    for c in ["resolved_at"]:
        d = resolved.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            resolved = resolved.with_columns(pl.col(c).dt.replace_time_zone(None))

    pnl = pl.read_parquet("data/derived/trader_market_pnl.parquet")
    for c in ["first_trade", "last_trade"]:
        d = pnl.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            pnl = pnl.with_columns(pl.col(c).dt.replace_time_zone(None))

    # Market-level aggregates from trade data
    market_stats = pnl.group_by("condition_id").agg(
        pl.col("wavg_yes_entry_price").median().alias("median_yes_price"),
        pl.col("first_trade").min().alias("earliest_trade"),
        pl.col("last_trade").max().alias("latest_trade"),
        pl.col("market_volume").sum().alias("total_market_volume"),
        pl.col("trader").n_unique().alias("n_traders"),
    )

    # Join everything
    df = resolved.join(markets, on="condition_id", how="inner")
    df = df.join(market_stats, on="condition_id", how="inner")

    # Filter: "Will" binary, YES 10-50%, resolved, 2025+
    df = df.filter(
        pl.col("question").str.to_lowercase().str.starts_with("will ")
        & (pl.col("resolution_value") == 1)
        & (pl.col("median_yes_price") >= 0.10)
        & (pl.col("median_yes_price") <= 0.50)
        & (pl.col("resolved_at") >= datetime(2025, 1, 1))
    )

    # Compute resolution duration (from earliest trade to resolution)
    df = df.with_columns(
        (pl.col("resolved_at") - pl.col("earliest_trade")).dt.total_hours().alias("lockup_hours"),
        (pl.col("resolved_at") - pl.col("earliest_trade")).dt.total_days().alias("lockup_days"),
        (~pl.col("yes_won")).alias("no_won"),
        (1.0 - pl.col("median_yes_price")).alias("no_entry_price"),
    )

    # Filter out negative lockup (data issues)
    df = df.filter(pl.col("lockup_hours") >= 0)

    print(f"S2 eligible markets (2025+): {df.height:,}")
    print(f"Median lockup: {float(df['lockup_days'].median()):.1f} days")
    print(f"Mean lockup: {float(df['lockup_days'].mean()):.1f} days")
    print(f"NO win rate: {float(df['no_won'].mean())*100:.1f}%")
    print()

    # ===================================================================
    # 1. LOCKUP DISTRIBUTION
    # ===================================================================
    print(f"{'=' * 70}")
    print("  1. LOCKUP DISTRIBUTION")
    print(f"{'=' * 70}\n")

    buckets = [
        ("< 1 day", 0, 1),
        ("1-2 days", 1, 2),
        ("2-3 days", 2, 3),
        ("3-7 days", 3, 7),
        ("7-14 days", 7, 14),
        ("14-30 days", 14, 30),
        ("30+ days", 30, 9999),
    ]

    print(f"{'Lockup':<15} {'N':>7} {'%':>7} {'NO HR':>7} {'Avg PnL/$100':>13} {'Cum%':>7}")
    print("-" * 60)

    cum_pct = 0.0
    for label, lo, hi in buckets:
        sub = df.filter(
            (pl.col("lockup_days") >= lo) & (pl.col("lockup_days") < hi)
        )
        n = sub.height
        pct = n / df.height * 100
        cum_pct += pct
        hr = float(sub["no_won"].mean()) * 100 if n > 0 else 0

        # PnL per $100 bet on NO
        if n > 0:
            pnl_sum = 0.0
            for row in sub.iter_rows(named=True):
                if row["no_won"]:
                    pnl_sum += 100 * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
                else:
                    pnl_sum -= 100
            avg_pnl = pnl_sum / n
        else:
            avg_pnl = 0

        print(f"{label:<15} {n:>7,} {pct:>6.1f}% {hr:>6.1f}% ${avg_pnl:>11.2f} {cum_pct:>6.1f}%")

    # ===================================================================
    # 2. CATEGORY × LOCKUP
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  2. CATEGORY: Which categories resolve fastest?")
    print(f"{'=' * 70}\n")

    cat_stats = df.group_by("category").agg(
        pl.len().alias("n"),
        pl.col("lockup_days").median().alias("med_lockup"),
        pl.col("lockup_days").mean().alias("mean_lockup"),
        pl.col("no_won").mean().alias("no_hr"),
        pl.col("lockup_days").quantile(0.25).alias("p25_lockup"),
    ).filter(pl.col("n") >= 20).sort("med_lockup")

    print(f"{'Category':<25} {'N':>7} {'Med d':>7} {'Mean d':>8} {'P25 d':>7} {'NO HR':>7}")
    print("-" * 65)
    for row in cat_stats.iter_rows(named=True):
        print(f"{str(row['category'])[:24]:<25} {row['n']:>7,} {row['med_lockup']:>6.1f} "
              f"{row['mean_lockup']:>7.1f} {row['p25_lockup']:>6.1f} {row['no_hr']*100:>6.1f}%")

    # ===================================================================
    # 3. QUESTION KEYWORDS → LOCKUP
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  3. QUESTION KEYWORDS: Time-related words predict fast resolution")
    print(f"{'=' * 70}\n")

    keywords = [
        ("today", "today"),
        ("tonight", "tonight"),
        ("this week", "this week"),
        ("tomorrow", "tomorrow"),
        ("by end of", "by end of"),
        ("before", "before"),
        ("this month", "this month"),
        ("in 2025", "in 2025"),
        ("in 2026", "in 2026"),
        ("ever", "ever"),
        ("by", " by "),
        ("reach", "reach"),
        ("hit", " hit "),
        ("drop", "drop"),
        ("above", "above"),
        ("below", "below"),
    ]

    q_lower = df.with_columns(pl.col("question").str.to_lowercase().alias("q_lower"))

    print(f"{'Keyword':<20} {'N':>7} {'Med d':>7} {'Mean d':>8} {'NO HR':>7} {'PnL/$100':>10}")
    print("-" * 62)

    for label, kw in keywords:
        sub = q_lower.filter(pl.col("q_lower").str.contains(kw))
        n = sub.height
        if n < 10:
            continue
        med_d = float(sub["lockup_days"].median())
        mean_d = float(sub["lockup_days"].mean())
        hr = float(sub["no_won"].mean()) * 100

        pnl_sum = 0.0
        for row in sub.iter_rows(named=True):
            if row["no_won"]:
                pnl_sum += 100 * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
            else:
                pnl_sum -= 100
        avg_pnl = pnl_sum / n

        print(f"{label:<20} {n:>7,} {med_d:>6.1f} {mean_d:>7.1f} {hr:>6.1f}% ${avg_pnl:>8.2f}")

    # ===================================================================
    # 4. YES PRICE × LOCKUP (does deeper longshot = faster/slower?)
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  4. YES PRICE LEVEL: Does deeper longshot affect lockup?")
    print(f"{'=' * 70}\n")

    price_bins = [
        ("YES 10-15%", 0.10, 0.15),
        ("YES 15-20%", 0.15, 0.20),
        ("YES 20-25%", 0.20, 0.25),
        ("YES 25-30%", 0.25, 0.30),
        ("YES 30-35%", 0.30, 0.35),
        ("YES 35-40%", 0.35, 0.40),
        ("YES 40-45%", 0.40, 0.45),
        ("YES 45-50%", 0.45, 0.50),
    ]

    print(f"{'Bin':<15} {'N':>7} {'Med d':>7} {'NO HR':>7} {'PnL/$100':>10} {'PnL/day':>9}")
    print("-" * 58)

    for label, lo, hi in price_bins:
        sub = df.filter(
            (pl.col("median_yes_price") >= lo) & (pl.col("median_yes_price") < hi)
        )
        n = sub.height
        if n < 10:
            continue
        med_d = float(sub["lockup_days"].median())
        hr = float(sub["no_won"].mean()) * 100

        pnl_sum = 0.0
        for row in sub.iter_rows(named=True):
            if row["no_won"]:
                pnl_sum += 100 * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
            else:
                pnl_sum -= 100
        avg_pnl = pnl_sum / n
        pnl_per_day = avg_pnl / max(med_d, 0.5)

        print(f"{label:<15} {n:>7,} {med_d:>6.1f} {hr:>6.1f}% ${avg_pnl:>8.2f} ${pnl_per_day:>7.2f}")

    # ===================================================================
    # 5. VOLUME × LOCKUP
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  5. MARKET VOLUME: Does liquidity affect lockup or edge?")
    print(f"{'=' * 70}\n")

    vol_bins = [
        ("< $1K", 0, 1_000),
        ("$1K-$5K", 1_000, 5_000),
        ("$5K-$25K", 5_000, 25_000),
        ("$25K-$100K", 25_000, 100_000),
        ("$100K+", 100_000, 1e12),
    ]

    print(f"{'Volume':<15} {'N':>7} {'Med d':>7} {'NO HR':>7} {'PnL/$100':>10} {'PnL/day':>9}")
    print("-" * 58)

    for label, lo, hi in vol_bins:
        sub = df.filter(
            (pl.col("total_market_volume") >= lo) & (pl.col("total_market_volume") < hi)
        )
        n = sub.height
        if n < 10:
            continue
        med_d = float(sub["lockup_days"].median())
        hr = float(sub["no_won"].mean()) * 100

        pnl_sum = 0.0
        for row in sub.iter_rows(named=True):
            if row["no_won"]:
                pnl_sum += 100 * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
            else:
                pnl_sum -= 100
        avg_pnl = pnl_sum / n
        pnl_per_day = avg_pnl / max(med_d, 0.5)

        print(f"{label:<15} {n:>7,} {med_d:>6.1f} {hr:>6.1f}% ${avg_pnl:>8.2f} ${pnl_per_day:>7.2f}")

    # ===================================================================
    # 6. FAST-ROTATION FILTER: Combine best dimensions
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  6. FAST-ROTATION FILTER: What if we only take <3 day markets?")
    print(f"{'=' * 70}\n")

    fast = df.filter(pl.col("lockup_days") < 3)
    slow = df.filter(pl.col("lockup_days") >= 3)

    for label, sub in [("Fast (<3 days)", fast), ("Slow (3+ days)", slow), ("All", df)]:
        n = sub.height
        if n == 0:
            continue
        med_d = float(sub["lockup_days"].median())
        hr = float(sub["no_won"].mean()) * 100

        pnl_sum = 0.0
        for row in sub.iter_rows(named=True):
            if row["no_won"]:
                pnl_sum += 100 * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
            else:
                pnl_sum -= 100
        avg_pnl = pnl_sum / n
        total_pnl = pnl_sum

        print(f"  {label}:")
        print(f"    Markets: {n:,} ({n/df.height*100:.0f}% of total)")
        print(f"    Median lockup: {med_d:.1f} days")
        print(f"    NO HR: {hr:.1f}%")
        print(f"    Avg PnL/bet: ${avg_pnl:.2f}")
        print(f"    Total PnL: ${total_pnl:,.0f}")
        print()

    # Capital simulation: $300, $50/bet, fast-only vs all
    print(f"  --- $300 capital simulation (fast-only vs all) ---\n")

    months_list = sorted(
        m for m in df["resolved_at"].dt.strftime("%Y-%m").unique().to_list()
        if m and m >= "2025-01"
    )

    for filter_label, filter_df in [("Fast only (<3d)", fast), ("All markets", df)]:
        bankroll = 300.0
        bet_size = 50.0
        cum_pnl = 0.0
        total_bets = 0

        row_str = f"  {filter_label:<20}"
        for m in months_list:
            month_df = filter_df.filter(
                pl.col("resolved_at").dt.strftime("%Y-%m") == m
            )
            n_avail = month_df.height
            med_lock = float(month_df["lockup_days"].median()) if n_avail > 0 else 30
            med_lock = max(med_lock, 0.5)
            slots = int(bankroll / bet_size)
            capacity = int(slots * 30 / med_lock)
            placed = min(n_avail, capacity)

            if placed == 0:
                row_str += f" {'$0':>7}"
                continue

            selected = month_df.sort("resolved_at").head(placed)
            month_pnl = 0.0
            for row in selected.iter_rows(named=True):
                if row["no_won"]:
                    month_pnl += bet_size * (1 - row["no_entry_price"]) / row["no_entry_price"] * 0.98
                else:
                    month_pnl -= bet_size

            cum_pnl += month_pnl
            total_bets += placed
            row_str += f" ${month_pnl:>5,.0f}"

        row_str += f"  TOT=${cum_pnl:>7,.0f} ({total_bets} bets)"
        print(row_str)

    # ===================================================================
    # 7. NEG-RISK × LOCKUP
    # ===================================================================
    print(f"\n\n{'=' * 70}")
    print("  7. NEG-RISK: Does it affect resolution speed?")
    print(f"{'=' * 70}\n")

    for label, sub in [("neg_risk=True", df.filter(pl.col("neg_risk"))),
                        ("neg_risk=False", df.filter(~pl.col("neg_risk")))]:
        n = sub.height
        if n == 0:
            continue
        med_d = float(sub["lockup_days"].median())
        hr = float(sub["no_won"].mean()) * 100
        print(f"  {label}: {n:,} markets, med lockup={med_d:.1f}d, NO HR={hr:.1f}%")

    # ===================================================================
    # 8. DAY-OF-WEEK PATTERNS
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  8. RESOLUTION DAY-OF-WEEK")
    print(f"{'=' * 70}\n")

    df_dow = df.with_columns(
        pl.col("resolved_at").dt.weekday().alias("dow")
    )

    dow_names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    dow_stats = df_dow.group_by("dow").agg(
        pl.len().alias("n"),
        pl.col("lockup_days").median().alias("med_lockup"),
        pl.col("no_won").mean().alias("no_hr"),
    ).sort("dow")

    print(f"{'Day':<6} {'N':>7} {'Med d':>7} {'NO HR':>7}")
    print("-" * 30)
    for row in dow_stats.iter_rows(named=True):
        name = dow_names.get(row["dow"], "?")
        print(f"{name:<6} {row['n']:>7,} {row['med_lockup']:>6.1f} {row['no_hr']*100:>6.1f}%")


if __name__ == "__main__":
    main()
