"""S1 research: HR-based pool selection on positions held at resolution.

Two key corrections from previous research:
1. Pool selected on RESOLUTION HIT RATE, not PnL (avoids whale bias)
2. Only count positions where trader still holds at resolution (net_yes_tokens != 0)

Optimized: pre-compute all trainer stats ONCE per window, then sweep thresholds
via fast DataFrame filtering.

Usage:
    uv run python scripts/research_s1_hr_pool.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import polars as pl

DATA_DIR = Path("data/derived")
TRAIN_START = datetime(2023, 1, 1)
FEE_PCT = 0.02
BASE_BET = 100.0


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_data() -> tuple[pl.DataFrame, pl.DataFrame]:
    pnl = pl.read_parquet(DATA_DIR / "trader_market_pnl.parquet")
    resolved = pl.read_parquet(DATA_DIR / "markets_resolved.parquet")

    for col in ["resolved_at"]:
        dtype = resolved.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            resolved = resolved.with_columns(pl.col(col).dt.replace_time_zone(None))
    for col in ["first_trade", "last_trade"]:
        dtype = pnl.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            pnl = pnl.with_columns(pl.col(col).dt.replace_time_zone(None))

    # Join + filter: only positions HELD at resolution (net_yes_tokens != 0)
    df = pnl.join(resolved, on="condition_id", how="inner")
    df = df.filter(pl.col("net_yes_tokens") != 0)

    # Enrich
    df = df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes"),
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry"),
        (
            ((pl.col("net_yes_tokens") > 0) & pl.col("yes_won"))
            | ((pl.col("net_yes_tokens") <= 0) & ~pl.col("yes_won"))
        ).alias("correct"),
        pl.col("resolved_at").dt.strftime("%Y%m").alias("month"),
    )

    return df, resolved


# ---------------------------------------------------------------------------
# Pre-compute trader stats (ONCE per window, then reuse for all thresholds)
# ---------------------------------------------------------------------------

def compute_trader_stats(df: pl.DataFrame, train_end: datetime) -> pl.DataFrame:
    """Per-trader resolution-based stats for the training window.

    Returns one row per trader with:
      hr, n_markets, yes_frac, median_entry, active_months,
      yes_hr, no_hr, n_yes, n_no, good_months_50, good_months_55, good_months_60
    """
    train = df.filter(
        (pl.col("resolved_at") >= TRAIN_START)
        & (pl.col("resolved_at") < train_end)
    )

    # Overall stats
    overall = train.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        pl.col("bet_yes").mean().alias("yes_frac"),
        pl.col("dir_entry").median().alias("median_entry"),
        pl.col("month").n_unique().alias("active_months"),
        (pl.col("correct").filter(pl.col("bet_yes"))).mean().alias("yes_hr"),
        (pl.col("correct").filter(~pl.col("bet_yes"))).mean().alias("no_hr"),
        pl.col("bet_yes").sum().alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
    )

    # Monthly consistency: count months with HR > threshold (min 3 markets)
    monthly = train.group_by(["trader", "month"]).agg(
        pl.col("correct").mean().alias("month_hr"),
        pl.len().alias("month_n"),
    ).filter(pl.col("month_n") >= 3)

    for thr, col_name in [(0.50, "good_months_50"), (0.55, "good_months_55"),
                           (0.60, "good_months_60")]:
        m_good = monthly.filter(pl.col("month_hr") >= thr).group_by("trader").agg(
            pl.len().alias(col_name),
        )
        overall = overall.join(m_good, on="trader", how="left")

    overall = overall.with_columns(
        pl.col("good_months_50").fill_null(0),
        pl.col("good_months_55").fill_null(0),
        pl.col("good_months_60").fill_null(0),
    )

    return overall


def filter_pool(
    stats: pl.DataFrame,
    *,
    min_markets: int = 30,
    min_hr: float = 0.55,
    min_months: int = 6,
    monthly_min_hr: float = 0.50,
) -> frozenset[str]:
    """Fast pool filtering from pre-computed stats."""
    if monthly_min_hr >= 0.60:
        good_col = "good_months_60"
    elif monthly_min_hr >= 0.55:
        good_col = "good_months_55"
    else:
        good_col = "good_months_50"

    filtered = stats.filter(
        (pl.col("n_markets") >= min_markets)
        & (pl.col("hr") >= min_hr)
        & (pl.col("active_months") >= min_months)
        & (pl.col(good_col) >= min_months)
    )
    return frozenset(filtered["trader"].to_list())


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward_windows():
    windows = []
    for year, month in [
        (2025, 5), (2025, 6), (2025, 7), (2025, 8), (2025, 9),
        (2025, 10), (2025, 11), (2025, 12), (2026, 1),
    ]:
        train_end = datetime(year, month, 1)
        nm = month + 1
        ny = year
        if nm > 12:
            nm = 1
            ny += 1
        holdout_end = datetime(ny, nm, 1)
        windows.append((train_end, train_end, holdout_end))
    return windows


def get_holdout(df: pl.DataFrame, pool: frozenset[str],
                start: datetime, end: datetime) -> pl.DataFrame:
    return df.filter(
        (pl.col("resolved_at") >= start)
        & (pl.col("resolved_at") < end)
        & pl.col("trader").is_in(list(pool))
    )


# ---------------------------------------------------------------------------
# PnL
# ---------------------------------------------------------------------------

def compute_pnl(df: pl.DataFrame, label: str = "") -> dict:
    if df.height == 0:
        return {"label": label, "n": 0, "hr": 0.0, "pnl": 0.0, "pnl_per": 0.0, "avg_entry": 0.0}

    df = df.with_columns(
        pl.when(pl.col("correct"))
        .then(
            pl.lit(BASE_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BASE_BET) * FEE_PCT
        )
        .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
        .alias("pnl")
    )

    n = df.height
    hr = float(df["correct"].mean())
    total_pnl = float(df["pnl"].sum())
    return {
        "label": label, "n": n, "hr": hr, "pnl": total_pnl,
        "pnl_per": total_pnl / n, "avg_entry": float(df["dir_entry"].mean()),
    }


def pr(r: dict) -> None:
    if r["n"] == 0:
        print(f"  {r['label']:<60} n=0")
        return
    print(
        f"  {r['label']:<60} n={r['n']:>6,}  HR={r['hr']:>5.1%}"
        f"  PnL=${r['pnl']:>10,.0f}  $/sig=${r['pnl_per']:>7.1f}"
        f"  avg_e={r.get('avg_entry', 0):.3f}"
    )


# ---------------------------------------------------------------------------
# Pre-compute all window stats (THE KEY OPTIMIZATION)
# ---------------------------------------------------------------------------

def precompute_all_windows(df: pl.DataFrame) -> dict[datetime, pl.DataFrame]:
    """Compute trader stats for each walk-forward window ONCE."""
    window_stats = {}
    for train_end, _, _ in walk_forward_windows():
        t0 = time.time()
        stats = compute_trader_stats(df, train_end)
        elapsed = time.time() - t0
        print(f"  [stats] train_end={train_end.strftime('%Y-%m')} "
              f"traders={stats.height:,} ({elapsed:.1f}s)", flush=True)
        window_stats[train_end] = stats
    return window_stats


# ---------------------------------------------------------------------------
# T1: Pool sweep
# ---------------------------------------------------------------------------

def t1_pool_sweep(df, window_stats):
    print("\n" + "=" * 70)
    print("  T1: HR-BASED POOL SWEEP")
    print("  Find best min_hr / min_markets / min_months combination")
    print("=" * 70, flush=True)

    configs = [
        ("hr>50% mk>=20 mo>=6", 20, 0.50, 6, 0.50),
        ("hr>50% mk>=30 mo>=6", 30, 0.50, 6, 0.50),
        ("hr>50% mk>=50 mo>=6", 50, 0.50, 6, 0.50),
        ("hr>55% mk>=20 mo>=6", 20, 0.55, 6, 0.50),
        ("hr>55% mk>=30 mo>=6", 30, 0.55, 6, 0.50),
        ("hr>55% mk>=50 mo>=6", 50, 0.55, 6, 0.50),
        ("hr>55% mk>=30 mo>=9", 30, 0.55, 9, 0.50),
        ("hr>60% mk>=20 mo>=6", 20, 0.60, 6, 0.50),
        ("hr>60% mk>=30 mo>=6", 30, 0.60, 6, 0.50),
        ("hr>60% mk>=50 mo>=6", 50, 0.60, 6, 0.50),
        ("hr>60% mk>=30 mo>=9", 30, 0.60, 9, 0.50),
        ("hr>65% mk>=20 mo>=6", 20, 0.65, 6, 0.50),
        ("hr>65% mk>=30 mo>=6", 30, 0.65, 6, 0.50),
        ("hr>65% mk>=50 mo>=6", 50, 0.65, 6, 0.50),
        ("hr>70% mk>=20 mo>=6", 20, 0.70, 6, 0.50),
        ("hr>70% mk>=30 mo>=6", 30, 0.70, 6, 0.50),
        ("hr>55% mk>=30 mo>=6 mhr>55%", 30, 0.55, 6, 0.55),
        ("hr>60% mk>=30 mo>=6 mhr>55%", 30, 0.60, 6, 0.55),
        ("hr>55% mk>=30 mo>=6 mhr>60%", 30, 0.55, 6, 0.60),
    ]

    for label, mk, hr_min, mo, mhr in configs:
        all_sigs = []
        pool_sizes = []

        for train_end, hs, he in walk_forward_windows():
            stats = window_stats[train_end]
            pool = filter_pool(stats, min_markets=mk, min_hr=hr_min,
                               min_months=mo, monthly_min_hr=mhr)
            if len(pool) < 3:
                continue
            pool_sizes.append(len(pool))
            holdout = get_holdout(df, pool, hs, he)
            if holdout.height > 0:
                all_sigs.append(holdout)

        if all_sigs:
            combined = pl.concat(all_sigs, how="diagonal_relaxed")
            r = compute_pnl(combined, label)
            avg_pool = sum(pool_sizes) / len(pool_sizes)
            print(f"  {label:<55} n={r['n']:>6,}  HR={r['hr']:>5.1%}"
                  f"  $/sig=${r['pnl_per']:>7.1f}  pool~{avg_pool:.0f}", flush=True)
        else:
            print(f"  {label:<55} n=0 (pools too small)", flush=True)


# ---------------------------------------------------------------------------
# T2: Direction + entry band
# ---------------------------------------------------------------------------

def t2_direction_entry(df, window_stats):
    print("\n" + "=" * 70)
    print("  T2: DIRECTION + ENTRY BAND (on HR-selected pool)")
    print("=" * 70, flush=True)

    for pool_label, mk, hr_min, mo, mhr in [
        ("HR>55% mk>=30 mo>=6", 30, 0.55, 6, 0.50),
        ("HR>60% mk>=30 mo>=6", 30, 0.60, 6, 0.50),
    ]:
        print(f"\n  --- Pool: {pool_label} ---", flush=True)
        all_sigs = []
        for train_end, hs, he in walk_forward_windows():
            stats = window_stats[train_end]
            pool = filter_pool(stats, min_markets=mk, min_hr=hr_min,
                               min_months=mo, monthly_min_hr=mhr)
            if len(pool) < 3:
                continue
            holdout = get_holdout(df, pool, hs, he)
            if holdout.height > 0:
                all_sigs.append(holdout)

        if not all_sigs:
            print("  No data", flush=True)
            continue

        combined = pl.concat(all_sigs, how="diagonal_relaxed")
        pr(compute_pnl(combined, "ALL"))
        pr(compute_pnl(combined.filter(pl.col("bet_yes")), "YES-only"))
        pr(compute_pnl(combined.filter(~pl.col("bet_yes")), "NO-only"))

        bands = [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.0)]
        for dlabel, filt in [
            ("ALL", combined),
            ("NO", combined.filter(~pl.col("bet_yes"))),
            ("YES", combined.filter(pl.col("bet_yes"))),
        ]:
            print(f"\n  {dlabel} by entry band:", flush=True)
            for lo, hi in bands:
                band = filt.filter(
                    (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
                )
                if band.height >= 10:
                    pr(compute_pnl(band, f"  {dlabel} {lo:.0%}-{hi:.0%}"))
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# T3: Base rate comparison
# ---------------------------------------------------------------------------

def t3_base_rate(df, window_stats):
    print("\n" + "=" * 70)
    print("  T3: BASE RATE — HR POOL vs ALL TRADERS vs MARKET IMPLIED")
    print("=" * 70, flush=True)

    all_sigs = []
    for train_end, hs, he in walk_forward_windows():
        stats = window_stats[train_end]
        pool = filter_pool(stats, min_markets=30, min_hr=0.55,
                           min_months=6, monthly_min_hr=0.50)
        if len(pool) < 3:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height > 0:
            all_sigs.append(holdout)

    if not all_sigs:
        return

    combined = pl.concat(all_sigs, how="diagonal_relaxed")

    # All-trader holdout (same period, same resolution filter)
    holdout_all = df.filter(
        (pl.col("resolved_at") >= datetime(2025, 5, 1))
        & (pl.col("resolved_at") < datetime(2026, 2, 1))
    )

    bands = [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.0)]

    print(f"\n  {'Band':<12} {'Dir':>4} {'Pool HR':>8} {'All HR':>8} {'Mkt Imp':>8} "
          f"{'Edge/All':>9} {'Edge/Mkt':>9} {'Pool N':>8} {'All N':>8}", flush=True)
    print(f"  {'-'*80}", flush=True)

    for lo, hi in bands:
        for dlabel, yes_filter in [("ALL", None), ("NO", False), ("YES", True)]:
            pool_band = combined.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            all_band = holdout_all.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )

            if yes_filter is True:
                pool_band = pool_band.filter(pl.col("bet_yes"))
                all_band = all_band.filter(pl.col("bet_yes"))
            elif yes_filter is False:
                pool_band = pool_band.filter(~pl.col("bet_yes"))
                all_band = all_band.filter(~pl.col("bet_yes"))

            if pool_band.height < 10 or all_band.height < 10:
                continue

            pool_hr = float(pool_band["correct"].mean())
            all_hr = float(all_band["correct"].mean())
            avg_dir = float(pool_band["dir_entry"].mean())

            print(f"  {lo:.0%}-{hi:.0%}     {dlabel:>3} {pool_hr:>7.1%} {all_hr:>7.1%} "
                  f"{avg_dir:>7.1%} {pool_hr - all_hr:>+8.1%} {pool_hr - avg_dir:>+8.1%} "
                  f"{pool_band.height:>8,} {all_band.height:>8,}", flush=True)


# ---------------------------------------------------------------------------
# T4: Pool profile
# ---------------------------------------------------------------------------

def t4_pool_profile(df, window_stats):
    print("\n" + "=" * 70)
    print("  T4: POOL PROFILE — WHO ARE THE HR-SELECTED TRADERS?")
    print("=" * 70, flush=True)

    train_end = datetime(2026, 1, 1)
    stats = window_stats[train_end]

    for label, mk, hr_min, mo, mhr in [
        ("HR>55% mk>=30 mo>=6", 30, 0.55, 6, 0.50),
        ("HR>60% mk>=30 mo>=6", 30, 0.60, 6, 0.50),
    ]:
        pool_set = filter_pool(stats, min_markets=mk, min_hr=hr_min,
                               min_months=mo, monthly_min_hr=mhr)
        pool_stats = stats.filter(pl.col("trader").is_in(list(pool_set)))

        print(f"\n  --- {label} (n={len(pool_set)}) ---")
        if pool_stats.height == 0:
            print("  Empty pool")
            continue

        print(f"  {'Metric':<25} {'Mean':>8} {'Median':>8} {'Min':>8} {'Max':>8}")
        print(f"  {'-'*60}")
        for col in ["hr", "n_markets", "yes_frac", "median_entry", "active_months"]:
            vals = pool_stats[col]
            print(f"  {col:<25} {vals.mean():>8.3f} {vals.median():>8.3f} "
                  f"{vals.min():>8.3f} {vals.max():>8.3f}")

        yes_spec = pool_stats.filter(pl.col("yes_frac") > 0.70).height
        no_spec = pool_stats.filter(pl.col("yes_frac") < 0.30).height
        balanced = pool_stats.filter(
            (pl.col("yes_frac") >= 0.30) & (pl.col("yes_frac") <= 0.70)
        ).height
        print(f"\n  Direction: YES-spec={yes_spec}  NO-spec={no_spec}  balanced={balanced}")

        yes_hr = pool_stats.filter(pl.col("yes_hr").is_not_null())["yes_hr"]
        no_hr = pool_stats.filter(pl.col("no_hr").is_not_null())["no_hr"]
        if len(yes_hr) > 0:
            print(f"  YES-bet HR: mean={yes_hr.mean():.3f}  median={yes_hr.median():.3f}")
        if len(no_hr) > 0:
            print(f"  NO-bet HR:  mean={no_hr.mean():.3f}  median={no_hr.median():.3f}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# T5: Combined best
# ---------------------------------------------------------------------------

def t5_combined(df, window_stats):
    print("\n" + "=" * 70)
    print("  T5: COMBINED BEST — HR POOL + DIRECTION + ENTRY")
    print("=" * 70, flush=True)

    combos = [
        ("NO 50-80% hr>55 mk30", "NO", 0.50, 0.80, 30, 0.55, 6, 0.50),
        ("NO 60-90% hr>55 mk30", "NO", 0.60, 0.90, 30, 0.55, 6, 0.50),
        ("NO 50-90% hr>55 mk30", "NO", 0.50, 0.90, 30, 0.55, 6, 0.50),
        ("NO 40-70% hr>55 mk30", "NO", 0.40, 0.70, 30, 0.55, 6, 0.50),
        ("NO 50-80% hr>60 mk30", "NO", 0.50, 0.80, 30, 0.60, 6, 0.50),
        ("NO 60-90% hr>60 mk30", "NO", 0.60, 0.90, 30, 0.60, 6, 0.50),
        ("NO 50-80% hr>55 mk50", "NO", 0.50, 0.80, 50, 0.55, 6, 0.50),
        ("NO 50-80% hr>55 mk20", "NO", 0.50, 0.80, 20, 0.55, 6, 0.50),
        ("NO 50-80% hr>65 mk30", "NO", 0.50, 0.80, 30, 0.65, 6, 0.50),
        ("NO 50-80% hr>55 mk30 mhr>55", "NO", 0.50, 0.80, 30, 0.55, 6, 0.55),
        ("ALL 40-70% hr>55 mk30", "ALL", 0.40, 0.70, 30, 0.55, 6, 0.50),
        ("ALL 50-80% hr>55 mk30", "ALL", 0.50, 0.80, 30, 0.55, 6, 0.50),
        ("ALL 50-80% hr>60 mk30", "ALL", 0.50, 0.80, 30, 0.60, 6, 0.50),
        ("YES 30-60% hr>55 mk30", "YES", 0.30, 0.60, 30, 0.55, 6, 0.50),
        ("YES 40-70% hr>60 mk30", "YES", 0.40, 0.70, 30, 0.60, 6, 0.50),
        ("YES 30-60% hr>60 mk30", "YES", 0.30, 0.60, 30, 0.60, 6, 0.50),
    ]

    for label, direction, e_lo, e_hi, mk, hr_min, mo, mhr in combos:
        all_sigs = []
        pool_sizes = []

        for train_end, hs, he in walk_forward_windows():
            stats = window_stats[train_end]
            pool = filter_pool(stats, min_markets=mk, min_hr=hr_min,
                               min_months=mo, monthly_min_hr=mhr)
            if len(pool) < 3:
                continue
            pool_sizes.append(len(pool))
            holdout = get_holdout(df, pool, hs, he)
            if holdout.height == 0:
                continue

            if direction == "YES":
                holdout = holdout.filter(pl.col("bet_yes"))
            elif direction == "NO":
                holdout = holdout.filter(~pl.col("bet_yes"))

            holdout = holdout.filter(
                (pl.col("dir_entry") >= e_lo) & (pl.col("dir_entry") < e_hi)
            )

            if holdout.height > 0:
                all_sigs.append(holdout)

        if all_sigs:
            combined = pl.concat(all_sigs, how="diagonal_relaxed")
            r = compute_pnl(combined, label)
            avg_pool = sum(pool_sizes) / len(pool_sizes)
            print(f"  {label:<55} n={r['n']:>6,}  HR={r['hr']:>5.1%}"
                  f"  $/sig=${r['pnl_per']:>7.1f}  pool~{avg_pool:.0f}", flush=True)
        else:
            print(f"  {label:<55} n=0", flush=True)


# ---------------------------------------------------------------------------
# T6: Monthly breakdown
# ---------------------------------------------------------------------------

def t6_monthly(df, window_stats):
    print("\n" + "=" * 70)
    print("  T6: MONTHLY BREAKDOWN — IS EDGE STABLE?")
    print("=" * 70, flush=True)

    configs = [
        ("NO 50-80% hr>55 mk30", "NO", 0.50, 0.80, 30, 0.55),
        ("NO 60-90% hr>55 mk30", "NO", 0.60, 0.90, 30, 0.55),
        ("ALL (no filter) hr>55 mk30", "ALL", 0.0, 1.0, 30, 0.55),
    ]

    for label, direction, e_lo, e_hi, mk, hr_min in configs:
        print(f"\n  --- {label} ---")
        print(f"  {'Month':<10} {'N':>6} {'HR':>7} {'PnL':>10} {'$/sig':>8} {'Pool':>5}")

        total_n = 0
        total_pnl = 0.0
        win_months = 0

        for train_end, hs, he in walk_forward_windows():
            stats = window_stats[train_end]
            pool = filter_pool(stats, min_markets=mk, min_hr=hr_min,
                               min_months=6, monthly_min_hr=0.50)
            if len(pool) < 3:
                print(f"  {hs.strftime('%Y-%m'):<10} pool too small ({len(pool)})")
                continue

            holdout = get_holdout(df, pool, hs, he)
            if holdout.height == 0:
                print(f"  {hs.strftime('%Y-%m'):<10} {'n=0':>6}")
                continue

            if direction == "YES":
                holdout = holdout.filter(pl.col("bet_yes"))
            elif direction == "NO":
                holdout = holdout.filter(~pl.col("bet_yes"))

            if e_lo > 0 or e_hi < 1.0:
                holdout = holdout.filter(
                    (pl.col("dir_entry") >= e_lo) & (pl.col("dir_entry") < e_hi)
                )

            r = compute_pnl(holdout, "")
            if r["n"] > 0:
                total_n += r["n"]
                total_pnl += r["pnl"]
                if r["pnl"] > 0:
                    win_months += 1
                print(f"  {hs.strftime('%Y-%m'):<10} {r['n']:>6,} {r['hr']:>6.1%} "
                      f"${r['pnl']:>9,.0f} ${r['pnl_per']:>7.1f} {len(pool):>5}")
            else:
                print(f"  {hs.strftime('%Y-%m'):<10} {'n=0':>6}")

        if total_n > 0:
            print(f"  {'TOTAL':<10} {total_n:>6,} {'':>7} "
                  f"${total_pnl:>9,.0f} ${total_pnl/total_n:>7.1f} "
                  f"  win={win_months}/9")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print("[load] Reading derived data (positions held at resolution only)...", flush=True)
    df, resolved = load_data()
    print(f"[load] {df.height:,} rows (excl closed positions), "
          f"{df['trader'].n_unique():,} traders, "
          f"{df['condition_id'].n_unique():,} markets", flush=True)

    print("\n[precompute] Building trader stats for each window...", flush=True)
    window_stats = precompute_all_windows(df)

    t1_pool_sweep(df, window_stats)
    t2_direction_entry(df, window_stats)
    t3_base_rate(df, window_stats)
    t4_pool_profile(df, window_stats)
    t5_combined(df, window_stats)
    t6_monthly(df, window_stats)

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
