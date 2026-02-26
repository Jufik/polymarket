"""Assess S1 Proportional Copy strategy — pool construction, signal generation, profitability.

Walk-forward monthly holdout: train on expanding window, evaluate on next month.
Compares equal vs proportional sizing, with and without grading.

Usage:
    uv run python scripts/assess_s1_proportional_copy.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from polymarket_pipeline.strategies_impl.consensus_copy.consistency import (
    filter_consistent_traders,
)
from polymarket_pipeline.strategies_impl.proportional_copy.providers import (
    _compute_grading_stats,
)

DATA_DIR = Path("data/derived")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Pool construction
MIN_PERIODS = 9  # consecutive profitable months
MIN_MARKETS = 20
MAX_MVF = 0.10  # pure taker
MAX_MEDIAN_ENTRY = 0.80
MIN_LONGSHOT_YES_FRAC = 0.15
MAX_NO_FRACTION = 0.60

# Walk-forward windows
TRAIN_ANCHOR = datetime(2023, 1, 1, tzinfo=UTC)
FIRST_HOLDOUT = datetime(2025, 5, 1, tzinfo=UTC)
LAST_HOLDOUT = datetime(2026, 2, 1, tzinfo=UTC)
HOLDOUT_MONTHS = 1

# Sizing
BASE_BET = 100.0
FEE_PCT = 0.02
MAX_SIZING_MULT = 3.0
MAX_ENTRY_PRICE = 0.50  # slippage guard for proportional sizing evaluation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    y, m = divmod(total, 12)
    return datetime(y, m + 1, 1, tzinfo=dt.tzinfo)


def build_signal_table(
    holdout_pnl: pl.DataFrame,
    pool: frozenset[str],
) -> pl.DataFrame:
    """Build per-trader per-market signal table from holdout PnL data.

    Returns DataFrame with: trader, condition_id, first_trade, bet_yes,
    directional_entry, amount_usd, yes_won, signal_won.
    """
    df = holdout_pnl.filter(pl.col("trader").is_in(list(pool)))

    if df.height == 0:
        return pl.DataFrame()

    # Direction from net_yes_tokens
    df = df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes"),
    )

    # Directional entry price
    df = df.with_columns(
        pl.when(pl.col("bet_yes"))
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("directional_entry"),
    )

    # Did the bet win?
    df = df.with_columns(
        (
            (pl.col("bet_yes") & pl.col("yes_won"))
            | (~pl.col("bet_yes") & ~pl.col("yes_won"))
        ).alias("signal_won"),
    )

    return df.select([
        "trader",
        "condition_id",
        "first_trade",
        "bet_yes",
        "directional_entry",
        "market_volume",
        "yes_won",
        "signal_won",
    ])


def compute_metrics(
    signals: pl.DataFrame,
    *,
    sizing: str = "equal",
    base_bet: float = 100.0,
    fee_pct: float = 0.02,
    max_sizing_mult: float = 3.0,
    skip_contradicted: bool = True,
) -> dict:
    """Compute profitability metrics from a signal table.

    Parameters
    ----------
    sizing: "equal" or "proportional"
    skip_contradicted: Skip markets where pool traders disagree on direction.
    """
    if signals.height == 0:
        return {"n_signals": 0, "n_markets": 0, "hit_rate": 0.0, "total_pnl": 0.0}

    df = signals

    # Contradiction filter: skip markets with both YES and NO bets from pool
    if skip_contradicted:
        market_dirs = df.group_by("condition_id").agg(
            pl.col("bet_yes").sum().alias("n_yes"),
            (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
        )
        unanimous = market_dirs.filter(
            (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
        ).select("condition_id")
        df = df.join(unanimous, on="condition_id", how="inner")

    if df.height == 0:
        return {"n_signals": 0, "n_markets": 0, "hit_rate": 0.0, "total_pnl": 0.0}

    # Sizing
    if sizing == "proportional":
        # Per-trader average volume
        avg_vol = df.group_by("trader").agg(
            pl.col("market_volume").mean().alias("avg_vol")
        )
        df = df.join(avg_vol, on="trader", how="left")
        df = df.with_columns(
            (pl.col("market_volume") / pl.col("avg_vol"))
            .clip(0.0, max_sizing_mult)
            .fill_null(1.0)
            .alias("sizing_mult")
        )
        df = df.with_columns(
            (pl.lit(base_bet) * pl.col("sizing_mult")).alias("bet_usd")
        )
    else:
        df = df.with_columns(pl.lit(base_bet).alias("bet_usd"))

    # PnL: won → bet * (1-entry)/entry - fee; lost → -bet - fee
    df = df.with_columns(
        pl.when(pl.col("signal_won"))
        .then(
            pl.col("bet_usd") * (1.0 - pl.col("directional_entry"))
            / pl.col("directional_entry")
            - pl.col("bet_usd") * fee_pct
        )
        .otherwise(-pl.col("bet_usd") - pl.col("bet_usd") * fee_pct)
        .alias("pnl")
    )

    total_pnl = float(df["pnl"].sum())
    n_signals = df.height
    n_markets = df["condition_id"].n_unique()
    n_traders = df["trader"].n_unique()
    hit_rate = float(df["signal_won"].mean())
    avg_entry = float(df["directional_entry"].mean())

    # Monthly breakdown
    if "first_trade" in df.columns:
        monthly = (
            df.with_columns(
                pl.col("first_trade").dt.strftime("%Y-%m").alias("month")
            )
            .group_by("month")
            .agg(
                pl.col("pnl").sum().alias("monthly_pnl"),
                pl.len().alias("monthly_signals"),
                pl.col("signal_won").mean().alias("monthly_hr"),
            )
            .sort("month")
        )
    else:
        monthly = pl.DataFrame()

    # Direction breakdown
    yes_df = df.filter(pl.col("bet_yes"))
    no_df = df.filter(~pl.col("bet_yes"))

    return {
        "n_signals": n_signals,
        "n_markets": n_markets,
        "n_traders": n_traders,
        "hit_rate": hit_rate,
        "total_pnl": total_pnl,
        "avg_entry": avg_entry,
        "avg_pnl_per_signal": total_pnl / n_signals if n_signals > 0 else 0.0,
        "yes_signals": yes_df.height,
        "yes_hr": float(yes_df["signal_won"].mean()) if yes_df.height > 0 else 0.0,
        "yes_pnl": float(yes_df["pnl"].sum()) if yes_df.height > 0 else 0.0,
        "no_signals": no_df.height,
        "no_hr": float(no_df["signal_won"].mean()) if no_df.height > 0 else 0.0,
        "no_pnl": float(no_df["pnl"].sum()) if no_df.height > 0 else 0.0,
        "monthly": monthly,
    }


def _print_metrics(label: str, m: dict) -> None:
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  Signals: {m['n_signals']:,}  Markets: {m['n_markets']:,}  Traders: {m['n_traders']}")
    print(f"  Hit rate: {m['hit_rate']:.1%}  Avg entry: {m['avg_entry']:.3f}")
    print(f"  Total PnL: ${m['total_pnl']:,.0f}  Avg PnL/signal: ${m['avg_pnl_per_signal']:.2f}")
    print(f"  YES: {m['yes_signals']:,} sigs, {m['yes_hr']:.1%} HR, ${m['yes_pnl']:,.0f} PnL")
    print(f"  NO:  {m['no_signals']:,} sigs, {m['no_hr']:.1%} HR, ${m['no_pnl']:,.0f} PnL")

    monthly = m.get("monthly")
    if monthly is not None and monthly.height > 0:
        print(f"\n  Monthly breakdown:")
        print(f"  {'Month':<10} {'Signals':>8} {'HR':>8} {'PnL':>12}")
        print(f"  {'-'*40}")
        win_months = 0
        for row in monthly.to_dicts():
            pnl_str = f"${row['monthly_pnl']:,.0f}"
            hr_str = f"{row['monthly_hr']:.1%}"
            print(f"  {row['month']:<10} {row['monthly_signals']:>8} {hr_str:>8} {pnl_str:>12}")
            if row["monthly_pnl"] > 0:
                win_months += 1
        print(f"  {'-'*40}")
        print(f"  Win months: {win_months}/{monthly.height}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    t0 = time.time()

    print("[load] Reading derived data...")
    pnl = pl.read_parquet(DATA_DIR / "trader_market_pnl.parquet")
    resolved = pl.read_parquet(DATA_DIR / "markets_resolved.parquet")
    mvf = pl.read_parquet(DATA_DIR / "maker_volume_fractions.parquet")

    # Strip tz if present
    for col in ["resolved_at"]:
        dtype = resolved.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            resolved = resolved.with_columns(
                pl.col(col).dt.replace_time_zone(None)
            )
    for col in ["first_trade", "last_trade"]:
        dtype = pnl.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            pnl = pnl.with_columns(pl.col(col).dt.replace_time_zone(None))

    # Join PnL with resolution data
    df_pnl = pnl.join(resolved, on="condition_id", how="inner")
    print(f"[load] Joined PnL: {df_pnl.height:,} rows, "
          f"{df_pnl['trader'].n_unique():,} traders, "
          f"{df_pnl['condition_id'].n_unique():,} markets")

    base_rate_yes = float(resolved["yes_won"].mean())
    print(f"[load] Base rate: YES={base_rate_yes:.1%}, NO={1-base_rate_yes:.1%}")

    # ======================================================================
    # Walk-forward assessment
    # ======================================================================

    all_signals_graded: list[pl.DataFrame] = []
    all_signals_ungraded: list[pl.DataFrame] = []
    pool_sizes: list[dict] = []

    cursor = FIRST_HOLDOUT
    while cursor < LAST_HOLDOUT:
        holdout_start = cursor
        holdout_end = _add_months(cursor, HOLDOUT_MONTHS)
        train_end = holdout_start

        label = f"{holdout_start:%Y-%m}"
        print(f"\n--- Window {label}: train → {train_end:%Y-%m-%d}, "
              f"holdout {holdout_start:%Y-%m-%d} → {holdout_end:%Y-%m-%d} ---")

        # Strip tz for comparison
        ts_naive = TRAIN_ANCHOR.replace(tzinfo=None)
        te_naive = train_end.replace(tzinfo=None)
        hs_naive = holdout_start.replace(tzinfo=None)
        he_naive = holdout_end.replace(tzinfo=None)

        # Build base pool (consistency only)
        base_pool = filter_consistent_traders(
            pnl=df_pnl,
            resolved=resolved,
            mvf=mvf,
            train_start=ts_naive,
            train_end=te_naive,
            min_periods=MIN_PERIODS,
            min_markets=MIN_MARKETS,
            max_mvf=MAX_MVF,
            max_median_entry=MAX_MEDIAN_ENTRY,
        )

        if not base_pool:
            print(f"  No consistent traders, skipping")
            cursor = holdout_end
            continue

        # Compute grading stats
        grading = _compute_grading_stats(
            df_pnl, resolved, base_pool, ts_naive, te_naive
        )

        # Graded pool: longshot YES >15%, NO fraction <60%
        graded_df = grading.filter(
            (pl.col("longshot_yes_frac") >= MIN_LONGSHOT_YES_FRAC)
            & (pl.col("no_frac") <= MAX_NO_FRACTION)
        )
        graded_pool = frozenset(graded_df["trader"].to_list())

        pool_sizes.append({
            "month": label,
            "base_pool": len(base_pool),
            "graded_pool": len(graded_pool),
        })

        print(f"  Base pool: {len(base_pool)} traders → Graded pool: {len(graded_pool)} traders")

        if len(graded_pool) < 3:
            print(f"  Graded pool too small, skipping")
            cursor = holdout_end
            continue

        # Get holdout data
        holdout = df_pnl.filter(
            (pl.col("resolved_at") >= hs_naive)
            & (pl.col("resolved_at") < he_naive)
        )

        if holdout.height == 0:
            print(f"  No holdout data, skipping")
            cursor = holdout_end
            continue

        # Build signal tables
        sigs_graded = build_signal_table(holdout, graded_pool)
        sigs_ungraded = build_signal_table(holdout, base_pool)

        print(f"  Graded signals: {sigs_graded.height:,}  Ungraded: {sigs_ungraded.height:,}")

        if sigs_graded.height > 0:
            all_signals_graded.append(sigs_graded)
        if sigs_ungraded.height > 0:
            all_signals_ungraded.append(sigs_ungraded)

        cursor = holdout_end

    # ======================================================================
    # Aggregate results
    # ======================================================================

    if not all_signals_graded:
        print("\n[done] No signals produced.")
        return

    combined_graded = pl.concat(all_signals_graded, how="diagonal_relaxed")
    combined_ungraded = pl.concat(all_signals_ungraded, how="diagonal_relaxed")

    print(f"\n\n{'#'*70}")
    print(f"  ASSESSMENT RESULTS — S1 Proportional Copy")
    print(f"  Walk-forward: {FIRST_HOLDOUT:%Y-%m} to {LAST_HOLDOUT:%Y-%m}")
    print(f"{'#'*70}")

    # Pool size evolution
    print(f"\n  Pool Size Evolution:")
    print(f"  {'Month':<10} {'Base':>8} {'Graded':>8}")
    print(f"  {'-'*28}")
    for ps in pool_sizes:
        print(f"  {ps['month']:<10} {ps['base_pool']:>8} {ps['graded_pool']:>8}")

    # 1. Graded + Equal sizing (baseline)
    m1 = compute_metrics(
        combined_graded, sizing="equal", base_bet=BASE_BET, fee_pct=FEE_PCT
    )
    _print_metrics("GRADED POOL + EQUAL SIZING (baseline)", m1)

    # 2. Graded + Proportional sizing
    m2 = compute_metrics(
        combined_graded, sizing="proportional", base_bet=BASE_BET,
        fee_pct=FEE_PCT, max_sizing_mult=MAX_SIZING_MULT,
    )
    _print_metrics("GRADED POOL + PROPORTIONAL SIZING", m2)

    # 3. Ungraded + Equal sizing (ablation)
    m3 = compute_metrics(
        combined_ungraded, sizing="equal", base_bet=BASE_BET, fee_pct=FEE_PCT
    )
    _print_metrics("UNGRADED POOL + EQUAL SIZING (ablation)", m3)

    # 4. No contradiction filter
    m4 = compute_metrics(
        combined_graded, sizing="equal", base_bet=BASE_BET, fee_pct=FEE_PCT,
        skip_contradicted=False,
    )
    _print_metrics("GRADED + EQUAL — NO CONTRADICTION FILTER", m4)

    # 5. Entry price band analysis (graded, equal)
    print(f"\n\n{'='*70}")
    print(f"  ENTRY PRICE BAND ANALYSIS (Graded, Equal, w/ contradiction filter)")
    print(f"{'='*70}")
    bands = [(0.0, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50),
             (0.50, 0.60), (0.60, 0.80), (0.80, 1.0)]

    # Need signals with contradiction filter applied
    sigs = combined_graded
    market_dirs = sigs.group_by("condition_id").agg(
        pl.col("bet_yes").sum().alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
    )
    unanimous = market_dirs.filter(
        (pl.col("n_yes") == 0) | (pl.col("n_no") == 0)
    ).select("condition_id")
    sigs = sigs.join(unanimous, on="condition_id", how="inner")

    print(f"  {'Band':<12} {'Signals':>8} {'HR':>8} {'Avg Entry':>10} {'PnL':>12} {'$/sig':>8}")
    print(f"  {'-'*60}")
    for lo, hi in bands:
        band_df = sigs.filter(
            (pl.col("directional_entry") >= lo)
            & (pl.col("directional_entry") < hi)
        )
        if band_df.height == 0:
            continue
        # PnL
        band_df = band_df.with_columns(
            pl.when(pl.col("signal_won"))
            .then(
                pl.lit(BASE_BET) * (1.0 - pl.col("directional_entry"))
                / pl.col("directional_entry")
                - pl.lit(BASE_BET) * FEE_PCT
            )
            .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
            .alias("pnl")
        )
        n = band_df.height
        hr = float(band_df["signal_won"].mean())
        avg_e = float(band_df["directional_entry"].mean())
        pnl = float(band_df["pnl"].sum())
        per_sig = pnl / n
        print(f"  {lo:.0%}-{hi:.0%}     {n:>8} {hr:>7.1%} {avg_e:>10.3f} {f'${pnl:,.0f}':>12} {f'${per_sig:.1f}':>8}")

    # 6. Direction x Entry band (the key insight)
    print(f"\n\n{'='*70}")
    print(f"  DIRECTION x ENTRY BAND (key insight from copy/14)")
    print(f"{'='*70}")
    print(f"  {'Dir':<5} {'Band':<12} {'Signals':>8} {'HR':>8} {'Avg Entry':>10} {'PnL':>12} {'$/sig':>8}")
    print(f"  {'-'*65}")

    for direction, label in [(True, "YES"), (False, "NO")]:
        dir_df = sigs.filter(pl.col("bet_yes") == direction)
        for lo, hi in bands:
            band_df = dir_df.filter(
                (pl.col("directional_entry") >= lo)
                & (pl.col("directional_entry") < hi)
            )
            if band_df.height < 10:
                continue
            band_df = band_df.with_columns(
                pl.when(pl.col("signal_won"))
                .then(
                    pl.lit(BASE_BET) * (1.0 - pl.col("directional_entry"))
                    / pl.col("directional_entry")
                    - pl.lit(BASE_BET) * FEE_PCT
                )
                .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
                .alias("pnl")
            )
            n = band_df.height
            hr = float(band_df["signal_won"].mean())
            avg_e = float(band_df["directional_entry"].mean())
            pnl = float(band_df["pnl"].sum())
            per_sig = pnl / n
            print(f"  {label:<5} {lo:.0%}-{hi:.0%}     {n:>8} {hr:>7.1%} {avg_e:>10.3f} {f'${pnl:,.0f}':>12} {f'${per_sig:.1f}':>8}")

    # 7. Compound equity curve simulation (graded, equal, $1500 initial)
    print(f"\n\n{'='*70}")
    print(f"  COMPOUND EQUITY CURVE ($1,500 initial, graded, equal)")
    print(f"{'='*70}")

    monthly_data = m1["monthly"]
    if monthly_data is not None and monthly_data.height > 0:
        capital = 1500.0
        print(f"  {'Month':<10} {'Start':>10} {'PnL':>10} {'End':>10} {'ROI':>8}")
        print(f"  {'-'*50}")
        for row in monthly_data.to_dicts():
            month_signals = row["monthly_signals"]
            if month_signals == 0:
                continue
            # Scale PnL by capital ratio (base_bet=$100, scale to actual capital/N_traders)
            # Simplified: use monthly PnL as fraction of total signals * base_bet
            total_deployed = month_signals * BASE_BET
            roi_on_deployed = row["monthly_pnl"] / total_deployed if total_deployed > 0 else 0
            # Apply to current capital (assume deploying a fraction proportional to signals)
            month_pnl = capital * roi_on_deployed
            start = capital
            capital += month_pnl
            print(f"  {row['month']:<10} ${start:>9,.0f} ${month_pnl:>+9,.0f} ${capital:>9,.0f} {roi_on_deployed:>+7.1%}")
        print(f"  {'-'*50}")
        print(f"  Final: ${capital:,.0f} ({(capital/1500-1)*100:+.1f}%)")

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
