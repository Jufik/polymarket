"""
NO-Direction Sweep — edge-weighted-skill hypothesis.

Analyzes the 14,915 NO-skilled traders identified in decomposition sweep.

Steps:
1. Score NO-direction traders (train through Oct 2025)
2. Build NO-direction pools: K = {50, 100, 200}
3. Consensus sweep: N = {1, 2, 3} for each K
   - Signal = N distinct NO-pool traders enter NO side of same market
   - Entry price = Nth trader's actual entry price (causal, not avg)
   - Phantom filter: first_trade >= test_start
4. Per-tag breakdown: Politics, Esports, Sports, Crypto
5. Compare YES vs NO pool performance

All results are UPPER BOUNDS (vectorized).
"""

import json
import sys
import os

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG = "/mnt/nvme/git/polymarket/polymarket/tmp/no_direction_sweep.log"
OUT_JSON = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.json"
OUT_MD = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.md"

os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
os.makedirs("/mnt/nvme/git/polymarket/polymarket/tmp", exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")

with open(LOG, "w") as f:
    f.write("")

log("=== NO-Direction Sweep Starting ===")

from research.db import db
d = db()
con = d.con

# ─── Config ──────────────────────────────────────────────────────────────────
TRAIN_END   = "2025-11-01"   # train through Oct 2025 (exclusive upper bound)
TEST_START  = "2025-11-01"   # test window begins Nov 2025
TEST_END    = "2026-02-01"   # 3 months: Nov, Dec, Jan

TOP_TAGS = [
    "Politics", "Sports", "Basketball", "Soccer", "Esports",
    "NBA", "Crypto", "NCAA", "Tennis", "NFL"
]
FOCUS_TAGS = ["Politics", "Esports", "Sports", "Crypto"]

log(f"Train through: {TRAIN_END} | Test: {TEST_START} → {TEST_END}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0: Non-gambling markets with canonical tags
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 0] Building tag-market index...")

con.execute("""
CREATE OR REPLACE TABLE _no_tag_mkts AS
WITH tag_ranked AS (
    SELECT m.condition_id, m.slug, m.event_id, m.neg_risk, et.label,
        CASE
            WHEN et.label = 'Politics'    THEN 0
            WHEN et.label = 'Elections'   THEN 1
            WHEN et.label = 'Sports'      THEN 2
            WHEN et.label = 'Basketball'  THEN 3
            WHEN et.label = 'Soccer'      THEN 4
            WHEN et.label = 'Esports'     THEN 5
            WHEN et.label = 'NBA'         THEN 6
            WHEN et.label = 'Crypto'      THEN 7
            WHEN et.label = 'NCAA'        THEN 8
            WHEN et.label = 'Tennis'      THEN 9
            WHEN et.label = 'NFL'         THEN 10
            ELSE 999
        END AS tag_priority
    FROM markets m
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    WHERE m.slug NOT LIKE '%updown%' AND m.slug NOT LIKE '%up-or-down%'
      AND et.label IN (
          'Politics','Sports','Basketball','Soccer','Esports',
          'NBA','Crypto','NCAA','Tennis','NFL'
      )
      AND m.neg_risk = 0
),
tag_assigned AS (
    SELECT condition_id, slug, arg_min(label, tag_priority) AS primary_tag
    FROM tag_ranked GROUP BY condition_id, slug
)
SELECT * FROM tag_assigned
""")
n_mkts = con.execute("SELECT count(*) FROM _no_tag_mkts").fetchone()[0]
log(f"  Tagged markets: {n_mkts:,}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: NO base rate grid (price_bucket × tag) — TRAINING window only
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 1] Building NO base rate grid (train window)...")

con.execute(f"""
CREATE OR REPLACE TABLE _no_base_rate_grid AS
WITH no_positions AS (
    SELECT
        tm.primary_tag,
        -- For NO entries: entry_price in NO-space = 1 - YES entry price
        FLOOR((1.0 - COALESCE(ye.avg_entry_price, 0.5)) / 0.05) * 0.05 AS price_bucket_low,
        p.correct
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    LEFT JOIN (
        SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
        FROM yes_entry_data
    ) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
    WHERE p.position = 'NO'
      AND p.resolved_at IS NOT NULL
      AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10   -- conviction filter
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND (1.0 - COALESCE(ye.avg_entry_price, 0.5)) >= 0.05
      AND (1.0 - COALESCE(ye.avg_entry_price, 0.5)) < 1.0
)
SELECT
    primary_tag,
    price_bucket_low,
    ROUND(price_bucket_low + 0.05, 2) AS price_bucket_high,
    count(*) AS n_positions,
    avg(correct::DOUBLE) AS no_hr
FROM no_positions
GROUP BY primary_tag, price_bucket_low
HAVING count(*) >= 10
ORDER BY primary_tag, price_bucket_low
""")
n_cells = con.execute("SELECT count(*) FROM _no_base_rate_grid").fetchone()[0]
log(f"  Grid cells: {n_cells}")

sample = con.execute("""
    SELECT primary_tag, price_bucket_low, price_bucket_high, n_positions, no_hr
    FROM _no_base_rate_grid WHERE primary_tag = 'Politics'
    ORDER BY price_bucket_low
""").fetchdf()
log(f"  Politics NO base rates:\n{sample.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Score NO-direction traders on TRAINING window
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 2] Scoring NO-direction traders (train window)...")

con.execute(f"""
CREATE OR REPLACE TABLE _no_trader_scores AS
WITH no_positions AS (
    SELECT
        p.trader,
        tm.primary_tag,
        p.condition_id,
        p.correct,
        p.net_usd,
        p.volume,
        p.first_trade,
        -- NO-entry price in NO-space
        CASE
            WHEN ye.avg_entry_price IS NOT NULL
            THEN 1.0 - ye.avg_entry_price
            ELSE 0.5
        END AS no_entry_price
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    LEFT JOIN (
        SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
        FROM yes_entry_data
    ) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
    WHERE p.position = 'NO'
      AND p.resolved_at IS NOT NULL
      AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
),
bucketed AS (
    SELECT *,
           FLOOR(no_entry_price / 0.05) * 0.05 AS price_bucket_low
    FROM no_positions
    WHERE no_entry_price >= 0.05 AND no_entry_price < 1.0
),
with_base AS (
    SELECT b.*, g.no_hr AS base_hr, b.correct::DOUBLE - g.no_hr AS edge
    FROM bucketed b
    LEFT JOIN _no_base_rate_grid g
        ON b.primary_tag = g.primary_tag AND b.price_bucket_low = g.price_bucket_low
    WHERE g.no_hr IS NOT NULL
),
trader_agg AS (
    SELECT
        trader,
        count(*) AS n_total,
        avg(correct::DOUBLE) AS overall_hr,
        avg(base_hr) AS weighted_base_rate,
        avg(correct::DOUBLE) - avg(base_hr) AS excess_hr,
        -- Bucket excess HR: weighted average of per-bucket edge
        sum(edge) / count(*) AS bucket_excess_hr,
        avg(abs(net_usd) * correct::DOUBLE - abs(net_usd) * (1.0 - correct::DOUBLE)) AS avg_edge_usd,
        stddev(correct::DOUBLE) AS hr_std,
        -- Distinct markets traded (for volume-weight denominator)
        count(DISTINCT condition_id) AS n_markets
    FROM with_base
    GROUP BY trader
    HAVING count(*) >= 20
)
SELECT
    trader, n_total, n_markets, overall_hr, weighted_base_rate, excess_hr,
    bucket_excess_hr, avg_edge_usd, hr_std,
    CASE WHEN overall_hr > 0
         THEN 1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)
         ELSE 0 END AS consistency,
    -- Composite score (volume-weighted)
    (excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + bucket_excess_hr * 0.15) * ln(n_total + 1) AS composite_score_vw
FROM trader_agg
""")

n_qualified = con.execute("SELECT count(*) FROM _no_trader_scores").fetchone()[0]
log(f"  NO-direction qualified traders (>=20 positions, train): {n_qualified:,}")

score_dist = con.execute("""
    SELECT
        percentile_cont(0.50) WITHIN GROUP (ORDER BY composite_score_vw) AS p50,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY composite_score_vw) AS p75,
        percentile_cont(0.90) WITHIN GROUP (ORDER BY composite_score_vw) AS p90,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY composite_score_vw) AS p95,
        max(composite_score_vw) AS max_score,
        avg(overall_hr) AS avg_hr,
        avg(bucket_excess_hr) AS avg_beh
    FROM _no_trader_scores
""").fetchone()
log(f"  Score dist: p50={score_dist[0]:.3f}, p75={score_dist[1]:.3f}, p90={score_dist[2]:.3f}, p95={score_dist[3]:.3f}, max={score_dist[4]:.3f}")
log(f"  Avg HR={score_dist[5]:.4f}, avg BEH={score_dist[6]:.4f}")

# Show top-20
top20 = con.execute("""
    SELECT trader, n_total, overall_hr, excess_hr, bucket_excess_hr, composite_score_vw
    FROM _no_trader_scores ORDER BY composite_score_vw DESC LIMIT 20
""").fetchdf()
log(f"\n  Top-20 NO traders:\n{top20.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Build pools K = {50, 100, 200}
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 3] Building NO pools...")

for K in [50, 100, 200]:
    con.execute(f"""
        CREATE OR REPLACE TABLE _no_pool_{K} AS
        SELECT trader, n_total, overall_hr, excess_hr, bucket_excess_hr, composite_score_vw
        FROM _no_trader_scores ORDER BY composite_score_vw DESC LIMIT {K}
    """)
    pool_hr = con.execute(f"SELECT avg(overall_hr), avg(bucket_excess_hr) FROM _no_pool_{K}").fetchone()
    log(f"  Pool K={K}: avg HR={pool_hr[0]:.4f}, avg BEH={pool_hr[1]:.4f}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Consensus sweep — market-level aggregation (vectorized counting unit)
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 4] Consensus sweep (vectorized, UPPER BOUNDS)...")

# CRITICAL: aggregate to market level BEFORE computing metrics
# Signal count = count(DISTINCT condition_id), not count(*) over trader-positions
# Hold time = max(first_trade across consensus traders) → resolved_at
# Entry price = Nth trader's entry price = max(first_trade) → that trader's NO entry price

# Test window NO base rate (for excess HR computation)
test_no_base = con.execute(f"""
    SELECT avg(correct::DOUBLE) FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    WHERE p.position = 'NO'
      AND p.resolved_at IS NOT NULL
      AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
""").fetchone()[0]
log(f"  Test window NO base rate: {test_no_base:.4f}")

results = {}

for K in [50, 100, 200]:
    results[K] = {}
    for N in [1, 2, 3]:
        log(f"\n  K={K}, N={N}...")

        # PHANTOM FILTER: first_trade >= TEST_START ensures only copyable entries
        # MARKET-LEVEL: one row per condition_id, signal_entry = max(first_trade)
        # ENTRY PRICE: from the Nth (last to enter) trader's NO-space price
        sweep = con.execute(f"""
        WITH pool_positions AS (
            -- All NO positions by pool traders in test window
            SELECT
                p.trader,
                p.condition_id,
                tm.primary_tag,
                p.correct,
                p.yes_won,
                p.first_trade,
                p.resolved_at,
                -- NO-entry price in NO-space (what we pay)
                CASE
                    WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price
                    ELSE 0.5
                END AS no_entry_price
            FROM maker_positions p
            JOIN _no_pool_{K} pool ON p.trader = pool.trader
            JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
            LEFT JOIN (
                SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
                FROM yes_entry_data
            ) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
            WHERE p.position = 'NO'
              AND p.resolved_at IS NOT NULL
              AND p.volume > 0
              AND abs(p.net_usd) / p.volume >= 0.10
              -- Test window resolution
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
              -- PHANTOM FILTER: only copyable entries
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
        ),
        market_level AS (
            -- Aggregate to MARKET level: one row per condition_id
            SELECT
                condition_id,
                primary_tag,
                first(correct) AS correct,
                first(yes_won) AS yes_won,
                first(resolved_at) AS resolved_at,
                count(DISTINCT trader) AS n_pool_traders,
                -- Signal entry = when Nth trader enters (max first_trade)
                max(first_trade) AS signal_entry,
                -- Entry price = Nth trader's NO price (max first_trade → that entry)
                -- Use arg_max to get the no_entry_price of the last-to-enter trader
                arg_max(no_entry_price, first_trade) AS signal_entry_price,
                -- Hold time
                date_diff('hour', max(first_trade), first(resolved_at)) AS hold_hours,
                date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
            FROM pool_positions
            GROUP BY condition_id, primary_tag
            -- Consensus threshold
            HAVING count(DISTINCT trader) >= {N}
        ),
        with_pnl AS (
            SELECT *,
                -- NO position wins when yes_won = 0 (NO wins = YES loses)
                -- correct field already accounts for this in maker_positions
                -- For NO: correct=1 means NO won, pnl = 1/price - 1 per unit
                -- Simplified: edge = correct - signal_entry_price (breakeven at entry price)
                CASE
                    WHEN correct = 1 THEN (1.0 - signal_entry_price)  -- profit on $1 bet
                    ELSE -signal_entry_price                            -- loss on $1 bet
                END AS edge_per_dollar,
                -- Hold filter: keep only markets with hold >= 0 and <= 720 hours (30 days)
                hold_hours >= 0 AND hold_hours <= 720 AS valid_hold,
                -- Price quality: exclude near-certain positions
                signal_entry_price >= 0.05 AND signal_entry_price < 0.95 AS valid_price
            FROM market_level
        )
        SELECT
            count(*) AS n_signals,
            avg(correct::DOUBLE) AS hit_rate,
            -- Test window NO base rate excess
            avg(correct::DOUBLE) - {test_no_base:.6f} AS excess_hr,
            median(signal_entry_price) AS median_entry_price,
            median(hold_days) AS median_hold_days,
            median(hold_hours) AS median_hold_hours,
            avg(edge_per_dollar) AS avg_edge_per_dollar,
            -- PnL proxy: sum of edge per dollar (each market = 1 unit bet)
            sum(edge_per_dollar) AS total_edge_units,
            sum(CASE WHEN valid_hold AND valid_price THEN 1 ELSE 0 END) AS n_valid_signals,
            avg(CASE WHEN valid_hold AND valid_price THEN correct::DOUBLE ELSE NULL END) AS hr_valid
        FROM with_pnl
        """).fetchone()

        if sweep and sweep[0] and sweep[0] > 0:
            log(f"    n_signals={sweep[0]}, HR={sweep[1]:.4f}, excess_HR={sweep[2]:.4f}")
            log(f"    med_entry={sweep[3]:.4f}, med_hold_days={sweep[4]}, avg_edge/dollar={sweep[6]:.4f}")
            hr_valid_str = f"{sweep[9]:.4f}" if sweep[9] is not None else "N/A"
            log(f"    n_valid={sweep[8]}, HR_valid={hr_valid_str}")
        else:
            log(f"    No signals")

        results[K][N] = {
            "n_signals": int(sweep[0]) if sweep[0] else 0,
            "hit_rate": round(float(sweep[1]), 4) if sweep[1] else None,
            "excess_hr": round(float(sweep[2]), 4) if sweep[2] else None,
            "median_entry_price": round(float(sweep[3]), 4) if sweep[3] else None,
            "median_hold_days": float(sweep[4]) if sweep[4] else None,
            "median_hold_hours": float(sweep[5]) if sweep[5] else None,
            "avg_edge_per_dollar": round(float(sweep[6]), 4) if sweep[6] else None,
            "total_edge_units": round(float(sweep[7]), 2) if sweep[7] else None,
            "n_valid_signals": int(sweep[8]) if sweep[8] else 0,
            "hr_valid": round(float(sweep[9]), 4) if sweep[9] else None,
        }

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: Per-tag breakdown for best K
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 5] Per-tag breakdown (K=100, N=2)...")

K_FOCUS = 100
N_FOCUS = 2

# Tag-specific NO base rates in test window
tag_base_rates = con.execute(f"""
    SELECT tm.primary_tag, avg(p.correct::DOUBLE) AS no_base_rate, count(*) AS n
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    WHERE p.position = 'NO'
      AND p.resolved_at IS NOT NULL
      AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
    GROUP BY tm.primary_tag
    ORDER BY n DESC
""").fetchdf()
log(f"  Tag NO base rates:\n{tag_base_rates.to_string()}")

tag_base_map = dict(zip(tag_base_rates["primary_tag"], tag_base_rates["no_base_rate"]))

per_tag_results = {}
for tag in FOCUS_TAGS:
    base = tag_base_map.get(tag, test_no_base)
    sweep_tag = con.execute(f"""
    WITH pool_positions AS (
        SELECT
            p.trader, p.condition_id, tm.primary_tag, p.correct, p.yes_won,
            p.first_trade, p.resolved_at,
            CASE WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price ELSE 0.5 END AS no_entry_price
        FROM maker_positions p
        JOIN _no_pool_{K_FOCUS} pool ON p.trader = pool.trader
        JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
        LEFT JOIN (
            SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
            FROM yes_entry_data
        ) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
        WHERE p.position = 'NO'
          AND p.resolved_at IS NOT NULL AND p.volume > 0
          AND abs(p.net_usd) / p.volume >= 0.10
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND tm.primary_tag = '{tag}'
    ),
    market_level AS (
        SELECT condition_id, first(correct) AS correct, first(resolved_at) AS resolved_at,
               count(DISTINCT trader) AS n_pool_traders,
               max(first_trade) AS signal_entry,
               arg_max(no_entry_price, first_trade) AS signal_entry_price,
               date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
        FROM pool_positions
        GROUP BY condition_id
        HAVING count(DISTINCT trader) >= {N_FOCUS}
    )
    SELECT count(*) AS n, avg(correct::DOUBLE) AS hr, avg(correct::DOUBLE) - {base:.6f} AS excess,
           median(signal_entry_price) AS med_price, median(hold_days) AS med_hold,
           avg(CASE WHEN correct = 1 THEN (1.0 - signal_entry_price) ELSE -signal_entry_price END) AS avg_edge
    FROM market_level
    WHERE hold_days >= 0 AND hold_days <= 30
    """).fetchone()

    log(f"\n  {tag} (base={base:.4f}):")
    if sweep_tag and sweep_tag[0] > 0:
        log(f"    n={sweep_tag[0]}, HR={sweep_tag[1]:.4f}, excess={sweep_tag[2]:.4f}, med_price={sweep_tag[3]:.4f}, med_hold={sweep_tag[4]}d, avg_edge={sweep_tag[5]:.4f}")
    else:
        log(f"    No signals")

    per_tag_results[tag] = {
        "no_base_rate": round(float(base), 4),
        "n_signals": int(sweep_tag[0]) if sweep_tag else 0,
        "hit_rate": round(float(sweep_tag[1]), 4) if sweep_tag and sweep_tag[1] else None,
        "excess_hr": round(float(sweep_tag[2]), 4) if sweep_tag and sweep_tag[2] else None,
        "median_entry_price": round(float(sweep_tag[3]), 4) if sweep_tag and sweep_tag[3] else None,
        "median_hold_days": float(sweep_tag[4]) if sweep_tag and sweep_tag[4] else None,
        "avg_edge_per_dollar": round(float(sweep_tag[5]), 4) if sweep_tag and sweep_tag[5] else None,
    }

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: Monthly signal breakdown (Nov, Dec, Jan) for K=100, N=2
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 6] Monthly breakdown (K=100, N=2)...")

monthly = con.execute(f"""
WITH pool_positions AS (
    SELECT
        p.trader, p.condition_id, p.correct, p.first_trade, p.resolved_at,
        CASE WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price ELSE 0.5 END AS no_entry_price,
        strftime(p.resolved_at, '%Y-%m') AS res_month
    FROM maker_positions p
    JOIN _no_pool_100 pool ON p.trader = pool.trader
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    LEFT JOIN (
        SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
        FROM yes_entry_data
    ) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
    WHERE p.position = 'NO'
      AND p.resolved_at IS NOT NULL AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
),
market_level AS (
    SELECT res_month, condition_id, first(correct) AS correct,
           count(DISTINCT trader) AS n_traders,
           max(first_trade) AS signal_entry,
           arg_max(no_entry_price, first_trade) AS signal_entry_price,
           date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
    FROM pool_positions
    GROUP BY res_month, condition_id
    HAVING count(DISTINCT trader) >= 2
      AND date_diff('day', max(first_trade), first(resolved_at)) >= 0
      AND date_diff('day', max(first_trade), first(resolved_at)) <= 30
)
SELECT res_month,
       count(*) AS n_signals,
       avg(correct::DOUBLE) AS hit_rate,
       median(signal_entry_price) AS med_price,
       median(hold_days) AS med_hold_days,
       avg(CASE WHEN correct = 1 THEN (1.0 - signal_entry_price) ELSE -signal_entry_price END) AS avg_edge
FROM market_level
GROUP BY res_month ORDER BY res_month
""").fetchdf()
log(f"  Monthly breakdown:\n{monthly.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7: YES vs NO comparison
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 7] YES vs NO comparison (from decomposition results)...")

# YES comparison: load from existing results
yes_result_file = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/decomposition_results.json"
with open(yes_result_file, "r") as f:
    decomp = json.load(f)

# Quick YES pool stats from decomposition
yes_base_rate = con.execute(f"""
    SELECT avg(p.correct::DOUBLE)
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    WHERE p.position = 'YES'
      AND p.resolved_at IS NOT NULL
      AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
""").fetchone()[0]
log(f"  Test window YES base rate: {yes_base_rate:.4f}")
log(f"  Test window NO base rate: {test_no_base:.4f}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8: Compounding score for best configs
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Step 8] Compounding scores...")

best_configs = []
for K in [50, 100, 200]:
    for N in [1, 2, 3]:
        r = results[K][N]
        if r["n_signals"] > 0 and r["excess_hr"] and r["avg_edge_per_dollar"] and r["median_hold_days"]:
            hold = max(r["median_hold_days"], 0.5)  # avoid divide by zero
            cs = r["excess_hr"] * r["avg_edge_per_dollar"] / hold if hold > 0 else 0
            best_configs.append({
                "K": K, "N": N,
                "n_signals": r["n_signals"],
                "hit_rate": r["hit_rate"],
                "excess_hr": r["excess_hr"],
                "avg_edge_per_dollar": r["avg_edge_per_dollar"],
                "median_hold_days": r["median_hold_days"],
                "compounding_score": round(cs, 6),
            })
            log(f"  K={K}, N={N}: CS={cs:.6f} (excess={r['excess_hr']:.4f}, edge/dollar={r['avg_edge_per_dollar']:.4f}, hold={hold:.1f}d)")

best_configs.sort(key=lambda x: x["compounding_score"], reverse=True)

# ─────────────────────────────────────────────────────────────────────────────
# Save JSON
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Saving JSON]...")

result_json = {
    "metadata": {
        "train_end": TRAIN_END,
        "test_start": TEST_START,
        "test_end": TEST_END,
        "n_qualified_no_traders": n_qualified,
        "test_no_base_rate": round(float(test_no_base), 4),
        "test_yes_base_rate": round(float(yes_base_rate), 4),
    },
    "pool_metrics": {
        str(K): {
            "avg_hr": round(float(con.execute(f"SELECT avg(overall_hr) FROM _no_pool_{K}").fetchone()[0]), 4),
            "avg_beh": round(float(con.execute(f"SELECT avg(bucket_excess_hr) FROM _no_pool_{K}").fetchone()[0]), 4),
        }
        for K in [50, 100, 200]
    },
    "consensus_sweep": {
        str(K): {
            str(N): results[K][N]
            for N in [1, 2, 3]
        }
        for K in [50, 100, 200]
    },
    "per_tag_breakdown": per_tag_results,
    "monthly_breakdown": monthly.to_dict(orient="records"),
    "compounding_scores": best_configs,
    "tag_no_base_rates": tag_base_map,
}

with open(OUT_JSON, "w") as f:
    json.dump(result_json, f, indent=2)
log(f"JSON saved: {OUT_JSON}")

# ─────────────────────────────────────────────────────────────────────────────
# Save Markdown
# ─────────────────────────────────────────────────────────────────────────────
log("\n[Saving Markdown]...")

md_lines = [
    "# NO-Direction Sweep Results — edge-weighted-skill",
    "",
    "> **All results are UPPER BOUNDS** — vectorized, not tick-by-tick validated.",
    "> Phantom filter applied: `first_trade >= test_start`. Market-level aggregation enforced.",
    "",
    f"Train through: {TRAIN_END} | Test: {TEST_START} → {TEST_END} (3 months)",
    "",
    "## Overview",
    "",
    f"- NO-direction qualified traders (train, >=20 NO positions): **{n_qualified:,}**",
    f"- Test window NO base rate: **{test_no_base:.4f}** ({test_no_base*100:.1f}%)",
    f"- Test window YES base rate: **{yes_base_rate:.4f}** ({yes_base_rate*100:.1f}%)",
    "",
    "## Pool Metrics",
    "",
    "| K | avg HR | avg BEH |",
    "|---|--------|---------|",
]

for K in [50, 100, 200]:
    pm = result_json["pool_metrics"][str(K)]
    md_lines.append(f"| {K} | {pm['avg_hr']:.4f} | {pm['avg_beh']:.4f} |")

md_lines += [
    "",
    "## Consensus Sweep Results",
    "",
    "Signal = N distinct pool traders enter NO side of same market.",
    "Entry price = Nth trader's actual NO entry price (causal, max first_trade).",
    "",
    "| K | N | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ | Total Edge |",
    "|---|---|-----------|----|-----------|-----------|---------|-----------:|-----------|",
]

for K in [50, 100, 200]:
    for N in [1, 2, 3]:
        r = results[K][N]
        if r["n_signals"] > 0 and r["hit_rate"] is not None:
            hold_str = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "0d"
            md_lines.append(
                f"| {K} | {N} | {r['n_signals']} | {r['hit_rate']:.4f} | "
                f"{r['excess_hr']:+.4f} | {r['median_entry_price']:.4f} | "
                f"{hold_str} | {r['avg_edge_per_dollar']:+.4f} | "
                f"{r['total_edge_units']:+.1f} |"
            )
        else:
            md_lines.append(f"| {K} | {N} | 0 | — | — | — | — | — | — |")

md_lines += [
    "",
    "## Per-Tag Breakdown (K=100, N=2)",
    "",
    f"NO base rates in test window {TEST_START}–{TEST_END}:",
    "",
    "| Tag | NO Base Rate | n_signals | HR | Excess HR | Med Entry | Med Hold | Avg Edge/$ |",
    "|-----|-------------|-----------|----|-----------|-----------|---------|-----------:|",
]

for tag in FOCUS_TAGS:
    r = per_tag_results[tag]
    if r["n_signals"] > 0 and r["hit_rate"] is not None:
        hold_str = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "0d"
        md_lines.append(
            f"| {tag} | {r['no_base_rate']:.4f} | {r['n_signals']} | "
            f"{r['hit_rate']:.4f} | {r['excess_hr']:+.4f} | "
            f"{r['median_entry_price']:.4f} | {hold_str} | "
            f"{r['avg_edge_per_dollar']:+.4f} |"
        )
    else:
        md_lines.append(f"| {tag} | {r['no_base_rate']:.4f} | 0 | — | — | — | — | — |")

md_lines += [
    "",
    "## Monthly Signal Breakdown (K=100, N=2)",
    "",
    monthly.to_string(index=False),
    "",
    "## Compounding Scores (Top Configs)",
    "",
    "Formula: `excess_hr × avg_edge_per_dollar / median_hold_days`",
    "",
    "| K | N | n_signals | HR | Excess HR | Avg Edge/$ | Hold | Compounding Score |",
    "|---|---|-----------|----|-----------|-----------:|------|------------------:|",
]

for cfg in best_configs[:10]:
    hold_str = f"{cfg['median_hold_days']:.0f}d" if cfg["median_hold_days"] is not None else "0d"
    md_lines.append(
        f"| {cfg['K']} | {cfg['N']} | {cfg['n_signals']} | {cfg['hit_rate']:.4f} | "
        f"{cfg['excess_hr']:+.4f} | {cfg['avg_edge_per_dollar']:+.4f} | "
        f"{hold_str} | **{cfg['compounding_score']:.6f}** |"
    )

md_lines += [
    "",
    "## YES vs NO Comparison",
    "",
    "| Metric | YES Pool | NO Pool |",
    "|--------|----------|---------|",
    f"| Test base rate | {yes_base_rate:.4f} | {test_no_base:.4f} |",
    f"| Qualified traders | from decomp | {n_qualified:,} |",
    f"| Dominant direction | Politics YES | Sports/Esports NO |",
    "",
    "## Key Findings",
    "",
    "1. **NO-direction is where the volume of skill lives**: 14,915 NO-skilled traders vs 3,677 YES-skilled.",
    "",
    "2. **Tag asymmetry confirmed**: Esports almost pure NO (+0.446 BEH), Politics YES-dominated.",
    "",
    "3. **In-play contamination risk**: Sports/Basketball/Soccer signals may have hold < 24h.",
    "   These are UPPER BOUNDS — tick validation must apply hold filter.",
    "",
    "4. **ALL results are vectorized upper bounds**. Expected 20-40pp tick degradation.",
    "",
    "## Next Steps",
    "",
    "- Tick validation on best NO config",
    "- Hold-time filter ≥ 24h for Sports tags",
    "- Compare NO pool overlap with elite whale copy pool",
]

with open(OUT_MD, "w") as f:
    f.write("\n".join(md_lines))
log(f"Markdown saved: {OUT_MD}")
log("\n=== NO-Direction Sweep Complete ===")

print("\n" + "="*60)
print("NO-DIRECTION SWEEP COMPLETE")
print("="*60)
print(f"  Qualified NO traders: {n_qualified:,}")
print(f"  Test NO base rate: {test_no_base:.4f}")
print()
print("  Consensus sweep summary:")
for K in [50, 100, 200]:
    for N in [1, 2, 3]:
        r = results[K][N]
        if r["n_signals"] > 0:
            print(f"    K={K}, N={N}: {r['n_signals']} signals, HR={r['hit_rate']:.4f}, excess={r['excess_hr']:+.4f}")
print()
if best_configs:
    print(f"  Best config: K={best_configs[0]['K']}, N={best_configs[0]['N']}, CS={best_configs[0]['compounding_score']:.6f}")
print(f"\n  Output: {OUT_JSON}")
print(f"  Output: {OUT_MD}")
