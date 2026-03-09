"""Entry Price Bucket Analysis — Identify Truly Skilled Traders.

Key insight: HR alone doesn't capture skill because base rates differ wildly by price level.
A trader entering YES at 0.25 with 40% HR has far more edge than one entering at 0.80 with 90% HR.

This script:
1. Computes market base rates (overall HR) by entry price bucket (20 buckets, width 0.05)
2. Computes per-trader stats by bucket: n_pos, HR, edge over base, avg PnL
3. Ranks traders by edge-weighted composite score (across all buckets, min 10 positions/bucket)
4. Outputs: base rate table, top-50 by edge score, bucket edge distribution, naive HR vs edge HR comparison
"""

from __future__ import annotations

import json
import sys
import time

import polars as pl

# ── DB setup ────────────────────────────────────────────────────────────────

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from research.db import db  # noqa: E402

t0 = time.time()
d = db()
print(f"DB ready in {time.time()-t0:.1f}s\n")

OUT_DIR = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/in-play-traders/discovery"

# ── Step 1: Build working table (YES entries joined with win info) ───────────
# Use maker_positions for correct/yes_won and realized_pnl.
# yes_entry_data has the per-position avg entry price (price_x_vol/volume).
# Filter: resolved markets (yes_won is not null), YES positions only.

print("Building base dataset (yes_entry_data JOIN maker_positions)...")
t1 = time.time()

d.execute("""
CREATE OR REPLACE TABLE _epb_base AS
SELECT
    y.trader,
    y.condition_id,
    CAST(y.price_x_vol / y.volume AS DOUBLE) AS entry_price,
    y.volume                                  AS size_usdc,
    m.correct                                 AS correct,
    m.yes_won                                 AS yes_won,
    m.realized_pnl                            AS realized_pnl,
    -- price bucket: floor to nearest 0.05
    FLOOR(CAST(y.price_x_vol / y.volume AS DOUBLE) / 0.05) * 0.05 AS price_bucket
FROM yes_entry_data y
INNER JOIN maker_positions m
    ON y.trader = m.trader AND y.condition_id = m.condition_id
WHERE y.condition_id != ''
  AND m.position = 'YES'
  AND y.volume > 0
  -- Only include resolved markets where we have an outcome
  AND m.yes_won IS NOT NULL
  -- Exclude trivial endpoint buckets (< 0.01 or >= 1.0)
  AND (y.price_x_vol / y.volume) >= 0.01
  AND (y.price_x_vol / y.volume) <  1.00
""")

cnt = d.con.execute("SELECT count(*) FROM _epb_base").fetchone()[0]
print(f"  Built: {cnt:,} YES entries in {time.time()-t1:.1f}s\n")

# ── Step 2: Base rates by price bucket ──────────────────────────────────────
print("Computing base rates by price bucket...")

base_rates = d.query("""
SELECT
    price_bucket,
    ROUND(price_bucket + 0.025, 3)   AS bucket_midpoint,
    ROUND(price_bucket, 2)            AS bucket_lo,
    ROUND(price_bucket + 0.05, 2)    AS bucket_hi,
    count(*)                          AS n_positions,
    count(DISTINCT trader)            AS n_traders,
    count(DISTINCT condition_id)      AS n_markets,
    AVG(CAST(correct AS DOUBLE))      AS base_hr,
    AVG(realized_pnl)                 AS avg_pnl_per_pos,
    MEDIAN(realized_pnl)              AS med_pnl_per_pos,
    -- Break-even HR = entry price (to profit you need HR > entry price)
    AVG(entry_price)                  AS avg_entry_price
FROM _epb_base
GROUP BY price_bucket
ORDER BY price_bucket
""")

print("\n=== BASE RATES BY ENTRY PRICE BUCKET ===")
print(f"{'Bucket':>12} {'N Pos':>9} {'N Mkts':>8} {'Base HR':>8} {'Avg Entry':>10} "
      f"{'Edge Space':>11} {'Avg PnL':>9}")
print("-" * 80)
for row in base_rates.to_dicts():
    lo = row["bucket_lo"]
    hi = row["bucket_hi"]
    base_hr = row["base_hr"]
    avg_entry = row["avg_entry_price"]
    edge_space = base_hr - avg_entry   # positive = avg odds are in your favor
    print(f"  [{lo:.2f}-{hi:.2f}]  {row['n_positions']:>9,}  {row['n_markets']:>8,}  "
          f"{base_hr:>7.1%}  {avg_entry:>10.3f}  {edge_space:>+10.3f}  "
          f"${row['avg_pnl_per_pos']:>7.3f}")

# Save base rates
base_rates.write_json(f"{OUT_DIR}/entry_price_base_rates.json")
print(f"\nSaved: {OUT_DIR}/entry_price_base_rates.json")

# ── Step 3: Per-trader per-bucket stats ──────────────────────────────────────
print("\nComputing per-trader per-bucket stats...")
t2 = time.time()

d.execute("""
CREATE OR REPLACE TABLE _epb_trader_bucket AS
SELECT
    trader,
    price_bucket,
    count(*)                     AS n_positions,
    count(DISTINCT condition_id) AS n_markets,
    AVG(CAST(correct AS DOUBLE)) AS trader_hr,
    SUM(realized_pnl)            AS total_pnl,
    AVG(realized_pnl)            AS avg_pnl_per_pos,
    MEDIAN(realized_pnl)         AS med_pnl_per_pos,
    AVG(entry_price)             AS avg_entry_price
FROM _epb_base
GROUP BY trader, price_bucket
HAVING count(*) >= 5   -- minimal filter; real filter applied later
""")
cnt2 = d.con.execute("SELECT count(*) FROM _epb_trader_bucket").fetchone()[0]
print(f"  Built: {cnt2:,} (trader, bucket) pairs in {time.time()-t2:.1f}s")

# ── Step 4: Join with base rates to compute edge ──────────────────────────────
print("\nComputing edge over base rate per (trader, bucket)...")

d.execute("""
CREATE OR REPLACE TABLE _epb_edge AS
SELECT
    tb.trader,
    tb.price_bucket,
    tb.n_positions,
    tb.n_markets,
    tb.trader_hr,
    br.base_hr,
    tb.trader_hr - br.base_hr    AS edge_over_base,
    tb.total_pnl,
    tb.avg_pnl_per_pos,
    tb.med_pnl_per_pos,
    tb.avg_entry_price
FROM _epb_trader_bucket tb
JOIN (
    SELECT price_bucket, AVG(CAST(correct AS DOUBLE)) AS base_hr
    FROM _epb_base
    GROUP BY price_bucket
) br ON tb.price_bucket = br.price_bucket
""")

# ── Step 5: Composite score — edge weighted by sample size ───────────────────
# min 10 positions per bucket for inclusion in bucket score
# Composite = sum(edge_over_base * n_positions) / sum(n_positions) across qualifying buckets
# Also require at least 2 distinct qualifying buckets for multi-bucket traders
print("\nComputing composite trader scores (min 10 positions per bucket)...")

d.execute("""
CREATE OR REPLACE TABLE _epb_scores AS
WITH qualifying_buckets AS (
    SELECT *
    FROM _epb_edge
    WHERE n_positions >= 10   -- meaningful sample per bucket
),
trader_composite AS (
    SELECT
        trader,
        count(DISTINCT price_bucket)                    AS n_qualifying_buckets,
        SUM(n_positions)                                AS total_positions,
        SUM(n_markets)                                  AS total_markets,
        -- Weighted average edge across buckets
        SUM(edge_over_base * n_positions) / SUM(n_positions) AS weighted_avg_edge,
        -- Raw sum-of-weighted-edge (prioritizes traders active in many buckets)
        SUM(edge_over_base * n_positions)               AS edge_score,
        SUM(total_pnl)                                  AS total_pnl,
        AVG(trader_hr)                                  AS avg_hr_across_buckets,
        AVG(base_hr)                                    AS avg_base_hr_across_buckets
    FROM qualifying_buckets
    GROUP BY trader
    HAVING SUM(n_positions) >= 20   -- overall minimum
)
SELECT
    *,
    avg_hr_across_buckets - avg_base_hr_across_buckets AS simple_edge
FROM trader_composite
ORDER BY weighted_avg_edge DESC
""")

n_scored = d.con.execute("SELECT count(*) FROM _epb_scores").fetchone()[0]
print(f"  Scored {n_scored:,} traders with >= 20 qualifying positions\n")

# ── Step 6: Top 50 by weighted average edge ───────────────────────────────────
top50_edge = d.query("""
SELECT
    ROW_NUMBER() OVER (ORDER BY weighted_avg_edge DESC) AS rank_edge,
    trader,
    n_qualifying_buckets,
    total_positions,
    total_markets,
    ROUND(weighted_avg_edge, 4)   AS weighted_avg_edge,
    ROUND(avg_hr_across_buckets, 4)  AS avg_hr,
    ROUND(avg_base_hr_across_buckets, 4) AS avg_base_hr,
    ROUND(total_pnl, 2)           AS total_pnl,
    ROUND(edge_score, 2)          AS edge_score
FROM _epb_scores
ORDER BY weighted_avg_edge DESC
LIMIT 50
""")

print("=== TOP 50 TRADERS BY EDGE OVER BASE RATE (weighted avg across buckets) ===")
print(f"{'Rank':>4} {'Trader':>10} {'N Bkts':>6} {'N Pos':>7} {'N Mkts':>7} "
      f"{'Wtd Edge':>9} {'Avg HR':>7} {'Base HR':>8} {'Tot PnL':>10}")
print("-" * 82)
for row in top50_edge.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    print(f"  {row['rank_edge']:>3}  ...{trader_short}  {row['n_qualifying_buckets']:>6}  "
          f"{row['total_positions']:>7,}  {row['total_markets']:>7,}  "
          f"{row['weighted_avg_edge']:>+8.1%}  {row['avg_hr']:>6.1%}  "
          f"{row['avg_base_hr']:>7.1%}  ${row['total_pnl']:>9,.0f}")

top50_edge.write_json(f"{OUT_DIR}/top50_traders_edge_score.json")
print(f"\nSaved: {OUT_DIR}/top50_traders_edge_score.json")

# ── Step 7: Edge distribution by bucket — where is edge concentrated? ────────
print("\n=== EDGE DISTRIBUTION BY BUCKET (traders with >= 10 pos in bucket) ===")

bucket_edge_dist = d.query("""
SELECT
    ROUND(price_bucket, 2)          AS bucket_lo,
    ROUND(price_bucket + 0.05, 2)  AS bucket_hi,
    count(DISTINCT trader)          AS n_traders_qualifying,
    AVG(edge_over_base)             AS avg_edge,
    MEDIAN(edge_over_base)          AS median_edge,
    QUANTILE_CONT(edge_over_base, 0.75)  AS q75_edge,
    QUANTILE_CONT(edge_over_base, 0.90)  AS q90_edge,
    QUANTILE_CONT(edge_over_base, 0.25)  AS q25_edge,
    -- Fraction of traders who beat base rate in this bucket
    AVG(CASE WHEN edge_over_base > 0 THEN 1.0 ELSE 0.0 END) AS frac_beat_base
FROM _epb_edge
WHERE n_positions >= 10
GROUP BY price_bucket
ORDER BY price_bucket
""")

print(f"\n{'Bucket':>12} {'N Traders':>10} {'Avg Edge':>9} {'Med Edge':>9} "
      f"{'Q90 Edge':>9} {'Frac Beat':>10}")
print("-" * 70)
for row in bucket_edge_dist.to_dicts():
    lo = row["bucket_lo"]
    hi = row["bucket_hi"]
    print(f"  [{lo:.2f}-{hi:.2f}]  {row['n_traders_qualifying']:>10,}  "
          f"{row['avg_edge']:>+8.1%}  {row['median_edge']:>+8.1%}  "
          f"{row['q90_edge']:>+8.1%}  {row['frac_beat_base']:>9.1%}")

bucket_edge_dist.write_json(f"{OUT_DIR}/bucket_edge_distribution.json")
print(f"\nSaved: {OUT_DIR}/bucket_edge_distribution.json")

# ── Step 8: Naive HR ranking vs edge ranking (comparison) ───────────────────
print("\n=== NAIVE HR vs EDGE RANKING: TOP 50 BY NAIVE HR ===")

# For traders with >= 20 qualifying positions
top50_naive_hr = d.query("""
SELECT
    ROW_NUMBER() OVER (ORDER BY avg_hr_across_buckets DESC) AS rank_naive_hr,
    trader,
    total_positions,
    ROUND(avg_hr_across_buckets, 4) AS avg_hr,
    ROUND(avg_base_hr_across_buckets, 4) AS avg_base_hr,
    ROUND(weighted_avg_edge, 4) AS weighted_avg_edge,
    DENSE_RANK() OVER (ORDER BY weighted_avg_edge DESC) AS rank_edge,
    ROUND(total_pnl, 2) AS total_pnl
FROM _epb_scores
ORDER BY avg_hr_across_buckets DESC
LIMIT 50
""")

print(f"{'HR Rank':>8} {'Edge Rank':>10} {'Trader':>10} {'N Pos':>7} {'Avg HR':>7} "
      f"{'Base HR':>8} {'Edge':>9} {'Tot PnL':>10}")
print("-" * 82)
for row in top50_naive_hr.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    rank_diff = row["rank_naive_hr"] - row["rank_edge"]
    diff_str = f"({rank_diff:+d})" if abs(rank_diff) > 5 else "     "
    print(f"  {row['rank_naive_hr']:>6}  {row['rank_edge']:>8} {diff_str}  ...{trader_short}  "
          f"{row['total_positions']:>7,}  {row['avg_hr']:>6.1%}  {row['avg_base_hr']:>7.1%}  "
          f"{row['weighted_avg_edge']:>+8.1%}  ${row['total_pnl']:>9,.0f}")

top50_naive_hr.write_json(f"{OUT_DIR}/top50_traders_naive_hr.json")
print(f"\nSaved: {OUT_DIR}/top50_traders_naive_hr.json")

# ── Step 9: Overlap between top-200 naive HR vs top-200 edge score ────────────
print("\n=== OVERLAP ANALYSIS: TOP-200 NAIVE HR vs TOP-200 EDGE SCORE ===")
overlap = d.query("""
WITH top200_edge AS (
    SELECT trader FROM _epb_scores ORDER BY weighted_avg_edge DESC LIMIT 200
),
top200_hr AS (
    SELECT trader FROM _epb_scores ORDER BY avg_hr_across_buckets DESC LIMIT 200
)
SELECT count(*) AS overlap_count
FROM top200_edge e
JOIN top200_hr h ON e.trader = h.trader
""")
overlap_n = overlap["overlap_count"][0]
print(f"  Traders in both top-200 lists: {overlap_n} / 200 ({overlap_n/2:.0f}%)")

# ── Step 10: Best bucket per top trader ───────────────────────────────────────
print("\n=== TOP TRADERS: BEST PRICE BUCKET (for top 20 by edge score) ===")
best_bucket = d.query("""
WITH top20 AS (
    SELECT trader, RANK() OVER (ORDER BY weighted_avg_edge DESC) AS overall_rank
    FROM _epb_scores
    ORDER BY weighted_avg_edge DESC
    LIMIT 20
),
edge_ranked AS (
    SELECT
        e.trader,
        t.overall_rank,
        e.price_bucket,
        e.n_positions,
        ROUND(e.trader_hr, 3) AS trader_hr,
        ROUND(e.base_hr, 3) AS base_hr,
        ROUND(e.edge_over_base, 3) AS edge,
        ROUND(e.total_pnl, 2) AS pnl,
        ROW_NUMBER() OVER (PARTITION BY e.trader ORDER BY e.edge_over_base DESC) AS bucket_rank
    FROM _epb_edge e
    JOIN top20 t ON e.trader = t.trader
    WHERE e.n_positions >= 10
)
SELECT *
FROM edge_ranked
WHERE bucket_rank = 1
ORDER BY overall_rank
""")

print(f"{'OvRank':>7} {'Trader':>10} {'Best Bucket':>12} {'N Pos':>7} "
      f"{'HR':>7} {'Base HR':>8} {'Edge':>7} {'PnL':>10}")
print("-" * 78)
for row in best_bucket.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    lo = row["price_bucket"]
    hi = lo + 0.05
    print(f"  {row['overall_rank']:>5}  ...{trader_short}  [{lo:.2f}-{hi:.2f}]  "
          f"{row['n_positions']:>7,}  {row['trader_hr']:>6.1%}  {row['base_hr']:>7.1%}  "
          f"{row['edge']:>+6.1%}  ${row['pnl']:>9,.0f}")

# ── Step 11: Summary stats ────────────────────────────────────────────────────
print("\n=== SUMMARY STATISTICS ===")
summary = d.query("""
SELECT
    count(DISTINCT trader)     AS total_traders_with_yes_positions,
    count(DISTINCT condition_id) AS total_markets,
    count(*)                   AS total_positions,
    AVG(CAST(correct AS DOUBLE)) AS overall_base_hr
FROM _epb_base
""")
row = summary.to_dicts()[0]
print(f"  Total YES-position traders: {row['total_traders_with_yes_positions']:,}")
print(f"  Total resolved markets:     {row['total_markets']:,}")
print(f"  Total YES positions:        {row['total_positions']:,}")
print(f"  Overall YES hit rate:       {row['overall_base_hr']:.1%}")

scored_summary = d.query("SELECT count(*) AS n, AVG(weighted_avg_edge) AS avg_edge FROM _epb_scores")
sr = scored_summary.to_dicts()[0]
print(f"  Traders qualifying for scoring (>=20 pos): {sr['n']:,}")
print(f"  Average weighted edge among scored:        {sr['avg_edge']:+.1%}")

# ── Save findings JSON ────────────────────────────────────────────────────────
findings = {
    "overall_stats": {
        "total_traders": row["total_traders_with_yes_positions"],
        "total_markets": row["total_markets"],
        "total_yes_positions": row["total_positions"],
        "overall_yes_hr": round(row["overall_base_hr"], 4),
        "scored_traders": sr["n"],
        "avg_weighted_edge_scored": round(sr["avg_edge"], 4),
    },
    "base_rates_by_bucket": base_rates.to_dicts(),
    "bucket_edge_distribution": bucket_edge_dist.to_dicts(),
    "top50_by_edge_score": top50_edge.to_dicts(),
    "top50_by_naive_hr": top50_naive_hr.to_dicts(),
    "top20_best_buckets": best_bucket.to_dicts(),
    "overlap_top200_naive_hr_vs_edge": int(overlap_n),
}

out_path = f"{OUT_DIR}/entry_price_bucket_findings.json"
with open(out_path, "w") as f:
    json.dump(findings, f, indent=2, default=str)
print(f"\nFull findings saved: {out_path}")
print(f"\nTotal runtime: {time.time()-t0:.1f}s")

# ── Step 12: Deep-dive — is the 0-5% high HR signal real or artifact? ─────────
print("\n=== DEEP DIVE: 0-5% BUCKET HIGH-HR TRADERS ===")
print("Context: Base rate = 16%, top traders show 95-100% HR. Is this real skill?")

# Check raw positions for top 0-5% bucket traders — look at entry price distribution
low_bucket_check = d.query("""
SELECT
    price_bucket,
    count(DISTINCT trader)       AS n_traders,
    count(*)                     AS n_pos,
    AVG(entry_price)             AS avg_entry_price,
    MIN(entry_price)             AS min_entry_price,
    MAX(entry_price)             AS max_entry_price,
    -- How many are really near-zero (< 0.01)?
    SUM(CASE WHEN entry_price < 0.01 THEN 1 ELSE 0 END) AS n_very_low
FROM _epb_base
WHERE price_bucket < 0.05
GROUP BY price_bucket
""")
print("\nAll entries in 0.00-0.05 bucket:")
for row in low_bucket_check.to_dicts():
    print(f"  Avg entry: {row['avg_entry_price']:.4f}, Min: {row['min_entry_price']:.6f}, "
          f"Max: {row['max_entry_price']:.4f}, N<0.01: {row['n_very_low']:,} / {row['n_pos']:,}")

# Check top traders' actual entry prices
top_traders_low_bucket = d.query("""
WITH top20_edge AS (
    SELECT trader FROM _epb_scores ORDER BY weighted_avg_edge DESC LIMIT 20
)
SELECT
    e.trader,
    ROUND(e.entry_price, 4) AS entry_price,
    e.correct,
    ROUND(e.realized_pnl, 4) AS pnl
FROM _epb_base e
JOIN top20_edge t ON e.trader = t.trader
WHERE e.price_bucket < 0.05
ORDER BY e.trader, e.entry_price
LIMIT 30
""")
print("\nSample positions of top-20 edge traders in 0-5% bucket:")
print(f"{'Trader':>12} {'Entry':>8} {'Correct':>8} {'PnL':>10}")
for row in top_traders_low_bucket.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    print(f"  ...{trader_short}  {row['entry_price']:>7.4f}  {row['correct']:>8}  ${row['pnl']:>9.4f}")

# Is this just the in-play signal? Check resolution timing
in_play_check = d.query("""
WITH top10_edge AS (
    SELECT trader FROM _epb_scores ORDER BY weighted_avg_edge DESC LIMIT 10
)
SELECT
    e.trader,
    e.price_bucket,
    e.correct,
    e.realized_pnl,
    -- time from entry to resolution (in hours) — use first_trade from maker_positions
    date_diff('hour', m.first_trade, m.resolved_at) AS hours_to_resolution
FROM _epb_base e
JOIN maker_positions m ON e.trader = m.trader AND e.condition_id = m.condition_id AND m.position = 'YES'
JOIN top10_edge t ON e.trader = t.trader
WHERE e.price_bucket < 0.05
ORDER BY hours_to_resolution
LIMIT 20
""")
print("\nHours-to-resolution for top-10 edge traders (0-5% bucket):")
print(f"{'Trader':>12} {'Entry':>7} {'Correct':>8} {'PnL':>8} {'Hrs to Res':>12}")
for row in in_play_check.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    print(f"  ...{trader_short}  {row['price_bucket']:>6.3f}  {row['correct']:>8}  "
          f"${row['realized_pnl']:>7.2f}  {row['hours_to_resolution']:>12}")

# ── Step 13: Exclude <0.05 bucket and re-rank (more realistic skill test) ────
print("\n=== EDGE RANKING: EXCLUDING <0.05 BUCKET (longshot artifact check) ===")

longshot_removed = d.query("""
WITH qualifying_buckets AS (
    SELECT *
    FROM _epb_edge
    WHERE n_positions >= 10
      AND price_bucket >= 0.05   -- exclude < 5% bucket
),
trader_composite AS (
    SELECT
        trader,
        count(DISTINCT price_bucket)                    AS n_qualifying_buckets,
        SUM(n_positions)                                AS total_positions,
        SUM(edge_over_base * n_positions) / SUM(n_positions) AS weighted_avg_edge,
        SUM(total_pnl)                                  AS total_pnl,
        AVG(trader_hr)                                  AS avg_hr,
        AVG(base_hr)                                    AS avg_base_hr
    FROM qualifying_buckets
    GROUP BY trader
    HAVING SUM(n_positions) >= 20
)
SELECT
    ROW_NUMBER() OVER (ORDER BY weighted_avg_edge DESC) AS rank,
    trader,
    n_qualifying_buckets,
    total_positions,
    ROUND(weighted_avg_edge, 4) AS wtd_edge,
    ROUND(avg_hr, 4) AS avg_hr,
    ROUND(avg_base_hr, 4) AS avg_base_hr,
    ROUND(total_pnl, 2) AS total_pnl
FROM trader_composite
ORDER BY weighted_avg_edge DESC
LIMIT 30
""")

print(f"\n{'Rank':>4} {'Trader':>10} {'N Bkts':>6} {'N Pos':>7} {'Wtd Edge':>9} "
      f"{'Avg HR':>7} {'Base HR':>8} {'Tot PnL':>10}")
print("-" * 75)
for row in longshot_removed.to_dicts():
    trader_short = row["trader"][-8:] if row["trader"] else "?"
    print(f"  {row['rank']:>3}  ...{trader_short}  {row['n_qualifying_buckets']:>6}  "
          f"{row['total_positions']:>7,}  {row['wtd_edge']:>+8.1%}  "
          f"{row['avg_hr']:>6.1%}  {row['avg_base_hr']:>7.1%}  ${row['total_pnl']:>9,.0f}")

# Save this alternative ranking too
longshot_removed.write_json(f"{OUT_DIR}/top30_traders_edge_ex_longshot.json")
print(f"\nSaved: {OUT_DIR}/top30_traders_edge_ex_longshot.json")
print(f"\nTotal script runtime: {time.time()-t0:.1f}s")
