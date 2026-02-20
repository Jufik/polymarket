"""Comprehensive analysis of consensus-copy strategy sweep results (v3).

Reads sweep_results.parquet (config-driven anchored-expanding walk-forward)
and top_configs.json, with explicit dev/test separation and base-rate metrics.

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
    "execution_delay",
]


def header(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def sub_header(title: str) -> None:
    print(f"\n{SECTION}")
    print(f"  {title}")
    print(SECTION)


def pct(val: float | None) -> str:
    if val is None or math.isnan(val):
        return "N/A"
    return f"{val * 100:.2f}%"


def usd(val: float | None) -> str:
    if val is None or math.isnan(val):
        return "N/A"
    return f"${val:,.2f}"


def fmt(val: float | None, decimals: int = 4) -> str:
    if val is None or math.isnan(val):
        return "N/A"
    return f"{val:.{decimals}f}"


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=False)) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0


# ============================================================================
# A. OVERVIEW
# ============================================================================
def analyze_overview(df: pl.DataFrame) -> None:
    header("A. OVERVIEW")

    dev = df.filter(~pl.col("is_test"))
    test = df.filter(pl.col("is_test"))

    print(f"\n  Total rows: {df.height:,}")
    print(f"  Dev rows:   {dev.height:,} across {dev['window'].n_unique()} windows")
    print(f"  Test rows:  {test.height:,} across {test['window'].n_unique()} windows")
    print(f"\n  Dev windows:  {sorted(dev['window'].unique().to_list())}")
    print(f"  Test windows: {sorted(test['window'].unique().to_list())}")

    print(f"\n  Config dimensions:")
    for col in CONFIG_COLS:
        vals = sorted(str(v) for v in df[col].unique().to_list())
        print(f"    {col}: {vals}")

    sub_header("Per-window summary")
    print(f"  {'Window':<20} {'Type':>5} {'Rows':>7} {'MedHR':>8} {'MedSharpe':>10} "
          f"{'MedPnL':>12} {'MedExHR':>10} {'MedBASh':>10}")

    for row in (
        df.group_by(["window", "is_test"])
        .agg(
            pl.len().alias("n"),
            pl.col("hit_rate").median().alias("med_hr"),
            pl.col("sharpe").median().alias("med_sharpe"),
            pl.col("total_pnl").median().alias("med_pnl"),
            pl.col("excess_hr").median().alias("med_excess_hr"),
            pl.col("base_adjusted_sharpe").median().alias("med_ba_sharpe"),
        )
        .sort("window")
        .to_dicts()
    ):
        wtype = "TEST" if row["is_test"] else "dev"
        print(
            f"  {row['window']:<20} {wtype:>5} {row['n']:>7} "
            f"{pct(row['med_hr']):>8} {fmt(row['med_sharpe'], 2):>10} "
            f"{usd(row['med_pnl']):>12} {pct(row['med_excess_hr']):>10} "
            f"{fmt(row['med_ba_sharpe'], 2):>10}"
        )


# ============================================================================
# B. DEV-ONLY RANKING (top configs by stability)
# ============================================================================
def analyze_dev_ranking(df: pl.DataFrame) -> None:
    header("B. DEV-ONLY RANKING (top 15 by avg dev sharpe)")

    dev = df.filter(~pl.col("is_test"))
    grouped = (
        dev.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("sharpe").std().alias("std_sharpe"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("hit_rate").mean().alias("avg_hr"),
            pl.col("excess_hr").mean().alias("avg_excess_hr"),
            pl.col("base_adjusted_sharpe").mean().alias("avg_ba_sharpe"),
            pl.col("pool_size").mean().alias("avg_pool"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("pnl_per_bet").mean().alias("avg_pnl_per_bet"),
            pl.col("window").n_unique().alias("n_dev_windows"),
        )
        .filter(pl.col("n_dev_windows") >= 2)
        .sort("avg_sharpe", descending=True)
    )

    print(f"\n  Configs present in >= 2 dev windows: {grouped.height:,}")
    top15 = grouped.head(15).to_dicts()

    for rank, cfg in enumerate(top15, 1):
        print(f"\n  #{rank}: avg_sharpe={fmt(cfg['avg_sharpe'], 2)}, "
              f"avg_excess_hr={pct(cfg['avg_excess_hr'])}, "
              f"avg_ba_sharpe={fmt(cfg['avg_ba_sharpe'], 2)}")
        print(f"    months={cfg['consistency_months']} mkts={cfg['min_markets']} "
              f"mvf={cfg['mvf_band']} traders>={cfg['min_traders']} "
              f"agree={cfg['agreement_pct']} dir={cfg['direction']} "
              f"band=[{cfg['price_band_lo']},{cfg['price_band_hi']}] "
              f"sizing={cfg['sizing']}")
        print(f"    avg_pnl={usd(cfg['avg_pnl'])} avg_hr={pct(cfg['avg_hr'])} "
              f"avg_pool={cfg['avg_pool']:.0f} avg_bets={cfg['avg_bets']:.0f} "
              f"pnl/bet={usd(cfg['avg_pnl_per_bet'])}")

        # Per-window breakdown
        mask = pl.lit(True)
        for col in CONFIG_COLS:
            mask = mask & (pl.col(col) == cfg[col])
        windows = dev.filter(mask).sort("window")
        for row in windows.to_dicts():
            print(f"      {row['window']}: sharpe={row['sharpe']:.2f} "
                  f"hr={pct(row['hit_rate'])} ex_hr={pct(row['excess_hr'])} "
                  f"pnl={usd(row['total_pnl'])} bets={row['total_bets']} "
                  f"pool={row['pool_size']}")

    return grouped


# ============================================================================
# C. OOS VALIDATION (test window performance of dev-ranked configs)
# ============================================================================
def analyze_oos(df: pl.DataFrame) -> None:
    header("C. OUT-OF-SAMPLE VALIDATION (test window)")

    dev = df.filter(~pl.col("is_test"))
    test = df.filter(pl.col("is_test"))

    if test.height == 0:
        print("\n  No test windows found. Skipping OOS analysis.")
        return

    # Rank by dev performance
    dev_ranked = (
        dev.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("dev_avg_sharpe"),
            pl.col("hit_rate").mean().alias("dev_avg_hr"),
            pl.col("excess_hr").mean().alias("dev_avg_excess_hr"),
            pl.col("total_pnl").mean().alias("dev_avg_pnl"),
            pl.col("base_adjusted_sharpe").mean().alias("dev_avg_ba_sharpe"),
            pl.col("window").n_unique().alias("n_dev_windows"),
        )
        .filter(pl.col("n_dev_windows") >= 2)
        .sort("dev_avg_sharpe", descending=True)
    )

    # Join with test results
    test_stats = (
        test.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("test_sharpe"),
            pl.col("hit_rate").mean().alias("test_hr"),
            pl.col("excess_hr").mean().alias("test_excess_hr"),
            pl.col("total_pnl").mean().alias("test_pnl"),
            pl.col("base_adjusted_sharpe").mean().alias("test_ba_sharpe"),
            pl.col("total_bets").mean().alias("test_bets"),
            pl.col("pool_size").mean().alias("test_pool"),
            pl.col("pnl_per_bet").mean().alias("test_pnl_per_bet"),
        )
    )

    joined = dev_ranked.join(test_stats, on=CONFIG_COLS, how="left")

    sub_header("Top 20 dev-ranked configs: OOS performance")
    print(f"  {'Rank':>4} {'DevSh':>7} {'TestSh':>7} {'DevHR':>7} {'TestHR':>7} "
          f"{'DevExHR':>8} {'TestExHR':>8} {'DevPnL':>10} {'TestPnL':>10} "
          f"{'TestBets':>8} {'Sizing':<20}")

    top20 = joined.head(20).to_dicts()
    for i, row in enumerate(top20, 1):
        test_sh = fmt(row.get("test_sharpe"), 2) if row.get("test_sharpe") is not None else "N/A"
        test_hr = pct(row.get("test_hr"))
        test_ex = pct(row.get("test_excess_hr"))
        test_p = usd(row.get("test_pnl"))
        test_b = f"{row.get('test_bets', 0):.0f}" if row.get("test_bets") is not None else "N/A"

        print(
            f"  {i:>4} {fmt(row['dev_avg_sharpe'], 2):>7} {test_sh:>7} "
            f"{pct(row['dev_avg_hr']):>7} {test_hr:>7} "
            f"{pct(row['dev_avg_excess_hr']):>8} {test_ex:>8} "
            f"{usd(row['dev_avg_pnl']):>10} {test_p:>10} "
            f"{test_b:>8} {row['sizing']:<20}"
        )

    # Aggregate OOS statistics
    sub_header("OOS aggregate statistics")

    has_test = joined.filter(pl.col("test_sharpe").is_not_null())
    n_with_test = has_test.height
    n_total = joined.height

    print(f"\n  Configs with test results: {n_with_test}/{n_total}")

    if n_with_test > 0:
        # Dev vs test correlation
        dev_sh = has_test["dev_avg_sharpe"].to_list()
        test_sh = has_test["test_sharpe"].to_list()
        dev_hr = has_test["dev_avg_hr"].to_list()
        test_hr = has_test["test_hr"].to_list()

        print(f"  Correlation(dev_sharpe, test_sharpe):  {pearson(dev_sh, test_sh):+.4f}")
        print(f"  Correlation(dev_hr, test_hr):          {pearson(dev_hr, test_hr):+.4f}")

        # How many dev-positive configs remain positive OOS?
        dev_pos = has_test.filter(pl.col("dev_avg_sharpe") > 0)
        dev_pos_test_pos = dev_pos.filter(pl.col("test_sharpe") > 0)
        print(f"\n  Dev sharpe > 0:                       {dev_pos.height}")
        print(f"  Of those, test sharpe > 0:             {dev_pos_test_pos.height} "
              f"({pct(dev_pos_test_pos.height / dev_pos.height) if dev_pos.height > 0 else 'N/A'})")

        dev_pos_ex = has_test.filter(pl.col("dev_avg_excess_hr") > 0)
        dev_pos_test_pos_ex = dev_pos_ex.filter(pl.col("test_excess_hr") > 0)
        print(f"\n  Dev excess_hr > 0:                    {dev_pos_ex.height}")
        print(f"  Of those, test excess_hr > 0:          {dev_pos_test_pos_ex.height} "
              f"({pct(dev_pos_test_pos_ex.height / dev_pos_ex.height) if dev_pos_ex.height > 0 else 'N/A'})")

        # Top-N dev configs: OOS performance
        for n in [10, 25, 50]:
            topn = has_test.head(n)
            if topn.height == 0:
                continue
            med_test_sh = topn["test_sharpe"].median()
            med_test_hr = topn["test_hr"].median()
            med_test_pnl = topn["test_pnl"].median()
            med_test_ex = topn["test_excess_hr"].median()
            pct_pos = topn.filter(pl.col("test_sharpe") > 0).height / topn.height
            print(f"\n  Top {n} dev configs OOS:")
            print(f"    Med test sharpe:     {fmt(med_test_sh, 2)}")
            print(f"    Med test HR:         {pct(med_test_hr)}")
            print(f"    Med test excess_hr:  {pct(med_test_ex)}")
            print(f"    Med test PnL:        {usd(med_test_pnl)}")
            print(f"    % with test sh > 0:  {pct(pct_pos)}")


# ============================================================================
# D. PARAMETER SENSITIVITY
# ============================================================================
def analyze_parameters(df: pl.DataFrame) -> None:
    header("D. PARAMETER SENSITIVITY (dev windows only)")

    dev = df.filter(~pl.col("is_test"))

    pool_perf = (
        dev.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("avg_sharpe"),
            pl.col("hit_rate").mean().alias("avg_hr"),
            pl.col("excess_hr").mean().alias("avg_excess_hr"),
            pl.col("total_pnl").mean().alias("avg_pnl"),
            pl.col("pool_size").mean().alias("avg_pool"),
            pl.col("total_bets").mean().alias("avg_bets"),
            pl.col("window").n_unique().alias("n_windows"),
        )
        .filter(pl.col("n_windows") >= 2)
    )

    # By sizing strategy
    sub_header("D1. Sizing strategy")
    print(f"\n  {'Sizing':<22} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'%Sh>0':>7}")
    for sz in sorted(dev["sizing"].unique().to_list()):
        subset = pool_perf.filter(pl.col("sizing") == sz)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {sz:<22} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {pct(pct_pos):>7}"
        )

    # By MVF band
    sub_header("D2. MVF band")
    print(f"\n  {'MVF Band':<18} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'%Sh>0':>7}")
    for band in sorted(dev["mvf_band"].unique().to_list()):
        subset = pool_perf.filter(pl.col("mvf_band") == band)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {band:<18} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {pct(pct_pos):>7}"
        )

    # By consistency_months
    sub_header("D3. Consistency months")
    print(f"\n  {'Months':>6} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'%Sh>0':>7}")
    for cm in sorted(dev["consistency_months"].unique().to_list()):
        subset = pool_perf.filter(pl.col("consistency_months") == cm)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {cm:>6} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {pct(pct_pos):>7}"
        )

    # By min_markets
    sub_header("D4. Min markets")
    print(f"\n  {'MinMkts':>7} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'%Sh>0':>7}")
    for mm in sorted(dev["min_markets"].unique().to_list()):
        subset = pool_perf.filter(pl.col("min_markets") == mm)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {mm:>7} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {pct(pct_pos):>7}"
        )

    # By agreement_pct
    sub_header("D5. Agreement threshold")
    print(f"\n  {'Agree':>7} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'%Sh>0':>7}")
    for ap in sorted(dev["agreement_pct"].unique().to_list()):
        subset = pool_perf.filter(pl.col("agreement_pct") == ap)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {ap:>7.0%} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {pct(pct_pos):>7}"
        )

    # By min_traders
    sub_header("D6. Min traders threshold")
    print(f"\n  {'MinT':>6} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'AvgPool':>8} {'%Sh>0':>7}")
    for mt in sorted(dev["min_traders"].unique().to_list()):
        subset = pool_perf.filter(pl.col("min_traders") == mt)
        if subset.height < 3:
            continue
        pct_pos = subset.filter(pl.col("avg_sharpe") > 0).height / subset.height
        print(
            f"  {mt:>6} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8} "
            f"{usd(subset['avg_pnl'].median()):>10} {subset['avg_pool'].median():>8.0f} "
            f"{pct(pct_pos):>7}"
        )

    # By price band
    sub_header("D7. Price band")
    print(f"\n  {'Band':<14} {'Count':>6} {'MedSh':>8} {'MedHR':>8} "
          f"{'MedExHR':>8} {'MedPnL':>10} {'MedBets':>8} {'%Sh>0':>7}")
    for row in (
        pool_perf.group_by(["price_band_lo", "price_band_hi"])
        .agg(
            pl.col("avg_sharpe").median().alias("med_sharpe"),
            pl.col("avg_hr").median().alias("med_hr"),
            pl.col("avg_excess_hr").median().alias("med_ex_hr"),
            pl.col("avg_pnl").median().alias("med_pnl"),
            pl.col("avg_bets").median().alias("med_bets"),
            pl.len().alias("count"),
            (pl.col("avg_sharpe") > 0).sum().alias("n_pos"),
        )
        .sort("med_sharpe", descending=True)
        .to_dicts()
    ):
        pct_pos = row["n_pos"] / row["count"] if row["count"] > 0 else 0
        band_label = f"[{row['price_band_lo']:.2f},{row['price_band_hi']:.2f}]"
        print(
            f"  {band_label:<14} {row['count']:>6} {fmt(row['med_sharpe'], 2):>8} "
            f"{pct(row['med_hr']):>8} {pct(row['med_ex_hr']):>8} "
            f"{usd(row['med_pnl']):>10} {row['med_bets']:>8.0f} {pct(pct_pos):>7}"
        )

    # Pool size vs performance correlation
    sub_header("D8. Pool size vs performance")
    if pool_perf.height > 10:
        pool_arr = pool_perf["avg_pool"].to_list()
        sharpe_arr = pool_perf["avg_sharpe"].to_list()
        hr_arr = pool_perf["avg_hr"].to_list()
        exhr_arr = pool_perf["avg_excess_hr"].to_list()
        print(f"\n  Correlation(pool_size, sharpe):     {pearson(pool_arr, sharpe_arr):+.4f}")
        print(f"  Correlation(pool_size, hit_rate):   {pearson(pool_arr, hr_arr):+.4f}")
        print(f"  Correlation(pool_size, excess_hr):  {pearson(pool_arr, exhr_arr):+.4f}")

    pool_bins = [
        ("< 100", 0, 100),
        ("100-500", 100, 500),
        ("500-2000", 500, 2000),
        ("2000+", 2000, 1_000_000),
    ]
    print(f"\n  {'Pool Range':<12} {'Count':>6} {'MedSh':>8} {'MedHR':>8} {'MedExHR':>8}")
    for label, lo, hi in pool_bins:
        subset = pool_perf.filter(
            (pl.col("avg_pool") >= lo) & (pl.col("avg_pool") < hi)
        )
        if subset.height == 0:
            continue
        print(
            f"  {label:<12} {subset.height:>6} {fmt(subset['avg_sharpe'].median(), 2):>8} "
            f"{pct(subset['avg_hr'].median()):>8} {pct(subset['avg_excess_hr'].median()):>8}"
        )


# ============================================================================
# E. BASE RATE ANALYSIS
# ============================================================================
def analyze_base_rates(df: pl.DataFrame) -> None:
    header("E. BASE RATE ANALYSIS")

    sub_header("E1. Excess hit rate distribution")
    dev = df.filter(~pl.col("is_test"))

    print(f"\n  Dev excess_hr stats:")
    exhr = dev["excess_hr"]
    print(f"    Mean:   {pct(exhr.mean())}")
    print(f"    Median: {pct(exhr.median())}")
    print(f"    Std:    {pct(exhr.std())}")
    print(f"    P10:    {pct(exhr.quantile(0.10))}")
    print(f"    P25:    {pct(exhr.quantile(0.25))}")
    print(f"    P75:    {pct(exhr.quantile(0.75))}")
    print(f"    P90:    {pct(exhr.quantile(0.90))}")
    n_pos = dev.filter(pl.col("excess_hr") > 0).height
    print(f"    % positive: {pct(n_pos / dev.height)}")

    sub_header("E2. Base-adjusted Sharpe distribution")
    bash = dev["base_adjusted_sharpe"]
    print(f"\n  Dev base_adjusted_sharpe stats:")
    print(f"    Mean:   {fmt(bash.mean(), 2)}")
    print(f"    Median: {fmt(bash.median(), 2)}")
    print(f"    Std:    {fmt(bash.std(), 2)}")
    print(f"    P10:    {fmt(bash.quantile(0.10), 2)}")
    print(f"    P90:    {fmt(bash.quantile(0.90), 2)}")
    n_pos_ba = dev.filter(pl.col("base_adjusted_sharpe") > 0).height
    print(f"    % positive: {pct(n_pos_ba / dev.height)}")

    sub_header("E3. Raw HR vs excess HR by window")
    for w in sorted(dev["window"].unique().to_list()):
        w_df = dev.filter(pl.col("window") == w)
        raw_hr = w_df["hit_rate"].median()
        ex_hr = w_df["excess_hr"].median()
        # Base rate = raw_hr - excess_hr (approximately)
        base_rate = raw_hr - ex_hr if raw_hr is not None and ex_hr is not None else None
        print(f"  {w:<20}: raw_hr={pct(raw_hr)}, excess_hr={pct(ex_hr)}, "
              f"implied_base={pct(base_rate)}")


# ============================================================================
# F. REALISTIC P&L PROJECTIONS
# ============================================================================
def analyze_pnl_projections(df: pl.DataFrame) -> None:
    header("F. REALISTIC P&L PROJECTIONS")

    dev = df.filter(~pl.col("is_test"))
    test = df.filter(pl.col("is_test"))

    # Use dev-ranked top config that also has test results
    dev_ranked = (
        dev.group_by(CONFIG_COLS)
        .agg(
            pl.col("sharpe").mean().alias("dev_sharpe"),
            pl.col("total_pnl").mean().alias("dev_pnl"),
            pl.col("total_bets").mean().alias("dev_bets"),
            pl.col("hit_rate").mean().alias("dev_hr"),
            pl.col("pnl_per_bet").mean().alias("dev_pnl_per_bet"),
            pl.col("n_days").mean().alias("dev_days"),
            pl.col("max_drawdown").mean().alias("dev_max_dd"),
            pl.col("excess_hr").mean().alias("dev_excess_hr"),
            pl.col("window").n_unique().alias("n_dev"),
        )
        .filter(pl.col("n_dev") >= 2)
        .sort("dev_sharpe", descending=True)
    )

    if test.height > 0:
        test_agg = (
            test.group_by(CONFIG_COLS)
            .agg(
                pl.col("pnl_per_bet").mean().alias("test_pnl_per_bet"),
                pl.col("total_bets").mean().alias("test_bets"),
                pl.col("n_days").mean().alias("test_days"),
                pl.col("hit_rate").mean().alias("test_hr"),
            )
        )
        dev_ranked = dev_ranked.join(test_agg, on=CONFIG_COLS, how="left")

    # Show top 5 with projections
    for rank, cfg in enumerate(dev_ranked.head(5).to_dicts(), 1):
        sub_header(f"Config #{rank} P&L projection")

        dev_ppb = cfg["dev_pnl_per_bet"]
        dev_bpd = cfg["dev_bets"] / cfg["dev_days"] if cfg["dev_days"] > 0 else 0
        test_ppb = cfg.get("test_pnl_per_bet")
        test_bpd = (
            cfg.get("test_bets", 0) / cfg.get("test_days", 1)
            if cfg.get("test_days", 0) > 0 else 0
        )

        print(f"  Dev:  pnl/bet={usd(dev_ppb)} bets/day={dev_bpd:.1f} "
              f"hr={pct(cfg['dev_hr'])} excess_hr={pct(cfg['dev_excess_hr'])}")
        if test_ppb is not None:
            print(f"  Test: pnl/bet={usd(test_ppb)} bets/day={test_bpd:.1f} "
                  f"hr={pct(cfg.get('test_hr'))}")

        # Use test if available, otherwise dev
        ppb = test_ppb if test_ppb is not None else dev_ppb
        bpd = test_bpd if test_bpd > 0 else dev_bpd
        label = "test" if test_ppb is not None else "dev"

        avg_hold = 7
        est_concurrent = min(bpd * avg_hold, 200)

        print(f"\n  Projections (using {label} pnl/bet, hold={avg_hold}d):")
        print(f"  {'Bet$':>8} {'Daily':>10} {'Monthly':>12} {'Annual':>12} "
              f"{'Capital':>10} {'ROI/mo':>8}")
        for bet in [10, 50, 100, 500]:
            daily = ppb * bet * bpd
            monthly = daily * 30
            annual = daily * 365
            capital = bet * est_concurrent
            roi = monthly / capital * 100 if capital > 0 else 0
            print(
                f"  {usd(bet):>8} {usd(daily):>10} {usd(monthly):>12} {usd(annual):>12} "
                f"{usd(capital):>10} {roi:>7.1f}%"
            )


# ============================================================================
# G. VERDICT
# ============================================================================
def print_verdict(df: pl.DataFrame) -> None:
    header("G. VERDICT")

    dev = df.filter(~pl.col("is_test"))
    test = df.filter(pl.col("is_test"))

    # Key dev stats
    n_dev = dev.height
    n_pos_exhr_dev = dev.filter(pl.col("excess_hr") > 0).height
    pct_pos_dev = n_pos_exhr_dev / n_dev if n_dev > 0 else 0

    print(f"\n  DEV SUMMARY:")
    print(f"    {n_dev} config-window rows, {pct(pct_pos_dev)} have positive excess_hr")
    print(f"    Median excess_hr: {pct(dev['excess_hr'].median())}")
    print(f"    Median sharpe: {fmt(dev['sharpe'].median(), 2)}")

    if test.height > 0:
        n_test = test.height
        n_pos_exhr_test = test.filter(pl.col("excess_hr") > 0).height
        pct_pos_test = n_pos_exhr_test / n_test if n_test > 0 else 0

        print(f"\n  TEST (OOS) SUMMARY:")
        print(f"    {n_test} config-window rows, {pct(pct_pos_test)} have positive excess_hr")
        print(f"    Median excess_hr: {pct(test['excess_hr'].median())}")
        print(f"    Median sharpe: {fmt(test['sharpe'].median(), 2)}")

    # Overall direction
    all_excess = df["excess_hr"]
    overall_pos_pct = df.filter(pl.col("excess_hr") > 0).height / df.height

    direction_note = ""
    directions = df["direction"].unique().to_list()
    if len(directions) == 1 and directions[0] == "both":
        direction_note = (
            "\n  NOTE: This sweep used direction='both' only. Prior analysis found"
            "\n  NO-only strongly dominates. Consider re-running with NO-only direction"
            "\n  to see if the signal improves when the anti-predictive YES bets are removed."
        )

    print(f"""
  OVERALL:
    {pct(overall_pos_pct)} of all config-window combos beat their base rate
    Median excess_hr across all data: {pct(all_excess.median())}
    {direction_note}

  INTERPRETATION:
    - excess_hr < 0 means the strategy underperforms a naive base-rate bettor
    - With direction='both', the signal mixes YES bets (which prior analysis
      showed are anti-predictive: 18.5% HR vs 38.1% base) with NO bets
    - This dilutes whatever NO-only edge exists

  NEXT STEPS:
    1. Re-run sweep with directions = ["NO-only"] to isolate the proven signal
    2. If NO-only shows positive excess_hr, that confirms the edge is real
    3. The "both" direction results serve as a useful negative control
    4. Compare sizing strategies under NO-only — kelly/edge_weighted may
       amplify the edge more effectively when the underlying signal is clean
""")


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    print(f"\n{'#' * 90}")
    print(f"  CONSENSUS-COPY STRATEGY: V3 SWEEP ANALYSIS")
    print(f"  Data: {SWEEP_PATH}")
    print(f"{'#' * 90}")

    df = pl.read_parquet(SWEEP_PATH)
    print(f"\n  Loaded {df.height:,} rows across {df['window'].n_unique()} windows")
    print(f"  Windows: {sorted(df['window'].unique().to_list())}")

    if TOP_CONFIGS_PATH.exists():
        with open(TOP_CONFIGS_PATH) as f:
            top_configs = json.load(f)
        print(f"  Top configs: {len(top_configs)} entries")

    analyze_overview(df)
    analyze_dev_ranking(df)
    analyze_oos(df)
    analyze_parameters(df)
    analyze_base_rates(df)
    analyze_pnl_projections(df)
    print_verdict(df)


if __name__ == "__main__":
    main()
