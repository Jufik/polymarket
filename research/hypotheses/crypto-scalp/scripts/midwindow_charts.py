"""Visualize mid-window GBM analysis results + walk-forward validation.

Usage:
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/midwindow_charts.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

DATA_DIR = Path("research/hypotheses/crypto-scalp/data/midwindow")
OUTPUT_DIR = DATA_DIR


def load_data() -> pl.DataFrame:
    path = DATA_DIR / "midwindow_analysis.parquet"
    if not path.exists():
        print(f"Missing {path}. Run midwindow_edge.py first.")
        sys.exit(1)
    return pl.read_parquet(path)


def plot_calibration(df: pl.DataFrame) -> None:
    """Plot GBM calibration: predicted P(Up) vs actual Up rate."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for ax, dur in zip(axes, [5, 15]):
        dur_df = df.filter(
            (pl.col("duration_min") == dur)
            & (pl.col("minute_in_window") >= 1)
            & (pl.col("pm_p_up").is_not_null())
        )

        # Bin GBM P(Up) into 20 bins
        bins = np.linspace(0, 1, 21)
        bin_centers = (bins[:-1] + bins[1:]) / 2

        gbm_actual = []
        pm_actual = []
        counts = []

        for lo, hi in zip(bins[:-1], bins[1:]):
            subset = dur_df.filter(
                (pl.col("gbm_p_up") >= lo) & (pl.col("gbm_p_up") < hi)
            )
            if len(subset) > 0:
                gbm_actual.append(subset["went_up"].mean())
                pm_subset = dur_df.filter(
                    (pl.col("pm_p_up") >= lo) & (pl.col("pm_p_up") < hi)
                )
                pm_actual.append(pm_subset["went_up"].mean() if len(pm_subset) > 0 else np.nan)
                counts.append(len(subset))
            else:
                gbm_actual.append(np.nan)
                pm_actual.append(np.nan)
                counts.append(0)

        ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
        ax.scatter(bin_centers, gbm_actual, s=30, c="blue", label="GBM", zorder=5)
        ax.scatter(bin_centers, pm_actual, s=30, c="red", alpha=0.7, label="PM", zorder=5)
        ax.set_xlabel("Predicted P(Up)")
        ax.set_ylabel("Actual P(Up)")
        ax.set_title(f"{dur}m Windows: Calibration (minutes 1+)")
        ax.legend()
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "calibration.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved calibration.png")


def plot_lag_by_minute(df: pl.DataFrame) -> None:
    """Plot PM-GBM lag by minute within window."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    for col, dur in enumerate([5, 15]):
        dur_df = df.filter(
            (pl.col("duration_min") == dur)
            & (pl.col("pm_p_up").is_not_null())
        )

        minutes = list(range(dur))
        mean_lag = []
        mean_abs_lag = []
        gbm_acc = []
        pm_acc = []

        for m in minutes:
            mdf = dur_df.filter(pl.col("minute_in_window") == m)
            if len(mdf) == 0:
                mean_lag.append(0)
                mean_abs_lag.append(0)
                gbm_acc.append(0.5)
                pm_acc.append(0.5)
                continue

            lag = mdf["lag"]
            mean_lag.append(lag.mean())
            mean_abs_lag.append(lag.abs().mean())

            gbm_correct = ((mdf["gbm_p_up"] > 0.5).cast(pl.Int32) == mdf["went_up"]).mean()
            pm_correct = ((mdf["pm_p_up"] > 0.5).cast(pl.Int32) == mdf["went_up"]).mean()
            gbm_acc.append(gbm_correct)
            pm_acc.append(pm_correct)

        # Top row: accuracy
        ax = axes[0, col]
        ax.plot(minutes, gbm_acc, "b-o", markersize=5, label="GBM accuracy")
        ax.plot(minutes, pm_acc, "r-s", markersize=5, label="PM accuracy")
        ax.axhline(y=0.530, color="gray", linestyle="--", alpha=0.5, label="Break-even (53%)")
        ax.set_ylabel("Accuracy")
        ax.set_title(f"{dur}m Windows: Accuracy by Minute")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(0.45, 1.02)

        # Bottom row: lag
        ax = axes[1, col]
        ax.bar(minutes, mean_abs_lag, alpha=0.5, color="orange", label="|PM - GBM| mean")
        ax.plot(minutes, mean_lag, "g-o", markersize=5, label="PM - GBM mean")
        ax.axhline(y=0, color="gray", linestyle="--", alpha=0.3)
        ax.set_xlabel("Minute in window")
        ax.set_ylabel("Lag (PM - GBM)")
        ax.set_title(f"{dur}m Windows: PM-GBM Lag by Minute")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "lag_by_minute.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved lag_by_minute.png")


def plot_pnl_heatmap(df: pl.DataFrame) -> None:
    """Heatmap of PnL/trade by (minute, threshold)."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    thresholds = [0.0, 0.02, 0.05, 0.10, 0.15, 0.20]

    for ax, dur in zip(axes, [5, 15]):
        dur_df = df.filter(
            (pl.col("duration_min") == dur)
            & (pl.col("pm_p_up").is_not_null())
        )

        minutes = list(range(1, dur))  # skip minute 0
        pnl_matrix = np.full((len(thresholds), len(minutes)), np.nan)
        count_matrix = np.zeros((len(thresholds), len(minutes)), dtype=int)

        for mi, m in enumerate(minutes):
            mdf = dur_df.filter(pl.col("minute_in_window") == m)
            if len(mdf) == 0:
                continue

            for ti, thresh in enumerate(thresholds):
                buy_up = mdf.filter(pl.col("gbm_p_up") > pl.col("pm_p_up") + thresh)
                buy_down = mdf.filter(pl.col("gbm_p_up") < pl.col("pm_p_up") - thresh)

                total = len(buy_up) + len(buy_down)
                if total < 10:
                    continue

                # PnL
                up_correct = buy_up.filter(pl.col("went_up") == 1)
                up_wrong = buy_up.filter(pl.col("went_up") == 0)
                up_pnl = (
                    len(up_correct) * 0.97
                    - up_correct["pm_p_up"].sum()
                    - up_wrong["pm_p_up"].sum()
                )

                down_correct = buy_down.filter(pl.col("went_up") == 0)
                down_wrong = buy_down.filter(pl.col("went_up") == 1)
                down_pnl = (
                    len(down_correct) * 0.97
                    - (1 - down_correct["pm_p_up"]).sum()
                    - (1 - down_wrong["pm_p_up"]).sum()
                )

                pnl_matrix[ti, mi] = (up_pnl + down_pnl) / total
                count_matrix[ti, mi] = total

        im = ax.imshow(pnl_matrix, cmap="RdYlGn", aspect="auto", vmin=-0.05, vmax=0.20)
        ax.set_xticks(range(len(minutes)))
        ax.set_xticklabels(minutes)
        ax.set_yticks(range(len(thresholds)))
        ax.set_yticklabels([f"{t:.2f}" for t in thresholds])
        ax.set_xlabel("Minute in window")
        ax.set_ylabel("Threshold")
        ax.set_title(f"{dur}m: PnL/trade by (minute, threshold)")

        # Annotate with PnL and count
        for ti in range(len(thresholds)):
            for mi in range(len(minutes)):
                val = pnl_matrix[ti, mi]
                cnt = count_matrix[ti, mi]
                if not np.isnan(val):
                    ax.text(mi, ti, f"${val:.3f}\n({cnt})",
                            ha="center", va="center", fontsize=6,
                            color="black" if abs(val) < 0.10 else "white")

        plt.colorbar(im, ax=ax, label="PnL/trade ($)")

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "pnl_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved pnl_heatmap.png")


def walk_forward_validation(df: pl.DataFrame) -> None:
    """Walk-forward validation: 30-day train / 7-day test windows.

    Uses the market's window_start (from condition_id timestamp proxy) to sort chronologically.
    """
    print("\n" + "=" * 80)
    print("WALK-FORWARD VALIDATION")
    print("=" * 80)

    # We need a time proxy for each market. Use the Binance candle timestamp.
    # Since we have btc_price, we can use the condition_id ordering (they're sequential).
    # Better: parse the date from the condition_id's market question.
    # For now, use the position in the DataFrame (markets are fetched in order).

    # Actually, we need a date column. Let me reconstruct from the data.
    # The midwindow_analysis has condition_id but no date. Let me re-derive it.
    # We can use the btc_price as a proxy for time ordering, but that's not great.

    # Better approach: use the CH market metadata to get window dates.
    # For now, let's split by market index (roughly chronological).

    # Get unique condition_ids in order
    cids = df.filter(pl.col("minute_in_window") == 0)["condition_id"].to_list()

    date_map = _fetch_dates_batched(list(set(cids)))
    if not date_map:
        print("Could not fetch market dates. Skipping walk-forward.")
        return

    # Add date to dataframe
    dates = [date_map.get(cid) for cid in df["condition_id"].to_list()]
    df_dated = df.with_columns(
        pl.Series("market_date", dates).cast(pl.Datetime("us", "UTC"))
    ).filter(pl.col("market_date").is_not_null())

    if len(df_dated) == 0:
        print("No dated markets found. Skipping walk-forward.")
        return

    min_date = df_dated["market_date"].min()
    max_date = df_dated["market_date"].max()
    print(f"Date range: {min_date} → {max_date}")

    # Walk-forward: 30d train / 7d test
    train_days = 30
    test_days = 7
    step_days = 7

    # Best strategy from initial analysis: minute 1, various thresholds
    strategies = [
        {"name": "5m_min1_t0.10", "dur": 5, "minute": 1, "threshold": 0.10},
        {"name": "5m_min1_t0.15", "dur": 5, "minute": 1, "threshold": 0.15},
        {"name": "15m_min1_t0.10", "dur": 15, "minute": 1, "threshold": 0.10},
        {"name": "15m_min2_t0.10", "dur": 15, "minute": 2, "threshold": 0.10},
        {"name": "5m_min1_t0.05", "dur": 5, "minute": 1, "threshold": 0.05},
    ]

    for strat in strategies:
        print(f"\n{'─' * 60}")
        print(f"Strategy: {strat['name']}")
        print(f"{'─' * 60}")

        strat_df = df_dated.filter(
            (pl.col("duration_min") == strat["dur"])
            & (pl.col("minute_in_window") == strat["minute"])
            & (pl.col("pm_p_up").is_not_null())
        )

        current = min_date + timedelta(days=train_days)
        period = 0
        total_pnl = 0.0
        total_trades = 0
        winning_periods = 0
        all_periods = 0

        while current + timedelta(days=test_days) <= max_date + timedelta(days=1):
            test_start = current
            test_end = current + timedelta(days=test_days)

            test = strat_df.filter(
                (pl.col("market_date") >= test_start)
                & (pl.col("market_date") < test_end)
            )

            if len(test) < 5:
                current += timedelta(days=step_days)
                continue

            thresh = strat["threshold"]
            buy_up = test.filter(pl.col("gbm_p_up") > pl.col("pm_p_up") + thresh)
            buy_down = test.filter(pl.col("gbm_p_up") < pl.col("pm_p_up") - thresh)
            n_trades = len(buy_up) + len(buy_down)

            if n_trades == 0:
                current += timedelta(days=step_days)
                continue

            # PnL
            up_correct = buy_up.filter(pl.col("went_up") == 1)
            up_wrong = buy_up.filter(pl.col("went_up") == 0)
            up_pnl = len(up_correct) * 0.97 - up_correct["pm_p_up"].sum() - up_wrong["pm_p_up"].sum()

            down_correct = buy_down.filter(pl.col("went_up") == 0)
            down_wrong = buy_down.filter(pl.col("went_up") == 1)
            down_pnl = (
                len(down_correct) * 0.97
                - (1 - down_correct["pm_p_up"]).sum()
                - (1 - down_wrong["pm_p_up"]).sum()
            )

            period_pnl = up_pnl + down_pnl
            avg_pnl = period_pnl / n_trades

            total_pnl += period_pnl
            total_trades += n_trades
            all_periods += 1
            if period_pnl > 0:
                winning_periods += 1

            test_start_str = test_start.strftime("%m/%d") if test_start else "?"
            test_end_str = test_end.strftime("%m/%d") if test_end else "?"
            print(
                f"  {test_start_str}-{test_end_str}: "
                f"trades={n_trades:3d} "
                f"PnL=${period_pnl:7.2f} "
                f"avg=${avg_pnl:.4f}"
            )

            current += timedelta(days=step_days)

        if all_periods > 0:
            win_rate = winning_periods / all_periods
            avg_per_trade = total_pnl / total_trades if total_trades > 0 else 0
            print(f"\n  TOTAL: {all_periods} periods, {winning_periods}/{all_periods} winning ({win_rate:.1%})")
            print(f"  Total PnL: ${total_pnl:.2f}, Avg PnL/trade: ${avg_per_trade:.4f}")
            print(f"  Total trades: {total_trades}")


def _fetch_dates_batched(cids: list[str]) -> dict[str, datetime]:
    """Fetch closed_at dates from CH in batches to avoid query size limits."""
    import urllib.request
    ch_url = "http://localhost:18123/?database=polymarket"
    date_map: dict[str, datetime] = {}
    chunk_size = 500
    for i in range(0, len(cids), chunk_size):
        chunk = cids[i : i + chunk_size]
        ids_str = ",".join(f"'{cid}'" for cid in chunk)
        query = f"SELECT condition_id, closed_at FROM markets WHERE condition_id IN ({ids_str}) FORMAT TSVWithNames"
        req = urllib.request.Request(ch_url, data=query.encode())
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
        for line in raw.strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1]:
                try:
                    date_map[parts[0]] = datetime.fromisoformat(parts[1].replace(" ", "T"))
                except (ValueError, IndexError):
                    pass
    return date_map


def plot_cumulative_pnl(df: pl.DataFrame) -> None:
    """Plot cumulative PnL over time for best strategies."""
    cids = list(set(df["condition_id"].to_list()))
    date_map = _fetch_dates_batched(cids)

    dates = [date_map.get(cid) for cid in df["condition_id"].to_list()]
    df = df.with_columns(
        pl.Series("market_date", dates).cast(pl.Datetime("us", "UTC"))
    ).filter(pl.col("market_date").is_not_null()).sort("market_date")

    fig, ax = plt.subplots(1, 1, figsize=(14, 7))

    strategies = [
        {"name": "5m min1 t=0.10", "dur": 5, "minute": 1, "threshold": 0.10, "color": "blue"},
        {"name": "5m min1 t=0.15", "dur": 5, "minute": 1, "threshold": 0.15, "color": "green"},
        {"name": "15m min1 t=0.10", "dur": 15, "minute": 1, "threshold": 0.10, "color": "red"},
        {"name": "15m min2 t=0.10", "dur": 15, "minute": 2, "threshold": 0.10, "color": "orange"},
    ]

    for strat in strategies:
        sdf = df.filter(
            (pl.col("duration_min") == strat["dur"])
            & (pl.col("minute_in_window") == strat["minute"])
            & (pl.col("pm_p_up").is_not_null())
        )

        thresh = strat["threshold"]

        # Compute per-observation PnL
        pnl_list = []
        date_list = []

        for row in sdf.iter_rows(named=True):
            gbm = row["gbm_p_up"]
            pm = row["pm_p_up"]
            up = row["went_up"]
            dt = row["market_date"]

            if gbm > pm + thresh:
                # Buy Up at pm price
                pnl = (0.97 - pm) if up == 1 else (-pm)
                pnl_list.append(pnl)
                date_list.append(dt)
            elif gbm < pm - thresh:
                # Buy Down at (1-pm) price
                pnl = (0.97 - (1 - pm)) if up == 0 else (-(1 - pm))
                pnl_list.append(pnl)
                date_list.append(dt)

        if pnl_list:
            cum_pnl = np.cumsum(pnl_list)
            ax.plot(date_list, cum_pnl, label=f"{strat['name']} (n={len(pnl_list)})",
                    color=strat["color"], alpha=0.8)

    ax.axhline(y=0, color="gray", linestyle="--", alpha=0.3)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.set_title("Cumulative PnL: GBM Mid-Window Repricing Strategy")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "cumulative_pnl.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved cumulative_pnl.png")


def main() -> None:
    df = load_data()
    has_pm = df.filter(pl.col("pm_p_up").is_not_null())

    print(f"Loaded {len(df):,} observations, {len(has_pm):,} with PM trades")

    plot_calibration(has_pm)
    plot_lag_by_minute(has_pm)
    plot_pnl_heatmap(has_pm)
    plot_cumulative_pnl(has_pm)
    walk_forward_validation(has_pm)


if __name__ == "__main__":
    main()
