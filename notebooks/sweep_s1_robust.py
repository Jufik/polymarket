"""
S1 Pool — Robust Parameter Sweep
=================================
Walk-forward validation with rolling windows, risk metrics, stability ranking,
bootstrap confidence intervals, and concentration analysis.

Outputs:
  notebooks/sweep_s1_robust_all.csv     — every (config × window) result
  notebooks/sweep_s1_robust_stable.csv  — configs ranked by cross-window stability
  notebooks/sweep_s1_robust_best.csv    — top configs with bootstrap CI
"""
import polars as pl
import clickhouse_connect
import itertools
import time
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════
CH_HOST = "192.168.0.148"
BET = 100.0

# Classification (fixed)
MAX_ENTRY = 0.90
MAX_POSITIONS = 5000
MIN_POSITIONS = 3

# Walk-forward windows: anchored expanding, monthly holdout step
# Train always starts at ANCHOR. Holdout is 2-month window stepping forward.
ANCHOR = datetime(2024, 1, 1)
WINDOWS = [
    # (train_end, holdout_end, label)
    (datetime(2025, 5, 1), datetime(2025, 7, 1), "May-Jun25"),
    (datetime(2025, 7, 1), datetime(2025, 9, 1), "Jul-Aug25"),
    (datetime(2025, 9, 1), datetime(2025, 11, 1), "Sep-Oct25"),
    (datetime(2025, 11, 1), datetime(2026, 1, 1), "Nov-Dec25"),
    (datetime(2026, 1, 1),  datetime(2026, 3, 1), "Jan-Feb26"),
]

# Sweep grid
GRID = {
    "min_hr":      [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85],
    "min_markets":  [10, 30, 50],
    "min_months":   [3, 6, 9],
    "direction":    ["ALL", "NO", "YES"],
    "entry_band":   [(0.0, 1.0), (0.0, 0.50), (0.0, 0.70), (0.30, 0.80)],
}

MONTHLY_HR = 0.50
MONTHLY_MIN_BETS = 3
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42


# ═══════════════════════════════════════════════════════════════════════
#  LOAD DATA
# ═══════════════════════════════════════════════════════════════════════
ch = clickhouse_connect.get_client(host=CH_HOST, port=18123, database="polymarket")
t0 = time.time()
try:
    arrow = ch.query_arrow("""
        SELECT trader, position, dir_entry, correct,
               market_volume, trade_count, resolved_at, month
        FROM _tmp_s1_enriched
    """)
    src = "enriched"
except Exception:
    arrow = ch.query_arrow("""
        SELECT p.trader,
            CASE WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
                 WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
                 WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
                 ELSE 'CLOSED' END AS position,
            CASE WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN p.wavg_yes_price
                 WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - p.wavg_yes_price
                 WHEN p.net_yes >= p.net_no THEN p.wavg_yes_price
                 ELSE 1.0 - p.wavg_yes_price END AS dir_entry,
            CASE WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN r.yes_won
                 WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT r.yes_won
                 WHEN p.net_yes >= p.net_no THEN r.yes_won
                 ELSE NOT r.yes_won END AS correct,
            p.volume AS market_volume, toUInt32(p.n_trades) AS trade_count,
            r.resolved_at, formatDateTime(r.resolved_at, '%Y-%m') AS month
        FROM _tmp_s1_positions p
        JOIN (SELECT m.condition_id, m.resolved_at, coalesce(t.yes_won, false) AS yes_won
              FROM markets m LEFT JOIN (
                  SELECT condition_id, true AS yes_won FROM token_market_map
                  WHERE outcome = 'YES' AND winner = true
              ) t ON m.condition_id = t.condition_id
              WHERE m.resolution_value = 1
        ) r ON p.condition_id = r.condition_id
        WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01)
    """)
    src = "positions+JOIN"

df = pl.from_arrow(arrow)
if "resolved_at" in df.columns:
    dtype = df.schema["resolved_at"]
    if isinstance(dtype, pl.Datetime) and dtype.time_zone:
        df = df.with_columns(pl.col("resolved_at").dt.replace_time_zone(None))
    elif dtype in (pl.UInt32, pl.Int64, pl.UInt64):
        df = df.with_columns(pl.from_epoch(pl.col("resolved_at"), time_unit="s").alias("resolved_at"))
if df.schema.get("correct") not in (pl.Boolean,):
    df = df.with_columns(pl.col("correct").cast(pl.Boolean))

# Apply classification once
directional = df.filter(pl.col("position").is_in(["YES", "NO"]))
s1 = directional.filter(pl.col("dir_entry") <= MAX_ENTRY)
counts = s1.group_by("trader").agg(pl.len().alias("n"))
bots = set(counts.filter(pl.col("n") > MAX_POSITIONS)["trader"].to_list())
oneoffs = set(counts.filter(pl.col("n") < MIN_POSITIONS)["trader"].to_list())
clean = s1.filter(~pl.col("trader").is_in(list(bots)) & ~pl.col("trader").is_in(list(oneoffs)))

print(f"Loaded {df.height:,} rows ({src}) in {time.time()-t0:.1f}s")
print(f"After classification: {clean.height:,} positions, {clean['trader'].n_unique():,} traders")
print(f"Walk-forward: {len(WINDOWS)} windows, anchor={ANCHOR:%Y-%m-%d}")
total_combos = (len(GRID['min_hr']) * len(GRID['min_markets']) * len(GRID['min_months'])
                * len(GRID['direction']) * len(GRID['entry_band']))
print(f"Sweep grid: {total_combos} configs × {len(WINDOWS)} windows = {total_combos * len(WINDOWS)} evals\n")


# ═══════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════
def compute_pnl(data: pl.DataFrame) -> pl.DataFrame:
    """Add pnl column."""
    return data.with_columns(
        pl.when(pl.col("correct"))
        .then(pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry"))
        .otherwise(-pl.lit(BET))
        .alias("pnl")
    )


def weekly_metrics(h: pl.DataFrame) -> dict:
    """Compute weekly Sharpe, max drawdown, profit factor, worst/best week."""
    if h.height < 5:
        return {"sharpe": 0, "max_dd": 0, "profit_factor": 0,
                "worst_week": 0, "best_week": 0, "n_weeks": 0,
                "pct_pos_weeks": 0}

    w = (
        h.with_columns(pl.col("resolved_at").dt.truncate("1w").alias("week"))
        .group_by("week").agg(
            pl.col("pnl").sum().alias("w_pnl"),
            pl.len().alias("w_n"),
        ).sort("week")
    )
    pnls = w["w_pnl"].to_numpy()
    n_weeks = len(pnls)
    if n_weeks < 2:
        return {"sharpe": 0, "max_dd": 0, "profit_factor": 0,
                "worst_week": float(pnls[0]) if n_weeks else 0,
                "best_week": float(pnls[0]) if n_weeks else 0,
                "n_weeks": n_weeks, "pct_pos_weeks": float((pnls > 0).mean())}

    mu = pnls.mean()
    sigma = pnls.std(ddof=1)
    sharpe = (mu / sigma * np.sqrt(52)) if sigma > 0 else 0  # annualized

    # Max drawdown on cumulative
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(dd.max())

    gains = pnls[pnls > 0].sum()
    losses = abs(pnls[pnls < 0].sum())
    profit_factor = gains / losses if losses > 0 else float("inf")

    return {
        "sharpe": round(float(sharpe), 2),
        "max_dd": round(max_dd, 1),
        "profit_factor": round(float(profit_factor), 2),
        "worst_week": round(float(pnls.min()), 1),
        "best_week": round(float(pnls.max()), 1),
        "n_weeks": n_weeks,
        "pct_pos_weeks": round(float((pnls > 0).mean()), 3),
    }


def concentration_metrics(h: pl.DataFrame) -> dict:
    """Top-trader concentration and Herfindahl index."""
    if h.height < 5:
        return {"top1_pct": 0, "top3_pct": 0, "herfindahl": 0, "n_active": 0}

    by_trader = h.group_by("trader").agg(
        pl.col("pnl").sum().alias("t_pnl"),
        pl.len().alias("t_n"),
    ).sort("t_pnl", descending=True)

    total_pnl = float(by_trader["t_pnl"].sum())
    if abs(total_pnl) < 1:
        # Use signal count for concentration if PnL near zero
        total = by_trader["t_n"].sum()
        shares = (by_trader["t_n"] / total).to_numpy()
    else:
        # Use absolute PnL contribution
        shares = (by_trader["t_pnl"].abs() / by_trader["t_pnl"].abs().sum()).to_numpy()

    top1 = float(shares[0]) if len(shares) >= 1 else 0
    top3 = float(shares[:3].sum()) if len(shares) >= 3 else float(shares.sum())
    hhi = float((shares ** 2).sum())

    return {
        "top1_pct": round(top1, 3),
        "top3_pct": round(top3, 3),
        "herfindahl": round(hhi, 4),
        "n_active": len(shares),
    }


def bootstrap_ci(pnls: np.ndarray, n_boot: int = BOOTSTRAP_N, seed: int = BOOTSTRAP_SEED):
    """Bootstrap 95% CI on mean PnL per signal and HR."""
    rng = np.random.default_rng(seed)
    n = len(pnls)
    if n < 10:
        return {"roi_lo": 0, "roi_hi": 0, "hr_lo": 0, "hr_hi": 0}

    boot_roi = np.empty(n_boot)
    boot_hr = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        sample = pnls[idx]
        boot_roi[i] = sample.mean() / BET * 100
        boot_hr[i] = (sample > 0).mean()

    return {
        "roi_lo": round(float(np.percentile(boot_roi, 2.5)), 2),
        "roi_hi": round(float(np.percentile(boot_roi, 97.5)), 2),
        "hr_lo": round(float(np.percentile(boot_hr, 2.5)), 4),
        "hr_hi": round(float(np.percentile(boot_hr, 97.5)), 4),
    }


# ═══════════════════════════════════════════════════════════════════════
#  WALK-FORWARD SWEEP
# ═══════════════════════════════════════════════════════════════════════
all_results = []
t_sweep = time.time()

for wi, (train_end, holdout_end, w_label) in enumerate(WINDOWS):
    print(f"Window {wi+1}/{len(WINDOWS)}: train [{ANCHOR:%Y-%m-%d}, {train_end:%Y-%m-%d}) "
          f"→ holdout [{train_end:%Y-%m-%d}, {holdout_end:%Y-%m-%d}) [{w_label}]")

    train_data = clean.filter(
        (pl.col("resolved_at") >= ANCHOR) & (pl.col("resolved_at") < train_end)
    )
    holdout_data = clean.filter(
        (pl.col("resolved_at") >= train_end) & (pl.col("resolved_at") < holdout_end)
    )

    if train_data.height == 0 or holdout_data.height == 0:
        print(f"  Skipping — train={train_data.height}, holdout={holdout_data.height}")
        continue

    # Pre-compute trader stats for this window
    t_stats = train_data.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        (pl.col("position") == "YES").mean().alias("yes_frac"),
        pl.col("dir_entry").median().alias("median_entry"),
        pl.col("month").n_unique().alias("active_months"),
    )
    _monthly = (
        train_data.group_by(["trader", "month"]).agg(
            pl.col("correct").mean().alias("m_hr"), pl.len().alias("m_n"),
        ).filter(pl.col("m_n") >= MONTHLY_MIN_BETS)
    )
    _good = (
        _monthly.filter(pl.col("m_hr") >= MONTHLY_HR)
        .group_by("trader").agg(pl.len().alias("good_months"))
    )
    t_stats = t_stats.join(_good, on="trader", how="left").with_columns(
        pl.col("good_months").fill_null(0)
    )

    # Baselines
    bl_cache = {}
    for d in ["ALL", "NO", "YES"]:
        b = holdout_data if d == "ALL" else holdout_data.filter(pl.col("position") == d)
        if b.height > 0:
            b = compute_pnl(b)
            bl_cache[d] = {"hr": float(b["correct"].mean()), "n": b.height}
        else:
            bl_cache[d] = {"hr": 0, "n": 0}

    n_configs = 0
    for min_hr, min_mkts, min_mos, direction, (elo, ehi) in itertools.product(
        GRID["min_hr"], GRID["min_markets"], GRID["min_months"],
        GRID["direction"], GRID["entry_band"],
    ):
        pool = t_stats.filter(
            (pl.col("hr") >= min_hr)
            & (pl.col("n_markets") >= min_mkts)
            & (pl.col("good_months") >= min_mos)
        )
        if pool.height < 3:
            continue

        pool_set = set(pool["trader"].to_list())
        h = holdout_data.filter(pl.col("trader").is_in(list(pool_set)))
        if direction != "ALL":
            h = h.filter(pl.col("position") == direction)
        # Entry band filter
        h = h.filter((pl.col("dir_entry") >= elo) & (pl.col("dir_entry") <= ehi))
        if h.height < 10:
            continue

        h = compute_pnl(h)
        hr = float(h["correct"].mean())
        total_pnl = float(h["pnl"].sum())
        roi = total_pnl / (h.height * BET) * 100
        bl = bl_cache[direction]

        wm = weekly_metrics(h)
        cm = concentration_metrics(h)

        all_results.append({
            "window": w_label,
            "min_hr": min_hr, "min_mkts": min_mkts, "min_mos": min_mos,
            "direction": direction, "entry_lo": elo, "entry_hi": ehi,
            "pool": pool.height, "signals": h.height,
            "HR": round(hr, 4), "ROI%": round(roi, 2),
            "PnL": round(total_pnl, 0),
            "bl_HR": round(bl["hr"], 4),
            "edge": round(hr - bl["hr"], 4),
            **wm, **cm,
        })
        n_configs += 1

    print(f"  {n_configs} valid configs, holdout={holdout_data.height:,} positions")

elapsed = time.time() - t_sweep
print(f"\nTotal: {len(all_results)} (config × window) results in {elapsed:.0f}s\n")


# ═══════════════════════════════════════════════════════════════════════
#  STABILITY RANKING
# ═══════════════════════════════════════════════════════════════════════
res_df = pl.DataFrame(all_results)
res_df.write_csv("notebooks/sweep_s1_robust_all.csv")
print(f"Saved {res_df.height} rows to sweep_s1_robust_all.csv\n")

# Group by config, aggregate across windows
config_cols = ["min_hr", "min_mkts", "min_mos", "direction", "entry_lo", "entry_hi"]
stability = (
    res_df.group_by(config_cols).agg(
        pl.len().alias("n_windows"),
        pl.col("HR").mean().alias("avg_HR"),
        pl.col("HR").std().alias("std_HR"),
        pl.col("HR").min().alias("min_HR"),
        pl.col("ROI%").mean().alias("avg_ROI"),
        pl.col("ROI%").std().alias("std_ROI"),
        pl.col("ROI%").min().alias("min_ROI"),
        pl.col("PnL").sum().alias("total_PnL"),
        pl.col("PnL").mean().alias("avg_PnL"),
        pl.col("signals").sum().alias("total_signals"),
        pl.col("signals").mean().alias("avg_signals"),
        pl.col("sharpe").mean().alias("avg_sharpe"),
        pl.col("sharpe").min().alias("min_sharpe"),
        pl.col("max_dd").max().alias("worst_dd"),
        pl.col("profit_factor").mean().alias("avg_pf"),
        pl.col("profit_factor").min().alias("min_pf"),
        pl.col("pct_pos_weeks").mean().alias("avg_pct_pos_wk"),
        pl.col("pool").mean().alias("avg_pool"),
        pl.col("herfindahl").mean().alias("avg_hhi"),
        pl.col("top3_pct").mean().alias("avg_top3"),
        # Count how many windows have positive ROI
        (pl.col("ROI%") > 0).sum().alias("pos_windows"),
    )
    .filter(pl.col("n_windows") >= 3)  # must appear in at least 3 windows
    .with_columns(
        (pl.col("pos_windows") / pl.col("n_windows")).alias("win_rate_windows"),
        # Stability score: avg_ROI penalized by variance and worst case
        (pl.col("avg_ROI") - pl.col("std_ROI") * 0.5
         + pl.col("min_ROI") * 0.3).alias("stability_score"),
    )
    .sort("stability_score", descending=True)
)

stability.write_csv("notebooks/sweep_s1_robust_stable.csv")

print("=" * 120)
print("TOP 30 MOST STABLE CONFIGS (across walk-forward windows)")
print("=" * 120)
_display = stability.select(
    *config_cols, "n_windows", "pos_windows",
    pl.col("avg_HR").round(3), pl.col("min_HR").round(3),
    pl.col("avg_ROI").round(1), pl.col("min_ROI").round(1), pl.col("std_ROI").round(1),
    pl.col("total_PnL").round(0), pl.col("avg_sharpe").round(1), pl.col("min_sharpe").round(1),
    pl.col("worst_dd").round(0), pl.col("avg_pf").round(1),
    pl.col("avg_pct_pos_wk").round(2),
    pl.col("avg_pool").round(0), pl.col("avg_hhi").round(3),
    pl.col("stability_score").round(1),
).head(30)
print(_display.to_pandas().to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════
#  BOOTSTRAP CI ON TOP CONFIGS
# ═══════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 120)
print("BOOTSTRAP 95% CI ON TOP 10 STABLE CONFIGS (pooled across all windows)")
print("=" * 120)

top_configs = stability.head(10)
boot_results = []

for row in top_configs.iter_rows(named=True):
    # Get all holdout signals for this config across all windows
    mask = res_df
    for c in config_cols:
        mask = mask.filter(pl.col(c) == row[c])

    # Reconstruct pooled holdout data
    all_pnls = []
    for w_row in mask.iter_rows(named=True):
        w_label = w_row["window"]
        # Find matching window
        for train_end, holdout_end, label in WINDOWS:
            if label != w_label:
                continue
            train_data = clean.filter(
                (pl.col("resolved_at") >= ANCHOR) & (pl.col("resolved_at") < train_end)
            )
            holdout_data_w = clean.filter(
                (pl.col("resolved_at") >= train_end) & (pl.col("resolved_at") < holdout_end)
            )
            ts = train_data.group_by("trader").agg(
                pl.col("correct").mean().alias("hr"),
                pl.len().alias("n_markets"),
                pl.col("month").n_unique().alias("active_months"),
            )
            _mo = (train_data.group_by(["trader", "month"]).agg(
                pl.col("correct").mean().alias("m_hr"), pl.len().alias("m_n"),
            ).filter(pl.col("m_n") >= MONTHLY_MIN_BETS))
            _gm = _mo.filter(pl.col("m_hr") >= MONTHLY_HR).group_by("trader").agg(
                pl.len().alias("good_months"))
            ts = ts.join(_gm, on="trader", how="left").with_columns(
                pl.col("good_months").fill_null(0))

            pool = ts.filter(
                (pl.col("hr") >= row["min_hr"])
                & (pl.col("n_markets") >= row["min_mkts"])
                & (pl.col("good_months") >= row["min_mos"])
            )
            pool_set = set(pool["trader"].to_list())
            h = holdout_data_w.filter(pl.col("trader").is_in(list(pool_set)))
            if row["direction"] != "ALL":
                h = h.filter(pl.col("position") == row["direction"])
            h = h.filter(
                (pl.col("dir_entry") >= row["entry_lo"])
                & (pl.col("dir_entry") <= row["entry_hi"])
            )
            if h.height > 0:
                h = compute_pnl(h)
                all_pnls.extend(h["pnl"].to_list())

    pnls_arr = np.array(all_pnls)
    ci = bootstrap_ci(pnls_arr)

    boot_results.append({
        "min_hr": row["min_hr"], "min_mkts": row["min_mkts"],
        "min_mos": row["min_mos"], "direction": row["direction"],
        "entry": f"{row['entry_lo']:.1f}-{row['entry_hi']:.1f}",
        "N": len(pnls_arr),
        "HR": round(float((pnls_arr > 0).mean()), 3),
        "ROI%": round(float(pnls_arr.mean() / BET * 100), 1),
        "ROI_95CI": f"[{ci['roi_lo']}, {ci['roi_hi']}]",
        "HR_95CI": f"[{ci['hr_lo']}, {ci['hr_hi']}]",
        "avg_sharpe": round(row["avg_sharpe"], 1),
        "worst_dd": round(row["worst_dd"], 0),
        "win_windows": f"{row['pos_windows']}/{row['n_windows']}",
    })

boot_df = pl.DataFrame(boot_results)
boot_df.write_csv("notebooks/sweep_s1_robust_best.csv")
print(boot_df.to_pandas().to_string(index=False))


# ═══════════════════════════════════════════════════════════════════════
#  PER-WINDOW BREAKDOWN FOR USER'S CONFIG (HR>=0.80, mkts>=30, mos>=6)
# ═══════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 120)
print("YOUR CONFIG (HR>=0.80, mkts>=30, mos>=6, ALL, entry 0-1) — PER WINDOW")
print("=" * 120)
user_cfg = res_df.filter(
    (pl.col("min_hr") == 0.80)
    & (pl.col("min_mkts") == 30)
    & (pl.col("min_mos") == 6)
    & (pl.col("direction") == "ALL")
    & (pl.col("entry_lo") == 0.0)
    & (pl.col("entry_hi") == 1.0)
).sort("window")
if user_cfg.height > 0:
    print(user_cfg.select(
        "window", "pool", "signals", "HR", "ROI%", "PnL",
        "edge", "sharpe", "max_dd", "profit_factor",
        "pct_pos_weeks", "top3_pct", "herfindahl", "n_active",
    ).to_pandas().to_string(index=False))
else:
    print("  (not enough data for this config)")


# ═══════════════════════════════════════════════════════════════════════
#  DIRECTION × ENTRY BAND HEATMAP DATA
# ═══════════════════════════════════════════════════════════════════════
print("\n")
print("=" * 120)
print("DIRECTION × ENTRY BAND — AVG ROI% (HR>=0.75, mkts>=30, mos>=6)")
print("=" * 120)
heatmap = (
    stability.filter(
        (pl.col("min_hr") == 0.75)
        & (pl.col("min_mkts") == 30)
        & (pl.col("min_mos") == 6)
    ).select("direction", "entry_lo", "entry_hi", "avg_ROI", "total_PnL",
             "avg_HR", "avg_sharpe", "total_signals")
    .sort(["direction", "entry_lo"])
)
if heatmap.height > 0:
    print(heatmap.to_pandas().to_string(index=False))

print(f"\nDone. All results in notebooks/sweep_s1_robust_*.csv")
