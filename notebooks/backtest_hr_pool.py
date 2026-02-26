"""
HR Pool Strategy — Walk-Forward Backtest from Nov 2025
======================================================
Monthly retrain: rebuild pool on all data up to month start,
trade during that month. Simulates live deployment.

Tests multiple config variants to find robust parameters.
"""
import polars as pl
import clickhouse_connect
import numpy as np
import time
from datetime import datetime, timedelta
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════
CH_HOST = "192.168.0.148"
ANCHOR = datetime(2024, 1, 1)
BET = 100.0

# Walk-forward: monthly retrain from Nov 2025
WF_MONTHS = [
    (datetime(2025, 11, 1), datetime(2025, 12, 1), "2025-11"),
    (datetime(2025, 12, 1), datetime(2026, 1, 1),  "2025-12"),
    (datetime(2026, 1, 1),  datetime(2026, 2, 1),  "2026-01"),
    (datetime(2026, 2, 1),  datetime(2026, 3, 1),  "2026-02"),
]

# Strategy variants to test
@dataclass(frozen=True)
class Variant:
    name: str
    min_hr: float
    min_markets: int
    min_good_months: int
    direction: str
    max_entry: float

VARIANTS = [
    # Robust configs from sweep
    Variant("robust_NO_050",     0.65, 30, 6, "NO",  0.50),
    Variant("robust_NO_070",     0.65, 30, 6, "NO",  0.70),
    Variant("sharp_NO_050",      0.75, 30, 6, "NO",  0.50),
    Variant("sharp_NO_070",      0.75, 30, 6, "NO",  0.70),
    Variant("tight_NO_050",      0.80, 30, 6, "NO",  0.50),
    # User's original config for comparison
    Variant("user_ALL_100",      0.80, 30, 6, "ALL", 1.00),
    # ALL direction variants
    Variant("robust_ALL_050",    0.65, 30, 6, "ALL", 0.50),
    Variant("robust_ALL_070",    0.65, 30, 6, "ALL", 0.70),
    # Mid-range blend
    Variant("blend_NO_070",      0.70, 30, 6, "NO",  0.70),
    Variant("blend_ALL_050",     0.70, 30, 6, "ALL", 0.50),
]

# Classification (fixed)
MAX_CLASS_ENTRY = 0.90
MAX_POSITIONS = 5000
MIN_POSITIONS = 3
MONTHLY_HR = 0.50
MONTHLY_MIN_BETS = 3


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
    print("ERROR: _tmp_s1_enriched not found. Run s1_build.py first.")
    raise SystemExit(1)

df = pl.from_arrow(arrow)
if "resolved_at" in df.columns:
    dtype = df.schema["resolved_at"]
    if isinstance(dtype, pl.Datetime) and dtype.time_zone:
        df = df.with_columns(pl.col("resolved_at").dt.replace_time_zone(None))
    elif dtype in (pl.UInt32, pl.Int64, pl.UInt64):
        df = df.with_columns(pl.from_epoch(pl.col("resolved_at"), time_unit="s").alias("resolved_at"))
if df.schema.get("correct") not in (pl.Boolean,):
    df = df.with_columns(pl.col("correct").cast(pl.Boolean))

# Pre-classify
directional = df.filter(pl.col("position").is_in(["YES", "NO"]))
s1 = directional.filter(pl.col("dir_entry") <= MAX_CLASS_ENTRY)
counts = s1.group_by("trader").agg(pl.len().alias("n"))
bots = set(counts.filter(pl.col("n") > MAX_POSITIONS)["trader"].to_list())
oneoffs = set(counts.filter(pl.col("n") < MIN_POSITIONS)["trader"].to_list())
clean = s1.filter(~pl.col("trader").is_in(list(bots)) & ~pl.col("trader").is_in(list(oneoffs)))

print(f"Loaded {df.height:,} rows ({src}) in {time.time()-t0:.1f}s")
print(f"Clean: {clean.height:,} positions, {clean['trader'].n_unique():,} traders\n")


# ═══════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════
def build_pool(data: pl.DataFrame, min_hr: float, min_mkts: int, min_mos: int) -> set[str]:
    """Build trader pool from training data."""
    stats = data.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        pl.col("month").n_unique().alias("active_months"),
    )
    monthly = (
        data.group_by(["trader", "month"]).agg(
            pl.col("correct").mean().alias("m_hr"), pl.len().alias("m_n"),
        ).filter(pl.col("m_n") >= MONTHLY_MIN_BETS)
    )
    good = (
        monthly.filter(pl.col("m_hr") >= MONTHLY_HR)
        .group_by("trader").agg(pl.len().alias("good_months"))
    )
    stats = stats.join(good, on="trader", how="left").with_columns(
        pl.col("good_months").fill_null(0)
    )
    pool = stats.filter(
        (pl.col("hr") >= min_hr)
        & (pl.col("n_markets") >= min_mkts)
        & (pl.col("good_months") >= min_mos)
    )
    return set(pool["trader"].to_list())


def simulate_month(
    holdout: pl.DataFrame, pool: set[str], direction: str, max_entry: float,
) -> pl.DataFrame:
    """Simulate trading for a month: filter, compute PnL."""
    h = holdout.filter(pl.col("trader").is_in(list(pool)))
    if direction != "ALL":
        h = h.filter(pl.col("position") == direction)
    h = h.filter(pl.col("dir_entry") <= max_entry)
    if h.height == 0:
        return h.with_columns(pl.lit(0.0).alias("pnl"))
    return h.with_columns(
        pl.when(pl.col("correct"))
        .then(pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry"))
        .otherwise(-pl.lit(BET))
        .alias("pnl")
    )


def weekly_stats(h: pl.DataFrame) -> dict:
    """Compute weekly Sharpe and drawdown."""
    if h.height < 5:
        return {"sharpe": 0.0, "max_dd": 0.0, "pct_pos_wk": 0.0, "n_weeks": 0}
    w = (
        h.with_columns(pl.col("resolved_at").dt.truncate("1w").alias("week"))
        .group_by("week").agg(pl.col("pnl").sum().alias("w_pnl"))
        .sort("week")
    )
    pnls = w["w_pnl"].to_numpy()
    n_wk = len(pnls)
    if n_wk < 2:
        return {"sharpe": 0.0, "max_dd": 0.0, "pct_pos_wk": float((pnls > 0).mean()), "n_weeks": n_wk}
    mu, sigma = pnls.mean(), pnls.std(ddof=1)
    sharpe = mu / sigma * np.sqrt(52) if sigma > 0 else 0
    cum = np.cumsum(pnls)
    dd = np.maximum.accumulate(cum) - cum
    return {
        "sharpe": round(float(sharpe), 2),
        "max_dd": round(float(dd.max()), 1),
        "pct_pos_wk": round(float((pnls > 0).mean()), 2),
        "n_weeks": n_wk,
    }


# ═══════════════════════════════════════════════════════════════════════
#  WALK-FORWARD SIMULATION
# ═══════════════════════════════════════════════════════════════════════
all_rows = []
all_trades = []  # for equity curve

for v in VARIANTS:
    cumulative_pnl = 0.0
    month_results = []

    for train_end, holdout_end, m_label in WF_MONTHS:
        # Build pool on all data up to month start
        train = clean.filter(
            (pl.col("resolved_at") >= ANCHOR) & (pl.col("resolved_at") < train_end)
        )
        pool = build_pool(train, v.min_hr, v.min_markets, v.min_good_months)

        # Trade during the month
        holdout = clean.filter(
            (pl.col("resolved_at") >= train_end) & (pl.col("resolved_at") < holdout_end)
        )
        h = simulate_month(holdout, pool, v.direction, v.max_entry)

        n = h.height
        if n == 0:
            month_results.append({
                "variant": v.name, "month": m_label, "pool": len(pool),
                "signals": 0, "HR": 0, "ROI%": 0, "PnL": 0,
                "cum_PnL": cumulative_pnl, **{k: 0 for k in ["sharpe", "max_dd", "pct_pos_wk", "n_weeks"]},
            })
            continue

        hr = float(h["correct"].mean())
        pnl = float(h["pnl"].sum())
        roi = pnl / (n * BET) * 100
        cumulative_pnl += pnl
        ws = weekly_stats(h)

        month_results.append({
            "variant": v.name, "month": m_label, "pool": len(pool),
            "signals": n, "HR": round(hr, 4), "ROI%": round(roi, 2),
            "PnL": round(pnl, 0), "cum_PnL": round(cumulative_pnl, 0),
            **ws,
        })

        # Collect trades for equity curve
        for row in h.select("resolved_at", "pnl", "correct", "dir_entry", "position").iter_rows(named=True):
            all_trades.append({"variant": v.name, "month": m_label, **row})

    all_rows.extend(month_results)

results = pl.DataFrame(all_rows)

# ═══════════════════════════════════════════════════════════════════════
#  RESULTS
# ═══════════════════════════════════════════════════════════════════════
print("=" * 130)
print("WALK-FORWARD: MONTHLY RETRAIN FROM NOV 2025")
print("=" * 130)

for v in VARIANTS:
    vr = results.filter(pl.col("variant") == v.name)
    print(f"\n--- {v.name} (HR>={v.min_hr}, {v.direction}, entry<={v.max_entry}) ---")
    print(vr.select(
        "month", "pool", "signals",
        pl.col("HR").round(3), pl.col("ROI%"), pl.col("PnL"),
        "cum_PnL", "sharpe", "max_dd", "pct_pos_wk",
    ).to_pandas().to_string(index=False))

# ═══════════════════════════════════════════════════════════════════════
#  SUMMARY TABLE
# ═══════════════════════════════════════════════════════════════════════
print("\n\n")
print("=" * 130)
print("SUMMARY: ALL VARIANTS (Nov 2025 → Feb 2026)")
print("=" * 130)

summary = (
    results.group_by("variant").agg(
        pl.col("signals").sum().alias("total_sigs"),
        pl.col("PnL").sum().alias("total_PnL"),
        pl.col("HR").filter(pl.col("signals") > 0).mean().alias("avg_HR"),
        pl.col("ROI%").filter(pl.col("signals") > 0).mean().alias("avg_ROI"),
        (pl.col("PnL") > 0).sum().alias("pos_months"),
        pl.len().alias("n_months"),
        pl.col("sharpe").filter(pl.col("signals") > 0).mean().alias("avg_sharpe"),
        pl.col("max_dd").max().alias("worst_dd"),
        pl.col("pool").mean().alias("avg_pool"),
    )
    .with_columns(
        (pl.col("pos_months").cast(pl.Utf8) + pl.lit("/") + pl.col("n_months").cast(pl.Utf8)).alias("win_months"),
        (pl.col("total_PnL") / (pl.col("total_sigs") * BET) * 100).alias("total_ROI%"),
    )
    .sort("total_PnL", descending=True)
)

print(summary.select(
    "variant", "total_sigs",
    pl.col("avg_HR").round(3), pl.col("avg_ROI").round(1), pl.col("total_ROI%").round(1),
    pl.col("total_PnL").round(0), "win_months",
    pl.col("avg_sharpe").round(1), pl.col("worst_dd").round(0),
    pl.col("avg_pool").round(0),
).to_pandas().to_string(index=False))

# ═══════════════════════════════════════════════════════════════════════
#  WEEKLY EQUITY CURVE (best variant)
# ═══════════════════════════════════════════════════════════════════════
if all_trades:
    best_variant = summary.row(0, named=True)["variant"]
    trades_df = pl.DataFrame(all_trades).filter(pl.col("variant") == best_variant)

    if trades_df.height > 0:
        weekly = (
            trades_df
            .with_columns(pl.col("resolved_at").dt.truncate("1w").alias("week"))
            .group_by("week").agg(
                pl.col("pnl").sum().alias("PnL"),
                pl.len().alias("trades"),
                pl.col("correct").mean().alias("HR"),
            ).sort("week")
            .with_columns(
                pl.col("PnL").cum_sum().alias("cum_PnL"),
                (pl.col("PnL") / (pl.col("trades") * BET) * 100).alias("ROI%"),
            )
        )

        print(f"\n\n{'='*100}")
        print(f"WEEKLY EQUITY CURVE: {best_variant}")
        print(f"{'='*100}")
        print(weekly.select(
            pl.col("week").dt.strftime("%Y-%m-%d"),
            "trades", pl.col("HR").round(3), pl.col("ROI%").round(1),
            pl.col("PnL").round(0), pl.col("cum_PnL").round(0),
        ).to_pandas().to_string(index=False))

        # Drawdown analysis
        cum = weekly["cum_PnL"].to_numpy()
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        print(f"\nMax drawdown: ${dd.max():,.0f}")
        print(f"Peak: ${cum.max():,.0f}")
        print(f"Final: ${cum[-1]:,.0f}")
        print(f"Positive weeks: {(weekly['PnL'].to_numpy() > 0).sum()}/{len(weekly)}")

# ═══════════════════════════════════════════════════════════════════════
#  POOL TURNOVER
# ═══════════════════════════════════════════════════════════════════════
print(f"\n\n{'='*100}")
print("POOL TURNOVER (robust_NO_050)")
print(f"{'='*100}")
prev_pool: set[str] | None = None
for train_end, _, m_label in WF_MONTHS:
    train = clean.filter(
        (pl.col("resolved_at") >= ANCHOR) & (pl.col("resolved_at") < train_end)
    )
    pool = build_pool(train, 0.65, 30, 6)
    if prev_pool is not None:
        added = pool - prev_pool
        removed = prev_pool - pool
        retained = pool & prev_pool
        print(f"  {m_label}: {len(pool)} traders | "
              f"+{len(added)} new, -{len(removed)} dropped, {len(retained)} retained "
              f"({len(retained)/max(len(prev_pool),1):.0%} stable)")
    else:
        print(f"  {m_label}: {len(pool)} traders (initial)")
    prev_pool = pool

print("\nDone.")
