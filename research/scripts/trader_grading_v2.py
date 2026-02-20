"""Trader grading v2: deeper analysis on surprising results from v1.

Key surprises from v1:
- "Longshot YES gamblers" have BEST holdout ROI (+28.4%)
- NO-heavy traders have WORST holdout ROI (7.5%)
- Composite grade favoring NO + low gambling LOSES in holdout

This script investigates:
1. Is this driven by a few whales? (PnL concentration)
2. Does it hold across multiple holdout windows? (robustness)
3. Deep value entry (<0.55) seems key — what's the mechanism?
4. Can we combine entry price + direction for a robust grading?

Usage:
    PYTHONPATH=. uv run python scripts/trader_grading_v2.py
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

from strategies.consistency_copy.backtester.runner import (
    _compute_trader_median_entry,
    _load_data,
    _precompute_mvf_subsets,
    get_consistent_traders,
)

# Pool config
CONSISTENCY_MONTHS = 9
MIN_MARKETS = 20
MAX_MEDIAN_ENTRY = 0.90
MVF_BAND = "pure_taker"
TRAIN_ANCHOR = datetime(2023, 1, 1)

WINDOWS = [
    ("2025-05", datetime(2025, 5, 1), datetime(2025, 6, 1)),
    ("2025-06", datetime(2025, 6, 1), datetime(2025, 7, 1)),
    ("2025-07", datetime(2025, 7, 1), datetime(2025, 8, 1)),
    ("2025-08", datetime(2025, 8, 1), datetime(2025, 9, 1)),
    ("2025-09", datetime(2025, 9, 1), datetime(2025, 10, 1)),
    ("2025-10", datetime(2025, 10, 1), datetime(2025, 11, 1)),
    ("2025-11", datetime(2025, 11, 1), datetime(2025, 12, 1)),
    ("2025-12", datetime(2025, 12, 1), datetime(2026, 1, 1)),
    ("2026-01", datetime(2026, 1, 1), datetime(2026, 2, 1)),
]


def compute_per_trader_features(
    df_pnl: pl.DataFrame,
    pool: set[str],
    start: datetime,
    end: datetime,
) -> pl.DataFrame:
    """Compute behavioral features per trader over a period."""
    df = df_pnl.filter(
        (pl.col("resolved_at") >= start)
        & (pl.col("resolved_at") < end)
        & pl.col("trader").is_in(list(pool))
    )
    if df.height == 0:
        return pl.DataFrame(schema={"trader": pl.Utf8})

    df = df.with_columns(
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("direction"),
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry_price"),
        pl.when(
            (pl.col("net_yes_tokens") > 0) & pl.col("yes_won")
            | (pl.col("net_yes_tokens") <= 0) & ~pl.col("yes_won")
        )
        .then(True)
        .otherwise(False)
        .alias("won"),
        (
            (pl.col("net_yes_tokens") > 0) & (pl.col("wavg_yes_entry_price") < 0.50)
        ).alias("is_longshot_yes"),
    )

    features = df.group_by("trader").agg(
        pl.col("market_pnl").len().alias("n_markets"),
        pl.col("market_volume").sum().alias("total_volume"),
        pl.col("market_pnl").sum().alias("total_pnl"),
        (pl.col("direction") == "NO").mean().alias("no_fraction"),
        pl.col("won").mean().alias("win_rate"),
        pl.col("dir_entry_price").mean().alias("mean_entry"),
        pl.col("dir_entry_price").median().alias("median_entry"),
        (
            (pl.col("direction") == "YES") & pl.col("is_longshot_yes")
        ).mean().alias("longshot_yes_fraction"),
        # Volume concentration
        pl.col("market_volume").std().alias("vol_std"),
        pl.col("market_volume").mean().alias("vol_mean"),
        # Win/loss volume asymmetry
        pl.col("market_volume").filter(pl.col("won")).mean().alias("avg_win_vol"),
        pl.col("market_volume").filter(~pl.col("won")).mean().alias("avg_loss_vol"),
    ).with_columns(
        (pl.col("total_pnl") / pl.col("total_volume")).alias("roi"),
        (pl.col("vol_std") / pl.col("vol_mean").clip(lower_bound=1.0)).alias("volume_cv"),
        (
            pl.col("avg_win_vol") / pl.col("avg_loss_vol").clip(lower_bound=1.0)
        ).alias("sizing_conviction"),
    )

    return features


def main():
    print("Loading data...")
    df_pnl, mvf_df, _markets, _price_ts = _load_data()
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    med_entry_df = _compute_trader_median_entry(df_pnl)

    # ===================================================================
    # 1. PnL CONCENTRATION: Is v1's result driven by whale traders?
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  1. PnL CONCENTRATION IN HOLDOUT (Oct 2025 - Feb 2026)")
    print(f"{'=' * 70}\n")

    t_end = datetime(2025, 10, 1)
    skilled = get_consistent_traders(df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS)
    eligible = set(
        med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
    )
    pool = skilled & mvf_subsets[MVF_BAND] & eligible
    print(f"Pool: {len(pool)} traders")

    ho_feat = compute_per_trader_features(
        df_pnl, pool, datetime(2025, 10, 1), datetime(2026, 2, 1)
    )
    ho_feat = ho_feat.sort("total_pnl", descending=True)

    total_pnl = float(ho_feat["total_pnl"].sum())
    total_vol = float(ho_feat["total_volume"].sum())

    print(f"\nTotal holdout PnL: ${total_pnl:,.0f}")
    print(f"Total holdout volume: ${total_vol:,.0f}")
    print()

    print(f"{'Rank':<6} {'Volume':>12} {'PnL':>12} {'ROI':>8} {'%TotalPnL':>10} "
          f"{'NO%':>6} {'Longshot%':>10} {'MeanEntry':>10}")
    print("-" * 80)

    cum_pnl = 0.0
    for i, row in enumerate(ho_feat.iter_rows(named=True)):
        cum_pnl += row["total_pnl"]
        share = cum_pnl / total_pnl * 100 if total_pnl != 0 else 0
        roi = row["roi"] * 100 if row["roi"] else 0
        pnl_s = f"${row['total_pnl']:,.0f}"
        vol_s = f"${row['total_volume']:,.0f}"
        lf = row.get("longshot_yes_fraction", 0) or 0
        me = row.get("mean_entry", 0) or 0
        nf = row.get("no_fraction", 0) or 0
        print(
            f"  #{i+1:<3} {vol_s:>12} {pnl_s:>12} {roi:>7.1f}% {share:>9.1f}% "
            f"{nf*100:>5.0f}% {lf*100:>9.1f}% {me:>9.3f}"
        )
        if i >= 14:
            break

    # How many traders to reach 80% PnL?
    sorted_pnl = ho_feat.sort("total_pnl", descending=True)["total_pnl"].to_list()
    cum = 0.0
    for i, p in enumerate(sorted_pnl):
        cum += p
        if cum >= total_pnl * 0.80:
            print(f"\n  Top {i+1} traders account for 80% of holdout PnL")
            break

    # ===================================================================
    # 2. WALK-FORWARD ROBUSTNESS: Do the same dimensions win every month?
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  2. WALK-FORWARD: Training features → monthly holdout ROI")
    print(f"{'=' * 70}\n")

    # Define grade buckets using TRAINING features
    grades = {
        "Deep value (entry<0.55)": lambda f: f.filter(pl.col("mean_entry") < 0.55),
        "Mid value (0.55-0.70)": lambda f: f.filter(
            (pl.col("mean_entry") >= 0.55) & (pl.col("mean_entry") < 0.70)
        ),
        "Expensive (entry>0.70)": lambda f: f.filter(pl.col("mean_entry") >= 0.70),
        "Longshot YES >15%": lambda f: f.filter(pl.col("longshot_yes_fraction") > 0.15),
        "Longshot YES <5%": lambda f: f.filter(pl.col("longshot_yes_fraction") < 0.05),
        "NO-heavy (>60%)": lambda f: f.filter(pl.col("no_fraction") > 0.60),
        "YES-lean (<40% NO)": lambda f: f.filter(pl.col("no_fraction") < 0.40),
        "Concentrated (CV>1.5)": lambda f: f.filter(pl.col("volume_cv") > 1.5),
        "Spread (CV<1.5)": lambda f: f.filter(pl.col("volume_cv") <= 1.5),
    }

    # Collect monthly results per grade
    monthly_data: dict[str, list[tuple[str, float, int]]] = {g: [] for g in grades}

    for month_name, h_start, h_end in WINDOWS:
        t_end = h_start
        skilled = get_consistent_traders(
            df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS
        )
        eligible = set(
            med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
        )
        pool = skilled & mvf_subsets[MVF_BAND] & eligible

        if len(pool) < 5:
            continue

        train_feat = compute_per_trader_features(df_pnl, pool, TRAIN_ANCHOR, t_end)
        ho_feat = compute_per_trader_features(df_pnl, pool, h_start, h_end)

        for grade_name, grade_fn in grades.items():
            grade_traders = set(grade_fn(train_feat)["trader"].to_list())
            if len(grade_traders) == 0:
                monthly_data[grade_name].append((month_name, 0.0, 0))
                continue

            grade_ho = ho_feat.filter(pl.col("trader").is_in(list(grade_traders)))
            if grade_ho.height == 0:
                monthly_data[grade_name].append((month_name, 0.0, 0))
                continue

            # Equal-weight ROI: mean of per-trader ROIs
            avg_roi = float(grade_ho["roi"].mean()) * 100
            monthly_data[grade_name].append((month_name, avg_roi, grade_ho.height))

    # Print walk-forward table
    months = [w[0] for w in WINDOWS]
    header = f"{'Grade':<25}" + "".join(f" {m:>8}" for m in months) + f" {'AVG':>8} {'Win%':>6}"
    print(header)
    print("-" * len(header))

    for grade_name in grades:
        rois = [r for _, r, n in monthly_data[grade_name] if n > 0]
        row_str = f"{grade_name:<25}"
        for month_name, roi, n in monthly_data[grade_name]:
            if n > 0:
                row_str += f" {roi:>7.1f}%"
            else:
                row_str += f" {'---':>8}"
        avg = sum(rois) / len(rois) if rois else 0
        win_pct = sum(1 for r in rois if r > 0) / len(rois) * 100 if rois else 0
        row_str += f" {avg:>7.1f}% {win_pct:>5.0f}%"
        print(row_str)

    # ===================================================================
    # 3. DEEP VALUE MECHANISM: What are deep value traders actually doing?
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  3. DEEP VALUE TRADERS: What makes them different?")
    print(f"{'=' * 70}\n")

    # Use full training period
    pool_full = skilled & mvf_subsets[MVF_BAND] & eligible
    train_feat_full = compute_per_trader_features(
        df_pnl, pool_full, TRAIN_ANCHOR, datetime(2025, 10, 1)
    )

    deep = train_feat_full.filter(pl.col("mean_entry") < 0.55)
    expensive = train_feat_full.filter(pl.col("mean_entry") >= 0.70)

    for label, subset in [("Deep value (<0.55)", deep), ("Expensive (>0.70)", expensive)]:
        print(f"  {label}: {subset.height} traders")
        if subset.height == 0:
            continue
        print(f"    Avg NO fraction:   {float(subset['no_fraction'].mean())*100:.0f}%")
        print(f"    Avg win rate:      {float(subset['win_rate'].mean())*100:.1f}%")
        print(f"    Avg ROI:           {float(subset['roi'].mean())*100:.1f}%")
        print(f"    Avg longshot YES:  {float(subset['longshot_yes_fraction'].mean())*100:.1f}%")
        print(f"    Avg markets:       {float(subset['n_markets'].mean()):.0f}")
        print(f"    Avg volume:        ${float(subset['total_volume'].mean()):,.0f}")
        print(f"    Volume CV:         {float(subset['volume_cv'].mean()):.2f}")
        print(f"    Sizing conviction: {float(subset['sizing_conviction'].mean()):.2f}")
        print()

    # ===================================================================
    # 4. COPY PnL SIMULATION: Grade → capital allocation → holdout PnL
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  4. COPY SIMULATION: $1,500 capital, graded allocation")
    print(f"{'=' * 70}\n")

    CAPITAL = 1_500.0

    strategies = {
        "All equal-weight": lambda f: set(f["trader"].to_list()),
        "Deep value only": lambda f: set(f.filter(pl.col("mean_entry") < 0.55)["trader"].to_list()),
        "Expensive excluded": lambda f: set(f.filter(pl.col("mean_entry") < 0.70)["trader"].to_list()),
        "Longshot YES >15%": lambda f: set(
            f.filter(pl.col("longshot_yes_fraction") > 0.15)["trader"].to_list()
        ),
        "NO-heavy only": lambda f: set(f.filter(pl.col("no_fraction") > 0.60)["trader"].to_list()),
        "YES-lean only": lambda f: set(f.filter(pl.col("no_fraction") < 0.40)["trader"].to_list()),
        "Deep + concentrated": lambda f: set(
            f.filter(
                (pl.col("mean_entry") < 0.55) & (pl.col("volume_cv") > 1.5)
            )["trader"].to_list()
        ),
    }

    header = f"{'Strategy':<25}" + "".join(f" {m:>8}" for m in months) + f" {'TOTAL':>9} {'N_avg':>6}"
    print(header)
    print("-" * len(header))

    for strat_name, strat_fn in strategies.items():
        bankroll = CAPITAL
        pnls = []
        n_traders_list = []

        for month_name, h_start, h_end in WINDOWS:
            t_end = h_start
            skilled = get_consistent_traders(
                df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS
            )
            eligible = set(
                med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
            )
            pool = skilled & mvf_subsets[MVF_BAND] & eligible

            if len(pool) < 5:
                pnls.append(0.0)
                n_traders_list.append(0)
                continue

            train_feat = compute_per_trader_features(df_pnl, pool, TRAIN_ANCHOR, t_end)
            selected = strat_fn(train_feat)

            if len(selected) < 2:
                pnls.append(0.0)
                n_traders_list.append(0)
                continue

            # Holdout PnL per trader
            ho = df_pnl.filter(
                (pl.col("resolved_at") >= h_start)
                & (pl.col("resolved_at") < h_end)
                & pl.col("trader").is_in(list(selected))
            )
            if ho.height == 0:
                pnls.append(0.0)
                n_traders_list.append(len(selected))
                continue

            th = ho.group_by("trader").agg(
                pl.col("market_pnl").sum().alias("pnl"),
                pl.col("market_volume").sum().alias("vol"),
            ).with_columns(
                (pl.col("pnl") / pl.col("vol")).alias("roi")
            )

            n_traders = th.height
            cap_per = bankroll / n_traders
            month_pnl = float((cap_per * th["roi"]).sum())
            pnls.append(month_pnl)
            n_traders_list.append(n_traders)
            bankroll += month_pnl

        row_str = f"{strat_name:<25}"
        for p in pnls:
            row_str += f" ${p:>6,.0f}" if abs(p) < 100_000 else f" ${p:>6,.0f}"
        total = sum(pnls)
        avg_n = sum(n_traders_list) / len(n_traders_list) if n_traders_list else 0
        row_str += f"  ${total:>7,.0f} {avg_n:>5.0f}"
        print(row_str)

    # ===================================================================
    # 5. DIRECTION × ENTRY PRICE interaction
    # ===================================================================
    print(f"\n{'=' * 70}")
    print("  5. DIRECTION x ENTRY PRICE INTERACTION")
    print(f"{'=' * 70}\n")

    # For the full pool, compute position-level stats
    pool_full = skilled & mvf_subsets[MVF_BAND] & eligible
    ho_all = df_pnl.filter(
        (pl.col("resolved_at") >= datetime(2025, 5, 1))
        & (pl.col("resolved_at") < datetime(2026, 2, 1))
        & pl.col("trader").is_in(list(pool_full))
    ).with_columns(
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("direction"),
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry_price"),
    )

    # Entry price bins × direction
    ho_all = ho_all.with_columns(
        pl.when(pl.col("dir_entry_price") < 0.40)
        .then(pl.lit("A: <40c"))
        .when(pl.col("dir_entry_price") < 0.55)
        .then(pl.lit("B: 40-55c"))
        .when(pl.col("dir_entry_price") < 0.70)
        .then(pl.lit("C: 55-70c"))
        .when(pl.col("dir_entry_price") < 0.85)
        .then(pl.lit("D: 70-85c"))
        .otherwise(pl.lit("E: >85c"))
        .alias("entry_bin"),
    )

    cross = ho_all.group_by(["direction", "entry_bin"]).agg(
        pl.col("market_pnl").len().alias("n_bets"),
        pl.col("market_pnl").sum().alias("total_pnl"),
        pl.col("market_volume").sum().alias("total_vol"),
        (pl.col("market_pnl") > 0).mean().alias("win_rate"),
    ).with_columns(
        (pl.col("total_pnl") / pl.col("total_vol") * 100).alias("roi_pct"),
        (pl.col("total_pnl") / pl.col("n_bets")).alias("pnl_per_bet"),
    ).sort(["direction", "entry_bin"])

    print(f"{'Direction':<10} {'Entry Bin':<12} {'N Bets':>8} {'WR':>7} {'ROI':>8} "
          f"{'PnL':>12} {'$/bet':>8}")
    print("-" * 70)
    for row in cross.iter_rows(named=True):
        pnl_s = f"${row['total_pnl']:,.0f}"
        ppb = f"${row['pnl_per_bet']:,.0f}"
        print(
            f"{row['direction']:<10} {row['entry_bin']:<12} {row['n_bets']:>8} "
            f"{row['win_rate']*100:>6.1f}% {row['roi_pct']:>7.1f}% "
            f"{pnl_s:>12} {ppb:>8}"
        )


if __name__ == "__main__":
    main()
