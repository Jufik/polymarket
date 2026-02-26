"""Deep research: S1 copy strategy using RESOLUTION-BASED ground truth.

The old research used market_pnl > 0 (trader profitability) as "win".
This script uses yes_won (market resolution) — the only metric that matters
for a hold-to-resolution copy strategy.

Hypotheses tested:
  H1: Resolution-correct traders exist and predict forward
  H2: Edge lives in specific entry price bands (not ultra-cheap)
  H3: NO-only copy from the pool beats YES-only
  H4: Consensus (2+ traders agree) improves resolution HR
  H5: Market characteristics filter (volume, time-to-resolution)
  H6: Signal freshness (first_trade closeness to resolution)
  H7: Trader resolution accuracy grading (replace pnl-based grading)
  H8: Combined best filters

Usage:
    uv run python scripts/research_s1_resolution.py
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import polars as pl

from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
    filter_consistent_traders,
)

DATA_DIR = Path("data/derived")
TRAIN_START = datetime(2023, 1, 1)
FEE_PCT = 0.02
BASE_BET = 100.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    pnl = pl.read_parquet(DATA_DIR / "trader_market_pnl.parquet")
    resolved = pl.read_parquet(DATA_DIR / "markets_resolved.parquet")
    mvf = pl.read_parquet(DATA_DIR / "maker_volume_fractions.parquet")

    for col in ["resolved_at"]:
        dtype = resolved.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            resolved = resolved.with_columns(pl.col(col).dt.replace_time_zone(None))
    for col in ["first_trade", "last_trade"]:
        dtype = pnl.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            pnl = pnl.with_columns(pl.col(col).dt.replace_time_zone(None))

    df = pnl.join(resolved, on="condition_id", how="inner")
    return df, resolved, mvf


# ---------------------------------------------------------------------------
# Signal + PnL computation
# ---------------------------------------------------------------------------

def enrich_positions(df: pl.DataFrame) -> pl.DataFrame:
    """Add direction, directional entry, and resolution-based win columns."""
    return df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes"),
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry"),
        (
            ((pl.col("net_yes_tokens") > 0) & pl.col("yes_won"))
            | ((pl.col("net_yes_tokens") <= 0) & ~pl.col("yes_won"))
        ).alias("signal_won"),
    )


def compute_pnl(df: pl.DataFrame, label: str = "") -> dict:
    """Compute PnL metrics for a signal table."""
    if df.height == 0:
        return {"label": label, "n": 0, "hr": 0.0, "pnl": 0.0, "pnl_per": 0.0}

    df = df.with_columns(
        pl.when(pl.col("signal_won"))
        .then(
            pl.lit(BASE_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BASE_BET) * FEE_PCT
        )
        .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
        .alias("pnl")
    )

    n = df.height
    hr = float(df["signal_won"].mean())
    total_pnl = float(df["pnl"].sum())
    return {
        "label": label,
        "n": n,
        "hr": hr,
        "pnl": total_pnl,
        "pnl_per": total_pnl / n,
        "avg_entry": float(df["dir_entry"].mean()),
    }


def print_result(r: dict) -> None:
    if r["n"] == 0:
        print(f"  {r['label']:<55} n=0")
        return
    print(
        f"  {r['label']:<55} n={r['n']:>6,}  HR={r['hr']:>5.1%}"
        f"  PnL=${r['pnl']:>10,.0f}  $/sig=${r['pnl_per']:>7.1f}"
        f"  avg_e={r.get('avg_entry', 0):.3f}"
    )


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def walk_forward_windows():
    """Generate (train_end, holdout_start, holdout_end) tuples."""
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


def build_pool(
    df: pl.DataFrame,
    resolved: pl.DataFrame,
    mvf: pl.DataFrame,
    train_end: datetime,
    *,
    min_periods: int = 9,
    min_markets: int = 20,
    max_mvf: float = 0.10,
    max_median_entry: float = 0.90,
) -> frozenset[str]:
    """Build base consistency pool."""
    return filter_consistent_traders(
        pnl=df, resolved=resolved, mvf=mvf,
        train_start=TRAIN_START, train_end=train_end,
        min_periods=min_periods, min_markets=min_markets,
        max_mvf=max_mvf, max_median_entry=max_median_entry,
    )


def get_holdout(df: pl.DataFrame, pool: frozenset[str],
                start: datetime, end: datetime) -> pl.DataFrame:
    """Get enriched holdout positions for a pool."""
    holdout = df.filter(
        (pl.col("resolved_at") >= start)
        & (pl.col("resolved_at") < end)
        & pl.col("trader").is_in(list(pool))
    )
    return enrich_positions(holdout)


# ---------------------------------------------------------------------------
# Grading: RESOLUTION-based trader stats
# ---------------------------------------------------------------------------

def compute_resolution_stats(
    df: pl.DataFrame, pool: frozenset[str], train_end: datetime,
) -> pl.DataFrame:
    """Per-trader resolution-based stats from training data.

    For each pool trader, compute:
      - resolution_accuracy: fraction of markets where bet direction matched resolution
      - yes_frac: fraction of bets that are YES
      - avg_dir_entry: average directional entry price
      - n_markets: total markets traded
    """
    train = df.filter(
        (pl.col("resolved_at") < train_end)
        & pl.col("trader").is_in(list(pool))
    )
    train = enrich_positions(train)

    stats = train.group_by("trader").agg(
        pl.col("signal_won").mean().alias("resolution_accuracy"),
        pl.col("bet_yes").mean().alias("yes_frac"),
        pl.col("dir_entry").mean().alias("avg_dir_entry"),
        pl.col("dir_entry").median().alias("median_dir_entry"),
        pl.len().alias("n_markets"),
        # YES-specific accuracy
        (pl.col("signal_won").filter(pl.col("bet_yes"))).mean().alias("yes_accuracy"),
        # NO-specific accuracy
        (pl.col("signal_won").filter(~pl.col("bet_yes"))).mean().alias("no_accuracy"),
        # Count by direction
        pl.col("bet_yes").sum().alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
    )
    return stats


# ---------------------------------------------------------------------------
# H1: Resolution-correct traders as forward signal
# ---------------------------------------------------------------------------

def h1_resolution_grading(df, resolved, mvf):
    """H1: Do traders with high resolution accuracy in training predict forward?"""
    print("\n" + "=" * 70)
    print("  H1: RESOLUTION ACCURACY GRADING")
    print("  Do traders with high training resolution accuracy predict forward?")
    print("=" * 70)

    all_sigs = {k: [] for k in [
        "all", "acc>50%", "acc>55%", "acc>60%",
        "acc<40%", "acc<45%", "acc<50%",  # contrarian
    ]}

    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        stats = compute_resolution_stats(df, pool, train_end)
        holdout_all = get_holdout(df, pool, hs, he)
        if holdout_all.height == 0:
            continue

        all_sigs["all"].append(holdout_all)

        for threshold, key in [
            (0.50, "acc>50%"), (0.55, "acc>55%"), (0.60, "acc>60%"),
        ]:
            good = frozenset(
                stats.filter(pl.col("resolution_accuracy") >= threshold)["trader"].to_list()
            )
            if len(good) >= 3:
                sigs = holdout_all.filter(pl.col("trader").is_in(list(good)))
                all_sigs[key].append(sigs)

        for threshold, key in [
            (0.40, "acc<40%"), (0.45, "acc<45%"), (0.50, "acc<50%"),
        ]:
            bad = frozenset(
                stats.filter(pl.col("resolution_accuracy") < threshold)["trader"].to_list()
            )
            if len(bad) >= 3:
                sigs = holdout_all.filter(pl.col("trader").is_in(list(bad)))
                all_sigs[key].append(sigs)

    for key, sig_list in all_sigs.items():
        if sig_list:
            combined = pl.concat(sig_list, how="diagonal_relaxed")
            print_result(compute_pnl(combined, f"Pool resolution {key}"))


# ---------------------------------------------------------------------------
# H2: Entry price band analysis
# ---------------------------------------------------------------------------

def h2_entry_bands(df, resolved, mvf):
    """H2: Edge at specific entry price bands."""
    print("\n" + "=" * 70)
    print("  H2: ENTRY PRICE BAND ANALYSIS")
    print("  Which directional entry prices have positive edge?")
    print("=" * 70)

    all_sigs = []
    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height > 0:
            all_sigs.append(holdout)

    if not all_sigs:
        print("  No data")
        return

    combined = pl.concat(all_sigs, how="diagonal_relaxed")

    bands = [
        (0.00, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
        (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
        (0.80, 0.90), (0.90, 1.00),
    ]

    print("\n  --- ALL DIRECTIONS ---")
    for lo, hi in bands:
        band = combined.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        print_result(compute_pnl(band, f"Entry {lo:.0%}-{hi:.0%}"))

    print("\n  --- YES ONLY ---")
    yes_df = combined.filter(pl.col("bet_yes"))
    for lo, hi in bands:
        band = yes_df.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"YES {lo:.0%}-{hi:.0%}"))

    print("\n  --- NO ONLY ---")
    no_df = combined.filter(~pl.col("bet_yes"))
    for lo, hi in bands:
        band = no_df.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"NO {lo:.0%}-{hi:.0%}"))


# ---------------------------------------------------------------------------
# H3: Direction-only copy (YES-only, NO-only)
# ---------------------------------------------------------------------------

def h3_direction_filter(df, resolved, mvf):
    """H3: Is NO-only copy better than YES-only from this pool?"""
    print("\n" + "=" * 70)
    print("  H3: DIRECTION FILTER")
    print("  YES-only vs NO-only vs ALL from consistency pool")
    print("=" * 70)

    all_yes = []
    all_no = []
    all_both = []

    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height == 0:
            continue
        all_both.append(holdout)
        all_yes.append(holdout.filter(pl.col("bet_yes")))
        all_no.append(holdout.filter(~pl.col("bet_yes")))

    for label, sig_list in [
        ("ALL directions", all_both),
        ("YES-only", all_yes),
        ("NO-only", all_no),
    ]:
        if sig_list:
            combined = pl.concat(sig_list, how="diagonal_relaxed")
            print_result(compute_pnl(combined, label))

    # Also test NO-only with entry band filters
    if all_no:
        no_combined = pl.concat(all_no, how="diagonal_relaxed")
        print("\n  --- NO-only by entry band ---")
        for lo, hi in [(0.0, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 1.0)]:
            band = no_combined.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            if band.height >= 10:
                print_result(compute_pnl(band, f"NO entry {lo:.0%}-{hi:.0%}"))


# ---------------------------------------------------------------------------
# H4: Consensus signal
# ---------------------------------------------------------------------------

def h4_consensus(df, resolved, mvf):
    """H4: Does consensus (2+ traders agree) improve resolution HR?"""
    print("\n" + "=" * 70)
    print("  H4: CONSENSUS SIGNAL")
    print("  Does requiring multiple traders to agree improve resolution HR?")
    print("=" * 70)

    all_sigs = {f"min_{k}": [] for k in [1, 2, 3, 5]}
    all_sigs["unanimous"] = []

    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height == 0:
            continue

        # Group by market: count YES and NO, determine majority direction
        market_agg = holdout.group_by("condition_id").agg(
            pl.col("bet_yes").sum().alias("n_yes"),
            (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
            pl.col("yes_won").first().alias("yes_won"),
            pl.col("dir_entry").mean().alias("avg_entry"),
            pl.len().alias("n_traders"),
        )

        market_agg = market_agg.with_columns(
            (pl.col("n_yes") >= pl.col("n_no")).alias("consensus_yes"),
            pl.max_horizontal("n_yes", "n_no").alias("majority_count"),
            (
                pl.max_horizontal("n_yes", "n_no").cast(pl.Float64)
                / (pl.col("n_yes") + pl.col("n_no")).cast(pl.Float64)
            ).alias("agreement_frac"),
        )

        # Consensus direction matches resolution?
        market_agg = market_agg.with_columns(
            (
                (pl.col("consensus_yes") & pl.col("yes_won"))
                | (~pl.col("consensus_yes") & ~pl.col("yes_won"))
            ).alias("signal_won"),
        )

        # Directional entry for consensus signal
        market_agg = market_agg.with_columns(
            pl.when(pl.col("consensus_yes"))
            .then(pl.col("avg_entry"))
            .otherwise(1.0 - pl.col("avg_entry"))
            .alias("dir_entry"),
        )

        for min_k in [1, 2, 3, 5]:
            filtered = market_agg.filter(pl.col("majority_count") >= min_k)
            if filtered.height > 0:
                all_sigs[f"min_{min_k}"].append(filtered)

        # Unanimous (no disagreement)
        unanimous = market_agg.filter(
            (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
        )
        if unanimous.height > 0:
            all_sigs["unanimous"].append(unanimous)

    for key, sig_list in all_sigs.items():
        if sig_list:
            combined = pl.concat(sig_list, how="diagonal_relaxed")
            print_result(compute_pnl(combined, f"Consensus {key}"))

    # Also test consensus + direction
    print("\n  --- Consensus + direction ---")
    for direction, dir_label in [(True, "YES"), (False, "NO")]:
        for min_k in [2, 3]:
            key = f"min_{min_k}"
            if all_sigs[key]:
                combined = pl.concat(all_sigs[key], how="diagonal_relaxed")
                filtered = combined.filter(pl.col("consensus_yes") == direction)
                if filtered.height >= 10:
                    print_result(compute_pnl(
                        filtered, f"Consensus {dir_label} (min {min_k} agree)"
                    ))


# ---------------------------------------------------------------------------
# H5: Market characteristics
# ---------------------------------------------------------------------------

def h5_market_characteristics(df, resolved, mvf):
    """H5: Do market characteristics (volume, time-to-resolution) affect edge?"""
    print("\n" + "=" * 70)
    print("  H5: MARKET CHARACTERISTICS")
    print("  Volume, time-to-resolution, trade count")
    print("=" * 70)

    all_sigs = []
    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height > 0:
            all_sigs.append(holdout)

    if not all_sigs:
        print("  No data")
        return

    combined = pl.concat(all_sigs, how="diagonal_relaxed")

    # Add time-to-resolution
    combined = combined.with_columns(
        (pl.col("resolved_at") - pl.col("first_trade")).dt.total_hours().alias("hours_to_resolution"),
    )

    # Volume bands
    print("\n  --- Market volume (trader's volume in market) ---")
    vol_qs = combined["market_volume"].quantile(0.25), combined["market_volume"].quantile(0.50), combined["market_volume"].quantile(0.75)
    vol_bands = [(0, vol_qs[0]), (vol_qs[0], vol_qs[1]), (vol_qs[1], vol_qs[2]), (vol_qs[2], 1e12)]
    labels = ["Q1 (smallest)", "Q2", "Q3", "Q4 (largest)"]
    for (lo, hi), label in zip(vol_bands, labels):
        band = combined.filter(
            (pl.col("market_volume") >= lo) & (pl.col("market_volume") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"Volume {label} ({lo:.0f}-{hi:.0f})"))

    # Time-to-resolution bands
    print("\n  --- Time to resolution ---")
    time_bands = [(0, 24), (24, 168), (168, 720), (720, 8760), (8760, 1e8)]
    time_labels = ["<1 day", "1-7 days", "1-4 weeks", "1-12 months", ">1 year"]
    for (lo, hi), label in zip(time_bands, time_labels):
        band = combined.filter(
            (pl.col("hours_to_resolution") >= lo) & (pl.col("hours_to_resolution") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"Time {label}"))

    # Trade count
    print("\n  --- Trader's trade count in market ---")
    for lo, hi, label in [(1, 2, "1 trade"), (2, 5, "2-4 trades"), (5, 20, "5-19 trades"), (20, 1e6, "20+ trades")]:
        band = combined.filter(
            (pl.col("trade_count") >= lo) & (pl.col("trade_count") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"Trades {label}"))


# ---------------------------------------------------------------------------
# H6: Signal freshness
# ---------------------------------------------------------------------------

def h6_freshness(df, resolved, mvf):
    """H6: Does signal freshness (entry close to resolution) matter?"""
    print("\n" + "=" * 70)
    print("  H6: SIGNAL FRESHNESS")
    print("  Is the signal better for recent entries vs old entries?")
    print("=" * 70)

    all_sigs = []
    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height > 0:
            all_sigs.append(holdout)

    if not all_sigs:
        return

    combined = pl.concat(all_sigs, how="diagonal_relaxed")
    combined = combined.with_columns(
        (pl.col("resolved_at") - pl.col("first_trade")).dt.total_hours().alias("hours_to_res"),
        (pl.col("resolved_at") - pl.col("last_trade")).dt.total_hours().alias("hours_last_to_res"),
    )

    # Entry time relative to resolution
    print("\n  --- First trade to resolution ---")
    for lo, hi, label in [
        (0, 1, "<1h"), (1, 24, "1-24h"), (24, 168, "1-7d"),
        (168, 720, "1-4w"), (720, 4320, "1-6mo"), (4320, 1e8, ">6mo"),
    ]:
        band = combined.filter(
            (pl.col("hours_to_res") >= lo) & (pl.col("hours_to_res") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"Entry→Resolution {label}"))

    # Last trade to resolution
    print("\n  --- Last trade to resolution (trader still active) ---")
    for lo, hi, label in [
        (0, 1, "<1h"), (1, 24, "1-24h"), (24, 168, "1-7d"),
        (168, 720, "1-4w"), (720, 4320, "1-6mo"), (4320, 1e8, ">6mo"),
    ]:
        band = combined.filter(
            (pl.col("hours_last_to_res") >= lo) & (pl.col("hours_last_to_res") < hi)
        )
        if band.height >= 10:
            print_result(compute_pnl(band, f"LastTrade→Resolution {label}"))


# ---------------------------------------------------------------------------
# H7: Resolution-based grading
# ---------------------------------------------------------------------------

def h7_resolution_grading(df, resolved, mvf):
    """H7: Grade traders by resolution accuracy, not profitability."""
    print("\n" + "=" * 70)
    print("  H7: RESOLUTION-BASED TRADER GRADING")
    print("  Filter traders by direction-level resolution accuracy")
    print("=" * 70)

    configs = [
        # (label, res_acc_min, yes_frac_min, yes_frac_max, no_acc_min, yes_acc_min, entry_lo, entry_hi)
        ("baseline (no filter)", 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("res_acc > 50%", 0.50, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("res_acc > 55%", 0.55, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("res_acc > 60%", 0.60, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("NO specialists (yes_frac < 30%)", 0.0, 0.0, 0.30, 0.0, 0.0, 0.0, 1.0),
        ("NO specialists + acc>50%", 0.50, 0.0, 0.30, 0.0, 0.0, 0.0, 1.0),
        ("YES specialists (yes_frac > 70%)", 0.0, 0.70, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("YES specialists + acc>50%", 0.50, 0.70, 1.0, 0.0, 0.0, 0.0, 1.0),
        ("NO-acc > 55%", 0.0, 0.0, 1.0, 0.55, 0.0, 0.0, 1.0),
        ("NO-acc > 60%", 0.0, 0.0, 1.0, 0.60, 0.0, 0.0, 1.0),
        ("NO-acc > 65%", 0.0, 0.0, 1.0, 0.65, 0.0, 0.0, 1.0),
        ("YES-acc > 40%", 0.0, 0.0, 1.0, 0.0, 0.40, 0.0, 1.0),
        ("YES-acc > 50%", 0.0, 0.0, 1.0, 0.0, 0.50, 0.0, 1.0),
        # Combined: accuracy + entry band
        ("res_acc>50% + entry 30-70%", 0.50, 0.0, 1.0, 0.0, 0.0, 0.30, 0.70),
        ("res_acc>55% + entry 30-70%", 0.55, 0.0, 1.0, 0.0, 0.0, 0.30, 0.70),
        ("NO-acc>60% + entry 50-90%", 0.0, 0.0, 1.0, 0.60, 0.0, 0.50, 0.90),
        ("NO-acc>55% + entry 30-70%", 0.0, 0.0, 1.0, 0.55, 0.0, 0.30, 0.70),
    ]

    for label, res_min, yf_min, yf_max, no_acc_min, yes_acc_min, e_lo, e_hi in configs:
        all_sigs = []
        pool_sizes = []

        for train_end, hs, he in walk_forward_windows():
            pool = build_pool(df, resolved, mvf, train_end)
            if len(pool) < 5:
                continue

            stats = compute_resolution_stats(df, pool, train_end)

            # Apply filters
            filtered = stats
            if res_min > 0:
                filtered = filtered.filter(pl.col("resolution_accuracy") >= res_min)
            if yf_min > 0:
                filtered = filtered.filter(pl.col("yes_frac") >= yf_min)
            if yf_max < 1.0:
                filtered = filtered.filter(pl.col("yes_frac") <= yf_max)
            if no_acc_min > 0:
                filtered = filtered.filter(
                    pl.col("no_accuracy").is_not_null()
                    & (pl.col("no_accuracy") >= no_acc_min)
                )
            if yes_acc_min > 0:
                filtered = filtered.filter(
                    pl.col("yes_accuracy").is_not_null()
                    & (pl.col("yes_accuracy") >= yes_acc_min)
                )

            graded_pool = frozenset(filtered["trader"].to_list())
            if len(graded_pool) < 3:
                continue

            pool_sizes.append(len(graded_pool))
            holdout = get_holdout(df, graded_pool, hs, he)

            # Apply entry band filter on holdout signals
            if e_lo > 0 or e_hi < 1.0:
                holdout = holdout.filter(
                    (pl.col("dir_entry") >= e_lo) & (pl.col("dir_entry") < e_hi)
                )

            if holdout.height > 0:
                all_sigs.append(holdout)

        if all_sigs:
            combined = pl.concat(all_sigs, how="diagonal_relaxed")
            r = compute_pnl(combined, label)
            avg_pool = sum(pool_sizes) / len(pool_sizes) if pool_sizes else 0
            print(f"  {label:<55} n={r['n']:>6,}  HR={r['hr']:>5.1%}"
                  f"  PnL=${r['pnl']:>10,.0f}  $/sig=${r['pnl_per']:>7.1f}"
                  f"  pool~{avg_pool:.0f}")


# ---------------------------------------------------------------------------
# H8: Combined best filters
# ---------------------------------------------------------------------------

def h8_combined(df, resolved, mvf):
    """H8: Combine the best-performing filters from H1-H7."""
    print("\n" + "=" * 70)
    print("  H8: COMBINED HYPOTHESES — BEST FILTERS")
    print("  Systematic combination of promising signals")
    print("=" * 70)

    combos = [
        # (label, direction_filter, entry_lo, entry_hi, res_acc_min, no_acc_min,
        #  consensus_min, max_hours_to_res, min_trades)
        ("NO-only + entry 50-80%", "NO", 0.50, 0.80, 0.0, 0.0, 0, 0, 0),
        ("NO-only + entry 60-90%", "NO", 0.60, 0.90, 0.0, 0.0, 0, 0, 0),
        ("NO-only + entry 50-90%", "NO", 0.50, 0.90, 0.0, 0.0, 0, 0, 0),
        ("NO-only + entry 50-80% + no_acc>55%", "NO", 0.50, 0.80, 0.0, 0.55, 0, 0, 0),
        ("NO-only + entry 50-80% + no_acc>60%", "NO", 0.50, 0.80, 0.0, 0.60, 0, 0, 0),
        ("NO-only + entry 50-80% + res_acc>50%", "NO", 0.50, 0.80, 0.50, 0.0, 0, 0, 0),
        ("NO-only + entry 50-80% + consensus>=2", "NO", 0.50, 0.80, 0.0, 0.0, 2, 0, 0),
        ("NO-only + entry 50-80% + <7d to res", "NO", 0.50, 0.80, 0.0, 0.0, 0, 168, 0),
        ("NO-only + entry 50-80% + <30d to res", "NO", 0.50, 0.80, 0.0, 0.0, 0, 720, 0),
        ("NO-only + entry 50-80% + 2+ trades", "NO", 0.50, 0.80, 0.0, 0.0, 0, 0, 2),
        ("ALL + entry 50-80%", "ALL", 0.50, 0.80, 0.0, 0.0, 0, 0, 0),
        ("ALL + entry 50-80% + res_acc>50%", "ALL", 0.50, 0.80, 0.50, 0.0, 0, 0, 0),
        ("ALL + entry 50-80% + res_acc>55%", "ALL", 0.50, 0.80, 0.55, 0.0, 0, 0, 0),
        ("ALL + entry 30-60%", "ALL", 0.30, 0.60, 0.0, 0.0, 0, 0, 0),
        ("ALL + entry 30-60% + res_acc>50%", "ALL", 0.30, 0.60, 0.50, 0.0, 0, 0, 0),
        # YES-only (probably bad, but test)
        ("YES-only + entry 30-50%", "YES", 0.30, 0.50, 0.0, 0.0, 0, 0, 0),
        ("YES-only + entry 30-50% + yes_acc>40%", "YES", 0.30, 0.50, 0.0, 0.0, 0, 0, 0),
        # Wider MVF
        ("NO-only + entry 50-80% + mvf<0.30", "NO", 0.50, 0.80, 0.0, 0.0, 0, 0, 0),
        # Relaxed consistency
        ("NO-only + entry 50-80% + 6mo consistency", "NO", 0.50, 0.80, 0.0, 0.0, 0, 0, 0),
    ]

    for (label, dir_filter, e_lo, e_hi, res_min, no_acc_min,
         cons_min, max_hrs, min_trades) in combos:

        all_sigs = []

        for train_end, hs, he in walk_forward_windows():
            # Special cases
            if "mvf<0.30" in label:
                pool = build_pool(df, resolved, mvf, train_end, max_mvf=0.30)
            elif "6mo consistency" in label:
                pool = build_pool(df, resolved, mvf, train_end, min_periods=6)
            else:
                pool = build_pool(df, resolved, mvf, train_end)

            if len(pool) < 5:
                continue

            # Apply resolution-based grading
            if res_min > 0 or no_acc_min > 0:
                stats = compute_resolution_stats(df, pool, train_end)
                filtered = stats
                if res_min > 0:
                    filtered = filtered.filter(pl.col("resolution_accuracy") >= res_min)
                if no_acc_min > 0:
                    filtered = filtered.filter(
                        pl.col("no_accuracy").is_not_null()
                        & (pl.col("no_accuracy") >= no_acc_min)
                    )
                pool = frozenset(filtered["trader"].to_list())
                if len(pool) < 3:
                    continue

            holdout = get_holdout(df, pool, hs, he)
            if holdout.height == 0:
                continue

            # Direction filter
            if dir_filter == "YES":
                holdout = holdout.filter(pl.col("bet_yes"))
            elif dir_filter == "NO":
                holdout = holdout.filter(~pl.col("bet_yes"))

            # Entry band
            holdout = holdout.filter(
                (pl.col("dir_entry") >= e_lo) & (pl.col("dir_entry") < e_hi)
            )

            # Time filter
            if max_hrs > 0:
                holdout = holdout.with_columns(
                    (pl.col("resolved_at") - pl.col("first_trade")).dt.total_hours()
                    .alias("hours_to_res")
                )
                holdout = holdout.filter(pl.col("hours_to_res") < max_hrs)

            # Trade count filter
            if min_trades > 0:
                holdout = holdout.filter(pl.col("trade_count") >= min_trades)

            # Consensus filter
            if cons_min > 0:
                # Need market-level agreement
                market_counts = holdout.group_by("condition_id").agg(
                    (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("mkt_n_no"),
                )
                ok_markets = market_counts.filter(
                    pl.col("mkt_n_no") >= cons_min
                ).select("condition_id")
                holdout = holdout.join(ok_markets, on="condition_id", how="inner")

            if holdout.height > 0:
                all_sigs.append(holdout)

        if all_sigs:
            combined = pl.concat(all_sigs, how="diagonal_relaxed")
            print_result(compute_pnl(combined, label))
        else:
            print(f"  {label:<55} n=0")


# ---------------------------------------------------------------------------
# H9: Base rate edge — is the pool better than random?
# ---------------------------------------------------------------------------

def h9_base_rate(df, resolved, mvf):
    """H9: Compare pool resolution accuracy vs market base rates."""
    print("\n" + "=" * 70)
    print("  H9: BASE RATE COMPARISON")
    print("  Pool accuracy vs random at each entry price band")
    print("=" * 70)

    # Compute ALL-TRADER base rate at each price band
    all_df = enrich_positions(df)
    all_sigs = []
    for train_end, hs, he in walk_forward_windows():
        pool = build_pool(df, resolved, mvf, train_end)
        if len(pool) < 5:
            continue
        holdout = get_holdout(df, pool, hs, he)
        if holdout.height > 0:
            all_sigs.append(holdout)

    if not all_sigs:
        return

    pool_combined = pl.concat(all_sigs, how="diagonal_relaxed")

    # Market-level base rates from resolution data
    holdout_markets = resolved.filter(
        (pl.col("resolved_at") >= datetime(2025, 5, 1))
        & (pl.col("resolved_at") < datetime(2026, 2, 1))
    )
    base_yes_rate = float(holdout_markets["yes_won"].mean())
    print(f"\n  Holdout period YES base rate: {base_yes_rate:.1%}")
    print(f"  If you always bet NO at any price, HR = {1 - base_yes_rate:.1%}")

    bands = [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.0)]
    print(f"\n  {'Band':<12} {'Pool HR':>8} {'Base HR':>8} {'Edge':>8} {'Pool N':>8}")
    print(f"  {'-'*48}")

    for lo, hi in bands:
        pool_band = pool_combined.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        if pool_band.height < 10:
            continue

        pool_hr = float(pool_band["signal_won"].mean())

        # Base rate for this entry band: what fraction of markets resolve
        # in the "correct" direction if you bet randomly at this entry?
        # For dir_entry = 0.30, you're betting on a 30% implied event.
        # For YES bets at 0.30: base HR = YES resolution rate for these markets
        # For NO bets at 0.30 (=YES price 0.70): base HR = NO resolution rate

        # Average directional entry in this band
        avg_dir = float(pool_band["dir_entry"].mean())
        # Simple model: base HR ≈ 1 - avg_dir (NO bets) or avg_dir (YES bets)
        # Actually we need per-direction base rates
        yes_in_band = pool_band.filter(pl.col("bet_yes"))
        no_in_band = pool_band.filter(~pl.col("bet_yes"))

        yes_hr = float(yes_in_band["signal_won"].mean()) if yes_in_band.height > 0 else 0
        no_hr = float(no_in_band["signal_won"].mean()) if no_in_band.height > 0 else 0

        # Market-implied base: directional entry ≈ implied probability of the event
        # So expected HR for random = avg_dir for YES, (1-avg_dir) for NO
        yes_base = float(yes_in_band["dir_entry"].mean()) if yes_in_band.height > 0 else avg_dir
        no_base = 1.0 - float(no_in_band["wavg_yes_entry_price"].mean()) if no_in_band.height > 0 else (1 - avg_dir)

        # Weighted base HR
        n_yes = yes_in_band.height
        n_no = no_in_band.height
        n_total = n_yes + n_no
        weighted_base = (yes_base * n_yes + no_base * n_no) / n_total if n_total > 0 else avg_dir

        edge = pool_hr - weighted_base
        print(f"  {lo:.0%}-{hi:.0%}     {pool_hr:>7.1%} {weighted_base:>7.1%} {edge:>+7.1%} {n_total:>8,}")

        if n_yes >= 10:
            edge_y = yes_hr - yes_base
            print(f"    YES       {yes_hr:>7.1%} {yes_base:>7.1%} {edge_y:>+7.1%} {n_yes:>8,}")
        if n_no >= 10:
            edge_n = no_hr - no_base
            print(f"    NO        {no_hr:>7.1%} {no_base:>7.1%} {edge_n:>+7.1%} {n_no:>8,}")


# ---------------------------------------------------------------------------
# H10: Pool relaxation — more traders, less strict
# ---------------------------------------------------------------------------

def h10_pool_relaxation(df, resolved, mvf):
    """H10: Test relaxed pool criteria to find larger viable pools."""
    print("\n" + "=" * 70)
    print("  H10: POOL RELAXATION")
    print("  Test different consistency and MVF thresholds")
    print("=" * 70)

    pool_configs = [
        # (label, min_periods, min_markets, max_mvf, max_median_entry)
        ("9mo/20mkt/mvf<0.10/entry<0.90 (base)", 9, 20, 0.10, 0.90),
        ("6mo/20mkt/mvf<0.10/entry<0.90", 6, 20, 0.10, 0.90),
        ("3mo/20mkt/mvf<0.10/entry<0.90", 3, 20, 0.10, 0.90),
        ("9mo/10mkt/mvf<0.10/entry<0.90", 9, 10, 0.10, 0.90),
        ("9mo/20mkt/mvf<0.30/entry<0.90", 9, 20, 0.30, 0.90),
        ("6mo/10mkt/mvf<0.10/entry<0.90", 6, 10, 0.10, 0.90),
        ("6mo/20mkt/mvf<0.30/entry<0.90", 6, 20, 0.30, 0.90),
        ("9mo/20mkt/mvf<0.10/entry<0.70", 9, 20, 0.10, 0.70),
    ]

    for label, mp, mm, max_mvf, max_entry in pool_configs:
        all_sigs_all = []
        all_sigs_no = []
        pool_sizes = []

        for train_end, hs, he in walk_forward_windows():
            pool = build_pool(
                df, resolved, mvf, train_end,
                min_periods=mp, min_markets=mm,
                max_mvf=max_mvf, max_median_entry=max_entry,
            )
            if len(pool) < 5:
                continue

            pool_sizes.append(len(pool))
            holdout = get_holdout(df, pool, hs, he)
            if holdout.height > 0:
                all_sigs_all.append(holdout)
                no_only = holdout.filter(~pl.col("bet_yes"))
                if no_only.height > 0:
                    all_sigs_no.append(no_only)

        avg_pool = sum(pool_sizes) / len(pool_sizes) if pool_sizes else 0

        if all_sigs_all:
            combined = pl.concat(all_sigs_all, how="diagonal_relaxed")
            r = compute_pnl(combined, f"{label} ALL")
            print(f"  {label:<50} ALL   n={r['n']:>6,}  HR={r['hr']:>5.1%}"
                  f"  $/sig=${r['pnl_per']:>7.1f}  pool~{avg_pool:.0f}")

        if all_sigs_no:
            combined = pl.concat(all_sigs_no, how="diagonal_relaxed")
            r = compute_pnl(combined, f"{label} NO")
            print(f"  {' '*50} NO    n={r['n']:>6,}  HR={r['hr']:>5.1%}"
                  f"  $/sig=${r['pnl_per']:>7.1f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print("[load] Reading derived data...")
    df, resolved, mvf = load_data()
    print(f"[load] {df.height:,} rows, {df['trader'].n_unique():,} traders, "
          f"{df['condition_id'].n_unique():,} markets")

    h1_resolution_grading(df, resolved, mvf)
    h2_entry_bands(df, resolved, mvf)
    h3_direction_filter(df, resolved, mvf)
    h4_consensus(df, resolved, mvf)
    h5_market_characteristics(df, resolved, mvf)
    h6_freshness(df, resolved, mvf)
    h7_resolution_grading(df, resolved, mvf)
    h8_combined(df, resolved, mvf)
    h9_base_rate(df, resolved, mvf)
    h10_pool_relaxation(df, resolved, mvf)

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
