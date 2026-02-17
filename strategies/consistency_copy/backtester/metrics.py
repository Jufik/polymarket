"""P&L curve analytics — compute summary metrics from daily PnL time series.

Takes a DataFrame with columns (resolved_date: Date, daily_pnl: Float64,
n_bets: Int64, n_wins: Int64) and returns a dict of summary statistics.
"""

from __future__ import annotations

import math

import polars as pl


def _max_streak(signs: list[bool]) -> int:
    """Return the length of the longest consecutive True run."""
    max_run = 0
    current = 0
    for s in signs:
        if s:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def compute_metrics(
    daily_pnl: pl.DataFrame,
    base_rate: float | None = None,
    baseline_daily_pnl: list[float] | None = None,
) -> dict:
    """Compute summary metrics from a daily PnL DataFrame.

    Parameters
    ----------
    daily_pnl
        DataFrame with columns: resolved_date (Date), daily_pnl (Float64),
        n_bets (Int64), n_wins (Int64).
    base_rate
        Optional base hit rate. When provided, adds ``excess_hr`` and
        ``base_adjusted_sharpe`` to the returned dict.
    baseline_daily_pnl
        Optional list of daily PnL values for the baseline strategy, used to
        compute ``base_adjusted_sharpe``.

    Returns
    -------
    dict
        Dictionary with keys: total_pnl, total_bets, total_wins, hit_rate,
        sharpe, max_drawdown, max_drawdown_pct, profit_factor, avg_daily_pnl,
        pnl_per_bet, n_days, win_days, loss_days, best_day, worst_day,
        max_win_streak, max_loss_streak.  When *base_rate* is provided, also
        includes excess_hr and base_adjusted_sharpe.
    """
    if daily_pnl.height == 0:
        result = {
            "total_pnl": 0,
            "total_bets": 0,
            "total_wins": 0,
            "hit_rate": 0,
            "sharpe": 0,
            "max_drawdown": 0,
            "max_drawdown_pct": 0,
            "profit_factor": 0,
            "avg_daily_pnl": 0,
            "pnl_per_bet": 0,
            "n_days": 0,
            "win_days": 0,
            "loss_days": 0,
            "best_day": 0,
            "worst_day": 0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
        }
        if base_rate is not None:
            result["excess_hr"] = 0.0
            result["base_adjusted_sharpe"] = 0.0
        return result

    # Sort by date to ensure correct temporal ordering
    df = daily_pnl.sort("resolved_date")

    pnl_series = df["daily_pnl"].to_list()
    n_days = len(pnl_series)

    # Basic aggregates
    total_pnl = sum(pnl_series)
    total_bets = int(df["n_bets"].sum())
    total_wins = int(df["n_wins"].sum())
    hit_rate = total_wins / total_bets if total_bets > 0 else 0.0

    avg_daily_pnl = total_pnl / n_days
    pnl_per_bet = total_pnl / total_bets if total_bets > 0 else 0.0

    # Day-level stats
    win_days = sum(1 for x in pnl_series if x > 0)
    loss_days = sum(1 for x in pnl_series if x < 0)
    best_day = max(pnl_series)
    worst_day = min(pnl_series)

    # Gross wins / gross losses for profit factor
    gross_wins = sum(x for x in pnl_series if x > 0)
    gross_losses = sum(-x for x in pnl_series if x < 0)
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 0.0

    # Sharpe ratio: annualized from daily, sqrt(365)
    if n_days >= 2:
        mean_pnl = total_pnl / n_days
        var_pnl = sum((x - mean_pnl) ** 2 for x in pnl_series) / (n_days - 1)
        std_pnl = math.sqrt(var_pnl)
        sharpe = (mean_pnl / std_pnl) * math.sqrt(365) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown (absolute and percentage)
    cumulative = []
    running = 0.0
    for x in pnl_series:
        running += x
        cumulative.append(running)

    peak = cumulative[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for cum in cumulative:
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak if peak > 0 else 0.0

    # Streaks
    win_signs = [x > 0 for x in pnl_series]
    loss_signs = [x < 0 for x in pnl_series]
    max_win_streak = _max_streak(win_signs)
    max_loss_streak = _max_streak(loss_signs)

    result = {
        "total_pnl": total_pnl,
        "total_bets": total_bets,
        "total_wins": total_wins,
        "hit_rate": hit_rate,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "profit_factor": profit_factor,
        "avg_daily_pnl": avg_daily_pnl,
        "pnl_per_bet": pnl_per_bet,
        "n_days": n_days,
        "win_days": win_days,
        "loss_days": loss_days,
        "best_day": best_day,
        "worst_day": worst_day,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
    }

    if base_rate is not None:
        result["excess_hr"] = hit_rate - base_rate

        # Base-adjusted Sharpe: Sharpe of the excess series vs baseline
        if baseline_daily_pnl is not None and n_days >= 2:
            excess_series = [
                actual - baseline
                for actual, baseline in zip(pnl_series, baseline_daily_pnl)
            ]
            mean_excess = sum(excess_series) / len(excess_series)
            var_excess = sum(
                (x - mean_excess) ** 2 for x in excess_series
            ) / (len(excess_series) - 1)
            std_excess = math.sqrt(var_excess)
            if std_excess == 0:
                result["base_adjusted_sharpe"] = 0.0
            else:
                result["base_adjusted_sharpe"] = (
                    mean_excess / std_excess
                ) * math.sqrt(365)
        else:
            result["base_adjusted_sharpe"] = 0.0

    return result
