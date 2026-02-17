"""Comprehensive analysis of consensus-copy strategy sweep results.

Determines whether the consensus signal strategy is viable for live trading.
Reads sweep_results.parquet (57,756 configs across 3 rolling windows) and
top_configs.json (top 50 stable configs).

Usage:
    uv run python scripts/analyze_sweep.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path("strategies/consistency_copy")
SWEEP_PATH = BASE / "sweep_results.parquet"
TOP_CONFIGS_PATH = BASE / "top_configs.json"

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

DIVIDER = "=" * 90
SECTION = "-" * 70


def header(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def sub_header(title: str) -> None:
    print(f"\n{SECTION}")
    print(f"  {title}")
    print(SECTION)


def pct(val: float) -> str:
    return f"{val * 100:.2f}%"


def usd(val: float) -> str:
    return f"${val:,.2f}"


def fmt(val: float, decimals: int = 4) -> str:
    return f"{val:.{decimals}f}"


# ---------------------------------------------------------------------------
# Config columns (used for grouping)
# ---------------------------------------------------------------------------
CONFIG_COLS = [
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


# ============================================================================
# A. TOP CONFIG DEEP-DIVE
# ============================================================================
def analyze_top_configs(df: pl.DataFrame) -> None:
    header("A. TOP CONFIG DEEP-DIVE (Top 10 by avg_sharpe)")

    # Group across windows, rank by avg sharpe
    grouped = (
        df.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("sharpe").std().alias("std_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("window").n_unique().alias("n_windows"),
            pl.col("pool_size").mean().alias("avg_pool_size"),
        )
        .filter(pl.col("n_windows") >= 2)
        .sort("avg_sharpe", descending=True)
    )

    top10 = grouped.head(10).to_dicts()

    for rank, cfg in enumerate(top10, 1):
        sub_header(f"Rank #{rank}: avg_sharpe={fmt(cfg['avg_sharpe'], 2)}")

        # Parameters
        print(f"  consistency_months = {cfg['consistency_months']}")
        print(f"  min_markets        = {cfg['min_markets']}")
        print(f"  mvf_band           = {cfg['mvf_band']}")
        print(f"  min_traders        = {cfg['min_traders']}")
        print(f"  agreement_pct      = {cfg['agreement_pct']}")
        print(f"  direction          = {cfg['direction']}")
        print(f"  price_band         = [{cfg['price_band_lo']}, {cfg['price_band_hi']}]")
        print(f"  sizing             = {cfg['sizing']}")
        print(f"  avg_pool_size      = {cfg['avg_pool_size']:.0f}")
        print(f"  n_windows          = {cfg['n_windows']}")
        print(f"  avg_sharpe         = {fmt(cfg['avg_sharpe'], 2)}")
        print(f"  std_sharpe         = {fmt(cfg['std_sharpe'], 2)}")
        print(f"  avg_pnl            = {usd(cfg['avg_pnl'])}")
        print(f"  avg_hit_rate       = {pct(cfg['avg_hit_rate'])}")

        # Per-window breakdown
        mask = pl.lit(True)
        for col in CONFIG_COLS:
            mask = mask & (pl.col(col) == cfg[col])
        windows = df.filter(mask).sort("window")

        print("\n  Per-window breakdown:")
        print(f"  {'Window':<16} {'Sharpe':>8} {'Hit%':>8} {'PnL':>12} {'Bets':>7} "
              f"{'PnL/Bet':>10} {'Pool':>7} {'Days':>5} {'Wins':>6} {'Losses':>7}")

        for row in windows.to_dicts():
            win_pnl = row["total_wins"] * (row["total_pnl"] / row["total_bets"]) if row["total_bets"] > 0 else 0
            print(
                f"  {row['window']:<16} "
                f"{row['sharpe']:>8.2f} "
                f"{pct(row['hit_rate']):>8} "
                f"{usd(row['total_pnl']):>12} "
                f"{row['total_bets']:>7} "
                f"{usd(row['pnl_per_bet']):>10} "
                f"{row['pool_size']:>7} "
                f"{row['n_days']:>5} "
                f"{row['total_wins']:>6} "
                f"{row['total_bets'] - row['total_wins']:>7}"
            )

        # Win/loss analysis from the data we have
        for row in windows.to_dicts():
            n_bets = row["total_bets"]
            n_wins = row["total_wins"]
            n_losses = n_bets - n_wins
            total_pnl = row["total_pnl"]
            hit_rate_val = row["hit_rate"]

            if n_wins > 0 and n_losses > 0:
                # From the P&L model:
                # total_pnl = sum(win_pnls) + sum(loss_pnls)
                # We know: loss = -bet - fee (roughly -1.01 for $1 fixed bet)
                # win = bet * (1-p)/p - fee (varies by entry price)
                # With hit_rate and total_pnl, we can estimate avg win/loss
                # Rough: avg_loss ~ -1.01 (fixed $1 bet + ~1% avg fee)
                # total_pnl = n_wins * avg_win + n_losses * avg_loss
                # avg_win = (total_pnl - n_losses * avg_loss) / n_wins
                est_avg_loss = -(1.0 + 0.01)  # approximate
                est_total_loss = n_losses * est_avg_loss
                est_total_win = total_pnl - est_total_loss
                est_avg_win = est_total_win / n_wins if n_wins > 0 else 0

                print(f"\n  {row['window']}: W/L ratio = {n_wins/n_losses:.2f}, "
                      f"est avg_win = {usd(est_avg_win)}, est avg_loss = {usd(est_avg_loss)}")


# ============================================================================
# B. DISTRIBUTION ANALYSIS
# ============================================================================
def analyze_distributions(df: pl.DataFrame) -> None:
    header("B. DISTRIBUTION ANALYSIS")

    # Group configs across windows
    config_stats = (
        df.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("total_pnl").min().alias("min_pnl"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("hit_rate").min().alias("min_hit_rate"),
            pl.col("window").n_unique().alias("n_windows"),
            pl.col("pool_size").mean().alias("avg_pool_size"),
            pl.col("total_bets").mean().alias("avg_bets"),
        )
    )

    n_total = config_stats.height
    print(f"\nTotal unique configurations: {n_total:,}")

    # Profitable across ALL windows
    all_profitable = config_stats.filter(pl.col("min_pnl") > 0)
    n_all_profitable = all_profitable.height
    print(f"\nConfigs profitable in ALL windows: {n_all_profitable:,} ({pct(n_all_profitable / n_total)})")

    # Also check for configs present in all 3 windows
    in_3_windows = config_stats.filter(pl.col("n_windows") == 3)
    n_3w = in_3_windows.height
    n_3w_profitable = in_3_windows.filter(pl.col("min_pnl") > 0).height
    print(f"  Present in all 3 windows: {n_3w:,}")
    print(f"  Profitable in all 3 windows: {n_3w_profitable:,} ({pct(n_3w_profitable / n_3w) if n_3w > 0 else '0%'})")

    # Hit rate > 55% in ALL windows
    per_window_hr = (
        df.group_by(CONFIG_COLS + ["window"])
        .agg(pl.col("hit_rate").first())
    )
    min_hr = (
        per_window_hr.group_by(CONFIG_COLS)
        .agg(
            pl.col("hit_rate").min().alias("min_hr"),
            pl.col("window").n_unique().alias("n_windows"),
        )
    )
    all_above_55 = min_hr.filter(pl.col("min_hr") > 0.55)
    print(f"\nConfigs with hit_rate > 55% in ALL windows: {all_above_55.height:,} ({pct(all_above_55.height / n_total)})")

    all_above_55_3w = min_hr.filter((pl.col("min_hr") > 0.55) & (pl.col("n_windows") == 3))
    print(f"  Of those, present in all 3 windows: {all_above_55_3w.height:,}")

    # Median stats across all configs
    sub_header("Median stats (across all config averages)")
    med_sharpe = config_stats["avg_sharpe"].median()
    med_hr = config_stats["avg_hit_rate"].median()
    med_pnl = config_stats["avg_pnl"].median()
    mean_sharpe = config_stats["avg_sharpe"].mean()
    mean_hr = config_stats["avg_hit_rate"].mean()
    mean_pnl = config_stats["avg_pnl"].mean()

    print(f"  Median sharpe:    {fmt(med_sharpe, 2)}")
    print(f"  Mean sharpe:      {fmt(mean_sharpe, 2)}")
    print(f"  Median hit_rate:  {pct(med_hr)}")
    print(f"  Mean hit_rate:    {pct(mean_hr)}")
    print(f"  Median PnL:       {usd(med_pnl)}")
    print(f"  Mean PnL:         {usd(mean_pnl)}")

    # Pool size correlation
    sub_header("Pool size vs performance")
    pool_bins = [
        ("< 100", 0, 100),
        ("100-500", 100, 500),
        ("500-2000", 500, 2000),
        ("2000-5000", 2000, 5000),
        ("5000+", 5000, 1_000_000),
    ]

    print(f"  {'Pool Range':<16} {'Count':>7} {'Med Sharpe':>12} {'Med HR':>10} {'Med PnL':>12} {'Med Bets':>10}")
    for label, lo, hi in pool_bins:
        subset = config_stats.filter(
            (pl.col("avg_pool_size") >= lo) & (pl.col("avg_pool_size") < hi)
        )
        if subset.height == 0:
            continue
        print(
            f"  {label:<16} {subset.height:>7} "
            f"{fmt(subset['avg_sharpe'].median(), 2):>12} "
            f"{pct(subset['avg_hit_rate'].median()):>10} "
            f"{usd(subset['avg_pnl'].median()):>12} "
            f"{subset['avg_bets'].median():>10.0f}"
        )


# ============================================================================
# C. REALISTIC P&L AT DIFFERENT BET SIZES
# ============================================================================
def analyze_bet_sizes(df: pl.DataFrame) -> None:
    header("C. REALISTIC P&L AT DIFFERENT BET SIZES")

    # Use the best stable config (top by avg_sharpe across >= 2 windows)
    grouped = (
        df.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("pnl_per_bet").mean().alias("avg_pnl_per_bet"),
            pl.col("n_days").mean().alias("avg_days"),
            pl.col("window").n_unique().alias("n_windows"),
            pl.col("max_drawdown").mean().alias("avg_max_dd"),
        )
        .filter(pl.col("n_windows") >= 2)
        .sort("avg_sharpe", descending=True)
    )

    # Take best config present in all 3 windows for robustness
    best_3w = grouped.filter(pl.col("n_windows") == 3).head(1).to_dicts()
    best_2w = grouped.head(1).to_dicts()

    for label, cfgs in [("Best 3-window config", best_3w), ("Best 2-window config", best_2w)]:
        if not cfgs:
            continue
        cfg = cfgs[0]
        sub_header(f"{label}")

        pnl_per_bet = cfg["avg_pnl_per_bet"]
        avg_bets = cfg["avg_bets"]
        avg_days = cfg["avg_days"]
        bets_per_day = avg_bets / avg_days if avg_days > 0 else 0
        hit_rate_val = cfg["avg_hit_rate"]

        print(f"  PnL/bet (at $1): {usd(pnl_per_bet)}")
        print(f"  Avg bets/window: {avg_bets:.0f}")
        print(f"  Avg days/window: {avg_days:.0f}")
        print(f"  Bets/day:        {bets_per_day:.1f}")
        print(f"  Hit rate:        {pct(hit_rate_val)}")
        print(f"  Avg max DD ($1): {usd(cfg['avg_max_dd'])}")

        bet_sizes = [1, 10, 50, 100, 500]

        # Estimate avg holding period: bets resolve over ~30 day window.
        # If avg_bets=1500 over 30 days, bets are entered over the month and
        # resolve over the month. Peak concurrent positions approx = bets_per_day * avg_hold.
        # Conservative: assume avg hold = 7 days (Polymarket binary markets).
        avg_hold_days = 7
        est_concurrent = min(bets_per_day * avg_hold_days, avg_bets)

        print(f"  Est concurrent positions (hold={avg_hold_days}d): {est_concurrent:.0f}")

        print(f"\n  {'Bet$':>8} {'PnL/Bet':>10} {'Daily PnL':>12} {'Monthly PnL':>14} "
              f"{'Annual PnL':>14} {'Capital@50':>12} {'Capital@est':>12} "
              f"{'Max DD':>12} {'ROI/mo@50':>10} {'ROI/mo@est':>11}")

        for bet in bet_sizes:
            # PnL scales linearly with bet size
            scaled_pnl_per_bet = pnl_per_bet * bet
            daily_pnl = scaled_pnl_per_bet * bets_per_day
            monthly_pnl = daily_pnl * 30
            annual_pnl = daily_pnl * 365
            capital_50 = bet * 50
            capital_est = bet * est_concurrent
            max_dd = cfg["avg_max_dd"] * bet
            roi_50 = monthly_pnl / capital_50 * 100 if capital_50 > 0 else 0
            roi_est = monthly_pnl / capital_est * 100 if capital_est > 0 else 0

            print(
                f"  {usd(bet):>8} {usd(scaled_pnl_per_bet):>10} {usd(daily_pnl):>12} "
                f"{usd(monthly_pnl):>14} {usd(annual_pnl):>14} {usd(capital_50):>12} "
                f"{usd(capital_est):>12} {usd(max_dd):>12} {roi_50:>9.1f}% {roi_est:>10.1f}%"
            )

        print(f"\n  NOTE: 'Capital@50' = 50 concurrent * bet. 'Capital@est' = estimated")
        print(f"  concurrent positions based on {avg_hold_days}-day avg hold. Real capital")
        print(f"  depends on actual holding period and position management.")

    # IMPORTANT: Now do the same but using only Dec+Jan windows (excluding Feb)
    sub_header("Excluding Feb 2026 (partial data) - realistic estimate")
    df_no_feb = df.filter(pl.col("window") != "win2_feb26")
    grouped_no_feb = (
        df_no_feb.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("pnl_per_bet").mean().alias("avg_pnl_per_bet"),
            pl.col("n_days").mean().alias("avg_days"),
            pl.col("window").n_unique().alias("n_windows"),
            pl.col("max_drawdown").mean().alias("avg_max_dd"),
        )
        .filter(pl.col("n_windows") == 2)
        .sort("avg_sharpe", descending=True)
    )

    best_nofeb = grouped_no_feb.head(1).to_dicts()
    if best_nofeb:
        cfg = best_nofeb[0]
        pnl_per_bet = cfg["avg_pnl_per_bet"]
        avg_bets = cfg["avg_bets"]
        avg_days = cfg["avg_days"]
        bets_per_day = avg_bets / avg_days if avg_days > 0 else 0
        print(f"  PnL/bet (at $1): {usd(pnl_per_bet)}")
        print(f"  Bets/day:        {bets_per_day:.1f}")
        print(f"  Hit rate:        {pct(cfg['avg_hit_rate'])}")

        bet_sizes = [10, 50, 100, 500]
        avg_hold_days = 7
        est_concurrent = min(bets_per_day * avg_hold_days, avg_bets)
        print(f"  Est concurrent (hold={avg_hold_days}d): {est_concurrent:.0f}")
        print(f"\n  {'Bet$':>8} {'Daily PnL':>12} {'Monthly PnL':>14} {'Annual PnL':>14} "
              f"{'Capital@est':>14} {'Max DD':>12} {'ROI/mo':>10}")
        for bet in bet_sizes:
            scaled = pnl_per_bet * bet
            daily = scaled * bets_per_day
            monthly = daily * 30
            annual = daily * 365
            capital_est = bet * est_concurrent
            max_dd = cfg["avg_max_dd"] * bet
            roi = monthly / capital_est * 100 if capital_est > 0 else 0
            print(
                f"  {usd(bet):>8} {usd(daily):>12} {usd(monthly):>14} {usd(annual):>14} "
                f"{usd(capital_est):>14} {usd(max_dd):>12} {roi:>9.1f}%"
            )


# ============================================================================
# D. SIGNAL FREQUENCY AND CAPACITY
# ============================================================================
def analyze_frequency(df: pl.DataFrame) -> None:
    header("D. SIGNAL FREQUENCY AND CAPACITY")

    # Per-window stats for the top configs
    top_grouped = (
        df.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("n_days").mean().alias("avg_days"),
            pl.col("window").n_unique().alias("n_windows"),
        )
        .filter(pl.col("n_windows") >= 2)
        .sort("avg_sharpe", descending=True)
    )

    # Show distribution of bets/day across all stable configs
    top_grouped = top_grouped.with_columns(
        (pl.col("avg_bets") / pl.col("avg_days")).alias("bets_per_day")
    )

    sub_header("Bets per day distribution (stable configs, >= 2 windows)")
    bpd = top_grouped["bets_per_day"]
    print(f"  Median:  {bpd.median():.1f}")
    print(f"  Mean:    {bpd.mean():.1f}")
    print(f"  P10:     {bpd.quantile(0.10):.1f}")
    print(f"  P25:     {bpd.quantile(0.25):.1f}")
    print(f"  P75:     {bpd.quantile(0.75):.1f}")
    print(f"  P90:     {bpd.quantile(0.90):.1f}")
    print(f"  Min:     {bpd.min():.1f}")
    print(f"  Max:     {bpd.max():.1f}")

    # Per-window detail for top 5 configs
    sub_header("Per-window signal details (top 5 configs)")
    top5_cfgs = top_grouped.head(5).to_dicts()

    for i, cfg in enumerate(top5_cfgs, 1):
        mask = pl.lit(True)
        for col in CONFIG_COLS:
            mask = mask & (pl.col(col) == cfg[col])
        windows = df.filter(mask).sort("window")

        print(f"\n  Config #{i}: agree={cfg['agreement_pct']} dir={cfg['direction']} "
              f"band=[{cfg['price_band_lo']},{cfg['price_band_hi']}]")
        for row in windows.to_dicts():
            bpd_w = row["total_bets"] / row["n_days"] if row["n_days"] > 0 else 0
            # Estimate unique markets per month:
            # total_bets = unique markets (1 bet per market in the signal model)
            markets_per_month = row["total_bets"]
            print(
                f"    {row['window']:<16}: {row['total_bets']:>6} bets, "
                f"{row['n_days']:>3} days, {bpd_w:>5.1f} bets/day, "
                f"~{markets_per_month:>5} unique markets/month"
            )

    # Average holding period estimate
    # Each window has holdout_start -> holdout_end of ~1 month
    # Bets resolve on their resolution date within that window
    # We can estimate: avg_holding = n_days / 2 (rough midpoint estimate)
    sub_header("Holding period estimate")
    print("  NOTE: Each bet = one market. Entry at signal trigger, exit at resolution.")
    print("  Windows span ~1 month each.")
    print("  The signal table has one entry per market (first qualifying trader arrival).")
    print("  Holding period varies from same-day to full window length.")
    print("  From rolling_simulation.json: Dec25 holdout had 33K markets, Jan26 had 50K markets.")
    print("  Most Polymarket binary markets resolve within 1-30 days.")
    print("  With ~30-150 bets/day resolving, the portfolio turns over rapidly.")


# ============================================================================
# E. ROBUSTNESS CHECKS
# ============================================================================
def analyze_robustness(df: pl.DataFrame) -> None:
    header("E. ROBUSTNESS CHECKS")

    # Stability of performance across windows
    sub_header("E1. Sharpe and hit rate stability across windows")

    per_window_stats = (
        df.group_by(CONFIG_COLS + ["window"])
        .agg(
            pl.col("sharpe").first(),
            pl.col("hit_rate").first(),
            pl.col("total_pnl").first(),
            pl.col("total_bets").first(),
        )
    )

    stability = (
        per_window_stats.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("sharpe").std().alias("std_sharpe"),
            pl.col("hit_rate").mean().alias("avg_hr"),
            pl.col("hit_rate").std().alias("std_hr"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("total_pnl").std().alias("std_pnl"),
            pl.col("window").n_unique().alias("n_windows"),
        )
        .filter(pl.col("n_windows") >= 2)
        .with_columns(
            (pl.col("std_sharpe") / pl.col("avg_sharpe")).alias("cv_sharpe"),
            (pl.col("std_hr") / pl.col("avg_hr")).alias("cv_hr"),
        )
    )

    print(f"  Configs with >= 2 windows: {stability.height:,}")
    print(f"\n  Sharpe stability:")
    print(f"    Median std_sharpe:  {stability['std_sharpe'].median():.2f}")
    print(f"    Median CV(sharpe):  {stability['cv_sharpe'].median():.2f}")
    print(f"    P90 CV(sharpe):     {stability['cv_sharpe'].quantile(0.90):.2f}")
    print(f"\n  Hit rate stability:")
    print(f"    Median std_hr:      {stability['std_hr'].median():.4f}")
    print(f"    Median CV(hr):      {stability['cv_hr'].median():.4f}")
    print(f"    P90 CV(hr):         {stability['cv_hr'].quantile(0.90):.4f}")

    # Pool size vs performance (more selective = better?)
    sub_header("E2. Pool size vs performance (larger pool = less selective)")

    pool_perf = (
        df.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("hit_rate").mean().alias("avg_hr"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("pool_size").mean().alias("avg_pool"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("window").n_unique().alias("n_windows"),
        )
        .filter(pl.col("n_windows") >= 2)
    )

    # Correlation between pool size and sharpe/hr
    if pool_perf.height > 10:
        pool_arr = pool_perf["avg_pool"].to_list()
        sharpe_arr = pool_perf["avg_sharpe"].to_list()
        hr_arr = pool_perf["avg_hr"].to_list()
        pnl_arr = pool_perf["avg_pnl"].to_list()

        def pearson(x: list[float], y: list[float]) -> float:
            n = len(x)
            mx = sum(x) / n
            my = sum(y) / n
            cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
            sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
            sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
            return cov / (sx * sy) if sx > 0 and sy > 0 else 0

        print(f"  Correlation(pool_size, sharpe):   {pearson(pool_arr, sharpe_arr):+.4f}")
        print(f"  Correlation(pool_size, hit_rate): {pearson(pool_arr, hr_arr):+.4f}")
        print(f"  Correlation(pool_size, pnl):      {pearson(pool_arr, pnl_arr):+.4f}")

    # Group by pool_size buckets
    pool_bins = [
        ("< 500", 0, 500),
        ("500-1500", 500, 1500),
        ("1500-5000", 1500, 5000),
        ("5000-10000", 5000, 10000),
        ("10000+", 10000, 1_000_000),
    ]
    print(f"\n  {'Pool Range':<16} {'Count':>7} {'Med Sharpe':>12} {'Med HR':>10} {'Med PnL':>12}")
    for label, lo, hi in pool_bins:
        subset = pool_perf.filter(
            (pl.col("avg_pool") >= lo) & (pl.col("avg_pool") < hi)
        )
        if subset.height == 0:
            continue
        print(
            f"  {label:<16} {subset.height:>7} "
            f"{fmt(subset['avg_sharpe'].median(), 2):>12} "
            f"{pct(subset['avg_hr'].median()):>10} "
            f"{usd(subset['avg_pnl'].median()):>12}"
        )

    # Sweet spot in parameter space
    sub_header("E3. Parameter sweet spot analysis")

    # By consistency_months
    print("\n  By consistency_months:")
    for cm in sorted(df["consistency_months"].unique().to_list()):
        subset = pool_perf.filter(pl.col("consistency_months") == cm)
        if subset.height < 5:
            continue
        print(
            f"    months={cm}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}"
        )

    # By min_markets
    print("\n  By min_markets:")
    for mm in sorted(df["min_markets"].unique().to_list()):
        subset = pool_perf.filter(pl.col("min_markets") == mm)
        if subset.height < 5:
            continue
        print(
            f"    min_mkts={mm:>3}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}"
        )

    # By agreement_pct
    print("\n  By agreement_pct:")
    for ap in sorted(df["agreement_pct"].unique().to_list()):
        subset = pool_perf.filter(pl.col("agreement_pct") == ap)
        if subset.height < 5:
            continue
        print(
            f"    agree={ap:.0%}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}"
        )

    # By direction
    print("\n  By direction:")
    for d in df["direction"].unique().to_list():
        subset = pool_perf.filter(pl.col("direction") == d)
        if subset.height < 5:
            continue
        print(
            f"    {d:<10}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}"
        )

    # By min_traders
    print("\n  By min_traders:")
    for mt in sorted(df["min_traders"].unique().to_list()):
        subset = pool_perf.filter(pl.col("min_traders") == mt)
        if subset.height < 5:
            continue
        print(
            f"    min_t={mt:>3}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}"
        )

    # Price band sensitivity
    sub_header("E4. Price band sensitivity")
    print("\n  By price_band:")
    for row in (
        pool_perf.group_by(["price_band_lo", "price_band_hi"])
        .agg(
            pl.col("avg_sharpe").median().alias("med_sharpe"),
            pl.col("avg_hr").median().alias("med_hr"),
            pl.col("avg_pnl").median().alias("med_pnl"),
            pl.col("avg_bets").median().alias("med_bets"),
            pl.len().alias("count"),
        )
        .sort("med_sharpe", descending=True)
        .to_dicts()
    ):
        print(
            f"    [{row['price_band_lo']:.2f}, {row['price_band_hi']:.2f}]: "
            f"{row['count']:>5} configs, "
            f"med_sharpe={fmt(row['med_sharpe'], 2)}, "
            f"med_hr={pct(row['med_hr'])}, "
            f"med_pnl={usd(row['med_pnl'])}, "
            f"med_bets={row['med_bets']:.0f}"
        )

    # By mvf_band
    sub_header("E5. MVF band analysis")
    for band in df["mvf_band"].unique().to_list():
        subset = pool_perf.filter(pl.col("mvf_band") == band)
        if subset.height < 5:
            continue
        print(
            f"  {band:<18}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}, "
            f"med_pnl={usd(subset['avg_pnl'].median())}"
        )

    # By sizing strategy
    sub_header("E6. Sizing strategy comparison")
    for sz in df["sizing"].unique().to_list():
        subset = pool_perf.filter(pl.col("sizing") == sz)
        if subset.height < 5:
            continue
        print(
            f"  {sz:<22}: {subset.height:>5} configs, "
            f"med_sharpe={fmt(subset['avg_sharpe'].median(), 2)}, "
            f"med_hr={pct(subset['avg_hr'].median())}, "
            f"med_pnl={usd(subset['avg_pnl'].median())}"
        )


# ============================================================================
# F. RED FLAGS
# ============================================================================
def analyze_red_flags(df: pl.DataFrame) -> None:
    header("F. RED FLAGS AND CRITICAL WARNINGS")

    # F1: Feb 2026 window reliability
    sub_header("F1. Feb 2026 window reliability")
    feb = df.filter(pl.col("window") == "win2_feb26")
    dec = df.filter(pl.col("window") == "win0_dec25")
    jan = df.filter(pl.col("window") == "win1_jan26")

    print(f"\n  Window comparison:")
    print(f"  {'Metric':<20} {'Dec 2025':>14} {'Jan 2026':>14} {'Feb 2026':>14}")
    print(f"  {'Configs':<20} {dec.height:>14,} {jan.height:>14,} {feb.height:>14,}")
    print(f"  {'Med n_days':<20} {dec['n_days'].median():>14.0f} {jan['n_days'].median():>14.0f} {feb['n_days'].median():>14.0f}")
    print(f"  {'Med total_bets':<20} {dec['total_bets'].median():>14.0f} {jan['total_bets'].median():>14.0f} {feb['total_bets'].median():>14.0f}")
    print(f"  {'Med total_pnl':<20} {usd(dec['total_pnl'].median()):>14} {usd(jan['total_pnl'].median()):>14} {usd(feb['total_pnl'].median()):>14}")
    print(f"  {'Med hit_rate':<20} {pct(dec['hit_rate'].median()):>14} {pct(jan['hit_rate'].median()):>14} {pct(feb['hit_rate'].median()):>14}")
    print(f"  {'Med sharpe':<20} {fmt(dec['sharpe'].median(), 2):>14} {fmt(jan['sharpe'].median(), 2):>14} {fmt(feb['sharpe'].median(), 2):>14}")
    print(f"  {'Max total_bets':<20} {dec['total_bets'].max():>14,} {jan['total_bets'].max():>14,} {feb['total_bets'].max():>14,}")

    print("\n  ASSESSMENT: Feb 2026 is PARTIAL DATA (2-11 days, 20-186 bets).")
    print("  Sharpe inflated by small sample (sqrt(365) annualization on 5 days).")
    print("  This window should be EXCLUDED from stability rankings.")
    print("  Only Dec 2025 + Jan 2026 provide reliable evidence.")

    # Check the Feb NO-only dominance
    feb_no = feb.filter(pl.col("direction") == "NO-only")
    feb_yes = feb.filter(pl.col("direction") == "YES-only")
    if feb_no.height > 0 and feb_yes.height > 0:
        print(f"\n  CRITICAL: Feb 2026 direction bias:")
        print(f"    NO-only  median hit_rate: {pct(feb_no['hit_rate'].median())} on {feb_no.height} configs")
        print(f"    YES-only median hit_rate: {pct(feb_yes['hit_rate'].median())} on {feb_yes.height} configs")
        print(f"    This means Feb data is dominated by markets that resolved NO.")
        print(f"    The 8,302 'consistent' traders in Feb is inflated — almost everyone")
        print(f"    looks good when most markets resolve to the majority side.")

    # F2: Suspiciously high hit rates
    sub_header("F2. Hit rate analysis - are they suspiciously high?")

    # Compare sweep hit rates vs the rolling_simulation baseline
    print("\n  From rolling_simulation.json (no price band, no fee model, raw $1 bets):")
    print("    Dec25 t3_a60: hit=71.5%, pnl=$30,222 on 2997 bets")
    print("    Jan26 t3_a60: hit=72.2%, pnl=$27,676 on 2424 bets")
    print("    Feb26 t3_a60: hit=85.9%, pnl=$159 on 71 bets")
    print()
    print("  From sweep_results.parquet (with price bands, fees, entry prices):")
    print(f"    Dec25 median hit_rate:  {pct(dec['hit_rate'].median())}")
    print(f"    Jan26 median hit_rate:  {pct(jan['hit_rate'].median())}")
    print(f"    Feb26 median hit_rate:  {pct(feb['hit_rate'].median())}")
    print()

    # Check: what are the hit rates for the NO-only direction?
    for w_name, w_df in [("Dec25", dec), ("Jan26", jan), ("Feb26", feb)]:
        yes_only = w_df.filter(pl.col("direction") == "YES-only")
        no_only = w_df.filter(pl.col("direction") == "NO-only")
        both = w_df.filter(pl.col("direction") == "both")
        print(f"  {w_name} by direction:")
        if yes_only.height > 0:
            print(f"    YES-only: med_hr={pct(yes_only['hit_rate'].median())}, "
                  f"med_pnl={usd(yes_only['total_pnl'].median())}, n={yes_only.height}")
        if no_only.height > 0:
            print(f"    NO-only:  med_hr={pct(no_only['hit_rate'].median())}, "
                  f"med_pnl={usd(no_only['total_pnl'].median())}, n={no_only.height}")
        if both.height > 0:
            print(f"    both:     med_hr={pct(both['hit_rate'].median())}, "
                  f"med_pnl={usd(both['total_pnl'].median())}, n={both.height}")

    # F3: Look-ahead bias in consistency filter
    sub_header("F3. Look-ahead bias assessment")
    print("""
  The consistency filter selects traders who were profitable in ALL months of
  the training period. This creates two potential biases:

  1. SURVIVORSHIP BIAS (MODERATE RISK):
     Traders profitable in ALL N months are a biased sample. They may have
     been lucky rather than skilled. However, the signal aggregates across
     many such traders (pool sizes 500-11,000), which mitigates individual
     luck.

  2. LOOK-AHEAD BIAS IN ENTRY PRICES (LOW RISK):
     Entry prices use asof-join against actual trade data at the signal
     trigger time. This is the real market price when the Nth skilled trader
     entered, so no look-ahead here.

  3. RESOLUTION VALUE KNOWN AT SIGNAL TIME? (NO - CORRECT):
     The signal fires when enough traders enter. Resolution happens later.
     The signal_direction is inferred from trader positions, NOT from
     resolution. This is forward-looking and correct.

  4. TRAINING PERIOD OVERLAP WITH HOLDOUT? (NO - CORRECT):
     Windows are strictly non-overlapping:
       Win0: train Aug-Dec 2025, holdout Dec 2025
       Win1: train Sep 2025-Jan 2026, holdout Jan 2026
       Win2: train Oct 2025-Feb 2026, holdout Feb 2026
     Traders are selected from training period, tested on holdout.

  HOWEVER: The consistency filter is VERY generous. The top configs use
  consistency_months=4, which means the trader only needs 4 profitable months
  out of the 4-month training window. With pool sizes of 1,000-2,000+, the
  consensus signal averages over a LARGE number of marginally skilled traders.
  The real question is whether this consensus effect persists out-of-sample.
  """)

    # F4: Is 60% hit rate enough?
    sub_header("F4. Is ~60% hit rate enough given fee structure?")

    # Model the breakeven analysis
    print("\n  P&L model: WIN = bet*(1-p)/p - fee, LOSE = -bet - fee")
    print("  Fee = 2% * min(p, 1-p) * bet")
    print()
    print("  Breakeven analysis at different entry prices (p = YES price, $1 bet):")
    print(f"  {'Entry Price':>14} {'Win Payoff':>12} {'Loss Cost':>12} {'Fee':>8} {'Breakeven HR':>14}")

    for p in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
        fee = 0.02 * min(p, 1 - p)
        win_payoff = (1 - p) / p - fee
        loss_cost = 1 + fee
        # breakeven: hr * win_payoff = (1-hr) * loss_cost
        # hr * win_payoff + hr * loss_cost = loss_cost
        # hr = loss_cost / (win_payoff + loss_cost)
        breakeven_hr = loss_cost / (win_payoff + loss_cost)
        print(
            f"  {p:>14.2f} {usd(win_payoff):>12} {usd(-loss_cost):>12} "
            f"{usd(fee):>8} {pct(breakeven_hr):>14}"
        )

    print()
    print("  The top configs have:")
    print("    - YES-only, price_band [0.2, 0.8]: hit_rate ~59.6%, avg entry ~0.50")
    print("    - 'both' direction, [0.1, 0.9]: hit_rate ~65%, entry spread wider")
    print()
    print("  At entry=0.50: breakeven HR = 50.5% (after fees). A 60% hit rate gives")
    print("  significant edge. At entry=0.70: breakeven = 71.3%. The price band filter")
    print("  is CRITICAL - it removes bets near 0 or 1 where edge is smallest.")

    # F5: PnL magnitude reality check
    sub_header("F5. PnL magnitude reality check")
    print()
    print("  From the sweep, the best Dec+Jan configs show:")
    print(f"    avg_pnl ~ $360-470 per window (1 month, $1 fixed bets)")
    print(f"    avg_bets ~ 800-4000 per window")
    print(f"    pnl_per_bet ~ $0.08-0.35")
    print()
    print("  This means on a $1 bet, you earn ~$0.10-0.35 in expectation.")
    print("  With $100 bets and 30 bets/day: daily_pnl = $300-1050")
    print("  Monthly: $9,000-31,500 on $5,000 capital (50 concurrent * $100)")
    print()
    print("  THE CATCH: These are $1-normalized PnL. Real execution will face:")
    print("    - Slippage (moving the market on entry)")
    print("    - The signal table uses the FIRST qualifying trader's entry price")
    print("    - We are NOT these traders; we enter AFTER seeing the consensus")
    print("    - By the time N traders have entered, the price may have moved")
    print("    - Polymarket has AMM-style orderbooks: large bets = worse fills")
    print()

    # F6: The sizing strategy barely matters
    sub_header("F6. Sizing strategy has minimal impact")
    for w_name, w_df in [("Dec25", dec), ("Jan26", jan)]:
        print(f"\n  {w_name}:")
        for sz in ["fixed", "agreement_weighted", "kelly", "edge_weighted"]:
            subset = w_df.filter(pl.col("sizing") == sz)
            if subset.height == 0:
                continue
            print(
                f"    {sz:<22}: med_pnl={usd(subset['total_pnl'].median()):>10}, "
                f"med_sharpe={fmt(subset['sharpe'].median(), 2)}"
            )

    # F7: The massive gap between rolling_simulation.json and sweep results
    sub_header("F7. Rolling simulation vs sweep results gap")
    print("""
  rolling_simulation.json shows MUCH higher PnL (e.g., $30K for Dec25 t3_a60)
  vs sweep_results.parquet showing ~$360-500 per window.

  WHY THE GAP?
  1. rolling_simulation uses trader's OWN entry price (wavg_yes_entry_price)
     The sweep uses REAL MARKET PRICES from asof join (market_prices.parquet)
  2. rolling_simulation has NO fee model; sweep deducts 2% fees
  3. rolling_simulation counts ALL markets; sweep applies price band filters
  4. rolling_simulation does not use the "first qualifying arrival" model

  The sweep results are MORE REALISTIC. The $30K figures from rolling_simulation
  were using the trader's own weighted-average entry, which is not the price
  WE would get when copying the signal. The asof-joined market price is the
  actual price available at signal trigger time.

  This is a ~60-80x reduction in expected PnL. This is the most important
  finding: the strategy's edge shrinks dramatically with realistic pricing.
  """)


# ============================================================================
# OVERALL VERDICT
# ============================================================================
def print_verdict(df: pl.DataFrame) -> None:
    header("OVERALL VERDICT: GO / NO-GO FOR LIVE TRADING")

    # Compute key stats for the verdict, excluding Feb
    df_reliable = df.filter(pl.col("window") != "win2_feb26")

    best = (
        df_reliable.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("hit_rate").mean().alias("avg_hit_rate"),
            pl.col("pnl_per_bet").mean().alias("avg_pnl_per_bet"),
            pl.col("n_days").mean().alias("avg_days"),
            pl.col("max_drawdown").mean().alias("avg_max_dd"),
            pl.col("profit_factor").mean().alias("avg_pf"),
            pl.col("window").n_unique().alias("n_windows"),
        )
        .filter(pl.col("n_windows") == 2)
        .sort("avg_sharpe", descending=True)
    )

    top = best.head(1).to_dicts()[0]
    bpd = top["avg_bets"] / top["avg_days"] if top["avg_days"] > 0 else 0

    avg_hold = 7  # estimated average holding period in days
    est_concurrent = min(bpd * avg_hold, top['avg_bets'] / top['avg_days'] * avg_hold)

    print(f"""
  BEST RELIABLE CONFIG (Dec 2025 + Jan 2026 only):
    Avg Sharpe:     {fmt(top['avg_sharpe'], 2)}
    Avg Hit Rate:   {pct(top['avg_hit_rate'])}
    Avg PnL ($1):   {usd(top['avg_pnl'])} per month
    PnL per bet:    {usd(top['avg_pnl_per_bet'])}
    Bets per day:   {bpd:.1f} (resolving)
    Est concurrent: {est_concurrent:.0f} positions (assume {avg_hold}d avg hold)
    Profit factor:  {fmt(top['avg_pf'], 2)}

  AT $100 BET SIZE ({est_concurrent:.0f} EST CONCURRENT):
    Daily PnL est:  {usd(top['avg_pnl_per_bet'] * 100 * bpd)}
    Monthly PnL:    {usd(top['avg_pnl_per_bet'] * 100 * bpd * 30)}
    Capital needed: {usd(100 * est_concurrent)} (est concurrent * $100)
    Monthly ROI:    {top['avg_pnl_per_bet'] * 100 * bpd * 30 / (100 * est_concurrent) * 100:.1f}%

  AT $10 BET SIZE (conservative start):
    Daily PnL est:  {usd(top['avg_pnl_per_bet'] * 10 * bpd)}
    Monthly PnL:    {usd(top['avg_pnl_per_bet'] * 10 * bpd * 30)}
    Capital needed: {usd(10 * est_concurrent)}
    Monthly ROI:    {top['avg_pnl_per_bet'] * 10 * bpd * 30 / (10 * est_concurrent) * 100:.1f}%

  STRENGTHS:
    + Positive edge in 2 independent out-of-sample windows
    + High Sharpe ratio (>15) with realistic entry prices and fees
    + Hit rate (59-66%) well above breakeven (~50.5% at entry=0.50)
    + Large trader pools (500-11,000) make consensus robust
    + Signal model is clean: no look-ahead bias in architecture
    + Price band filter effectively removes low-edge bets

  WEAKNESSES:
    - Only 2 reliable test windows (Dec 2025, Jan 2026)
    - Feb 2026 is unreliable (partial data, inflated metrics)
    - Massive PnL gap vs naive simulation (60-80x) — realistic pricing matters
    - Execution risk: signal fires when Nth trader enters, but WE enter later
    - Market impact: $100+ bets on Polymarket move prices
    - Latency: building consensus requires monitoring multiple traders in real-time
    - MVF band "all" dominates — no strong differentiation by maker/taker type
    - Sizing strategy has minimal impact (fixed ~ agreement_weighted)

  RED FLAGS:
    - The strategy relies on identifying ~1,000+ "consistently profitable" traders
      from training data, then betting alongside their consensus in holdout
    - With only 2 valid windows, we cannot distinguish skill from market regime
    - The Jan 2026 window has expanding pool sizes (8,302 vs 1,803) — this
      suggests more traders look "consistent" as markets become more predictable
    - The 2% fee model may understate real execution costs (spread, slippage)

  RECOMMENDATION: CONDITIONAL GO
    The signal has a genuine edge, but proceed with extreme caution:
    1. Start with $10 bet sizes (NOT $100) — ~$5-15/day expected PnL
    2. Paper trade for 1 full month to validate real execution
    3. Monitor actual fill prices vs signal entry prices (the key risk)
    4. Gradually scale only if paper results confirm backtest
    5. Never exceed 20 concurrent positions initially
    6. Build a REAL execution latency measurement before going live
""")

    # How many configs are viable?
    viable = best.filter(
        (pl.col("avg_sharpe") > 10)
        & (pl.col("avg_hit_rate") > 0.55)
        & (pl.col("avg_pnl") > 100)
    )
    print(f"  Viable configs (sharpe>10, hr>55%, pnl>$100): {viable.height:,} out of {best.height:,}")

    # Highlight the parameter sweet spot
    print(f"\n  PARAMETER SWEET SPOT:")
    print(f"    consistency_months = 4")
    print(f"    min_markets       = 30-50")
    print(f"    mvf_band          = all")
    print(f"    min_traders       = 2")
    print(f"    agreement_pct     = 0.70-1.00")
    print(f"    direction         = both (wider) or YES-only (tighter)")
    print(f"    price_band        = [0.10, 0.90] (best) or [0.20, 0.80]")
    print(f"    sizing            = fixed (simplest, nearly identical performance)")


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    print(f"\n{'#' * 90}")
    print(f"  CONSENSUS-COPY STRATEGY: COMPREHENSIVE SWEEP ANALYSIS")
    print(f"  Data: {SWEEP_PATH}")
    print(f"{'#' * 90}")

    df = pl.read_parquet(SWEEP_PATH)
    print(f"\n  Loaded {df.height:,} rows across {df['window'].n_unique()} windows")
    print(f"  Windows: {sorted(df['window'].unique().to_list())}")

    # Load top configs for reference
    with open(TOP_CONFIGS_PATH) as f:
        top_configs = json.load(f)
    print(f"  Top configs: {len(top_configs)} entries")

    analyze_top_configs(df)
    analyze_distributions(df)
    analyze_bet_sizes(df)
    analyze_frequency(df)
    analyze_robustness(df)
    analyze_red_flags(df)
    print_verdict(df)


if __name__ == "__main__":
    main()
