"""Analyze Politics Composite validation — reads ledger parquet and computes all metrics.

The backtest already ran and saved ledger to research/output/ledger_politics_composite_k100_n5.parquet.
This script reads the ledger and computes excess HR, direction decomposition, hold time, etc.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/analyze_politics.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/analyze_politics.log")
LOG_PATH.parent.mkdir(exist_ok=True)

with open(LOG_PATH, "w") as f:
    pass


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


import polars as pl

log("=" * 70)
log("Politics Composite K=100 N=5 — Ledger Analysis")
log("=" * 70)

LEDGER_PATH = Path("/mnt/nvme/git/polymarket/polymarket/research/output/ledger_politics_composite_k100_n5.parquet")

if not LEDGER_PATH.exists():
    log(f"ERROR: Ledger not found at {LEDGER_PATH}")
    sys.exit(1)

df = pl.read_parquet(LEDGER_PATH)
log(f"\nLedger loaded: {len(df)} records")
log(f"Columns: {df.columns}")

# Show schema
log("\n--- Schema ---")
for col in df.columns:
    dtype = df[col].dtype
    nulls = df[col].null_count()
    sample = df[col].drop_nulls().head(2).to_list()
    log(f"  {col:<30} {str(dtype):<20} nulls={nulls}  sample={sample}")

# ── Settlement status ──────────────────────────────────────────────────────────
log("\n--- Settlement Status ---")

# Find the resolved_at or settled indicator
if "resolved_at" in df.columns:
    settled = df.filter(pl.col("resolved_at").is_not_null())
    unsettled = df.filter(pl.col("resolved_at").is_null())
    log(f"  Settled:   {len(settled)}")
    log(f"  Unsettled: {len(unsettled)}")
elif "pnl_net" in df.columns:
    settled = df.filter(pl.col("pnl_net").is_not_null())
    unsettled = df.filter(pl.col("pnl_net").is_null())
    log(f"  With PnL:    {len(settled)}")
    log(f"  Without PnL: {len(unsettled)}")
else:
    settled = df
    log(f"  All records: {len(settled)} (no settlement column found)")

if len(settled) == 0:
    log("ERROR: No settled records found")
    sys.exit(1)

# ── Core metrics ───────────────────────────────────────────────────────────────
log("\n--- Core Metrics ---")

# Identify correct/win column
correct_col = None
for candidate in ["correct", "won", "win", "is_correct"]:
    if candidate in settled.columns:
        correct_col = candidate
        break

pnl_col = None
for candidate in ["pnl_net", "pnl", "net_pnl", "realized_pnl"]:
    if candidate in settled.columns:
        pnl_col = candidate
        break

log(f"  Correct column: {correct_col}")
log(f"  PnL column:     {pnl_col}")

if correct_col:
    overall_hr = settled[correct_col].cast(pl.Float64).mean()
    log(f"  Overall hit rate: {overall_hr:.3f} ({overall_hr:.1%})")
    log(f"  Total fills:      {len(settled)}")

if pnl_col:
    total_pnl = settled[pnl_col].cast(pl.Float64).sum()
    avg_pnl = settled[pnl_col].cast(pl.Float64).mean()
    log(f"  Total PnL:        ${total_pnl:,.2f}")
    log(f"  Avg PnL/fill:     ${avg_pnl:.2f}")

# ── Direction decomposition ────────────────────────────────────────────────────
log("\n--- Direction Decomposition ---")

# Find outcome/direction column
outcome_col = None
for candidate in ["outcome", "direction", "side", "signal_outcome"]:
    if candidate in settled.columns:
        outcome_col = candidate
        break
log(f"  Outcome column: {outcome_col}")

if outcome_col:
    vc = settled[outcome_col].value_counts().sort("outcome" if "outcome" in settled.columns else outcome_col)
    log(f"  Value counts: {vc.to_dicts()}")

    for direction in ["YES", "NO"]:
        subset = settled.filter(pl.col(outcome_col) == direction)
        if len(subset) > 0:
            hr = subset[correct_col].cast(pl.Float64).mean() if correct_col else None
            pnl = subset[pnl_col].cast(pl.Float64).sum() if pnl_col else None
            avg_p = subset[pnl_col].cast(pl.Float64).mean() if pnl_col else None

            # Entry price
            fill_price_col = None
            for c in ["fill_price", "entry_price", "price"]:
                if c in subset.columns:
                    fill_price_col = c
                    break

            avg_price = subset[fill_price_col].cast(pl.Float64).mean() if fill_price_col else None

            log(f"\n  {direction} direction:")
            log(f"    n={len(subset)}")
            if hr is not None:
                log(f"    HR={hr:.3f} ({hr:.1%})")
            if pnl is not None:
                log(f"    Total PnL=${pnl:,.2f}")
                log(f"    Avg PnL/fill=${avg_p:.2f}")
            if avg_price is not None:
                log(f"    Avg fill price={avg_price:.3f}")
        else:
            log(f"\n  {direction}: 0 records")

# ── Hold time distribution ─────────────────────────────────────────────────────
log("\n--- Hold Time Distribution ---")

hold_col = None
for candidate in ["hold_duration_s", "hold_s", "hold_seconds", "duration_s"]:
    if candidate in settled.columns:
        hold_col = candidate
        break

if hold_col:
    hold_hours = settled[hold_col].cast(pl.Float64) / 3600
    log(f"  Column: {hold_col}")
    log(f"  Min:  {hold_hours.min():.1f}h")
    log(f"  P25:  {hold_hours.quantile(0.25):.1f}h")
    log(f"  P50:  {hold_hours.quantile(0.50):.1f}h")
    log(f"  P75:  {hold_hours.quantile(0.75):.1f}h")
    log(f"  Max:  {hold_hours.max():.1f}h")
    log(f"  Avg:  {hold_hours.mean():.1f}h")
    log(f"  <4h:  {(hold_hours < 4).sum()} ({(hold_hours < 4).mean():.1%})")
    log(f"  <24h: {(hold_hours < 24).sum()} ({(hold_hours < 24).mean():.1%})")
    log(f"  >72h: {(hold_hours > 72).sum()} ({(hold_hours > 72).mean():.1%})")
else:
    log("  No hold time column found")
    # Check for timestamps
    for c in settled.columns:
        if "time" in c.lower() or "at" in c.lower():
            log(f"  Timestamp column found: {c} (type={settled[c].dtype})")

# ── Fill price distribution ────────────────────────────────────────────────────
log("\n--- Fill Price Distribution ---")
fill_price_col = None
for c in ["fill_price", "entry_price", "price"]:
    if c in settled.columns:
        fill_price_col = c
        break

if fill_price_col:
    prices = settled[fill_price_col].cast(pl.Float64)
    log(f"  P10: {prices.quantile(0.10):.3f}")
    log(f"  P25: {prices.quantile(0.25):.3f}")
    log(f"  P50: {prices.quantile(0.50):.3f}")
    log(f"  P75: {prices.quantile(0.75):.3f}")
    log(f"  P90: {prices.quantile(0.90):.3f}")
    # Flag high-price entries (>0.90) — near-resolved markets
    high_price = (prices > 0.90).sum()
    log(f"  >0.90 price: {high_price} ({high_price/len(settled):.1%}) -- near-resolved risk")
    # Low-price entries (<0.10) -- underdogs
    low_price = (prices < 0.10).sum()
    log(f"  <0.10 price: {low_price} ({low_price/len(settled):.1%}) -- underdog entries")

# ── Capital utilization ────────────────────────────────────────────────────────
log("\n--- Capital Utilization ---")

# Look for open position count tracking
if "signal_time" in settled.columns and hold_col:
    # Estimate max concurrent positions
    # Each fill is open from signal_time to signal_time + hold_duration_s
    signals = settled.select([
        pl.col("signal_time"),
        (pl.col("signal_time") + pl.col(hold_col)).alias("close_time")
    ]).sort("signal_time")

    # Count overlapping fills at each open time
    max_concurrent = 0
    opens = signals["signal_time"].to_list()
    closes = signals["close_time"].to_list()

    import heapq
    close_heap = []
    concurrent = 0
    for open_t in opens:
        while close_heap and close_heap[0] <= open_t:
            heapq.heappop(close_heap)
            concurrent -= 1
        heapq.heappush(close_heap, closes[len(close_heap) + concurrent if False else concurrent])
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)

    log(f"  Max concurrent positions (estimated): {max_concurrent}")
else:
    log(f"  Could not compute concurrent positions (missing signal_time or hold_col)")

# Total capital deployed
if pnl_col and fill_price_col:
    total_spent = (settled[fill_price_col].cast(pl.Float64) * 100.0).sum()  # $100/fill
    log(f"  Total capital deployed: ${total_spent:,.0f} (across all fills, $100 each)")
    log(f"  At $50k capital: avg utilization = {len(settled)*100/50000:.1%} (sequential)")

# ── DuckDB base rate ───────────────────────────────────────────────────────────
log("\n--- Politics Base Rate (Test Period) ---")
from research.db import db as get_db

con = get_db().con
TEST_START = "2025-07-01"

# Ensure shared tables exist
con.execute("""
CREATE OR REPLACE MACRO is_gambling_market(slug) AS (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    OR (
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (
            lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
            OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
            OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
            OR lower(slug) LIKE '%-close-%'
            OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%nvda%'
            OR lower(slug) LIKE '%aapl%' OR lower(slug) LIKE '%amzn%'
        )
    )
);
""")
con.execute("""
CREATE OR REPLACE TABLE _bp_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag,
    first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
""")

base_rows = con.execute(f"""
SELECT
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    count(DISTINCT p.condition_id) AS n_markets_test,
    count(*) AS n_positions
FROM maker_positions_resolved_corrected p
JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
WHERE mt.primary_tag = 'Politics'
  AND p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
""").fetchone()

yes_base_rate = base_rows[0]
n_markets_test = base_rows[1]
n_positions = base_rows[2]
log(f"  Politics test-period YES base rate: {yes_base_rate:.3f} ({yes_base_rate:.1%})")
log(f"  Test period markets: {n_markets_test}")
log(f"  Test period positions: {n_positions}")

# ── Excess HR & Compounding Score ─────────────────────────────────────────────
log("\n--- Excess HR & Compounding Score ---")

if correct_col and pnl_col and hold_col:
    overall_hr = settled[correct_col].cast(pl.Float64).mean()
    excess_hr = overall_hr - yes_base_rate
    total_pnl = settled[pnl_col].cast(pl.Float64).sum()
    avg_pnl = settled[pnl_col].cast(pl.Float64).mean()
    avg_hold_hours = settled[hold_col].cast(pl.Float64).mean() / 3600
    avg_hold_days = avg_hold_hours / 24

    log(f"  Strategy HR:      {overall_hr:.3f} ({overall_hr:.1%})")
    log(f"  YES base rate:    {yes_base_rate:.3f} ({yes_base_rate:.1%})")
    log(f"  Excess HR:        {excess_hr:+.3f} ({excess_hr:+.1%})")
    log(f"  Avg PnL/fill:     ${avg_pnl:.2f}")
    log(f"  Avg hold:         {avg_hold_hours:.1f}h ({avg_hold_days:.2f} days)")

    if avg_hold_days > 0 and excess_hr > 0:
        cs = excess_hr * abs(avg_pnl) / avg_hold_days
        log(f"  Compounding score: {cs:.4f}")
        log(f"    = {excess_hr:.3f} x {abs(avg_pnl):.2f} / {avg_hold_days:.2f}")

    # Vectorized UB was +62.5pp excess HR — compute degradation
    vectorized_ub_excess = 0.625
    degradation = vectorized_ub_excess - excess_hr
    log(f"\n  Vectorized UB excess HR: +{vectorized_ub_excess:.1%}")
    log(f"  Tick excess HR:          {excess_hr:+.1%}")
    log(f"  Degradation:             {degradation:.1%}")

    # Direction-specific excess HR
    if outcome_col:
        for direction, base_rate in [("YES", yes_base_rate), ("NO", 1 - yes_base_rate)]:
            subset = settled.filter(pl.col(outcome_col) == direction)
            if len(subset) > 0:
                dir_hr = subset[correct_col].cast(pl.Float64).mean()
                dir_excess = dir_hr - base_rate
                log(f"\n  {direction} excess HR: {dir_hr:.1%} - {base_rate:.1%} = {dir_excess:+.1%}  (n={len(subset)})")

log("\n" + "=" * 70)
log("ANALYSIS COMPLETE")
log("=" * 70)
