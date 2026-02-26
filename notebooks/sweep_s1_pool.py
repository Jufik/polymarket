"""
S1 Pool Explorer — Parameter Sweep
Replicates the notebook logic offline to sweep across parameter combinations.
Outputs a table of (config → pool_size, holdout_HR, ROI%, $/signal, baseline_HR).
"""
import polars as pl
import clickhouse_connect
import itertools
import time
from datetime import datetime

# ── Connect ──────────────────────────────────────────────────────────
CH_HOST = "192.168.0.148"
ch = clickhouse_connect.get_client(host=CH_HOST, port=18123, database="polymarket")

# ── Load data (prefer enriched table) ────────────────────────────────
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
        SELECT
            p.trader,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
                WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
                ELSE 'CLOSED'
            END AS position,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN p.wavg_yes_price
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - p.wavg_yes_price
                WHEN p.net_yes >= p.net_no                  THEN p.wavg_yes_price
                ELSE 1.0 - p.wavg_yes_price
            END AS dir_entry,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN r.yes_won
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT r.yes_won
                WHEN p.net_yes >= p.net_no                  THEN r.yes_won
                ELSE NOT r.yes_won
            END AS correct,
            p.volume AS market_volume,
            toUInt32(p.n_trades) AS trade_count,
            r.resolved_at,
            formatDateTime(r.resolved_at, '%Y-%m') AS month
        FROM _tmp_s1_positions p
        JOIN (
            SELECT m.condition_id, m.resolved_at,
                   coalesce(t.yes_won, false) AS yes_won
            FROM markets m
            LEFT JOIN (
                SELECT condition_id, true AS yes_won
                FROM token_market_map WHERE outcome = 'YES' AND winner = true
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
        df = df.with_columns(
            pl.from_epoch(pl.col("resolved_at"), time_unit="s").alias("resolved_at")
        )
if df.schema.get("correct") not in (pl.Boolean,):
    df = df.with_columns(pl.col("correct").cast(pl.Boolean))

print(f"Loaded {df.height:,} positions from {src} in {time.time()-t0:.1f}s")
print(f"  {df['trader'].n_unique():,} traders, resolved "
      f"{df['resolved_at'].min():%Y-%m-%d} → {df['resolved_at'].max():%Y-%m-%d}\n")


# ── Sweep parameters ─────────────────────────────────────────────────
# Classification (fixed — your validated config)
MAX_ENTRY = 0.90
MAX_POSITIONS = 5000
MIN_POSITIONS = 3
MIN_TRADES = 1
MIN_VOLUME = 0

# Pre-filter once (classification is fixed across sweep)
directional = df.filter(pl.col("position").is_in(["YES", "NO"]))
s1 = directional.filter(pl.col("dir_entry") <= MAX_ENTRY)
s1 = s1.filter(pl.col("market_volume") >= MIN_VOLUME)
s1 = s1.filter(pl.col("trade_count") >= MIN_TRADES)
counts = s1.group_by("trader").agg(pl.len().alias("n"))
bots = set(counts.filter(pl.col("n") > MAX_POSITIONS)["trader"].to_list())
oneoffs = set(counts.filter(pl.col("n") < MIN_POSITIONS)["trader"].to_list())
clean = s1.filter(
    ~pl.col("trader").is_in(list(bots)) & ~pl.col("trader").is_in(list(oneoffs))
)
print(f"After classification: {clean.height:,} positions, "
      f"{clean['trader'].n_unique():,} traders\n")

# Sweep axes
SWEEP = {
    "min_hr":       [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85],
    "min_markets":  [10, 20, 30, 50],
    "min_months":   [3, 6, 9],
    "direction":    ["ALL", "NO", "YES"],
}

# Fixed
TRAIN_START = datetime(2024, 10, 1)
TRAIN_END = datetime(2025, 11, 1)
HOLDOUT_END = datetime(2026, 3, 1)
BET = 100.0
MONTHLY_HR = 0.50
MONTHLY_MIN_BETS = 3
MAX_YES_FRAC = 1.0
MIN_MED_ENTRY = 0.0
MAX_MED_ENTRY = 1.0

holdout_start = TRAIN_END

# Pre-compute date-filtered subsets
train_data = clean.filter(
    (pl.col("resolved_at") >= TRAIN_START) & (pl.col("resolved_at") < TRAIN_END)
)
holdout_data = clean.filter(
    (pl.col("resolved_at") >= holdout_start) & (pl.col("resolved_at") < HOLDOUT_END)
)

# Pre-compute per-trader stats on training data
trader_stats = train_data.group_by("trader").agg(
    pl.col("correct").mean().alias("hr"),
    pl.len().alias("n_markets"),
    (pl.col("position") == "YES").mean().alias("yes_frac"),
    pl.col("dir_entry").median().alias("median_entry"),
    pl.col("month").n_unique().alias("active_months"),
)

# Monthly good-months
monthly = (
    train_data.group_by(["trader", "month"]).agg(
        pl.col("correct").mean().alias("m_hr"), pl.len().alias("m_n"),
    ).filter(pl.col("m_n") >= MONTHLY_MIN_BETS)
)
good_months = (
    monthly.filter(pl.col("m_hr") >= MONTHLY_HR)
    .group_by("trader").agg(pl.len().alias("good_months"))
)
trader_stats = trader_stats.join(good_months, on="trader", how="left").with_columns(
    pl.col("good_months").fill_null(0)
)

# Baseline for each direction
baseline_cache = {}
for d in ["ALL", "NO", "YES"]:
    b = holdout_data
    if d != "ALL":
        b = b.filter(pl.col("position") == d)
    if b.height > 0:
        b = b.with_columns(
            pl.when(pl.col("correct"))
            .then(pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry"))
            .otherwise(-pl.lit(BET))
            .alias("pnl")
        )
        baseline_cache[d] = {
            "hr": float(b["correct"].mean()),
            "n": b.height,
            "roi": float(b["pnl"].sum()) / (b.height * BET) * 100,
        }
    else:
        baseline_cache[d] = {"hr": 0, "n": 0, "roi": 0}


# ── Run sweep ─────────────────────────────────────────────────────────
results = []
combos = list(itertools.product(
    SWEEP["min_hr"], SWEEP["min_markets"], SWEEP["min_months"], SWEEP["direction"]
))
print(f"Sweeping {len(combos)} combinations...")
t_sweep = time.time()

for min_hr, min_mkts, min_mos, direction in combos:
    pool = trader_stats.filter(
        (pl.col("hr") >= min_hr)
        & (pl.col("n_markets") >= min_mkts)
        & (pl.col("good_months") >= min_mos)
        & (pl.col("yes_frac") <= MAX_YES_FRAC)
        & (pl.col("median_entry") >= MIN_MED_ENTRY)
        & (pl.col("median_entry") <= MAX_MED_ENTRY)
    )
    pool_size = pool.height
    if pool_size < 3:
        continue

    pool_set = set(pool["trader"].to_list())
    h = holdout_data.filter(pl.col("trader").is_in(list(pool_set)))
    if direction != "ALL":
        h = h.filter(pl.col("position") == direction)
    if h.height < 10:
        continue

    h = h.with_columns(
        pl.when(pl.col("correct"))
        .then(pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry"))
        .otherwise(-pl.lit(BET))
        .alias("pnl")
    )

    hr = float(h["correct"].mean())
    total_pnl = float(h["pnl"].sum())
    roi = total_pnl / (h.height * BET) * 100
    per_sig = total_pnl / h.height
    bl = baseline_cache[direction]

    results.append({
        "min_hr": min_hr,
        "min_mkts": min_mkts,
        "min_mos": min_mos,
        "direction": direction,
        "pool": pool_size,
        "signals": h.height,
        "HR": round(hr, 4),
        "ROI%": round(roi, 2),
        "$/sig": round(per_sig, 2),
        "PnL": round(total_pnl, 0),
        "bl_HR": round(bl["hr"], 4),
        "edge": round(hr - bl["hr"], 4),
    })

print(f"Done in {time.time()-t_sweep:.1f}s — {len(results)} valid configs\n")

# ── Display results ───────────────────────────────────────────────────
res_df = pl.DataFrame(results).sort("ROI%", descending=True)

print("=" * 100)
print("TOP 30 BY ROI%")
print("=" * 100)
print(res_df.head(30).to_pandas().to_string(index=False))

print("\n")
print("=" * 100)
print("TOP 30 BY $/signal")
print("=" * 100)
print(res_df.sort("$/sig", descending=True).head(30).to_pandas().to_string(index=False))

print("\n")
print("=" * 100)
print("TOP 30 BY PnL (total)")
print("=" * 100)
print(res_df.sort("PnL", descending=True).head(30).to_pandas().to_string(index=False))

# ── Sensitivity: fix direction=ALL, sweep min_hr ─────────────────────
print("\n")
print("=" * 100)
print("SENSITIVITY: min_hr (direction=ALL, min_mkts=30, min_mos=6)")
print("=" * 100)
sensitivity = res_df.filter(
    (pl.col("direction") == "ALL")
    & (pl.col("min_mkts") == 30)
    & (pl.col("min_mos") == 6)
).sort("min_hr")
print(sensitivity.to_pandas().to_string(index=False))

# ── Direction comparison at user's config ─────────────────────────────
print("\n")
print("=" * 100)
print("DIRECTION COMPARISON (min_hr=0.80, min_mkts=30, min_mos=6)")
print("=" * 100)
dir_comp = res_df.filter(
    (pl.col("min_hr") == 0.80)
    & (pl.col("min_mkts") == 30)
    & (pl.col("min_mos") == 6)
).sort("direction")
print(dir_comp.to_pandas().to_string(index=False))

# Save full results
res_df.write_csv("notebooks/sweep_s1_results.csv")
print(f"\nFull results saved to notebooks/sweep_s1_results.csv")
