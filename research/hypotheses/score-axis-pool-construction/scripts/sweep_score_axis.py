"""Score-Axis Pool Construction Discovery Sweep.

Hypothesis:
    Two DISJOINT pools — Pool A (top-K by excess_hr) and Pool B (top-K by
    consistency_sharpe, EXCLUDING Pool A) — must BOTH fire in a market +
    same direction before we signal.

    Sports YES only (Politics dropped — Spearman 0.95 between axes = collinear).

Pre-mortem issues addressed:
    1. hold >= 4h filter MANDATORY (in-play contamination guard)
    2. Price-level-adjusted base rate (0.86 avg entry != tag base 41.2%)
    3. first_trade >= test_start (phantom test signal fix)

SELL dual-test:
    - BUY-only: only BUY YES positions (join yes_entry_data)
    - Directional: net YES positions from maker_positions (BUY YES + SELL NO routes)

All results are UPPER BOUNDS — vectorized backtests 20-40pp optimistic.

Parameters swept:
    K_each ∈ {25, 50, 100}
    N_a    ∈ {1, 2}
    N_b    ∈ {1, 2}
    Total: 12 combos × 2 SELL modes = 24 runs

Outputs:
    research/hypotheses/score-axis-pool-construction/discovery/results.json
    research/hypotheses/score-axis-pool-construction/discovery/analysis.md
    research/hypotheses/score-axis-pool-construction/config.toml (best params)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

REPO         = "/mnt/nvme/git/polymarket/polymarket"
TRAIN_CUTOFF = "2025-07-01"
TEST_START   = "2025-07-01"
TEST_END     = "2026-03-01"
LOG_FILE     = f"{REPO}/tmp/sweep_score_axis.log"

# ─── Logging ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ─── DuckDB init ─────────────────────────────────────────────────────────────

from research.db import db as get_db
con = get_db().con
log("DuckDB singleton ready.")

# ─── Step 1: Shared infrastructure tables ────────────────────────────────────

log("Creating shared tables...")

con.execute("""
CREATE OR REPLACE MACRO is_gambling_market_sap(slug) AS (
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

# Market tags (priority-ordered: Sports > Basketball > Soccer, etc.)
con.execute("""
CREATE OR REPLACE TABLE _sap_market_tags AS
WITH tag_ranked AS (
    SELECT m.condition_id, m.slug,
        et.label,
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
    JOIN event_tags et ON m.event_id = et.event_id
    WHERE NOT is_gambling_market_sap(m.slug)
      AND m.neg_risk = 0
),
tag_assigned AS (
    SELECT condition_id, slug,
           arg_min(label, tag_priority) AS primary_tag
    FROM tag_ranked
    GROUP BY condition_id, slug
)
SELECT * FROM tag_assigned;
""")

n_sport_mkts = con.execute("""
SELECT count(*) FROM _sap_market_tags WHERE primary_tag = 'Sports'
""").fetchone()[0]
log(f"Sports markets (non-gambling): {n_sport_mkts}")

# ─── Step 2: Training pool construction (pre-train-cutoff) ───────────────────

log("Building training pool (Sports YES, pre-2025-07-01)...")

# Base trader stats (training window)
con.execute(f"""
CREATE OR REPLACE TABLE _sap_train_base AS
WITH base AS (
    SELECT
        p.trader, p.condition_id,
        CAST(p.yes_won AS DOUBLE) AS correct,
        abs(p.net_usd) AS abs_net_usd,
        p.volume,
        CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction
    FROM maker_positions p
    JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
      AND p.volume > 0
      AND p.net_usd IS NOT NULL
),
tag_base AS (
    SELECT avg(correct) AS train_base_rate FROM base
),
trader_stats AS (
    SELECT
        b.trader,
        count(DISTINCT b.condition_id) AS n_markets,
        avg(b.correct) AS hit_rate,
        avg(b.abs_net_usd) AS avg_pos_size,
        avg(b.conviction) AS avg_conviction,
        median(b.abs_net_usd) AS median_pos_size,
        count(*) AS n_positions
    FROM base b
    GROUP BY b.trader
    HAVING count(DISTINCT b.condition_id) >= 20
       AND avg(b.conviction) >= 0.90
       AND count(*) < 10000
)
SELECT
    ts.trader, ts.n_markets, ts.hit_rate, ts.avg_pos_size,
    ts.avg_conviction, ts.median_pos_size, ts.n_positions,
    (ts.hit_rate - tb.train_base_rate) AS excess_hr,
    tb.train_base_rate AS tag_base_rate
FROM trader_stats ts CROSS JOIN tag_base tb
WHERE (ts.hit_rate - tb.train_base_rate) > 0;
""")

n_base = con.execute("SELECT count(*) FROM _sap_train_base").fetchone()[0]
log(f"Qualified traders (base): {n_base}")

# Monthly consistency/Sharpe (training window)
con.execute(f"""
CREATE OR REPLACE TABLE _sap_train_consistency AS
WITH monthly AS (
    SELECT
        p.trader,
        date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
        avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
        count(DISTINCT p.condition_id) AS n_pos
    FROM maker_positions p
    JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
    JOIN _sap_train_base t ON p.trader = t.trader
    WHERE mt.primary_tag = 'Sports'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
      AND p.volume > 0
    GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
    HAVING count(DISTINCT p.condition_id) >= 3
)
SELECT
    trader,
    count(*) AS n_months,
    avg(month_hr) AS mean_hr,
    CASE WHEN count(*) >= 2
         THEN avg(month_hr) / (COALESCE(stddev(month_hr), 0.0) + 0.05)
         ELSE 0.0 END AS consistency_sharpe
FROM monthly
GROUP BY trader;
""")

# Merged: excess_hr + consistency_sharpe
con.execute("""
CREATE OR REPLACE TABLE _sap_train_merged AS
SELECT
    b.trader, b.n_markets, b.hit_rate, b.excess_hr, b.avg_pos_size,
    COALESCE(c.consistency_sharpe, 0.0) AS consistency_sharpe,
    COALESCE(c.n_months, 0) AS n_months
FROM _sap_train_base b
LEFT JOIN _sap_train_consistency c ON b.trader = c.trader;
""")

n_merged = con.execute("SELECT count(*) FROM _sap_train_merged").fetchone()[0]
log(f"Merged pool: {n_merged} traders")

# Verify axis correlation (Spearman approximation: corr(rank_a, rank_b))
# Must pre-compute ranks in a subquery, then compute corr()
corr_row = con.execute("""
SELECT corr(rank_excess, rank_consistency) AS spearman_approx
FROM (
    SELECT
        percent_rank() OVER (ORDER BY excess_hr) AS rank_excess,
        percent_rank() OVER (ORDER BY consistency_sharpe) AS rank_consistency
    FROM _sap_train_merged
) sub
""").fetchone()
spearman_approx = float(corr_row[0]) if corr_row and corr_row[0] is not None else None
log(f"Axis correlation (Spearman approx): {spearman_approx:.3f}" if spearman_approx else "Axis correlation: N/A")


# ─── Step 3: Pool builders ────────────────────────────────────────────────────

def build_pools(k_each: int) -> tuple[list[str], list[str], dict]:
    """Build Pool A (top-K excess_hr) and Pool B (top-K consistency_sharpe, disjoint from A)."""
    # Pool A: top-K by excess_hr
    rows_a = con.execute(f"""
        SELECT trader FROM _sap_train_merged
        ORDER BY excess_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_a = [r[0].lower() for r in rows_a]

    # Pool B: top-K by consistency_sharpe, NOT in Pool A
    pool_a_set = set(pool_a)
    rows_b = con.execute(f"""
        SELECT trader FROM _sap_train_merged
        WHERE lower(trader) NOT IN ({', '.join(f"'{t}'" for t in pool_a_set)})
        ORDER BY consistency_sharpe DESC
        LIMIT {k_each}
    """).fetchall()
    pool_b = [r[0].lower() for r in rows_b]

    overlap = len(set(pool_a) & set(pool_b))
    stats = {
        "k_each": k_each,
        "pool_a_size": len(pool_a),
        "pool_b_size": len(pool_b),
        "overlap": overlap,
        "jaccard": 0.0,  # always 0 by construction
    }
    log(f"  Pools K={k_each}: A={len(pool_a)}, B={len(pool_b)}, overlap={overlap}")

    # Log top-5 each pool for spot check
    row_a = con.execute(f"""
        SELECT trader, excess_hr, consistency_sharpe, n_markets
        FROM _sap_train_merged WHERE lower(trader) IN ({', '.join(f"'{t}'" for t in pool_a[:5])})
        ORDER BY excess_hr DESC LIMIT 5
    """).fetchall()
    row_b = con.execute(f"""
        SELECT trader, excess_hr, consistency_sharpe, n_markets
        FROM _sap_train_merged WHERE lower(trader) IN ({', '.join(f"'{t}'" for t in pool_b[:5])})
        ORDER BY consistency_sharpe DESC LIMIT 5
    """).fetchall()
    log(f"    Pool A top-5 (excess_hr): {[(r[0][:10], f'hr={r[1]:.3f}', f'cs={r[2]:.2f}') for r in row_a]}")
    log(f"    Pool B top-5 (consistency): {[(r[0][:10], f'hr={r[1]:.3f}', f'cs={r[2]:.2f}') for r in row_b]}")

    return pool_a, pool_b, stats


# ─── Step 4: Signal simulation (market-level, hold>=4h, price-level base rate) ──

def simulate_score_axis(
    pool_a: list[str],
    pool_b: list[str],
    k_each: int,
    n_a: int,
    n_b: int,
    sell_mode: str,  # "buy_only" or "directional"
    tag_base_rate: float,
) -> dict:
    """
    Market-level cross-pool consensus for score-axis pools.

    CRITICAL FIXES vs prior cross-pool-consensus sweep:
    1. hold_hours >= 4 (in-play contamination guard)
    2. first_trade >= test_start (phantom test signal fix)
    3. Price-level base rate computed separately (not just tag base)
    4. Aggregate to market level (one row per condition_id)
    """
    if not pool_a or not pool_b:
        return {"error": "empty pool"}

    pool_a_set = {t.lower() for t in pool_a}
    pool_b_set = {t.lower() for t in pool_b}

    # Enforce disjoint (by construction B excludes A, but double-check)
    pool_a_excl = pool_a_set - pool_b_set
    pool_b_excl = pool_b_set - pool_a_set

    if not pool_a_excl or not pool_b_excl:
        return {"error": "pools overlap fully"}

    pa_list = ", ".join(f"'{t}'" for t in pool_a_excl)
    pb_list = ", ".join(f"'{t}'" for t in pool_b_excl)

    suffix = f"k{k_each}_na{n_a}_nb{n_b}_{sell_mode[:3]}"
    lbl = suffix[:50]

    # ── Positions per pool ───────────────────────────────────────────────────
    # BUY-only: join yes_entry_data (guarantees direct BUY YES, has entry price)
    # Directional: maker_positions (net YES positions including SELL NO route)
    if sell_mode == "buy_only":
        join_clause = "JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_expr  = "yed.price_x_vol / yed.volume"
        price_valid = "AND yed.volume > 0 AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99"
    else:
        # Directional: use yes_entry_data for price where available (LEFT JOIN),
        # fall back to abs(net_usd)/net_yes (approximation)
        join_clause = "LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_expr  = "COALESCE(yed.price_x_vol / NULLIF(yed.volume, 0), abs(p.net_usd) / NULLIF(p.net_yes, 0))"
        price_valid = "AND p.volume > 0"

    common_where = f"""
        mt.primary_tag = 'Sports'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.correct IS NOT NULL
      AND p.volume > 0
      AND p.resolved_at IS NOT NULL
      {price_valid}
    """

    # Pool A positions
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pa_{lbl} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        p.yes_won, p.resolved_at,
        {price_expr} AS entry_price
    FROM maker_positions p
    JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
    {join_clause}
    WHERE {common_where}
      AND lower(p.trader) IN ({pa_list})
      AND {price_expr} IS NOT NULL
      AND {price_expr} BETWEEN 0.01 AND 0.99
    """)

    # Pool B positions
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pb_{lbl} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        p.yes_won, p.resolved_at,
        {price_expr} AS entry_price
    FROM maker_positions p
    JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
    {join_clause}
    WHERE {common_where}
      AND lower(p.trader) IN ({pb_list})
      AND {price_expr} IS NOT NULL
      AND {price_expr} BETWEEN 0.01 AND 0.99
    """)

    cnt_a = con.execute(f"SELECT count(*) FROM _sap_pa_{lbl}").fetchone()[0]
    cnt_b = con.execute(f"SELECT count(*) FROM _sap_pb_{lbl}").fetchone()[0]
    if cnt_a == 0 or cnt_b == 0:
        return {"error": f"no test positions — Pool A: {cnt_a}, Pool B: {cnt_b}"}

    # ── Market-level aggregation: Nth entry per pool ──────────────────────────
    # Pool A: Nth trader entry (by chronological first_trade)
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pa_nth_{lbl} AS
    SELECT
        condition_id,
        first_trade AS a_signal_time,
        entry_price AS a_price,
        yes_won,
        resolved_at,
        ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rn,
        COUNT(*) OVER (PARTITION BY condition_id) AS total_a
    FROM _sap_pa_{lbl}
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pa_signal_{lbl} AS
    SELECT condition_id, a_signal_time, a_price, yes_won, resolved_at
    FROM _sap_pa_nth_{lbl}
    WHERE rn = {n_a} AND total_a >= {n_a}
    """)

    # Pool B: Nth trader entry
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pb_nth_{lbl} AS
    SELECT
        condition_id,
        first_trade AS b_signal_time,
        entry_price AS b_price,
        yes_won,
        resolved_at,
        ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rn,
        COUNT(*) OVER (PARTITION BY condition_id) AS total_b
    FROM _sap_pb_{lbl}
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_pb_signal_{lbl} AS
    SELECT condition_id, b_signal_time, b_price, yes_won, resolved_at
    FROM _sap_pb_nth_{lbl}
    WHERE rn = {n_b} AND total_b >= {n_b}
    """)

    # ── Cross-pool consensus: both pools must fire, same condition_id ─────────
    # Signal time = GREATEST(a_signal_time, b_signal_time) — latest confirmer
    # Fill price = confirmer's price
    # CRITICAL: hold_hours >= 4 (in-play contamination guard)
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_signals_{lbl} AS
    SELECT
        a.condition_id,
        GREATEST(a.a_signal_time, b.b_signal_time) AS signal_time,
        CASE WHEN a.a_signal_time > b.b_signal_time
             THEN a.a_price ELSE b.b_price END AS signal_price,
        a.yes_won,
        a.resolved_at,
        (epoch(a.resolved_at) - epoch(GREATEST(a.a_signal_time, b.b_signal_time))) / 3600.0 AS hold_hours,
        (epoch(b.b_signal_time) - epoch(a.a_signal_time)) / 3600.0 AS pool_gap_hours
    FROM _sap_pa_signal_{lbl} a
    JOIN _sap_pb_signal_{lbl} b ON a.condition_id = b.condition_id
    WHERE (epoch(a.resolved_at) - epoch(GREATEST(a.a_signal_time, b.b_signal_time))) / 3600.0 >= 4.0
    """)

    n_signals = con.execute(f"SELECT count(*) FROM _sap_signals_{lbl}").fetchone()[0]
    if n_signals == 0:
        return {
            "error": "no signals after hold>=4h filter",
            "cnt_a": cnt_a,
            "cnt_b": cnt_b,
        }

    # ── Aggregate metrics (market-level — one row per condition_id) ───────────
    row = con.execute(f"""
    SELECT
        count(*) AS n_signals,
        avg(CAST(yes_won AS DOUBLE)) AS hit_rate,
        avg(signal_price) AS avg_signal_price,
        avg(CASE WHEN yes_won THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl_usd,
        median(hold_hours) AS med_hold_hours,
        percentile_cont(0.25) WITHIN GROUP (ORDER BY hold_hours) AS p25_hold_hours,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY hold_hours) AS p75_hold_hours,
        avg(pool_gap_hours) AS avg_pool_gap_hours,
        median(pool_gap_hours) AS med_pool_gap_hours,
        sum(CASE WHEN pool_gap_hours > 0 THEN 1 ELSE 0 END) AS a_first_count,
        sum(CASE WHEN pool_gap_hours < 0 THEN 1 ELSE 0 END) AS b_first_count
    FROM _sap_signals_{lbl}
    """).fetchone()

    (n_sig, hr, avg_price, avg_pnl, med_hold, p25_hold, p75_hold,
     avg_gap, med_gap, a_first, b_first) = row

    # ── Price-level base rate ─────────────────────────────────────────────────
    # Bucket avg_signal_price into 0.05-wide bands, get population HR in that band
    # This is the CRITICAL fix from the pre-mortem
    if avg_price is not None:
        price_lo = max(0.0, round(float(avg_price) - 0.05, 2))
        price_hi = min(1.0, round(float(avg_price) + 0.05, 2))
        pl_base_row = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS price_level_base
        FROM maker_positions p
        JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND p.correct IS NOT NULL
          AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN {price_lo} AND {price_hi}
        """).fetchone()
        price_level_base = float(pl_base_row[0]) if pl_base_row and pl_base_row[0] is not None else None
    else:
        price_level_base = None

    excess_hr_tag   = float(hr) - tag_base_rate
    excess_hr_price = (float(hr) - price_level_base) if price_level_base is not None else None

    # Hold-time in days (for compounding score)
    med_hold_days = float(med_hold) / 24.0 if med_hold else None

    # Compounding score = excess_hr_tag × avg_edge_usd / median_hold_days
    if med_hold_days and med_hold_days > 0 and avg_pnl is not None:
        comp_score = round(excess_hr_tag * float(avg_pnl) / med_hold_days, 4)
    else:
        comp_score = None

    # Monthly breakdown
    months = con.execute(f"""
    SELECT
        date_trunc('month', CAST(signal_time AS TIMESTAMP)) AS month,
        count(*) AS n_signals,
        avg(CAST(yes_won AS DOUBLE)) AS hit_rate,
        avg(CASE WHEN yes_won THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl
    FROM _sap_signals_{lbl}
    GROUP BY date_trunc('month', CAST(signal_time AS TIMESTAMP))
    ORDER BY month
    """).fetchall()

    n_months_data = len(months)
    signals_per_month = float(n_signals) / max(n_months_data, 1) if n_months_data > 0 else 0.0

    result = {
        "k_each":    k_each,
        "n_a":       n_a,
        "n_b":       n_b,
        "sell_mode": sell_mode,
        "n_signals": int(n_signals),
        "signals_per_month": round(signals_per_month, 1),
        "hit_rate":  round(float(hr), 4),
        "tag_base_rate": round(tag_base_rate, 4),
        "excess_hr_tag_pp":   round(excess_hr_tag * 100, 2),
        "price_level_base_rate": round(price_level_base, 4) if price_level_base else None,
        "excess_hr_price_pp": round(excess_hr_price * 100, 2) if excess_hr_price is not None else None,
        "avg_signal_price": round(float(avg_price), 4) if avg_price else None,
        "avg_pnl_usd": round(float(avg_pnl), 4) if avg_pnl is not None else None,
        "med_hold_hours": round(float(med_hold), 1) if med_hold else None,
        "med_hold_days":  round(med_hold_days, 2) if med_hold_days else None,
        "p25_hold_hours": round(float(p25_hold), 1) if p25_hold else None,
        "p75_hold_hours": round(float(p75_hold), 1) if p75_hold else None,
        "avg_pool_gap_hours": round(float(avg_gap), 1) if avg_gap else None,
        "med_pool_gap_hours": round(float(med_gap), 1) if med_gap else None,
        "pct_a_first": round(float(a_first) / max(n_signals, 1), 3),
        "compounding_score": comp_score,
        "fragile": n_signals < 30,
        "monthly": [
            {
                "month": str(m[0])[:7],
                "n_signals": int(m[1]),
                "hit_rate": round(float(m[2]), 4),
                "avg_pnl": round(float(m[3]), 4),
            }
            for m in months
        ],
    }
    return result


# ─── Step 5: SANITY COMBO (K=50, N_a=1, N_b=1, BUY-only) ────────────────────

log("=" * 60)
log("STEP 0.5: SANITY COMBO — K=50, N_a=1, N_b=1, BUY-only")
log("=" * 60)

# Fetch tag base rate from test window
tag_base_row = con.execute(f"""
SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
FROM maker_positions p
JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
WHERE mt.primary_tag = 'Sports'
  AND p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
  AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
  AND p.correct IS NOT NULL
""").fetchone()
TAG_BASE_RATE = float(tag_base_row[0]) if tag_base_row and tag_base_row[0] is not None else 0.412
log(f"Sports YES tag base rate (test window): {TAG_BASE_RATE:.4f}")

pool_a_50, pool_b_50, pool_stats_50 = build_pools(50)

sanity = simulate_score_axis(
    pool_a_50, pool_b_50, k_each=50,
    n_a=1, n_b=1, sell_mode="buy_only",
    tag_base_rate=TAG_BASE_RATE,
)
log(f"Sanity result: {sanity}")

if "error" in sanity:
    log(f"SANITY ABORT: {sanity['error']}")
    log("Writing abort results and exiting.")
    abort_output = {
        "hypothesis": "score-axis-pool-construction",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": "no_signal",
        "verdict_note": f"Sanity combo aborted: {sanity['error']}",
        "note": "ALL RESULTS ARE UPPER BOUNDS.",
        "sanity_combo": sanity,
        "tag_base_rate": TAG_BASE_RATE,
        "pool_stats": pool_stats_50,
        "axis_correlation_spearman_approx": spearman_approx,
    }
    out_dir = Path(f"{REPO}/research/hypotheses/score-axis-pool-construction/discovery")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.json", "w") as f:
        json.dump(abort_output, f, indent=2, default=str)
    sys.exit(0)

if sanity.get("excess_hr_tag_pp", -999) <= 0:
    log(f"SANITY NO-GO: excess HR = {sanity.get('excess_hr_tag_pp', '?')}pp <= 0. Aborting.")
    abort_output = {
        "hypothesis": "score-axis-pool-construction",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": "no_signal",
        "verdict_note": f"HR below tag base rate. Excess HR = {sanity.get('excess_hr_tag_pp')}pp.",
        "note": "ALL RESULTS ARE UPPER BOUNDS.",
        "sanity_combo": sanity,
        "tag_base_rate": TAG_BASE_RATE,
        "pool_stats": pool_stats_50,
        "axis_correlation_spearman_approx": spearman_approx,
    }
    out_dir = Path(f"{REPO}/research/hypotheses/score-axis-pool-construction/discovery")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.json", "w") as f:
        json.dump(abort_output, f, indent=2, default=str)
    sys.exit(0)

log(f"SANITY PASS: excess HR = +{sanity['excess_hr_tag_pp']:.1f}pp, "
    f"price-adj excess = {sanity.get('excess_hr_price_pp', 'N/A')}pp, "
    f"N={sanity['n_signals']} signals. Proceeding to full sweep.")

# ─── Step 6: VERIFY PRIOR ART (cross-pool sweep filters) ─────────────────────

log("=" * 60)
log("VERIFYING PRIOR ART: was +16pp inflated by missing filters?")
log("=" * 60)

# Run K=50 N_a=1 N_b=1 BUY-only WITHOUT hold filter for direct comparison with prior art
# We need to re-run sanity pools without hold>=4h to get the unfiltered figure
prior_k = 50
prior_lbl = "k50_na1_nb1_buy_nohold"
pa_prior, pb_prior = pool_a_50, pool_b_50

pool_a_excl_p = {t.lower() for t in pa_prior} - {t.lower() for t in pb_prior}
pool_b_excl_p = {t.lower() for t in pb_prior} - {t.lower() for t in pa_prior}

pa_list_p = ", ".join(f"'{t}'" for t in pool_a_excl_p)
pb_list_p = ", ".join(f"'{t}'" for t in pool_b_excl_p)

for pool_name, plist in [("pa", pa_list_p), ("pb", pb_list_p)]:
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_{pool_name}_nohold AS
    SELECT p.condition_id, p.trader, p.first_trade,
           p.yes_won, p.resolved_at,
           yed.price_x_vol / yed.volume AS entry_price
    FROM maker_positions p
    JOIN _sap_market_tags mt ON p.condition_id = mt.condition_id
    JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.correct IS NOT NULL
      AND p.volume > 0
      AND yed.volume > 0
      AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99
      AND lower(p.trader) IN ({plist})
    """)

for pool_name in ["pa", "pb"]:
    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_{pool_name}_nth_nohold AS
    SELECT condition_id, first_trade AS signal_time, entry_price, yes_won, resolved_at,
           ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rn,
           COUNT(*) OVER (PARTITION BY condition_id) AS total
    FROM _sap_{pool_name}_nohold
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _sap_{pool_name}_sig_nohold AS
    SELECT condition_id, signal_time, entry_price AS price, yes_won, resolved_at
    FROM _sap_{pool_name}_nth_nohold
    WHERE rn = 1 AND total >= 1
    """)

# Cross-pool without hold filter
con.execute("""
CREATE OR REPLACE TABLE _sap_signals_nohold AS
SELECT a.condition_id,
       GREATEST(a.signal_time, b.signal_time) AS signal_time,
       CASE WHEN a.signal_time > b.signal_time THEN a.price ELSE b.price END AS signal_price,
       a.yes_won,
       a.resolved_at,
       (epoch(a.resolved_at) - epoch(GREATEST(a.signal_time, b.signal_time))) / 3600.0 AS hold_hours
FROM _sap_pa_sig_nohold a
JOIN _sap_pb_sig_nohold b ON a.condition_id = b.condition_id
WHERE hold_hours >= 0
""")

nohold_row = con.execute("""
SELECT count(*) AS n, avg(CAST(yes_won AS DOUBLE)) AS hr, avg(signal_price) AS ap
FROM _sap_signals_nohold
""").fetchone()
nohold_n   = int(nohold_row[0]) if nohold_row else 0
nohold_hr  = float(nohold_row[1]) if nohold_row and nohold_row[1] else 0
nohold_pr  = float(nohold_row[2]) if nohold_row and nohold_row[2] else 0

# With hold>=4h (use sanity result already computed)
sanity_hr_with_filter = sanity.get("hit_rate", 0)
sanity_excess_with_filter = sanity.get("excess_hr_tag_pp", 0)
sanity_n_with_filter  = sanity.get("n_signals", 0)

hold_filter_hr_delta = nohold_hr - sanity_hr_with_filter
hold_filter_n_delta  = nohold_n  - sanity_n_with_filter
hold_filter_pct_survived = (sanity_n_with_filter / nohold_n * 100) if nohold_n > 0 else 0

# Prior art comparison (cross-pool-consensus used N_a=N_b=1, K=50, BUY-only, NO hold filter)
prior_hr_no_filter     = 0.667  # from cross-pool-consensus score_axis N=1x1 BUY-only
prior_excess_no_filter = 0.334  # +33.4pp

log(f"Prior art (cross-pool-consensus K=50 N=1 BUY-only, NO hold filter): "
    f"HR={prior_hr_no_filter:.1%}, excess=+{prior_excess_no_filter:.1%}, N=3 (in prior)")
log(f"CURRENT K=50 N=1 BUY-only, NO hold filter: "
    f"HR={nohold_hr:.1%}, N={nohold_n}, avg_price={nohold_pr:.3f}")
log(f"CURRENT K=50 N=1 BUY-only, WITH hold>=4h:  "
    f"HR={sanity_hr_with_filter:.1%}, excess=+{sanity_excess_with_filter:.1f}pp, N={sanity_n_with_filter}")
log(f"Hold>=4h filter impact: HR delta={hold_filter_hr_delta:+.3f}, "
    f"signals survived: {sanity_n_with_filter}/{nohold_n} ({hold_filter_pct_survived:.0f}%)")


# ─── Step 7: FULL PARAMETER SWEEP ────────────────────────────────────────────

log("=" * 60)
log("FULL SWEEP: K ∈ {25, 50, 100}, N_a ∈ {1, 2}, N_b ∈ {1, 2}")
log("=" * 60)

K_VALUES  = [25, 50, 100]
N_A_VALUES = [1, 2]
N_B_VALUES = [1, 2]
SELL_MODES = ["buy_only", "directional"]

all_results = []
pool_cache: dict[int, tuple[list, list, dict]] = {}

t0 = time.time()

for k in K_VALUES:
    log(f"\n--- K={k} ---")
    if k not in pool_cache:
        pool_cache[k] = build_pools(k)
    pa, pb, pstats = pool_cache[k]

    for n_a in N_A_VALUES:
        for n_b in N_B_VALUES:
            for sell_mode in SELL_MODES:
                log(f"  K={k} N_a={n_a} N_b={n_b} [{sell_mode}]...")
                r = simulate_score_axis(pa, pb, k_each=k, n_a=n_a, n_b=n_b,
                                        sell_mode=sell_mode,
                                        tag_base_rate=TAG_BASE_RATE)
                if "error" not in r:
                    log(f"    N={r['n_signals']} | HR={r['hit_rate']:.1%} | "
                        f"excess_tag=+{r['excess_hr_tag_pp']:.1f}pp | "
                        f"excess_price={r.get('excess_hr_price_pp', 'N/A')}pp | "
                        f"hold={r.get('med_hold_hours', '?')}h | "
                        f"cs={r.get('compounding_score', 'N/A')}")
                else:
                    log(f"    ERROR: {r['error']}")
                all_results.append(r)

elapsed = time.time() - t0
log(f"\nFull sweep done in {elapsed:.1f}s")

# ─── Step 8: SENSITIVITY ANALYSIS ────────────────────────────────────────────

log("=" * 60)
log("SENSITIVITY ANALYSIS: Top-3 combos, K ± 25")
log("=" * 60)

# Rank by compounding score (excluding errors)
valid_results = [r for r in all_results if "error" not in r and r.get("compounding_score") is not None]
valid_results.sort(key=lambda r: r.get("compounding_score", -999), reverse=True)

top3 = valid_results[:3]
sensitivity_results = []

for combo in top3:
    k_base = combo["k_each"]
    n_a    = combo["n_a"]
    n_b    = combo["n_b"]
    sell   = combo["sell_mode"]
    base_cs = combo["compounding_score"]
    base_hr = combo["hit_rate"]

    log(f"\nSensitivity for K={k_base} N_a={n_a} N_b={n_b} [{sell}] (CS={base_cs}, HR={base_hr:.1%})")

    sens_entry = {
        "params": {"k_each": k_base, "n_a": n_a, "n_b": n_b, "sell_mode": sell},
        "base_cs": base_cs,
        "base_hr": base_hr,
        "perturbations": []
    }

    for k_delta in [-25, +25]:
        k_perturb = k_base + k_delta
        if k_perturb <= 0 or k_perturb > 200:
            continue
        if k_perturb not in pool_cache:
            pool_cache[k_perturb] = build_pools(k_perturb)
        pa_p, pb_p, _ = pool_cache[k_perturb]

        r_perturb = simulate_score_axis(pa_p, pb_p, k_each=k_perturb,
                                        n_a=n_a, n_b=n_b, sell_mode=sell,
                                        tag_base_rate=TAG_BASE_RATE)
        if "error" not in r_perturb:
            hr_delta = r_perturb["hit_rate"] - base_hr
            fragile = abs(hr_delta) > 0.05
            log(f"  K={k_perturb}: HR={r_perturb['hit_rate']:.1%} (delta={hr_delta:+.1%}) "
                f"{'FRAGILE' if fragile else 'ROBUST'}")
            sens_entry["perturbations"].append({
                "k_perturb": k_perturb,
                "hr": r_perturb["hit_rate"],
                "hr_delta_pp": round(hr_delta * 100, 2),
                "n_signals": r_perturb["n_signals"],
                "compounding_score": r_perturb.get("compounding_score"),
                "fragile": fragile,
            })
        else:
            log(f"  K={k_perturb}: ERROR {r_perturb['error']}")
            sens_entry["perturbations"].append({
                "k_perturb": k_perturb,
                "error": r_perturb["error"],
            })

    # Mark fragile if any perturbation changed HR > 5pp
    any_fragile = any(p.get("fragile", False) for p in sens_entry["perturbations"] if "error" not in p)
    sens_entry["fragile"] = any_fragile
    sensitivity_results.append(sens_entry)


# ─── Step 9: POOL STATS (Spearman, K=25/50/100) ──────────────────────────────

pool_stats_all = {}
for k, (pa, pb, pstats) in pool_cache.items():
    # Compute avg excess_hr and consistency_sharpe per pool
    if pa:
        pa_list_q = ", ".join(f"'{t}'" for t in pa[:200])
        stats_a = con.execute(f"""
            SELECT avg(excess_hr), avg(consistency_sharpe), count(*)
            FROM _sap_train_merged WHERE lower(trader) IN ({pa_list_q})
        """).fetchone()
    else:
        stats_a = (None, None, 0)

    if pb:
        pb_list_q = ", ".join(f"'{t}'" for t in pb[:200])
        stats_b = con.execute(f"""
            SELECT avg(excess_hr), avg(consistency_sharpe), count(*)
            FROM _sap_train_merged WHERE lower(trader) IN ({pb_list_q})
        """).fetchone()
    else:
        stats_b = (None, None, 0)

    pool_stats_all[f"k{k}"] = {
        "k_each": k,
        **pstats,
        "pool_a_avg_excess_hr": round(float(stats_a[0]), 4) if stats_a[0] else None,
        "pool_a_avg_consistency_sharpe": round(float(stats_a[1]), 4) if stats_a[1] else None,
        "pool_b_avg_excess_hr": round(float(stats_b[0]), 4) if stats_b[0] else None,
        "pool_b_avg_consistency_sharpe": round(float(stats_b[1]), 4) if stats_b[1] else None,
    }


# ─── Step 10: Top combos by sell mode ────────────────────────────────────────

def top_n(results: list[dict], sell_mode: str, n: int = 5) -> list[dict]:
    filtered = [r for r in results
                if "error" not in r
                and r.get("sell_mode") == sell_mode
                and r.get("compounding_score") is not None
                and r.get("n_signals", 0) >= 3]
    filtered.sort(key=lambda r: r.get("compounding_score", -999), reverse=True)
    for i, r in enumerate(filtered[:n]):
        r["rank"] = i + 1
    return filtered[:n]

top5_buy  = top_n(all_results, "buy_only",     5)
top5_dir  = top_n(all_results, "directional",  5)

log("\nTop-5 BUY-only (compounding score):")
for r in top5_buy:
    log(f"  #{r['rank']} K={r['k_each']} N_a={r['n_a']} N_b={r['n_b']}: "
        f"CS={r['compounding_score']:.4f} HR={r['hit_rate']:.1%} "
        f"excess_tag=+{r['excess_hr_tag_pp']:.1f}pp "
        f"excess_price={r.get('excess_hr_price_pp', 'N/A')}pp "
        f"N={r['n_signals']}")

log("\nTop-5 Directional (compounding score):")
for r in top5_dir:
    log(f"  #{r['rank']} K={r['k_each']} N_a={r['n_a']} N_b={r['n_b']}: "
        f"CS={r['compounding_score']:.4f} HR={r['hit_rate']:.1%} "
        f"excess_tag=+{r['excess_hr_tag_pp']:.1f}pp "
        f"excess_price={r.get('excess_hr_price_pp', 'N/A')}pp "
        f"N={r['n_signals']}")

# ─── Step 11: Structured output ──────────────────────────────────────────────

log("Saving results...")

out_dir = Path(f"{REPO}/research/hypotheses/score-axis-pool-construction/discovery")
out_dir.mkdir(parents=True, exist_ok=True)

# Determine verdict
all_valid = [r for r in all_results if "error" not in r and r.get("n_signals", 0) >= 10]
all_valid_cs = [r for r in all_valid if r.get("compounding_score") is not None]

if not all_valid_cs:
    verdict = "no_signal"
    verdict_note = "No combos with >= 10 signals and compounding_score. Universe too thin."
elif max(r["compounding_score"] for r in all_valid_cs) <= 0:
    verdict = "no_signal"
    verdict_note = "All compounding scores <= 0. Strategy loses money."
elif max(r.get("excess_hr_price_pp", -999) or -999 for r in all_valid_cs) <= 2.0:
    verdict = "no_signal"
    verdict_note = "Price-level-adjusted excess HR <= 2pp. No real alpha above entry price base rate."
elif max(r.get("excess_hr_tag_pp", 0) for r in all_valid_cs) >= 10.0 and max(r["n_signals"] for r in all_valid_cs) >= 20:
    verdict = "promising"
    verdict_note = f"Best tag excess HR = +{max(r.get('excess_hr_tag_pp', 0) for r in all_valid_cs):.1f}pp with >= 20 signals."
else:
    verdict = "marginal"
    verdict_note = f"Best CS = {max(r['compounding_score'] for r in all_valid_cs):.4f} but signals thin or excess HR < 10pp."

# Best combo overall
best_overall = all_valid_cs[0] if all_valid_cs else None

output = {
    "hypothesis": "score-axis-pool-construction",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "verdict": verdict,
    "verdict_note": verdict_note,
    "note": "ALL RESULTS ARE UPPER BOUNDS — vectorized backtests 20-40pp optimistic vs tick.",
    "prior_art_verification": {
        "source": "cross-pool-consensus/discovery/results.json",
        "claimed_excess_hr_pp": 33.4,
        "claimed_hr_pct": 66.7,
        "claimed_n_signals": 3,
        "claimed_sell_mode": "buy_only",
        "claimed_k": 50,
        "claimed_na_nb": "1x1",
        "without_hold_filter": {
            "n_signals": nohold_n,
            "hit_rate": round(nohold_hr, 4),
            "avg_price": round(nohold_pr, 4),
            "excess_hr_tag_pp": round((nohold_hr - TAG_BASE_RATE) * 100, 2),
        },
        "with_hold_ge_4h": {
            "n_signals": sanity_n_with_filter,
            "hit_rate": round(sanity_hr_with_filter, 4),
            "excess_hr_tag_pp": round(sanity_excess_with_filter, 2),
            "excess_hr_price_pp": sanity.get("excess_hr_price_pp"),
        },
        "hold_filter_impact": {
            "hr_delta_pp": round(hold_filter_hr_delta * 100, 2),
            "signals_dropped": hold_filter_n_delta,
            "pct_survived": round(hold_filter_pct_survived, 1),
        },
        "conclusion": (
            f"Prior art +33.4pp was based on N=3 signals (2025-07 to 2026-03, K=50 BUY-only). "
            f"New sweep with same K=50/N=1 BUY-only gives N={nohold_n} signals without hold filter. "
            f"After hold>=4h: N={sanity_n_with_filter} signals, excess_tag=+{sanity_excess_with_filter:.1f}pp. "
            f"Price-adjusted excess: {sanity.get('excess_hr_price_pp', 'N/A')}pp."
        ),
    },
    "universe": {
        "tag": "Sports",
        "direction": "YES",
        "date_range": [TEST_START, TEST_END],
        "train_cutoff": TRAIN_CUTOFF,
        "qualified_traders_total": n_merged,
        "axis_correlation_spearman_approx": round(spearman_approx, 4) if spearman_approx else None,
        "tag_base_rate_test_window": round(TAG_BASE_RATE, 4),
        "sports_markets_total": n_sport_mkts,
    },
    "pool_stats": pool_stats_all,
    "sanity_combo": sanity,
    "buy_only_results": {
        "top_combos": top5_buy,
        "all_results": [r for r in all_results if r.get("sell_mode") == "buy_only"],
    },
    "directional_results": {
        "top_combos": top5_dir,
        "all_results": [r for r in all_results if r.get("sell_mode") == "directional"],
    },
    "sensitivity_analysis": sensitivity_results,
    "sell_sensitivity_note": (
        "BUY-only uses yes_entry_data JOIN (direct BUY YES only). "
        "Directional uses maker_positions (includes SELL NO routes). "
        "Difference in signal count reflects how many Sports YES positions were entered via SELL NO route."
    ),
    "knowledge_captures": [
        "Price-level base rate at avg signal price band ±0.05 is computed from all Sports YES positions in test window.",
        "hold >= 4h filter MANDATORY for Sports — in-play contamination otherwise inflates HR by 10-30pp.",
        "Score-axis construction: Pool A = top-K excess_hr, Pool B = top-K consistency_sharpe EXCLUDING Pool A. Jaccard = 0 by construction.",
        "first_trade >= test_start filter prevents phantom test signals from training-window entries.",
    ],
    "spawned_ideas": [
        {
            "name": "sequential-score-axis-trigger",
            "priority": "LOW",
            "description": (
                "If Pool A (excess_hr) consistently fires hours before Pool B (consistency), "
                "use Pool A as an early alert and Pool B confirmation as actual entry trigger. "
                "Test temporal gap >= 2h between Pool A and Pool B firing."
            ),
        },
        {
            "name": "score-axis-no-direction-sports",
            "priority": "MEDIUM",
            "description": (
                "Apply score-axis AND-gate to Sports NO direction. "
                "Base rate for Sports NO = 58.8%, much higher than YES 41.2%. "
                "Dual-axis agreement on NO may produce higher-confidence signals."
            ),
        },
        {
            "name": "three-axis-pool",
            "priority": "LOW",
            "description": (
                "Add a third pool (Pool C) ranked by timing lead (earliest average entry percentile). "
                "Require all three axes to agree. Will further reduce volume but may increase precision."
            ),
        },
    ],
    "classifications_proposed": [
        {
            "name": "sports_yes_excess_hr_pool",
            "description": "Top-K Sports YES traders by excess_hr (raw hit rate above tag base rate)",
            "rule": "top-K from maker_positions Sports YES where excess_hr > 0 AND n_markets >= 20 AND conviction >= 0.90, ordered by excess_hr DESC",
        },
        {
            "name": "sports_yes_consistency_sharpe_pool",
            "description": "Top-K Sports YES traders by consistency_sharpe (monthly HR stability), NOT in excess_hr pool",
            "rule": "top-K from monthly HR Sharpe = mean_monthly_hr / (std_monthly_hr + 0.05), excluding excess_hr pool traders",
        },
    ],
}

with open(out_dir / "results.json", "w") as f:
    json.dump(output, f, indent=2, default=str)
log(f"Saved: {out_dir / 'results.json'}")

log("Script complete.")
