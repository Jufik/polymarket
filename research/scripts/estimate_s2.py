"""Estimate S2 PnL: Fav-Longshot NO on "Will" binary questions at $300 capital.

The backtest (insight overpriceNo/03) assumed unlimited capital at $100/bet.
With $300, we're capacity-constrained: only N concurrent bets at a time.
This script models capital lockup and rotation realistically.

Usage:
    PYTHONPATH=. uv run python scripts/estimate_s2.py
"""

from __future__ import annotations

from datetime import datetime

import polars as pl


CAPITAL = 300.0
BET_SIZES = [50, 100]  # test both
FEE_RATE = 0.02


def main():
    # Load market metadata + resolution data
    markets = pl.read_parquet("data/metadata/markets.parquet").select(
        "condition_id", "question", "category", "neg_risk"
    )
    resolved = pl.read_parquet("data/derived/markets_resolved.parquet")
    for c in ["resolved_at"]:
        d = resolved.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            resolved = resolved.with_columns(pl.col(c).dt.replace_time_zone(None))

    # Load price data for entry prices
    pnl = pl.read_parquet("data/derived/trader_market_pnl.parquet")
    for c in ["first_trade", "last_trade"]:
        d = pnl.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            pnl = pnl.with_columns(pl.col(c).dt.replace_time_zone(None))

    # Join markets with resolution
    df = resolved.join(markets, on="condition_id", how="inner")

    # Filter: binary "Will" questions, resolved
    df = df.filter(
        pl.col("question").str.to_lowercase().str.starts_with("will ")
        & (pl.col("resolution_value") == 1)  # resolved (not voided)
    )

    # Get median early YES price per market from trade data
    # Use the pool-level wavg_yes_entry_price as proxy for "early price"
    market_prices = pnl.group_by("condition_id").agg(
        pl.col("wavg_yes_entry_price").median().alias("median_yes_price"),
        pl.col("first_trade").min().alias("earliest_trade"),
        pl.col("last_trade").max().alias("latest_trade"),
    )

    df = df.join(market_prices, on="condition_id", how="inner")

    # Filter: YES price 10-50% (our bet zone)
    df = df.filter(
        (pl.col("median_yes_price") >= 0.10)
        & (pl.col("median_yes_price") <= 0.50)
    )

    # Compute per-bet metrics
    # We buy NO at price = 1 - median_yes_price
    df = df.with_columns(
        (1.0 - pl.col("median_yes_price")).alias("no_entry_price"),
        # Resolution time proxy
        (pl.col("resolved_at") - pl.col("earliest_trade")).dt.total_days().alias("market_duration_days"),
    )

    # PnL per bet: if NO wins → profit = bet * (1 - no_price) / no_price * (1 - fee)
    # if YES wins → loss = -bet
    df = df.with_columns(
        (~pl.col("yes_won")).alias("no_won"),
    )

    print(f"Total 'Will' binary markets (YES 10-50%): {df.height:,}")
    print(f"Date range: {df['resolved_at'].min()} to {df['resolved_at'].max()}")
    print(f"NO win rate: {float(df['no_won'].mean())*100:.1f}%")
    print(f"Median NO entry price: {float(df['no_entry_price'].median()):.3f}")
    print(f"Median market duration: {float(df['market_duration_days'].median()):.1f} days")
    print()

    # ===================================================================
    # 1. UNLIMITED CAPITAL BASELINE (same as original backtest)
    # ===================================================================
    print(f"{'=' * 70}")
    print("  1. UNLIMITED CAPITAL (original backtest replication)")
    print(f"{'=' * 70}\n")

    # Monthly breakdown
    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
    )

    df = df.filter(pl.col("month").is_not_null())
    months_list = sorted(df["month"].unique().to_list())

    # Filter to 2025-01 onwards
    months_list = [m for m in months_list if m >= "2025-01"]

    for bet_size in [100]:
        print(f"  Bet size: ${bet_size}")
        print(f"  {'Month':<10} {'Bets':>6} {'HR':>7} {'PnL':>10} {'Cum PnL':>10}")
        print(f"  {'-' * 45}")

        cum_pnl = 0.0
        total_bets = 0
        total_wins = 0
        for m in months_list:
            month_df = df.filter(pl.col("month") == m)
            n = month_df.height
            wins = int(month_df["no_won"].sum())
            total_bets += n
            total_wins += wins

            # PnL per bet
            month_pnl = 0.0
            for row in month_df.iter_rows(named=True):
                if row["no_won"]:
                    profit = bet_size * (1 - row["no_entry_price"]) / row["no_entry_price"] * (1 - FEE_RATE)
                    month_pnl += profit
                else:
                    month_pnl -= bet_size

            cum_pnl += month_pnl
            hr = wins / n * 100 if n > 0 else 0
            print(f"  {m:<10} {n:>6} {hr:>6.1f}% ${month_pnl:>8,.0f} ${cum_pnl:>8,.0f}")

        overall_hr = total_wins / total_bets * 100 if total_bets > 0 else 0
        print(f"  {'-' * 45}")
        print(f"  {'TOTAL':<10} {total_bets:>6} {overall_hr:>6.1f}% ${cum_pnl:>8,.0f}")
        print()

    # ===================================================================
    # 2. CAPITAL-CONSTRAINED SIMULATION ($300)
    # ===================================================================
    print(f"\n{'=' * 70}")
    print(f"  2. CAPITAL-CONSTRAINED: ${CAPITAL:.0f} deployed")
    print(f"{'=' * 70}\n")

    # Strategy: each month, pick random bets from available markets
    # Limited by: capital / bet_size = max concurrent bets
    # Each bet locks capital for market_duration_days
    # Simplification: assume bets placed evenly through the month,
    # capital recycles after resolution

    for bet_size in BET_SIZES:
        max_concurrent = int(CAPITAL / bet_size)

        print(f"  === Bet size: ${bet_size}, max concurrent: {max_concurrent} ===\n")

        # Estimate bets/month from capital rotation
        # Avg lockup * bets must <= capital-days available
        # capital-days = max_concurrent * 30 (days in month)
        # bets = capital-days / avg_lockup

        print(f"  {'Month':<10} {'Avail':>6} {'Placed':>7} {'HR':>7} {'PnL':>10} {'Cum':>10}")
        print(f"  {'-' * 52}")

        cum_pnl = 0.0
        total_placed = 0
        total_wins = 0

        for m in months_list:
            month_df = df.filter(pl.col("month") == m).sort("resolved_at")
            available = month_df.height

            # How many can we place? capital-days / median lockup
            med_lockup = float(month_df["market_duration_days"].median()) if month_df.height > 0 else 30
            med_lockup = max(med_lockup, 1.0)  # at least 1 day
            capacity_days = max_concurrent * 30
            max_bets = int(capacity_days / med_lockup)
            placed = min(available, max_bets)

            if placed == 0:
                print(f"  {m:<10} {available:>6} {0:>7} {'---':>7} {'$0':>10} ${cum_pnl:>8,.0f}")
                continue

            # Take the first N bets (sorted by resolution date = FIFO)
            selected = month_df.head(placed)
            wins = int(selected["no_won"].sum())
            total_placed += placed
            total_wins += wins

            month_pnl = 0.0
            for row in selected.iter_rows(named=True):
                if row["no_won"]:
                    profit = bet_size * (1 - row["no_entry_price"]) / row["no_entry_price"] * (1 - FEE_RATE)
                    month_pnl += profit
                else:
                    month_pnl -= bet_size

            cum_pnl += month_pnl
            hr = wins / placed * 100
            print(f"  {m:<10} {available:>6} {placed:>7} {hr:>6.1f}% ${month_pnl:>8,.0f} ${cum_pnl:>8,.0f}")

        overall_hr = total_wins / total_placed * 100 if total_placed > 0 else 0
        print(f"  {'-' * 52}")
        print(f"  {'TOTAL':<10} {'':>6} {total_placed:>7} {overall_hr:>6.1f}% ${cum_pnl:>8,.0f}")
        avg_mo = cum_pnl / len(months_list) if months_list else 0
        roi_mo = avg_mo / CAPITAL * 100
        print(f"  Avg/month: ${avg_mo:,.0f} ({roi_mo:.1f}% ROI on ${CAPITAL:.0f})")
        print(f"  Annualized: ${avg_mo * 12:,.0f} ({roi_mo * 12:.0f}%)")
        print()

    # ===================================================================
    # 3. COMPOUND MODE
    # ===================================================================
    print(f"\n{'=' * 70}")
    print(f"  3. COMPOUND MODE: reinvest S2 earnings into more bets")
    print(f"{'=' * 70}\n")

    for bet_size in BET_SIZES:
        bankroll = CAPITAL
        print(f"  === Bet size: ${bet_size} (grows with bankroll) ===\n")
        print(f"  {'Month':<10} {'Bank':>8} {'Slots':>6} {'Placed':>7} {'HR':>7} {'PnL':>10} {'New Bank':>10}")
        print(f"  {'-' * 62}")

        for m in months_list:
            month_df = df.filter(pl.col("month") == m).sort("resolved_at")
            available = month_df.height

            max_concurrent = int(bankroll / bet_size)
            if max_concurrent < 1:
                print(f"  {m:<10} ${bankroll:>6,.0f} {0:>6} {0:>7} {'---':>7} {'$0':>10} ${bankroll:>8,.0f}")
                continue

            med_lockup = float(month_df["market_duration_days"].median()) if month_df.height > 0 else 30
            med_lockup = max(med_lockup, 1.0)
            capacity_days = max_concurrent * 30
            max_bets = int(capacity_days / med_lockup)
            placed = min(available, max_bets)

            if placed == 0:
                print(f"  {m:<10} ${bankroll:>6,.0f} {max_concurrent:>6} {0:>7} {'---':>7} {'$0':>10} ${bankroll:>8,.0f}")
                continue

            selected = month_df.head(placed)
            wins = int(selected["no_won"].sum())

            month_pnl = 0.0
            for row in selected.iter_rows(named=True):
                if row["no_won"]:
                    profit = bet_size * (1 - row["no_entry_price"]) / row["no_entry_price"] * (1 - FEE_RATE)
                    month_pnl += profit
                else:
                    month_pnl -= bet_size

            hr = wins / placed * 100
            bankroll += month_pnl
            print(f"  {m:<10} ${bankroll - month_pnl:>6,.0f} {max_concurrent:>6} {placed:>7} {hr:>6.1f}% ${month_pnl:>8,.0f} ${bankroll:>8,.0f}")

        total_return = (bankroll / CAPITAL - 1) * 100
        print(f"  {'-' * 62}")
        print(f"  Final bankroll: ${bankroll:,.0f} ({total_return:+.0f}% from ${CAPITAL:.0f})")
        print()


if __name__ == "__main__":
    main()
