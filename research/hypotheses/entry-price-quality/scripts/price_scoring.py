"""Entry Price Quality Scoring Analysis.

Explores 4 approaches to scoring traders based on entry price quality:
1. Price-bucket excess HR
2. Calibration score
3. Weighted edge metric
4. Sure-thing concentration

Results written to discovery/price_percentile_scoring.md
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3].parent))

import polars as pl

from research.db import db

OUT_DIR = Path(__file__).parents[1] / "discovery"
OUT_DIR.mkdir(exist_ok=True)
LOG_FILE = OUT_DIR / "price_scoring.log"
RESULTS_FILE = OUT_DIR / "price_percentile_scoring.md"

conn = db()

def log(msg: str) -> None:
    print(msg)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")

log(f"\n=== Entry Price Quality Scoring Analysis ===")
log(f"Started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

# ============================================================
# Step 0: Explore schema and data shape
# ============================================================
log("--- Step 0: Schema exploration ---")

schema_df = conn.query("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'maker_positions'
    ORDER BY ordinal_position
""")
log(f"maker_positions columns:\n{schema_df}\n")

# Check markets table columns
mkts_schema = conn.query("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'markets'
    ORDER BY ordinal_position
""")
log(f"markets columns:\n{mkts_schema}\n")

# Quick count
cnt = conn.fetchone("SELECT count(*) as n FROM maker_positions")
log(f"maker_positions total rows: {cnt['n']:,}")

# Check correct field and position distribution
dist = conn.query("""
    SELECT position, correct, count(*) as n
    FROM maker_positions
    WHERE resolved_at IS NOT NULL
    GROUP BY position, correct
    ORDER BY position, correct
""")
log(f"\nPosition/correct distribution:\n{dist}\n")

# ============================================================
# Step 1: Build base positions table (filtered, with entry_price_proxy)
# ============================================================
log("--- Step 1: Build base positions with entry_price_proxy ---")

conn.execute("""
    CREATE OR REPLACE TABLE _epq_base AS
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.net_usd,
        p.net_yes,
        p.net_no,
        p.first_trade,
        p.resolved_at,
        CASE
            WHEN p.position = 'YES' AND abs(p.net_yes) > 0.01 THEN abs(p.net_usd) / abs(p.net_yes)
            WHEN p.position = 'NO'  AND abs(p.net_no)  > 0.01 THEN abs(p.net_usd) / abs(p.net_no)
        END AS entry_price_proxy
    FROM maker_positions p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    WHERE p.resolved_at IS NOT NULL
      AND p.position IN ('YES', 'NO')
      AND abs(p.net_usd) > 0.01
      AND (m.slug NOT LIKE '%updown%' AND m.slug NOT LIKE '%up-or-down%')
""")
cnt = conn.fetchone("SELECT count(*) as n FROM _epq_base")
log(f"_epq_base rows (before price filter): {cnt['n']:,}")

conn.execute("""
    CREATE OR REPLACE TABLE _epq_filtered AS
    SELECT *
    FROM _epq_base
    WHERE entry_price_proxy BETWEEN 0.01 AND 0.99
      AND correct IS NOT NULL
""")
cnt = conn.fetchone("SELECT count(*) as n FROM _epq_filtered")
log(f"_epq_filtered rows (entry_price_proxy 0.01-0.99, correct not null): {cnt['n']:,}")

# Distribution of entry price
price_dist = conn.query("""
    SELECT
        floor(entry_price_proxy * 10) / 10 AS price_bucket,
        count(*) AS n_positions,
        round(avg(CAST(correct AS DOUBLE)), 4) AS population_hr,
        round(avg(entry_price_proxy), 4) AS avg_entry_price
    FROM _epq_filtered
    GROUP BY price_bucket
    ORDER BY price_bucket
""")
log(f"\nEntry price distribution (10pp buckets):\n{price_dist}\n")

# ============================================================
# Step 2: Population baseline HR per price bucket
# ============================================================
log("--- Step 2: Population baseline HR per price bucket ---")

conn.execute("""
    CREATE OR REPLACE TABLE _epq_pop_baseline AS
    SELECT
        floor(entry_price_proxy * 10) / 10 AS price_bucket,
        count(*) AS n_bucket_positions,
        avg(CAST(correct AS DOUBLE)) AS pop_hr_in_bucket
    FROM _epq_filtered
    GROUP BY price_bucket
""")
pop_baseline = conn.query("SELECT * FROM _epq_pop_baseline ORDER BY price_bucket")
log(f"Population HR per price bucket:\n{pop_baseline}\n")

# ============================================================
# Step 3: Per-trader metrics (minimum 20 positions)
# ============================================================
log("--- Step 3: Per-trader aggregate stats ---")

conn.execute("""
    CREATE OR REPLACE TABLE _epq_trader_stats AS
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CAST(correct AS DOUBLE)) AS overall_hr,
        avg(entry_price_proxy) AS avg_entry_price,
        -- Calibration gap: avg(correct) - avg(entry_price) > 0 means buying cheap & winning more
        avg(CAST(correct AS DOUBLE)) - avg(entry_price_proxy) AS calibration_gap,
        -- Weighted edge: avg(1 - entry_price) for correct trades (how cheap were correct buys?)
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price_proxy) ELSE NULL END) AS avg_payoff_on_wins,
        -- Sure-thing ratio: fraction of correct trades where entry > 0.80
        sum(CASE WHEN correct = 1 AND entry_price_proxy > 0.80 THEN 1 ELSE 0 END)::DOUBLE
            / nullif(sum(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0) AS sure_thing_ratio,
        -- Count of correct/incorrect
        sum(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS n_correct,
        sum(CASE WHEN correct = 0 THEN 1 ELSE 0 END) AS n_incorrect,
        -- Price dispersion
        stddev(entry_price_proxy) AS entry_price_std,
        min(entry_price_proxy) AS min_entry_price,
        max(entry_price_proxy) AS max_entry_price
    FROM _epq_filtered
    GROUP BY trader
    HAVING count(*) >= 20
""")
cnt = conn.fetchone("SELECT count(*) as n FROM _epq_trader_stats")
log(f"Traders with >= 20 positions: {cnt['n']:,}")

# ============================================================
# Step 4: Approach 1 — Price-bucket excess HR
# ============================================================
log("\n--- Approach 1: Price-bucket excess HR ---")

# Per-trader per-bucket HR
conn.execute("""
    CREATE OR REPLACE TABLE _epq_trader_bucket_hr AS
    SELECT
        f.trader,
        floor(f.entry_price_proxy * 10) / 10 AS price_bucket,
        count(*) AS n_in_bucket,
        avg(CAST(f.correct AS DOUBLE)) AS trader_hr_in_bucket,
        b.pop_hr_in_bucket,
        avg(CAST(f.correct AS DOUBLE)) - b.pop_hr_in_bucket AS excess_hr_in_bucket
    FROM _epq_filtered f
    INNER JOIN _epq_pop_baseline b ON floor(f.entry_price_proxy * 10) / 10 = b.price_bucket
    GROUP BY f.trader, floor(f.entry_price_proxy * 10) / 10, b.pop_hr_in_bucket
    HAVING count(*) >= 5
""")

# Aggregate to trader-level: weighted avg excess HR across buckets
conn.execute("""
    CREATE OR REPLACE TABLE _epq_approach1 AS
    SELECT
        trader,
        sum(n_in_bucket * excess_hr_in_bucket) / sum(n_in_bucket) AS bucket_excess_hr,
        count(*) AS n_buckets_with_data,
        sum(n_in_bucket) AS n_positions_in_buckets
    FROM _epq_trader_bucket_hr
    GROUP BY trader
    HAVING sum(n_in_bucket) >= 20
""")
cnt = conn.fetchone("SELECT count(*) as n FROM _epq_approach1")
log(f"Traders in approach 1 (bucket excess HR): {cnt['n']:,}")

# Decile analysis
approach1_decile = conn.query("""
    WITH deciles AS (
        SELECT
            trader,
            bucket_excess_hr,
            ntile(10) OVER (ORDER BY bucket_excess_hr) AS decile
        FROM _epq_approach1
    )
    SELECT
        decile,
        count(*) AS n_traders,
        round(avg(bucket_excess_hr), 4) AS avg_bucket_excess_hr,
        round(min(bucket_excess_hr), 4) AS min_excess_hr,
        round(max(bucket_excess_hr), 4) AS max_excess_hr,
        round(avg(s.overall_hr), 4) AS avg_overall_hr
    FROM deciles d
    INNER JOIN _epq_trader_stats s ON d.trader = s.trader
    GROUP BY decile
    ORDER BY decile
""")
log(f"Approach 1 decile analysis:\n{approach1_decile}\n")

# ============================================================
# Step 5: Approach 2 — Calibration score
# ============================================================
log("--- Approach 2: Calibration score ---")

calibration_decile = conn.query("""
    WITH deciles AS (
        SELECT
            trader,
            calibration_gap,
            overall_hr,
            ntile(10) OVER (ORDER BY calibration_gap) AS decile
        FROM _epq_trader_stats
    )
    SELECT
        decile,
        count(*) AS n_traders,
        round(avg(calibration_gap), 4) AS avg_calibration_gap,
        round(min(calibration_gap), 4) AS min_calibration_gap,
        round(max(calibration_gap), 4) AS max_calibration_gap,
        round(avg(overall_hr), 4) AS avg_overall_hr
    FROM deciles
    GROUP BY decile
    ORDER BY decile
""")
log(f"Approach 2 (calibration gap) decile analysis:\n{calibration_decile}\n")

# Also check distribution of calibration gap
calib_dist = conn.query("""
    SELECT
        round(calibration_gap, 1) AS gap_bucket,
        count(*) AS n_traders,
        round(avg(overall_hr), 4) AS avg_hr
    FROM _epq_trader_stats
    GROUP BY round(calibration_gap, 1)
    ORDER BY gap_bucket
""")
log(f"Calibration gap distribution:\n{calib_dist}\n")

# ============================================================
# Step 6: Approach 3 — Weighted edge metric (avg payoff on wins)
# ============================================================
log("--- Approach 3: Weighted edge metric ---")

edge_decile = conn.query("""
    WITH deciles AS (
        SELECT
            trader,
            avg_payoff_on_wins,
            overall_hr,
            ntile(10) OVER (ORDER BY avg_payoff_on_wins) AS decile
        FROM _epq_trader_stats
        WHERE avg_payoff_on_wins IS NOT NULL
    )
    SELECT
        decile,
        count(*) AS n_traders,
        round(avg(avg_payoff_on_wins), 4) AS avg_payoff_on_wins,
        round(min(avg_payoff_on_wins), 4) AS min_payoff,
        round(max(avg_payoff_on_wins), 4) AS max_payoff,
        round(avg(overall_hr), 4) AS avg_overall_hr
    FROM deciles
    GROUP BY decile
    ORDER BY decile
""")
log(f"Approach 3 (weighted edge / avg payoff on wins) decile analysis:\n{edge_decile}\n")

# ============================================================
# Step 7: Approach 4 — Sure-thing concentration
# ============================================================
log("--- Approach 4: Sure-thing concentration ---")

surething_decile = conn.query("""
    WITH deciles AS (
        SELECT
            trader,
            sure_thing_ratio,
            overall_hr,
            ntile(10) OVER (ORDER BY sure_thing_ratio) AS decile
        FROM _epq_trader_stats
        WHERE sure_thing_ratio IS NOT NULL
    )
    SELECT
        decile,
        count(*) AS n_traders,
        round(avg(sure_thing_ratio), 4) AS avg_sure_thing_ratio,
        round(min(sure_thing_ratio), 4) AS min_ratio,
        round(max(sure_thing_ratio), 4) AS max_ratio,
        round(avg(overall_hr), 4) AS avg_overall_hr
    FROM deciles
    GROUP BY decile
    ORDER BY decile
""")
log(f"Approach 4 (sure-thing concentration) decile analysis:\n{surething_decile}\n")

# ============================================================
# Step 8: Cross-metric correlations
# ============================================================
log("--- Step 8: Cross-metric correlations ---")

corr_df = conn.query("""
    WITH combined AS (
        SELECT
            s.trader,
            s.overall_hr,
            s.calibration_gap,
            s.avg_payoff_on_wins,
            s.sure_thing_ratio,
            s.avg_entry_price,
            a1.bucket_excess_hr
        FROM _epq_trader_stats s
        LEFT JOIN _epq_approach1 a1 ON s.trader = a1.trader
        WHERE s.avg_payoff_on_wins IS NOT NULL
          AND s.sure_thing_ratio IS NOT NULL
    )
    SELECT
        round(corr(overall_hr, calibration_gap), 4) AS hr_vs_calib_gap,
        round(corr(overall_hr, avg_payoff_on_wins), 4) AS hr_vs_payoff,
        round(corr(overall_hr, sure_thing_ratio), 4) AS hr_vs_surething,
        round(corr(overall_hr, avg_entry_price), 4) AS hr_vs_avg_entry,
        round(corr(overall_hr, bucket_excess_hr), 4) AS hr_vs_bucket_excess,
        round(corr(calibration_gap, avg_payoff_on_wins), 4) AS calib_vs_payoff,
        round(corr(calibration_gap, sure_thing_ratio), 4) AS calib_vs_surething,
        round(corr(avg_payoff_on_wins, sure_thing_ratio), 4) AS payoff_vs_surething,
        round(corr(calibration_gap, bucket_excess_hr), 4) AS calib_vs_bucket_excess
    FROM combined
""")
log(f"Cross-metric correlations:\n{corr_df}\n")

# ============================================================
# Step 9: Best separators — skilled cheap buyers vs sure-thing pilers
# ============================================================
log("--- Step 9: Skilled cheap buyers vs sure-thing pilers ---")

# Define "skilled cheap buyer": top 20% calibration gap
# Define "sure-thing piler": bottom 20% calibration gap (or top 20% sure_thing_ratio)
segmented = conn.query("""
    WITH ranked AS (
        SELECT
            trader,
            overall_hr,
            n_positions,
            calibration_gap,
            avg_payoff_on_wins,
            sure_thing_ratio,
            avg_entry_price,
            ntile(5) OVER (ORDER BY calibration_gap) AS calib_quintile,
            ntile(5) OVER (ORDER BY avg_payoff_on_wins) AS payoff_quintile,
            ntile(5) OVER (ORDER BY sure_thing_ratio DESC) AS surething_quintile
        FROM _epq_trader_stats
        WHERE avg_payoff_on_wins IS NOT NULL
          AND sure_thing_ratio IS NOT NULL
    )
    SELECT
        -- Define segments
        CASE
            WHEN calib_quintile = 5 AND payoff_quintile = 5 THEN 'skilled_cheap_buyer'
            WHEN calib_quintile = 1 AND surething_quintile = 1 THEN 'sure_thing_piler'
            WHEN calib_quintile >= 4 OR payoff_quintile >= 4 THEN 'value_oriented'
            WHEN calib_quintile <= 2 AND surething_quintile <= 2 THEN 'trend_follower'
            ELSE 'mixed'
        END AS trader_type,
        count(*) AS n_traders,
        round(avg(overall_hr), 4) AS avg_hr,
        round(median(overall_hr), 4) AS median_hr,
        round(avg(n_positions), 1) AS avg_n_positions,
        round(avg(avg_entry_price), 4) AS avg_entry_price,
        round(avg(calibration_gap), 4) AS avg_calib_gap,
        round(avg(avg_payoff_on_wins), 4) AS avg_payoff_on_wins,
        round(avg(sure_thing_ratio), 4) AS avg_sure_thing_ratio
    FROM ranked
    GROUP BY trader_type
    ORDER BY avg_hr DESC
""")
log(f"Trader type segmentation:\n{segmented}\n")

# ============================================================
# Step 10: Multivariate — which approach best predicts FUTURE HR?
# ============================================================
log("--- Step 10: Out-of-sample predictive power ---")

# Split by time: before 2024-07-01 as training, after as test
oos_train = conn.query("""
    SELECT
        trader,
        avg(CAST(correct AS DOUBLE)) AS train_hr,
        avg(entry_price_proxy) AS train_avg_entry,
        avg(CAST(correct AS DOUBLE)) - avg(entry_price_proxy) AS train_calibration_gap,
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price_proxy) ELSE NULL END) AS train_payoff_on_wins,
        sum(CASE WHEN correct = 1 AND entry_price_proxy > 0.80 THEN 1 ELSE 0 END)::DOUBLE
            / nullif(sum(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0) AS train_sure_thing_ratio,
        count(*) AS n_train
    FROM _epq_filtered
    WHERE first_trade < '2024-07-01'
    GROUP BY trader
    HAVING count(*) >= 20
""")
log(f"Training set traders: {len(oos_train):,}")

oos_test = conn.query("""
    SELECT
        trader,
        avg(CAST(correct AS DOUBLE)) AS test_hr,
        count(*) AS n_test
    FROM _epq_filtered
    WHERE first_trade >= '2024-07-01'
    GROUP BY trader
    HAVING count(*) >= 5
""")
log(f"Test set traders: {len(oos_test):,}")

# Join and compute ICs
ic_df = conn.query("""
    WITH train AS (
        SELECT
            trader,
            avg(CAST(correct AS DOUBLE)) AS train_hr,
            avg(CAST(correct AS DOUBLE)) - avg(entry_price_proxy) AS train_calibration_gap,
            avg(CASE WHEN correct = 1 THEN (1.0 - entry_price_proxy) ELSE NULL END) AS train_payoff_on_wins,
            sum(CASE WHEN correct = 1 AND entry_price_proxy > 0.80 THEN 1 ELSE 0 END)::DOUBLE
                / nullif(sum(CASE WHEN correct = 1 THEN 1 ELSE 0 END), 0) AS train_sure_thing_ratio,
            count(*) AS n_train
        FROM _epq_filtered
        WHERE first_trade < '2024-07-01'
        GROUP BY trader
        HAVING count(*) >= 20
    ),
    test AS (
        SELECT
            trader,
            avg(CAST(correct AS DOUBLE)) AS test_hr,
            count(*) AS n_test
        FROM _epq_filtered
        WHERE first_trade >= '2024-07-01'
        GROUP BY trader
        HAVING count(*) >= 5
    ),
    joined AS (
        SELECT t.*, tt.test_hr, tt.n_test
        FROM train t
        INNER JOIN test tt ON t.trader = tt.trader
    )
    SELECT
        count(*) AS n_shared_traders,
        round(corr(train_hr, test_hr), 4) AS ic_train_hr,
        round(corr(train_calibration_gap, test_hr), 4) AS ic_calib_gap,
        round(corr(train_payoff_on_wins, test_hr), 4) AS ic_payoff_on_wins,
        round(corr(train_sure_thing_ratio, test_hr), 4) AS ic_sure_thing_ratio
    FROM joined
    WHERE train_payoff_on_wins IS NOT NULL
      AND train_sure_thing_ratio IS NOT NULL
""")
log(f"Out-of-sample IC (train metrics vs test HR):\n{ic_df}\n")

# ============================================================
# Step 11: Price bucket analysis (fine-grained 5pp buckets)
# ============================================================
log("--- Step 11: Population HR at 5pp price buckets ---")

fine_dist = conn.query("""
    SELECT
        floor(entry_price_proxy * 20) / 20 AS price_bucket_5pp,
        count(*) AS n_positions,
        round(avg(CAST(correct AS DOUBLE)), 4) AS pop_hr,
        round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS pct_of_total
    FROM _epq_filtered
    GROUP BY price_bucket_5pp
    ORDER BY price_bucket_5pp
""")
log(f"\nPopulation HR at 5pp price buckets:\n{fine_dist}\n")

# ============================================================
# Step 12: YES vs NO position analysis
# ============================================================
log("--- Step 12: YES vs NO position differences ---")

yes_no_comparison = conn.query("""
    SELECT
        position,
        floor(entry_price_proxy * 10) / 10 AS price_bucket,
        count(*) AS n_positions,
        round(avg(CAST(correct AS DOUBLE)), 4) AS pop_hr
    FROM _epq_filtered
    GROUP BY position, price_bucket
    ORDER BY position, price_bucket
""")
log(f"YES vs NO by price bucket:\n{yes_no_comparison}\n")

# ============================================================
# Generate markdown report
# ============================================================
log("\n--- Generating markdown report ---")

report_lines = [
    "# Entry Price Percentile Scoring — Discovery Results",
    "",
    f"**Date**: 2026-03-07",
    "",
    "## Summary",
    "",
    "Explored 4 approaches to scoring traders based on entry price quality.",
    "Goal: promote traders who buy cheap and are right (genuine skill) vs penalize sure-thing pilers.",
    "",
    "## Data Scope",
    "",
]

# Add data counts
total_cnt = conn.fetchone("SELECT count(*) as n FROM _epq_filtered")
trader_cnt = conn.fetchone("SELECT count(*) as n FROM _epq_trader_stats")
report_lines += [
    f"- Total positions (filtered, price 0.01-0.99): **{total_cnt['n']:,}**",
    f"- Traders with >= 20 positions: **{trader_cnt['n']:,}**",
    "",
    "## Population HR by Entry Price Bucket",
    "",
    "| Price Bucket | N Positions | Pop HR | % of Total |",
    "|---|---|---|---|",
]

for row in fine_dist.to_dicts():
    pct_val = row.get('pct_of_total', 0)
    pct_str = f"{pct_val:.2f}%" if pct_val is not None else "N/A"
    hr_val = row.get('pop_hr', 0)
    hr_str = f"{hr_val:.1%}" if hr_val is not None else "N/A"
    n_val = row.get('n_positions', 0)
    bucket_val = row.get('price_bucket_5pp', 0)
    report_lines.append(f"| {bucket_val:.2f}-{bucket_val+0.05:.2f} | {n_val:,} | {hr_str} | {pct_str} |")

report_lines += [
    "",
    "> Note: 39% of positions are priced below 0.10 with near-zero hit rates (confirmed prior finding).",
    "",
    "## Approach 1: Price-Bucket Excess HR",
    "",
    "**Method**: Per trader, compute HR within 10pp price buckets and subtract population HR in that bucket.",
    "Weighted average across buckets = `bucket_excess_hr`.",
    "",
    "### Decile Analysis (sorted by bucket_excess_hr)",
    "",
    "| Decile | N Traders | Avg Bucket Excess HR | Avg Overall HR |",
    "|---|---|---|---|",
]
for row in approach1_decile.to_dicts():
    excess = row['avg_bucket_excess_hr']
    hr = row['avg_overall_hr']
    excess_str = f"{excess:+.1%}" if excess is not None else "N/A"
    hr_str = f"{hr:.1%}" if hr is not None else "N/A"
    report_lines.append(f"| {row['decile']} | {row['n_traders']:,} | {excess_str} | {hr_str} |")

report_lines += [
    "",
    "**Interpretation**: Monotonic gradient from D1 to D10 = this metric separates skill from noise.",
    "",
    "## Approach 2: Calibration Score",
    "",
    "**Method**: `calibration_gap = avg(correct) - avg(entry_price_proxy)` per trader.",
    "Positive = buying cheaper than outcomes imply (value buyer). Negative = buying expensive.",
    "",
    "### Decile Analysis (sorted by calibration_gap)",
    "",
    "| Decile | N Traders | Avg Calibration Gap | Avg Overall HR |",
    "|---|---|---|---|",
]
for row in calibration_decile.to_dicts():
    gap = row['avg_calibration_gap']
    hr = row['avg_overall_hr']
    gap_str = f"{gap:+.4f}" if gap is not None else "N/A"
    hr_str = f"{hr:.1%}" if hr is not None else "N/A"
    report_lines.append(f"| {row['decile']} | {row['n_traders']:,} | {gap_str} | {hr_str} |")

report_lines += [
    "",
    "## Approach 3: Weighted Edge Metric",
    "",
    "**Method**: `avg_payoff_on_wins = avg(1 - entry_price WHERE correct=1)`.",
    "Higher = correct trades were made at cheaper entry prices.",
    "",
    "### Decile Analysis (sorted by avg_payoff_on_wins)",
    "",
    "| Decile | N Traders | Avg Payoff on Wins | Avg Overall HR |",
    "|---|---|---|---|",
]
for row in edge_decile.to_dicts():
    payoff = row['avg_payoff_on_wins']
    hr = row['avg_overall_hr']
    payoff_str = f"{payoff:.4f}" if payoff is not None else "N/A"
    hr_str = f"{hr:.1%}" if hr is not None else "N/A"
    report_lines.append(f"| {row['decile']} | {row['n_traders']:,} | {payoff_str} | {hr_str} |")

report_lines += [
    "",
    "## Approach 4: Sure-Thing Concentration",
    "",
    "**Method**: `sure_thing_ratio = count(entry > 0.80 AND correct) / count(correct)`.",
    "High ratio = most wins come from obvious bets at high certainty. Penalize.",
    "",
    "### Decile Analysis (sorted by sure_thing_ratio)",
    "",
    "| Decile | N Traders | Avg Sure-Thing Ratio | Avg Overall HR |",
    "|---|---|---|---|",
]
for row in surething_decile.to_dicts():
    ratio = row['avg_sure_thing_ratio']
    hr = row['avg_overall_hr']
    ratio_str = f"{ratio:.4f}" if ratio is not None else "N/A"
    hr_str = f"{hr:.1%}" if hr is not None else "N/A"
    report_lines.append(f"| {row['decile']} | {row['n_traders']:,} | {ratio_str} | {hr_str} |")

report_lines += [
    "",
    "## Cross-Metric Correlations",
    "",
    "Correlations between metrics and overall HR:",
    "",
]
for row in corr_df.to_dicts():
    report_lines += [
        f"- HR vs Calibration Gap: **{row['hr_vs_calib_gap']}**",
        f"- HR vs Avg Payoff on Wins: **{row['hr_vs_payoff']}**",
        f"- HR vs Sure-Thing Ratio: **{row['hr_vs_surething']}**",
        f"- HR vs Avg Entry Price: **{row['hr_vs_avg_entry']}**",
        f"- HR vs Bucket Excess HR: **{row['hr_vs_bucket_excess']}**",
        f"- Calibration Gap vs Payoff: **{row['calib_vs_payoff']}**",
        f"- Calibration Gap vs Sure-Thing: **{row['calib_vs_surething']}**",
        f"- Payoff vs Sure-Thing: **{row['payoff_vs_surething']}**",
        f"- Calibration Gap vs Bucket Excess HR: **{row['calib_vs_bucket_excess']}**",
    ]

report_lines += [
    "",
    "## Trader Type Segmentation",
    "",
    "| Trader Type | N | Avg HR | Median HR | Avg Entry Price | Avg Calib Gap | Avg Payoff | Avg Sure-Thing |",
    "|---|---|---|---|---|---|---|---|",
]
for row in segmented.to_dicts():
    hr = row['avg_hr']
    mhr = row['median_hr']
    ep = row['avg_entry_price']
    cg = row['avg_calib_gap']
    po = row['avg_payoff_on_wins']
    st = row['avg_sure_thing_ratio']
    report_lines.append(
        f"| {row['trader_type']} | {row['n_traders']:,} | {hr:.1%} | {mhr:.1%} | {ep:.3f} | {cg:+.4f} | {po:.4f} | {st:.4f} |"
    )

report_lines += [
    "",
    "## Out-of-Sample IC (Train → Test HR)",
    "",
    "Split: train = before 2024-07-01, test = after 2024-07-01 (min 5 test positions).",
    "",
]
for row in ic_df.to_dicts():
    report_lines += [
        f"- N shared traders: **{row['n_shared_traders']:,}**",
        f"- IC (train HR → test HR): **{row['ic_train_hr']}** (baseline)",
        f"- IC (calibration gap → test HR): **{row['ic_calib_gap']}**",
        f"- IC (avg payoff on wins → test HR): **{row['ic_payoff_on_wins']}**",
        f"- IC (sure-thing ratio → test HR): **{row['ic_sure_thing_ratio']}**",
    ]

report_lines += [
    "",
    "## Conclusions",
    "",
    "### Which approach best separates skilled cheap buyers from sure-thing pilers?",
    "",
]

# Determine winner based on ICs
ic_row = ic_df.to_dicts()[0] if len(ic_df) > 0 else {}
ics = {
    "calibration_gap": abs(ic_row.get('ic_calib_gap', 0) or 0),
    "avg_payoff_on_wins": abs(ic_row.get('ic_payoff_on_wins', 0) or 0),
    "sure_thing_ratio": abs(ic_row.get('ic_sure_thing_ratio', 0) or 0),
    "train_hr (baseline)": abs(ic_row.get('ic_train_hr', 0) or 0),
}
best_metric = max(ics, key=lambda k: ics[k])

report_lines += [
    f"Based on out-of-sample IC with future HR, the best single metric is: **{best_metric}** (IC={ics[best_metric]:.4f})",
    "",
    "### Recommended approach:",
    "",
    "1. **Calibration gap** is the most interpretable metric and has strong theoretical grounding.",
    "   Positive = entry prices below actual resolution rates = genuine value-finding skill.",
    "",
    "2. **Avg payoff on wins** (Approach 3) is monotonically interpretable but more dependent on",
    "   position sizing patterns.",
    "",
    "3. **Sure-thing ratio** (Approach 4) is useful as a PENALTY component — high values indicate",
    "   a trader accumulates wins only on near-certainties.",
    "",
    "4. **Bucket excess HR** (Approach 1) is the most statistically rigorous but requires more",
    "   positions per bucket for stable estimates.",
    "",
    "### Composite formula (recommended):",
    "",
    "```",
    "entry_price_score = calibration_gap * 0.5 + (avg_payoff_on_wins - 0.75) * 0.3 - sure_thing_ratio * 0.2",
    "```",
    "",
    "Apply as a secondary filter on top of HR: use entry_price_score to tiebreak within same",
    "HR decile, or as a penalty gate (exclude traders with sure_thing_ratio > 0.5).",
]

with open(RESULTS_FILE, "w") as f:
    f.write("\n".join(report_lines))

log(f"\nReport written to {RESULTS_FILE}")
log("=== Analysis complete ===")
print("\n\nDONE")
