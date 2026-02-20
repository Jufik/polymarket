"""Kelly-optimized portfolio copy simulation.

Walk-forward monthly simulation: for each holdout month, build the trader pool
from training data, estimate per-trader Kelly fractions from monthly ROI, allocate
$1,500 capital accordingly, and simulate returns using actual holdout PnL.

Usage:
    PYTHONPATH=. uv run python scripts/kelly_portfolio_sim.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from strategies.consistency_copy.backtester.runner import (
    _compute_trader_median_entry,
    _load_data,
    _precompute_mvf_subsets,
    get_consistent_traders,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INITIAL_CAPITAL = 1_500.0
KELLY_CAPS = [0.10, 0.25, 0.50, 1.00]
MIN_TRAINING_MONTHS_OPTIONS = [4, 6]

# Pool config (best from portfolio analysis)
CONSISTENCY_MONTHS = 9
MIN_MARKETS = 20
MAX_MEDIAN_ENTRY = 0.80
MVF_BAND = "pure_taker"

# Walk-forward windows (monthly)
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
TRAIN_ANCHOR = datetime(2023, 1, 1)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MonthResult:
    month: str
    bankroll_start: float
    bankroll_end: float
    pnl: float
    roi_pct: float
    n_traders: int
    n_kelly_positive: int
    total_allocated: float
    best_trader_pnl: float
    worst_trader_pnl: float


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def compute_trader_monthly_roi(
    df_pnl: pl.DataFrame,
    pool: set[str],
    train_start: datetime,
    train_end: datetime,
) -> pl.DataFrame:
    """Compute per-trader monthly ROI series over the training period.

    Returns DataFrame: trader, month, monthly_pnl, monthly_vol, monthly_roi.
    """
    df = df_pnl.filter(
        (pl.col("resolved_at") >= train_start)
        & (pl.col("resolved_at") < train_end)
        & pl.col("trader").is_in(list(pool))
    )

    if df.height == 0:
        return pl.DataFrame(
            schema={
                "trader": pl.Utf8,
                "month": pl.UInt32,
                "monthly_pnl": pl.Float64,
                "monthly_vol": pl.Float64,
                "monthly_roi": pl.Float64,
            }
        )

    df = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y%m").cast(pl.UInt32).alias("month")
    )

    monthly = df.group_by(["trader", "month"]).agg(
        pl.col("market_pnl").sum().alias("monthly_pnl"),
        pl.col("market_volume").sum().alias("monthly_vol"),
    )

    monthly = monthly.with_columns(
        (pl.col("monthly_pnl") / pl.col("monthly_vol")).alias("monthly_roi")
    )

    return monthly


def estimate_kelly_fractions(
    monthly_roi: pl.DataFrame,
    kelly_cap: float,
    min_training_months: int,
) -> pl.DataFrame:
    """Estimate Kelly fraction per trader from their monthly ROI series.

    Kelly for continuous returns: f* = mu / sigma^2
    Capped at kelly_cap.

    Returns DataFrame: trader, mu, sigma, kelly_raw, kelly_capped, n_months.
    """
    stats = monthly_roi.group_by("trader").agg(
        pl.col("monthly_roi").mean().alias("mu"),
        pl.col("monthly_roi").std().alias("sigma"),
        pl.col("monthly_roi").len().alias("n_months"),
    )

    # Filter: enough months + positive mean + non-zero std
    stats = stats.filter(
        (pl.col("n_months") >= min_training_months)
        & (pl.col("mu") > 0)
        & (pl.col("sigma") > 0)
    )

    stats = stats.with_columns(
        (pl.col("mu") / (pl.col("sigma") ** 2)).alias("kelly_raw"),
    )

    stats = stats.with_columns(
        pl.col("kelly_raw").clip(0.0, kelly_cap).alias("kelly_capped"),
    )

    return stats


def allocate_capital(
    kelly_df: pl.DataFrame,
    bankroll: float,
) -> pl.DataFrame:
    """Allocate capital to traders based on Kelly fractions.

    Normalizes so total allocation <= bankroll (no leverage).

    Returns DataFrame with added column: allocation.
    """
    total_raw = kelly_df["kelly_capped"].sum()

    if total_raw <= 0:
        return kelly_df.with_columns(pl.lit(0.0).alias("allocation"))

    if total_raw <= 1.0:
        # Under-allocated: each trader gets kelly_capped * bankroll
        return kelly_df.with_columns(
            (pl.col("kelly_capped") * bankroll).alias("allocation")
        )
    else:
        # Over-allocated: normalize to bankroll
        return kelly_df.with_columns(
            (pl.col("kelly_capped") / total_raw * bankroll).alias("allocation")
        )


def simulate_holdout(
    df_pnl: pl.DataFrame,
    allocation: pl.DataFrame,
    h_start: datetime,
    h_end: datetime,
) -> pl.DataFrame:
    """Simulate holdout returns using actual trader PnL data.

    For each trader with an allocation, compute their holdout ROI
    and your PnL = allocation * holdout_roi.

    Returns DataFrame: trader, allocation, holdout_pnl, holdout_vol,
                       holdout_roi, your_pnl.
    """
    traders = set(allocation["trader"].to_list())

    holdout = df_pnl.filter(
        (pl.col("resolved_at") >= h_start)
        & (pl.col("resolved_at") < h_end)
        & pl.col("trader").is_in(list(traders))
    )

    if holdout.height == 0:
        return allocation.select(["trader", "allocation"]).with_columns(
            pl.lit(0.0).alias("holdout_pnl"),
            pl.lit(0.0).alias("holdout_vol"),
            pl.lit(0.0).alias("holdout_roi"),
            pl.lit(0.0).alias("your_pnl"),
        )

    trader_holdout = holdout.group_by("trader").agg(
        pl.col("market_pnl").sum().alias("holdout_pnl"),
        pl.col("market_volume").sum().alias("holdout_vol"),
    ).with_columns(
        (pl.col("holdout_pnl") / pl.col("holdout_vol")).alias("holdout_roi"),
    )

    result = allocation.select(["trader", "allocation"]).join(
        trader_holdout, on="trader", how="left"
    ).fill_null(0.0)

    result = result.with_columns(
        (pl.col("allocation") * pl.col("holdout_roi")).alias("your_pnl")
    )

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_simulation(
    df_pnl: pl.DataFrame,
    mvf_subsets: dict[str, set[str]],
    med_entry_df: pl.DataFrame,
    kelly_cap: float,
    min_training_months: int,
) -> list[MonthResult]:
    """Run walk-forward Kelly simulation across all windows."""
    bankroll = INITIAL_CAPITAL
    results: list[MonthResult] = []

    for month_name, h_start, h_end in WINDOWS:
        t_start = TRAIN_ANCHOR
        t_end = h_start

        # Build pool
        skilled = get_consistent_traders(
            df_pnl, t_start, t_end, CONSISTENCY_MONTHS, MIN_MARKETS
        )
        eligible = set(
            med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)[
                "trader"
            ].to_list()
        )
        pool = skilled & mvf_subsets[MVF_BAND] & eligible

        if len(pool) < 5:
            results.append(
                MonthResult(
                    month=month_name,
                    bankroll_start=bankroll,
                    bankroll_end=bankroll,
                    pnl=0.0,
                    roi_pct=0.0,
                    n_traders=len(pool),
                    n_kelly_positive=0,
                    total_allocated=0.0,
                    best_trader_pnl=0.0,
                    worst_trader_pnl=0.0,
                )
            )
            continue

        # Estimate Kelly fractions from training data
        monthly_roi = compute_trader_monthly_roi(df_pnl, pool, t_start, t_end)
        kelly_df = estimate_kelly_fractions(
            monthly_roi, kelly_cap, min_training_months
        )

        if kelly_df.height == 0:
            results.append(
                MonthResult(
                    month=month_name,
                    bankroll_start=bankroll,
                    bankroll_end=bankroll,
                    pnl=0.0,
                    roi_pct=0.0,
                    n_traders=len(pool),
                    n_kelly_positive=0,
                    total_allocated=0.0,
                    best_trader_pnl=0.0,
                    worst_trader_pnl=0.0,
                )
            )
            continue

        # Allocate capital
        alloc = allocate_capital(kelly_df, bankroll)

        # Simulate holdout
        holdout_result = simulate_holdout(df_pnl, alloc, h_start, h_end)

        month_pnl = float(holdout_result["your_pnl"].sum())
        total_allocated = float(alloc["allocation"].sum())

        results.append(
            MonthResult(
                month=month_name,
                bankroll_start=bankroll,
                bankroll_end=bankroll + month_pnl,
                pnl=month_pnl,
                roi_pct=(month_pnl / bankroll * 100) if bankroll > 0 else 0.0,
                n_traders=len(pool),
                n_kelly_positive=kelly_df.height,
                total_allocated=total_allocated,
                best_trader_pnl=float(holdout_result["your_pnl"].max()),
                worst_trader_pnl=float(holdout_result["your_pnl"].min()),
            )
        )

        bankroll += month_pnl

    return results


def run_equal_weight(
    df_pnl: pl.DataFrame,
    mvf_subsets: dict[str, set[str]],
    med_entry_df: pl.DataFrame,
) -> list[MonthResult]:
    """Run equal-weight (1/N) baseline for comparison."""
    bankroll = INITIAL_CAPITAL
    results: list[MonthResult] = []

    for month_name, h_start, h_end in WINDOWS:
        t_start = TRAIN_ANCHOR
        t_end = h_start

        skilled = get_consistent_traders(
            df_pnl, t_start, t_end, CONSISTENCY_MONTHS, MIN_MARKETS
        )
        eligible = set(
            med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)[
                "trader"
            ].to_list()
        )
        pool = skilled & mvf_subsets[MVF_BAND] & eligible

        if len(pool) < 5:
            results.append(
                MonthResult(
                    month=month_name,
                    bankroll_start=bankroll,
                    bankroll_end=bankroll,
                    pnl=0.0,
                    roi_pct=0.0,
                    n_traders=len(pool),
                    n_kelly_positive=0,
                    total_allocated=0.0,
                    best_trader_pnl=0.0,
                    worst_trader_pnl=0.0,
                )
            )
            continue

        holdout = df_pnl.filter(
            (pl.col("resolved_at") >= h_start)
            & (pl.col("resolved_at") < h_end)
            & pl.col("trader").is_in(list(pool))
        )
        if holdout.height == 0:
            results.append(
                MonthResult(
                    month=month_name,
                    bankroll_start=bankroll,
                    bankroll_end=bankroll,
                    pnl=0.0,
                    roi_pct=0.0,
                    n_traders=len(pool),
                    n_kelly_positive=0,
                    total_allocated=0.0,
                    best_trader_pnl=0.0,
                    worst_trader_pnl=0.0,
                )
            )
            continue

        th = holdout.group_by("trader").agg(
            pl.col("market_pnl").sum().alias("pnl"),
            pl.col("market_volume").sum().alias("vol"),
        ).with_columns(
            (pl.col("pnl") / pl.col("vol")).alias("roi")
        )

        n_traders = th.height
        cap_per = bankroll / n_traders
        per_trader_pnl = cap_per * th["roi"]
        month_pnl = float(per_trader_pnl.sum())

        results.append(
            MonthResult(
                month=month_name,
                bankroll_start=bankroll,
                bankroll_end=bankroll + month_pnl,
                pnl=month_pnl,
                roi_pct=(month_pnl / bankroll * 100) if bankroll > 0 else 0.0,
                n_traders=n_traders,
                n_kelly_positive=n_traders,
                total_allocated=bankroll,
                best_trader_pnl=float(per_trader_pnl.max()),
                worst_trader_pnl=float(per_trader_pnl.min()),
            )
        )

        bankroll += month_pnl

    return results


def print_results(
    label: str, results: list[MonthResult], show_detail: bool = True
) -> None:
    """Print simulation results."""
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print()

    if show_detail:
        hdr = (
            f"{'Month':>8} {'Start':>9} {'PnL':>9} {'End':>9} "
            f"{'ROI':>7} {'Traders':>8} {'Kelly+':>7} {'Alloc':>9}"
        )
        print(hdr)
        print("-" * len(hdr))

    for r in results:
        if show_detail:
            print(
                f"{r.month:>8} "
                f"${r.bankroll_start:>7,.0f} "
                f"{'$' + f'{r.pnl:,.0f}':>9} "
                f"${r.bankroll_end:>7,.0f} "
                f"{r.roi_pct:>6.1f}% "
                f"{r.n_traders:>8} "
                f"{r.n_kelly_positive:>7} "
                f"${r.total_allocated:>7,.0f}"
            )

    print()

    # Summary stats
    pnls = [r.pnl for r in results]
    bankrolls = [r.bankroll_end for r in results]
    total_pnl = sum(pnls)
    final = results[-1].bankroll_end
    total_return = (final / INITIAL_CAPITAL - 1) * 100
    n_months = len(results)
    avg_monthly = total_pnl / n_months
    positive_months = sum(1 for p in pnls if p > 0)

    # Sharpe (monthly)
    import statistics

    if len(pnls) > 1 and statistics.stdev(pnls) > 0:
        sharpe = statistics.mean(pnls) / statistics.stdev(pnls)
    else:
        sharpe = 0.0

    # Max drawdown
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    running = INITIAL_CAPITAL
    for r in results:
        running = r.bankroll_end
        if running > peak:
            peak = running
        dd = (peak - running) / peak
        if dd > max_dd:
            max_dd = dd

    print(f"  Initial capital:   ${INITIAL_CAPITAL:,.0f}")
    print(f"  Final capital:     ${final:,.0f}")
    print(f"  Total PnL:         ${total_pnl:,.0f} ({total_return:+.1f}%)")
    print(f"  Avg monthly PnL:   ${avg_monthly:,.0f}")
    print(f"  Monthly Sharpe:    {sharpe:.2f}")
    print(f"  Max drawdown:      {max_dd:.1%}")
    print(f"  Positive months:   {positive_months}/{n_months}")
    print(f"  Best month:        ${max(pnls):,.0f}")
    print(f"  Worst month:       ${min(pnls):,.0f}")


def main() -> None:
    print("Loading data...")
    df_pnl, mvf_df, _markets, _price_ts = _load_data()
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    med_entry_df = _compute_trader_median_entry(df_pnl)

    print(f"\nStarting capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Pool: {CONSISTENCY_MONTHS}m consistent, {MIN_MARKETS}+ mkts, "
          f"entry<={MAX_MEDIAN_ENTRY}, {MVF_BAND}")
    print(f"Windows: {len(WINDOWS)} months ({WINDOWS[0][0]} to {WINDOWS[-1][0]})")

    # Equal-weight baseline
    ew_results = run_equal_weight(df_pnl, mvf_subsets, med_entry_df)
    print_results("BASELINE: Equal-Weight (1/N)", ew_results)

    # Kelly simulations
    best_final = 0.0
    best_label = ""
    all_runs: list[tuple[str, list[MonthResult]]] = []

    for min_tm in MIN_TRAINING_MONTHS_OPTIONS:
        for kc in KELLY_CAPS:
            label = f"Kelly cap={kc:.0%}, min_months={min_tm}"
            results = run_simulation(
                df_pnl, mvf_subsets, med_entry_df,
                kelly_cap=kc, min_training_months=min_tm,
            )
            all_runs.append((label, results))
            final = results[-1].bankroll_end
            if final > best_final:
                best_final = final
                best_label = label

    # Print all runs (compact for non-best, detailed for best)
    for label, results in all_runs:
        is_best = label == best_label
        print_results(label, results, show_detail=is_best)

    # Summary comparison table
    print(f"\n{'=' * 70}")
    print("  COMPARISON TABLE")
    print(f"{'=' * 70}")
    print()
    print(f"{'Config':<35} {'Final':>8} {'PnL':>9} {'Return':>8} {'Sharpe':>7} {'MaxDD':>7}")
    print("-" * 78)

    import statistics

    # EW baseline
    pnls_ew = [r.pnl for r in ew_results]
    final_ew = ew_results[-1].bankroll_end
    sh_ew = (statistics.mean(pnls_ew) / statistics.stdev(pnls_ew)
             if len(pnls_ew) > 1 and statistics.stdev(pnls_ew) > 0 else 0.0)
    peak = INITIAL_CAPITAL
    dd_ew = 0.0
    run = INITIAL_CAPITAL
    for r in ew_results:
        run = r.bankroll_end
        if run > peak:
            peak = run
        dd = (peak - run) / peak
        if dd > dd_ew:
            dd_ew = dd
    ret_ew = (final_ew / INITIAL_CAPITAL - 1) * 100
    print(f"{'Equal-Weight (1/N)':<35} ${final_ew:>6,.0f} ${sum(pnls_ew):>7,.0f} "
          f"{ret_ew:>7.0f}% {sh_ew:>7.2f} {dd_ew:>6.1%}")

    for label, results in all_runs:
        pnls = [r.pnl for r in results]
        final = results[-1].bankroll_end
        sh = (statistics.mean(pnls) / statistics.stdev(pnls)
              if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0.0)
        peak = INITIAL_CAPITAL
        dd = 0.0
        run = INITIAL_CAPITAL
        for r in results:
            run = r.bankroll_end
            if run > peak:
                peak = run
            d = (peak - run) / peak
            if d > dd:
                dd = d
        ret = (final / INITIAL_CAPITAL - 1) * 100
        marker = " <-- BEST" if label == best_label else ""
        print(f"{label:<35} ${final:>6,.0f} ${sum(pnls):>7,.0f} "
              f"{ret:>7.0f}% {sh:>7.2f} {dd:>6.1%}{marker}")


if __name__ == "__main__":
    main()
