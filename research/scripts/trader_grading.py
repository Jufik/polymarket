"""Trader behavioral grading: find dimensions that predict copy profitability.

Analyses:
1. Direction bias (YES-heavy vs NO-heavy vs balanced)
2. Sizing conviction (does bet size predict outcome?)
3. Entry timing (early vs late in market lifecycle)
4. Win/loss asymmetry (payoff ratio)
5. Volume concentration (HHI of bet sizes)
6. Category specialization
7. "Gambling" detection (small YES longshots)
8. Forward performance by grade

Usage:
    PYTHONPATH=. uv run python scripts/trader_grading.py
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
MAX_MEDIAN_ENTRY = 0.90  # wider pool for grading analysis; we'll measure entry price as a dimension
MVF_BAND = "pure_taker"
TRAIN_START = datetime(2023, 1, 1)
TRAIN_END = datetime(2025, 10, 1)  # Use Oct 2025 as cutoff
HOLDOUT_START = datetime(2025, 10, 1)
HOLDOUT_END = datetime(2026, 2, 1)  # 4-month holdout


def load_and_build_pool():
    """Load data and build the filtered trader pool."""
    df_pnl, mvf_df, _markets, _price_ts = _load_data()
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    med_entry_df = _compute_trader_median_entry(df_pnl)

    skilled = get_consistent_traders(df_pnl, TRAIN_START, TRAIN_END, CONSISTENCY_MONTHS, MIN_MARKETS)
    eligible = set(
        med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
    )
    pool = skilled & mvf_subsets[MVF_BAND] & eligible

    # Load market metadata
    mkt_meta = pl.read_parquet("data/metadata/markets.parquet").select(
        "condition_id", "question", "category", "neg_risk"
    )

    return df_pnl, pool, med_entry_df, mkt_meta


def compute_trader_features(
    df_pnl: pl.DataFrame,
    pool: set[str],
    mkt_meta: pl.DataFrame,
    start: datetime,
    end: datetime,
) -> pl.DataFrame:
    """Compute behavioral features for each trader in the pool."""
    df = df_pnl.filter(
        (pl.col("resolved_at") >= start)
        & (pl.col("resolved_at") < end)
        & pl.col("trader").is_in(list(pool))
    )

    # Join market metadata
    df = df.join(mkt_meta, on="condition_id", how="left")

    # Per-position features
    df = df.with_columns(
        # Direction: YES if net_yes_tokens > 0, else NO
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("direction"),
        # Directional entry price
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry_price"),
        # Won this market?
        pl.when(
            (pl.col("net_yes_tokens") > 0) & pl.col("yes_won")
            | (pl.col("net_yes_tokens") <= 0) & ~pl.col("yes_won")
        )
        .then(pl.lit(True))
        .otherwise(pl.lit(False))
        .alias("won"),
        # Is this a "Will" question?
        pl.col("question").str.to_lowercase().str.starts_with("will ").alias("is_will_q"),
        # Is YES price < 0.50 at entry? (longshot YES)
        (pl.col("wavg_yes_entry_price") < 0.50).alias("is_longshot_yes"),
    )

    # ----- Per-trader aggregates -----
    features = df.group_by("trader").agg(
        # Volume & counts
        pl.col("market_pnl").len().alias("n_markets"),
        pl.col("market_volume").sum().alias("total_volume"),
        pl.col("market_pnl").sum().alias("total_pnl"),

        # Direction bias
        (pl.col("direction") == "NO").mean().alias("no_fraction"),
        (pl.col("direction") == "YES").mean().alias("yes_fraction"),

        # Win rates
        pl.col("won").mean().alias("win_rate"),
        # Win rate on NO bets
        pl.col("won").filter(pl.col("direction") == "NO").mean().alias("win_rate_no"),
        # Win rate on YES bets
        pl.col("won").filter(pl.col("direction") == "YES").mean().alias("win_rate_yes"),

        # Sizing conviction: correlation between volume and win
        # Use volume rank * win as proxy
        pl.col("market_volume").filter(pl.col("won")).mean().alias("avg_win_volume"),
        pl.col("market_volume").filter(~pl.col("won")).mean().alias("avg_loss_volume"),

        # PnL asymmetry
        pl.col("market_pnl").filter(pl.col("market_pnl") > 0).mean().alias("avg_win_pnl"),
        pl.col("market_pnl").filter(pl.col("market_pnl") <= 0).mean().alias("avg_loss_pnl"),
        pl.col("market_pnl").filter(pl.col("market_pnl") > 0).len().alias("n_wins"),
        pl.col("market_pnl").filter(pl.col("market_pnl") <= 0).len().alias("n_losses"),

        # Entry price distribution
        pl.col("dir_entry_price").mean().alias("mean_entry"),
        pl.col("dir_entry_price").median().alias("median_entry"),
        pl.col("dir_entry_price").std().alias("std_entry"),
        pl.col("dir_entry_price").quantile(0.25).alias("q25_entry"),

        # Volume concentration (coefficient of variation)
        pl.col("market_volume").std().alias("vol_std"),
        pl.col("market_volume").mean().alias("vol_mean"),

        # Entry timing: fraction of positions where first_trade is early
        # Use first_trade month relative to resolved_at as proxy
        # Simpler: median trade count (more trades = more active/monitoring)
        pl.col("trade_count").median().alias("median_trades_per_market"),

        # "Will" question exposure
        pl.col("is_will_q").mean().alias("will_q_fraction"),

        # Longshot YES exposure (gambling proxy)
        (
            (pl.col("direction") == "YES") & pl.col("is_longshot_yes")
        ).mean().alias("longshot_yes_fraction"),

        # Category diversity: number of unique categories
        pl.col("category").n_unique().alias("n_categories"),

        # Neg-risk market fraction
        pl.col("neg_risk").mean().alias("neg_risk_fraction"),
    )

    # Compute top_category_share separately (can't use value_counts in group_by agg)
    cat_counts = df.group_by(["trader", "category"]).agg(
        pl.len().alias("cat_count")
    )
    trader_totals = cat_counts.group_by("trader").agg(
        pl.col("cat_count").sum().alias("total_count"),
        pl.col("cat_count").max().alias("max_cat_count"),
    ).with_columns(
        (pl.col("max_cat_count") / pl.col("total_count")).alias("top_category_share")
    ).select("trader", "top_category_share")

    features = features.join(trader_totals, on="trader", how="left").with_columns(
        pl.col("top_category_share").fill_null(1.0),
    )

    # Derived features
    features = features.with_columns(
        # Sizing conviction ratio: winners vs losers volume
        (
            pl.col("avg_win_volume") / pl.col("avg_loss_volume").clip(lower_bound=1.0)
        ).alias("sizing_conviction_ratio"),
        # Payoff ratio: avg win / abs(avg loss)
        (
            pl.col("avg_win_pnl") / (-pl.col("avg_loss_pnl")).clip(lower_bound=1.0)
        ).alias("payoff_ratio"),
        # Volume CV (concentration)
        (pl.col("vol_std") / pl.col("vol_mean").clip(lower_bound=1.0)).alias("volume_cv"),
        # ROI
        (pl.col("total_pnl") / pl.col("total_volume")).alias("roi"),
        # Direction: classify as NO-heavy (>70%), YES-heavy (>70%), or balanced
        pl.when(pl.col("no_fraction") > 0.70)
        .then(pl.lit("NO-heavy"))
        .when(pl.col("yes_fraction") > 0.70)
        .then(pl.lit("YES-heavy"))
        .otherwise(pl.lit("balanced"))
        .alias("direction_type"),
    )

    return features


def analyze_dimension(
    features_train: pl.DataFrame,
    features_holdout: pl.DataFrame,
    col: str,
    bins: list[tuple[str, pl.Expr]],
    label: str,
):
    """Analyze a dimension: bucket traders by training feature, measure holdout performance."""
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print()

    header = f"{'Bucket':<25} {'N':>5} {'Train ROI':>10} {'HO ROI':>10} {'HO WR':>7} {'HO PnL':>10} {'Sizing R':>9}"
    print(header)
    print("-" * len(header))

    for bucket_name, expr in bins:
        train_traders = set(features_train.filter(expr)["trader"].to_list())
        if len(train_traders) == 0:
            print(f"{bucket_name:<25} {'0':>5} {'N/A':>10} {'N/A':>10} {'N/A':>7} {'N/A':>10} {'N/A':>9}")
            continue

        t = features_train.filter(expr)
        h = features_holdout.filter(pl.col("trader").is_in(list(train_traders)))

        n = len(train_traders)
        n_ho = h.height
        train_roi = float(t["roi"].mean()) * 100 if t.height > 0 else 0
        ho_roi = float(h["roi"].mean()) * 100 if h.height > 0 else 0
        ho_wr = float(h["win_rate"].mean()) * 100 if h.height > 0 else 0
        ho_pnl = float(h["total_pnl"].sum()) if h.height > 0 else 0
        ho_sizing = float(h["sizing_conviction_ratio"].median()) if h.height > 0 else 0

        pnl_s = f"${ho_pnl:,.0f}"
        print(
            f"{bucket_name:<25} {n:>5} {train_roi:>9.1f}% {ho_roi:>9.1f}% "
            f"{ho_wr:>6.1f}% {pnl_s:>10} {ho_sizing:>9.2f}"
        )


def main():
    print("Loading data...")
    df_pnl, pool, med_entry_df, mkt_meta = load_and_build_pool()
    print(f"Pool size: {len(pool)} traders")

    print("\nComputing training features...")
    feat_train = compute_trader_features(df_pnl, pool, mkt_meta, TRAIN_START, TRAIN_END)
    print(f"Training features: {feat_train.height} traders with data")

    print("Computing holdout features...")
    feat_holdout = compute_trader_features(df_pnl, pool, mkt_meta, HOLDOUT_START, HOLDOUT_END)
    print(f"Holdout features: {feat_holdout.height} traders with data")

    # ===== 1. DIRECTION BIAS =====
    analyze_dimension(
        feat_train, feat_holdout, "direction_type",
        [
            ("NO-heavy (>70% NO)", pl.col("no_fraction") > 0.70),
            ("Balanced (30-70%)", (pl.col("no_fraction") >= 0.30) & (pl.col("no_fraction") <= 0.70)),
            ("YES-heavy (>70% YES)", pl.col("yes_fraction") > 0.70),
        ],
        "1. DIRECTION BIAS: Does NO-heavy predict better forward performance?",
    )

    # ===== 2. SIZING CONVICTION =====
    analyze_dimension(
        feat_train, feat_holdout, "sizing_conviction_ratio",
        [
            ("Low conviction (<1.2)", pl.col("sizing_conviction_ratio") < 1.2),
            ("Medium (1.2 - 2.0)", (pl.col("sizing_conviction_ratio") >= 1.2) & (pl.col("sizing_conviction_ratio") < 2.0)),
            ("High (2.0 - 3.0)", (pl.col("sizing_conviction_ratio") >= 2.0) & (pl.col("sizing_conviction_ratio") < 3.0)),
            ("Very high (>3.0)", pl.col("sizing_conviction_ratio") >= 3.0),
        ],
        "2. SIZING CONVICTION: Do traders who bet bigger on winners keep doing it?",
    )

    # ===== 3. PAYOFF RATIO =====
    analyze_dimension(
        feat_train, feat_holdout, "payoff_ratio",
        [
            ("Low payoff (<0.5)", pl.col("payoff_ratio") < 0.5),
            ("Medium (0.5 - 1.0)", (pl.col("payoff_ratio") >= 0.5) & (pl.col("payoff_ratio") < 1.0)),
            ("Balanced (1.0 - 2.0)", (pl.col("payoff_ratio") >= 1.0) & (pl.col("payoff_ratio") < 2.0)),
            ("High payoff (>2.0)", pl.col("payoff_ratio") >= 2.0),
        ],
        "3. PAYOFF RATIO: Avg win / |avg loss| — does asymmetry persist?",
    )

    # ===== 4. ENTRY PRICE =====
    analyze_dimension(
        feat_train, feat_holdout, "mean_entry",
        [
            ("Deep value (<0.55)", pl.col("mean_entry") < 0.55),
            ("Value (0.55-0.65)", (pl.col("mean_entry") >= 0.55) & (pl.col("mean_entry") < 0.65)),
            ("Fair (0.65-0.75)", (pl.col("mean_entry") >= 0.65) & (pl.col("mean_entry") < 0.75)),
            ("Expensive (>0.75)", pl.col("mean_entry") >= 0.75),
        ],
        "4. ENTRY PRICE: Does entering at lower prices predict better holdout?",
    )

    # ===== 5. VOLUME CONCENTRATION =====
    analyze_dimension(
        feat_train, feat_holdout, "volume_cv",
        [
            ("Uniform (CV<0.5)", pl.col("volume_cv") < 0.5),
            ("Moderate (0.5-1.5)", (pl.col("volume_cv") >= 0.5) & (pl.col("volume_cv") < 1.5)),
            ("Concentrated (1.5-3)", (pl.col("volume_cv") >= 1.5) & (pl.col("volume_cv") < 3.0)),
            ("Whale bets (CV>3)", pl.col("volume_cv") >= 3.0),
        ],
        "5. VOLUME CONCENTRATION: Spread bets vs whale bets",
    )

    # ===== 6. LONGSHOT YES GAMBLING =====
    analyze_dimension(
        feat_train, feat_holdout, "longshot_yes_fraction",
        [
            ("No gambling (0%)", pl.col("longshot_yes_fraction") < 0.01),
            ("Low (1-10%)", (pl.col("longshot_yes_fraction") >= 0.01) & (pl.col("longshot_yes_fraction") < 0.10)),
            ("Medium (10-25%)", (pl.col("longshot_yes_fraction") >= 0.10) & (pl.col("longshot_yes_fraction") < 0.25)),
            ("High gambler (>25%)", pl.col("longshot_yes_fraction") >= 0.25),
        ],
        "6. LONGSHOT YES GAMBLING: Buying YES at <50c (fav-longshot bias territory)",
    )

    # ===== 7. CATEGORY SPECIALIZATION =====
    analyze_dimension(
        feat_train, feat_holdout, "top_category_share",
        [
            ("Generalist (<30%)", pl.col("top_category_share") < 0.30),
            ("Moderate (30-50%)", (pl.col("top_category_share") >= 0.30) & (pl.col("top_category_share") < 0.50)),
            ("Focused (50-75%)", (pl.col("top_category_share") >= 0.50) & (pl.col("top_category_share") < 0.75)),
            ("Specialist (>75%)", pl.col("top_category_share") >= 0.75),
        ],
        "7. CATEGORY SPECIALIZATION: Focused vs generalist traders",
    )

    # ===== 8. "WILL" QUESTION EXPOSURE =====
    analyze_dimension(
        feat_train, feat_holdout, "will_q_fraction",
        [
            ("Low Will (<20%)", pl.col("will_q_fraction") < 0.20),
            ("Medium (20-40%)", (pl.col("will_q_fraction") >= 0.20) & (pl.col("will_q_fraction") < 0.40)),
            ("High (40-60%)", (pl.col("will_q_fraction") >= 0.40) & (pl.col("will_q_fraction") < 0.60)),
            ("Will-focused (>60%)", pl.col("will_q_fraction") >= 0.60),
        ],
        "8. 'WILL' QUESTION EXPOSURE: Cross-ref with fav-longshot NO bias",
    )

    # ===== 9. NEG-RISK EXPOSURE =====
    analyze_dimension(
        feat_train, feat_holdout, "neg_risk_fraction",
        [
            ("No neg-risk (0%)", pl.col("neg_risk_fraction") < 0.01),
            ("Low (1-25%)", (pl.col("neg_risk_fraction") >= 0.01) & (pl.col("neg_risk_fraction") < 0.25)),
            ("Medium (25-50%)", (pl.col("neg_risk_fraction") >= 0.25) & (pl.col("neg_risk_fraction") < 0.50)),
            ("High neg-risk (>50%)", pl.col("neg_risk_fraction") >= 0.50),
        ],
        "9. NEG-RISK MARKET EXPOSURE: Neg-risk amplifies but also concentrates risk",
    )

    # ===== 10. COMPOSITE GRADE =====
    print(f"\n{'=' * 70}")
    print("  10. COMPOSITE GRADING: Combine best signals")
    print(f"{'=' * 70}")
    print()

    # Grade A: NO-heavy + high sizing conviction + low gambling
    grade_a = (
        (pl.col("no_fraction") > 0.60)
        & (pl.col("sizing_conviction_ratio") >= 1.5)
        & (pl.col("longshot_yes_fraction") < 0.10)
    )
    # Grade B: balanced direction + decent conviction
    grade_b = (
        (pl.col("no_fraction") >= 0.30)
        & (pl.col("no_fraction") <= 0.70)
        & (pl.col("sizing_conviction_ratio") >= 1.2)
        & (pl.col("longshot_yes_fraction") < 0.20)
    )
    # Grade C: YES-heavy or gambler
    grade_c = (pl.col("yes_fraction") > 0.60) | (pl.col("longshot_yes_fraction") >= 0.20)

    for grade_name, grade_expr in [
        ("A: NO-lean + conviction + no gambling", grade_a),
        ("B: Balanced + decent conviction", grade_b),
        ("C: YES-heavy or gambler", grade_c),
    ]:
        train_set = set(feat_train.filter(grade_expr)["trader"].to_list())
        ho = feat_holdout.filter(pl.col("trader").is_in(list(train_set)))
        n = len(train_set)
        n_ho = ho.height
        if n_ho == 0:
            print(f"  Grade {grade_name}: {n} traders, no holdout data")
            continue
        ho_roi = float(ho["roi"].mean()) * 100
        ho_wr = float(ho["win_rate"].mean()) * 100
        ho_pnl = float(ho["total_pnl"].sum())
        ho_pnl_per = ho_pnl / n_ho if n_ho > 0 else 0

        print(f"  Grade {grade_name}")
        print(f"    Traders: {n} (train) / {n_ho} (holdout)")
        print(f"    Holdout ROI: {ho_roi:.1f}%")
        print(f"    Holdout WR:  {ho_wr:.1f}%")
        print(f"    Holdout PnL: ${ho_pnl:,.0f} total, ${ho_pnl_per:,.0f}/trader")
        print()

    # ===== FEATURE CORRELATION WITH HOLDOUT ROI =====
    print(f"\n{'=' * 70}")
    print("  FEATURE RANK-CORRELATION WITH HOLDOUT ROI")
    print(f"{'=' * 70}")
    print()

    # Merge training features with holdout ROI
    train_with_ho = feat_train.join(
        feat_holdout.select(["trader", pl.col("roi").alias("holdout_roi")]),
        on="trader",
        how="inner",
    )

    if train_with_ho.height > 10:
        corr_cols = [
            "no_fraction", "sizing_conviction_ratio", "payoff_ratio",
            "mean_entry", "volume_cv", "longshot_yes_fraction",
            "top_category_share", "will_q_fraction", "neg_risk_fraction",
            "win_rate", "roi", "n_markets",
        ]

        print(f"{'Feature':<30} {'Spearman r':>10} {'Direction':>12}")
        print("-" * 55)

        correlations = []
        for col in corr_cols:
            try:
                r = float(
                    train_with_ho.select(
                        pl.corr(col, "holdout_roi", method="spearman")
                    ).item()
                )
                direction = "+" if r > 0 else "-"
                strength = "strong" if abs(r) > 0.3 else "moderate" if abs(r) > 0.15 else "weak"
                correlations.append((col, r, f"{direction} {strength}"))
            except Exception:
                correlations.append((col, 0.0, "error"))

        # Sort by absolute correlation
        correlations.sort(key=lambda x: abs(x[1]), reverse=True)
        for col, r, direction in correlations:
            print(f"{col:<30} {r:>10.3f} {direction:>12}")
    else:
        print("  Not enough overlapping traders for correlation analysis")


if __name__ == "__main__":
    main()
