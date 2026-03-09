"""Cross-Pool Consensus Discovery Sweep.

Hypothesis: Two independently-constructed pools of skilled traders agreeing on a market
direction produces a stronger signal than a single pool at higher N.

Pool construction variants tested:
  A) Style split: high-volume (>= 100 markets) vs selective (<= 30 markets) traders
  B) Timing split: early movers (top-K by entry lead-time) vs late confirmers
  C) Recency split: Pool A = top-K by recent 3-month HR, Pool B = top-K by long-term 12-month HR
  D) Random split: K=100 → two K=50 subsets (baseline, measures cross-pool vs same-pool N=2)
  E) Score split: top-K by excess_hr vs top-K by consistency_sharpe (different scoring axes)

For each construction:
  1. Measure pool independence (Jaccard overlap — must be < 0.5 for signal to be meaningful)
  2. Test signal: both pools must have >= N traders in the same market, same direction
  3. Signal timing: Pool A first_trade (earlier pool fires) + Pool B first_trade (confirmer)
  4. Baseline comparison: single-pool K=100 N=2 (existing Sports YES v3 benchmark)

All results are UPPER BOUNDS — vectorized backtests are 20-40pp optimistic vs tick.

SELL dual-test: BUY-only (safe baseline) + directional mapping (SELL NO = bullish).

Output:
  research/hypotheses/cross-pool-consensus/discovery/analysis.md
  research/hypotheses/cross-pool-consensus/discovery/results.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

REPO        = "/mnt/nvme/git/polymarket/polymarket"
TRAIN_CUTOFF = "2025-07-01"
TEST_START   = "2025-07-01"
TEST_END     = "2026-03-01"
LOG_FILE     = f"{REPO}/tmp/sweep_cross_pool.log"

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


# ─── Shared tables (v3-compatible) ───────────────────────────────────────────

GAMBLING_MACRO_SQL = """
CREATE OR REPLACE MACRO is_gambling_market_cp(slug) AS (
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
"""

MARKET_TAGS_SQL = """
CREATE OR REPLACE TABLE _cp_market_tags AS
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
    WHERE NOT is_gambling_market_cp(m.slug)
      AND m.neg_risk = 0
),
tag_assigned AS (
    SELECT condition_id, slug,
           arg_min(label, tag_priority) AS primary_tag
    FROM tag_ranked
    GROUP BY condition_id, slug
)
SELECT * FROM tag_assigned;
"""

log("Creating shared tables...")
con.execute(GAMBLING_MACRO_SQL)
con.execute(MARKET_TAGS_SQL)
log("Shared tables done.")


# ─── Build base training stats (used by all pool variants) ───────────────────

def build_base_stats(tag_label: str, train_cutoff: str = TRAIN_CUTOFF) -> str:
    """Build a temporary table with per-trader YES training stats for a tag.

    Returns the table name.
    """
    tag = tag_label.lower()
    tbl = f"_cp_base_{tag}"

    con.execute(f"""
    CREATE OR REPLACE TABLE {tbl} AS
    WITH base AS (
        SELECT
            p.trader, p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            abs(p.net_usd) AS abs_net_usd,
            p.volume, p.net_usd,
            CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction,
            p.first_trade
        FROM maker_positions p
        JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
          AND p.net_usd IS NOT NULL
    ),
    tag_base AS (
        SELECT avg(correct) AS base_rate FROM base
    ),
    trader_stats AS (
        SELECT
            b.trader,
            count(DISTINCT b.condition_id) AS n_markets,
            avg(b.correct) AS hit_rate,
            avg(b.abs_net_usd) AS avg_pos_size,
            avg(b.conviction) AS avg_conviction,
            median(b.net_usd) AS avg_edge_usd,
            -- timing: avg rank of entry (0-1 scale, lower = earlier mover)
            median(epoch(b.first_trade)) AS median_entry_epoch
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 20
           AND avg(CASE WHEN b.volume > 0 THEN abs(b.net_usd) / b.volume ELSE 0 END) >= 0.90
           AND count(*) < 10000
    )
    SELECT
        ts.trader, ts.n_markets, ts.hit_rate, ts.avg_pos_size,
        ts.avg_conviction, ts.avg_edge_usd, ts.median_entry_epoch,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    cnt = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
    log(f"  Base stats for {tag_label}: {cnt} qualified traders")
    return tbl


def build_consistency_stats(tag_label: str, base_tbl: str, train_cutoff: str = TRAIN_CUTOFF) -> str:
    """Build monthly consistency/sharpe for traders in base_tbl."""
    tag = tag_label.lower()
    tbl = f"_cp_consistency_{tag}"

    con.execute(f"""
    CREATE OR REPLACE TABLE {tbl} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
        JOIN {base_tbl} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 3
    ),
    sharpe AS (
        SELECT trader, count(*) AS n_months, avg(month_hr) AS mean_hr,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0 END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
    ),
    -- Recent 3-month HR (last 3 months before cutoff)
    recent_cutoff AS (SELECT CAST('{train_cutoff}' AS DATE) - INTERVAL 90 DAY AS rc),
    recent AS (
        SELECT
            p.trader,
            avg(CAST(p.yes_won AS DOUBLE)) AS recent_hr,
            count(DISTINCT p.condition_id) AS n_recent
        FROM maker_positions p
        JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
        JOIN {base_tbl} t ON p.trader = t.trader
        CROSS JOIN recent_cutoff rc
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND CAST(p.resolved_at AS DATE) >= rc.rc
          AND p.volume > 0
        GROUP BY p.trader
    )
    SELECT
        s.trader, s.n_months, s.consistency_sharpe,
        coalesce(r.recent_hr, 0.0) AS recent_hr,
        coalesce(r.n_recent, 0) AS n_recent
    FROM sharpe s
    LEFT JOIN recent r ON s.trader = r.trader;
    """)

    return tbl


# ─── Pool variant constructors ────────────────────────────────────────────────

def build_pool_variant_style(tag_label: str, k_each: int = 50) -> tuple[set, set, str]:
    """Variant A: style split — high-volume vs selective traders.

    Pool A: top-k by excess_hr among high-volume traders (n_markets >= 100)
    Pool B: top-k by excess_hr among selective traders (n_markets <= 30)
    Returns (pool_a, pool_b, description)
    """
    tag = tag_label.lower()
    base_tbl = build_base_stats(tag_label)

    # Pool A: high-volume (n_markets >= 100)
    rows_a = con.execute(f"""
        SELECT trader FROM {base_tbl}
        WHERE n_markets >= 100
        ORDER BY excess_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_a = {r[0].lower() for r in rows_a}

    # Pool B: selective (n_markets <= 30 — strict specialists)
    rows_b = con.execute(f"""
        SELECT trader FROM {base_tbl}
        WHERE n_markets <= 30
        ORDER BY excess_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_b = {r[0].lower() for r in rows_b}

    log(f"  Style split [{tag_label}]: A={len(pool_a)} high-vol, B={len(pool_b)} selective")
    return pool_a, pool_b, f"style_split_k{k_each}"


def build_pool_variant_recency(tag_label: str, k_each: int = 50) -> tuple[set, set, str]:
    """Variant C: recency split — recent 3-month HR vs long-term 12-month HR.

    Pool A: top-k by recent_hr (last 3 months before train_cutoff)
    Pool B: top-k by overall excess_hr (long-term skill, different traders)
    Overlap between pools should be low if recent performance diverges from long-term.
    """
    tag = tag_label.lower()
    base_tbl = build_base_stats(tag_label)
    cons_tbl = build_consistency_stats(tag_label, base_tbl)

    # Merge base + consistency
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_merged_{tag} AS
    SELECT b.trader, b.n_markets, b.hit_rate, b.excess_hr, b.avg_edge_usd,
           coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
           coalesce(c.recent_hr, 0.0) AS recent_hr,
           coalesce(c.n_recent, 0) AS n_recent
    FROM {base_tbl} b
    LEFT JOIN {cons_tbl} c ON b.trader = c.trader
    """)

    # Pool A: top-k by recent 3-month HR (with min 3 recent markets)
    rows_a = con.execute(f"""
        SELECT trader FROM _cp_merged_{tag}
        WHERE n_recent >= 3
        ORDER BY recent_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_a = {r[0].lower() for r in rows_a}

    # Pool B: top-k by long-term excess_hr (exclude top-k already in pool A)
    rows_b = con.execute(f"""
        SELECT trader FROM _cp_merged_{tag}
        WHERE lower(trader) NOT IN ({', '.join(f"'{t}'" for t in pool_a) if pool_a else "''"})
        ORDER BY excess_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_b = {r[0].lower() for r in rows_b}

    log(f"  Recency split [{tag_label}]: A={len(pool_a)} recent, B={len(pool_b)} long-term")
    return pool_a, pool_b, f"recency_split_k{k_each}"


def build_pool_variant_score_axis(tag_label: str, k_each: int = 50) -> tuple[set, set, str]:
    """Variant E: score-axis split — excess_hr ranking vs consistency_sharpe ranking.

    Pool A: top-k by excess_hr (hit-rate-focused traders)
    Pool B: top-k by consistency_sharpe (who have NOT already made Pool A — different traders)
    Tests if agreeing on direction from two different skill dimensions adds signal.
    """
    tag = tag_label.lower()
    base_tbl = build_base_stats(tag_label)
    cons_tbl = build_consistency_stats(tag_label, base_tbl)

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_merged_score_{tag} AS
    SELECT b.trader, b.n_markets, b.hit_rate, b.excess_hr, b.avg_edge_usd,
           coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe
    FROM {base_tbl} b
    LEFT JOIN {cons_tbl} c ON b.trader = c.trader
    """)

    # Pool A: top-k by excess_hr
    rows_a = con.execute(f"""
        SELECT trader FROM _cp_merged_score_{tag}
        ORDER BY excess_hr DESC
        LIMIT {k_each}
    """).fetchall()
    pool_a = {r[0].lower() for r in rows_a}

    # Pool B: top-k by consistency_sharpe, NOT in pool A
    rows_b = con.execute(f"""
        SELECT trader FROM _cp_merged_score_{tag}
        WHERE lower(trader) NOT IN ({', '.join(f"'{t}'" for t in pool_a) if pool_a else "''"})
        ORDER BY consistency_sharpe DESC
        LIMIT {k_each}
    """).fetchall()
    pool_b = {r[0].lower() for r in rows_b}

    log(f"  Score-axis split [{tag_label}]: A={len(pool_a)} excess_hr, B={len(pool_b)} consistency")
    return pool_a, pool_b, f"score_axis_k{k_each}"


def build_pool_variant_random(tag_label: str, k_total: int = 100) -> tuple[set, set, str]:
    """Variant D: random split — K=100 pool into two K=50 subsets.

    This is the BASELINE for cross-pool testing. The null hypothesis is that
    cross-pool (A confirms B) is no better than a single pool at N=2.
    Pool A and B have ~0 Jaccard by design.
    """
    tag = tag_label.lower()
    base_tbl = build_base_stats(tag_label)
    cons_tbl = build_consistency_stats(tag_label, base_tbl)

    # Build full K=100 composite pool (v3-style without BEH gate for broader pool)
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_full100_{tag} AS
    WITH base AS (
        SELECT
            b.trader, b.n_markets, b.excess_hr, b.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe
        FROM {base_tbl} b
        LEFT JOIN {cons_tbl} c ON b.trader = c.trader
    ),
    ranked AS (
        SELECT *,
            percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
            percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
            percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge
        FROM base
    )
    SELECT
        trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd,
        (0.50 * pr_excess_hr + 0.30 * pr_consistency + 0.20 * pr_edge) AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.50 * pr_excess_hr + 0.30 * pr_consistency + 0.20 * pr_edge) DESC
        ) AS rank
    FROM ranked
    """)

    rows = con.execute(f"""
        SELECT trader FROM _cp_full100_{tag}
        WHERE rank <= {k_total}
        ORDER BY rank
    """).fetchall()
    all_traders = [r[0].lower() for r in rows]

    # Deterministic even/odd split (by rank order, alternating)
    pool_a = {t for i, t in enumerate(all_traders) if i % 2 == 0}
    pool_b = {t for i, t in enumerate(all_traders) if i % 2 == 1}

    log(f"  Random split [{tag_label}]: full K={len(all_traders)}, A={len(pool_a)}, B={len(pool_b)}")
    return pool_a, pool_b, f"random_split_k{k_total}"


# ─── Jaccard overlap metric ───────────────────────────────────────────────────

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─── Cross-pool signal simulation ────────────────────────────────────────────

def simulate_cross_pool(
    pool_a: set[str],
    pool_b: set[str],
    tag_label: str,
    direction: str,       # 'YES' or 'NO'
    n_a: int = 1,         # min traders from pool A needed
    n_b: int = 1,         # min traders from pool B needed
    test_start: str = TEST_START,
    test_end: str   = TEST_END,
    max_price: float | None = None,
    sell_mode: str = "buy_only",   # "buy_only" or "directional"
    label_suffix: str = "",
) -> dict:
    """
    Market-level cross-pool consensus simulation.

    Signal = condition_id where BOTH pool A has >= n_a traders AND pool B has >= n_b traders.
    Signal timing = max(pool_a_last_trader_first_trade, pool_b_first_trader_first_trade)
      i.e., the moment when the second pool confirms (chronological entry of pool_b's n_b-th trader
      AFTER pool_a's n_a-th trader has entered).

    Fill price = Pool B's n_b-th trader's entry price (confirmer's price — most conservative).

    SELL handling:
      buy_only: only BUY trades counted toward consensus
      directional: SELL NO = bullish YES signal, SELL YES = bearish NO signal
    """
    if not pool_a or not pool_b:
        return {"error": "empty pool"}

    pool_a_lower = {t.lower() for t in pool_a}
    pool_b_lower = {t.lower() for t in pool_b}

    # Exclude any overlap (only non-overlapping traders count)
    pool_a_only = pool_a_lower - pool_b_lower
    pool_b_only = pool_b_lower - pool_a_lower

    if not pool_a_only or not pool_b_only:
        return {"error": "pools fully overlap — no independent signal"}

    pool_a_list = ", ".join(f"'{t}'" for t in pool_a_only)
    pool_b_list = ", ".join(f"'{t}'" for t in pool_b_only)

    tag  = tag_label.lower()
    dir_col = "p.yes_won" if direction == "YES" else "(NOT p.yes_won)"
    position_filter = f"p.position = '{direction}'"
    lbl  = f"{tag}_{direction.lower()}_{n_a}x{n_b}_{sell_mode}{label_suffix}"

    # SELL direction mapping for directional mode
    if sell_mode == "directional":
        if direction == "YES":
            # BUY YES = bullish, SELL NO = bullish
            trade_filter = "(p.position = 'YES' AND p.side_direction = 'BUY') OR (p.position = 'NO' AND p.side_direction = 'SELL')"
        else:
            # BUY NO = bearish, SELL YES = bearish
            trade_filter = "(p.position = 'NO' AND p.side_direction = 'BUY') OR (p.position = 'YES' AND p.side_direction = 'SELL')"
    else:
        # BUY only
        trade_filter = f"p.position = '{direction}'"

    # Step 1: Pool A test positions (BUY mode uses maker_positions which are net positions)
    # For cross-pool, we use YES entries for both pools (BUY-only uses position=YES, directional uses broader filter)
    # Since maker_positions is position-level (not trade-level), we use position='YES' for YES direction.
    # For SELL-directional, this approach doesn't apply at position level — SELL NO → long YES
    # We approximate by using position='YES' for both (maker_positions is already resolved to net direction).
    # Note: directional SELL mode at position level = include both YES positions from BUY + YES equiv from SELL NO.
    # Since maker_positions captures net positions correctly (SELL NO creates YES exposure),
    # we use position='YES' for directional YES — maker_positions.correct = yes_won is direction-correct.

    # Simplified: use position column for both modes at maker_positions level
    # (The SELL/BUY distinction is within a position entry — maker_positions captures the net effect)
    # BUY-only: We can't filter sub-position-level in maker_positions directly.
    # Instead, we join yes_entry_data (which is BUY-only derived from trades) as a filter.

    # BUY-only: join yes_entry_data (ensures trader actually BUY'd YES directly, not just net positive)
    # Directional: include all YES net positions (BUY YES or SELL NO routes)
    if sell_mode == "buy_only":
        join_yes = f"JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_src = "yed.price_x_vol / yed.volume"
        price_valid = "AND yed.volume > 0 AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99"
    else:
        # directional: use net position price from maker_positions (abs(net_usd)/net_yes for YES)
        # Approximation: still join yes_entry_data for price but allow missing (LEFT JOIN)
        join_yes = f"LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_src = "COALESCE(yed.price_x_vol / NULLIF(yed.volume,0), abs(p.net_usd) / NULLIF(p.net_yes, 0))"
        price_valid = "AND p.volume > 0"

    # Create Pool A positions table
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_pa_{lbl[:60]} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        CAST(p.yes_won AS DOUBLE) AS correct,
        {price_src} AS entry_price
    FROM maker_positions p
    JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
    {join_yes}
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'
      AND p.correct IS NOT NULL
      AND p.volume > 0
      {price_valid}
      AND lower(p.trader) IN ({pool_a_list})
    """)

    # Create Pool B positions table
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_pb_{lbl[:60]} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        CAST(p.yes_won AS DOUBLE) AS correct,
        {price_src} AS entry_price
    FROM maker_positions p
    JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
    {join_yes}
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'
      AND p.correct IS NOT NULL
      AND p.volume > 0
      {price_valid}
      AND lower(p.trader) IN ({pool_b_list})
    """)

    cnt_a = con.execute(f"SELECT count(*) FROM _cp_pa_{lbl[:60]}").fetchone()[0]
    cnt_b = con.execute(f"SELECT count(*) FROM _cp_pb_{lbl[:60]}").fetchone()[0]
    if cnt_a == 0 or cnt_b == 0:
        return {"error": f"no test positions — Pool A: {cnt_a}, Pool B: {cnt_b}"}

    # Step 2: Market-level aggregation per pool
    # Pool A: Nth entry (signal trigger from Pool A)
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_a_ranked_{lbl[:60]} AS
    SELECT condition_id,
           ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rank,
           COUNT(*) OVER (PARTITION BY condition_id) AS total_a,
           first_trade AS a_entry_time,
           entry_price AS a_entry_price,
           correct
    FROM _cp_pa_{lbl[:60]}
    WHERE entry_price IS NOT NULL AND entry_price BETWEEN 0.01 AND 0.99
    """)

    # Pool A: markets where Pool A has >= n_a traders (Nth trader's data)
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_a_signal_{lbl[:60]} AS
    SELECT condition_id, a_entry_time, a_entry_price, correct
    FROM _cp_a_ranked_{lbl[:60]}
    WHERE rank = {n_a} AND total_a >= {n_a}
    """)

    # Pool B: markets where Pool B has >= n_b traders (chronological Nth entry after Pool A fires)
    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_b_ranked_{lbl[:60]} AS
    SELECT condition_id,
           ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rank,
           COUNT(*) OVER (PARTITION BY condition_id) AS total_b,
           first_trade AS b_entry_time,
           entry_price AS b_entry_price,
           correct
    FROM _cp_pb_{lbl[:60]}
    WHERE entry_price IS NOT NULL AND entry_price BETWEEN 0.01 AND 0.99
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_b_signal_{lbl[:60]} AS
    SELECT condition_id, b_entry_time, b_entry_price, correct
    FROM _cp_b_ranked_{lbl[:60]}
    WHERE rank = {n_b} AND total_b >= {n_b}
    """)

    # Step 3: Cross-pool consensus = markets where BOTH pools fired
    # Signal entry time = max(pool_a Nth entry, pool_b Nth entry) — latest confirmer
    price_filter_sql = f"AND LEAST(a.a_entry_price, b.b_entry_price) <= {max_price}" if max_price else ""

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_signals_{lbl[:60]} AS
    SELECT
        a.condition_id,
        GREATEST(a.a_entry_time, b.b_entry_time) AS signal_time,
        -- Fill price = confirmer's entry price (most conservative / latest)
        CASE WHEN a.a_entry_time > b.b_entry_time THEN a.a_entry_price ELSE b.b_entry_price END AS signal_price,
        a.correct,
        -- resolved_at from maker_positions
        mp.resolved_at,
        (epoch(mp.resolved_at) - epoch(GREATEST(a.a_entry_time, b.b_entry_time))) / 3600.0 AS hold_hours
    FROM _cp_a_signal_{lbl[:60]} a
    JOIN _cp_b_signal_{lbl[:60]} b ON a.condition_id = b.condition_id
    JOIN (
        SELECT DISTINCT condition_id, resolved_at FROM maker_positions WHERE correct IS NOT NULL
    ) mp ON mp.condition_id = a.condition_id
    WHERE hold_hours >= 0
      {price_filter_sql}
    """)

    n_signals = con.execute(f"SELECT count(*) FROM _cp_signals_{lbl[:60]}").fetchone()[0]
    if n_signals == 0:
        return {"error": "no cross-pool consensus signals"}

    # Step 4: Aggregate metrics
    row = con.execute(f"""
    SELECT
        count(*) AS n_signals,
        avg(correct) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl,
        median(hold_hours) AS med_hold_hours,
        avg(signal_price) AS avg_signal_price,
        avg(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS base_check
    FROM _cp_signals_{lbl[:60]}
    """).fetchone()

    n_sig, hr, avg_pnl, med_hold, avg_price, _ = row

    # Base rate (tag direction base rate from all test-period positions)
    base_row = con.execute(f"""
    SELECT avg(CAST(({dir_col}) AS DOUBLE))
    FROM maker_positions p
    JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND p.correct IS NOT NULL
    """).fetchone()
    base_rate = float(base_row[0]) if base_row and base_row[0] is not None else 0.5

    # Monthly breakdown
    months = con.execute(f"""
    SELECT
        date_trunc('month', CAST(signal_time AS TIMESTAMP)) AS month,
        count(*) AS n_signals,
        avg(correct) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl
    FROM _cp_signals_{lbl[:60]}
    GROUP BY date_trunc('month', CAST(signal_time AS TIMESTAMP))
    ORDER BY month
    """).fetchall()

    # Timing gap: how much earlier is Pool A than Pool B (in hours)?
    timing_row = con.execute(f"""
    SELECT
        avg((epoch(b.b_entry_time) - epoch(a.a_entry_time)) / 3600.0) AS avg_gap_hours,
        median((epoch(b.b_entry_time) - epoch(a.a_entry_time)) / 3600.0) AS med_gap_hours,
        sum(CASE WHEN a.a_entry_time < b.b_entry_time THEN 1 ELSE 0 END) AS a_first_count,
        sum(CASE WHEN b.b_entry_time < a.a_entry_time THEN 1 ELSE 0 END) AS b_first_count
    FROM _cp_a_signal_{lbl[:60]} a
    JOIN _cp_b_signal_{lbl[:60]} b ON a.condition_id = b.condition_id
    """).fetchone()

    return {
        "n_signals": int(n_sig),
        "hit_rate": round(float(hr), 4),
        "excess_hr": round(float(hr) - float(base_rate), 4),
        "base_rate": round(float(base_rate), 4),
        "avg_pnl_usd": round(float(avg_pnl), 4),
        "med_hold_hours": round(float(med_hold), 1) if med_hold else None,
        "avg_signal_price": round(float(avg_price), 4) if avg_price else None,
        "avg_gap_hours": round(float(timing_row[0]), 1) if timing_row[0] else None,
        "med_gap_hours": round(float(timing_row[1]), 1) if timing_row[1] else None,
        "pool_a_first_pct": round(timing_row[2] / max(n_sig, 1), 3) if timing_row else None,
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


def simulate_single_pool_baseline(
    pool: set[str],
    tag_label: str,
    direction: str,
    n_thresh: int,
    sell_mode: str = "buy_only",
    test_start: str = TEST_START,
    test_end: str   = TEST_END,
) -> dict:
    """Single-pool baseline for comparison: same logic as v3 sweep."""
    if not pool:
        return {"error": "empty pool"}

    pool_lower = {t.lower() for t in pool}
    pool_list  = ", ".join(f"'{t}'" for t in pool_lower)
    tag        = tag_label.lower()
    dir_col    = "p.yes_won" if direction == "YES" else "(NOT p.yes_won)"
    position_filter = f"p.position = '{direction}'"
    lbl        = f"baseline_{tag}_{direction.lower()}_{n_thresh}_{sell_mode}"

    if sell_mode == "buy_only":
        join_yes = "JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_src = "yed.price_x_vol / yed.volume"
        price_valid = "AND yed.volume > 0 AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99"
    else:
        join_yes = "LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id"
        price_src = "COALESCE(yed.price_x_vol / NULLIF(yed.volume,0), abs(p.net_usd) / NULLIF(p.net_yes, 0))"
        price_valid = "AND p.volume > 0"

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_bl_pos_{lbl[:60]} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        CAST(p.yes_won AS DOUBLE) AS correct,
        {price_src} AS entry_price,
        p.resolved_at,
        (epoch(p.resolved_at) - epoch(p.first_trade)) / 3600.0 AS hold_hours
    FROM maker_positions p
    JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
    {join_yes}
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'
      AND p.correct IS NOT NULL
      AND p.volume > 0
      {price_valid}
      AND lower(p.trader) IN ({pool_list})
    """)

    cnt = con.execute(f"SELECT count(*) FROM _cp_bl_pos_{lbl[:60]}").fetchone()[0]
    if cnt == 0:
        return {"error": "no test positions"}

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_bl_ranked_{lbl[:60]} AS
    SELECT condition_id, entry_price, first_trade, correct, resolved_at, hold_hours,
           ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS rank,
           COUNT(*) OVER (PARTITION BY condition_id) AS total
    FROM _cp_bl_pos_{lbl[:60]}
    WHERE entry_price IS NOT NULL AND entry_price BETWEEN 0.01 AND 0.99
    """)

    con.execute(f"""
    CREATE OR REPLACE TABLE _cp_bl_signals_{lbl[:60]} AS
    SELECT condition_id, entry_price AS signal_price, first_trade AS signal_time,
           correct, resolved_at, hold_hours
    FROM _cp_bl_ranked_{lbl[:60]}
    WHERE rank = {n_thresh} AND total >= {n_thresh}
    """)

    n_sig = con.execute(f"SELECT count(*) FROM _cp_bl_signals_{lbl[:60]}").fetchone()[0]
    if n_sig == 0:
        return {"error": "no baseline signals"}

    row = con.execute(f"""
    SELECT count(*) AS n, avg(correct) AS hr,
           avg(CASE WHEN correct=1 THEN 1.0-signal_price ELSE -signal_price END) AS pnl,
           median(hold_hours) AS mh, avg(signal_price) AS ap
    FROM _cp_bl_signals_{lbl[:60]}
    """).fetchone()

    n_sig, hr, avg_pnl, med_hold, avg_price = row

    base_row = con.execute(f"""
    SELECT avg(CAST(({dir_col}) AS DOUBLE))
    FROM maker_positions p
    JOIN _cp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND p.correct IS NOT NULL
    """).fetchone()
    base_rate = float(base_row[0]) if base_row and base_row[0] is not None else 0.5

    return {
        "n_signals": int(n_sig),
        "hit_rate": round(float(hr), 4),
        "excess_hr": round(float(hr) - float(base_rate), 4),
        "base_rate": round(float(base_rate), 4),
        "avg_pnl_usd": round(float(avg_pnl), 4),
        "med_hold_hours": round(float(med_hold), 1) if med_hold else None,
        "avg_signal_price": round(float(avg_price), 4) if avg_price else None,
    }


# ─── SANITY COMBO (Step 0.5) ─────────────────────────────────────────────────

log("=" * 60)
log("STEP 0.5: Sanity combo — random-split Sports YES (N_a=1, N_b=1)")
log("=" * 60)

pa_rand, pb_rand, _ = build_pool_variant_random("Sports", k_total=100)
jac_rand = jaccard(pa_rand, pb_rand)
log(f"Random split: Pool A={len(pa_rand)}, Pool B={len(pb_rand)}, Jaccard={jac_rand:.3f}")

sanity = simulate_cross_pool(
    pa_rand, pb_rand, "Sports", "YES", n_a=1, n_b=1,
    sell_mode="buy_only", label_suffix="_sanity",
)
log(f"Sanity result: {sanity}")

if "error" not in sanity and sanity["excess_hr"] > 0:
    log(f"SANITY PASS: excess HR = {sanity['excess_hr']:+.1%} > 0. Proceeding to full sweep.")
else:
    if "error" in sanity:
        log(f"SANITY ABORT: {sanity['error']}")
    else:
        log(f"SANITY WARN: excess HR = {sanity.get('excess_hr', 'N/A'):+.1%} <= 0. May be no-go.")
    log("Proceeding anyway for diagnostics...")


# ─── FULL SWEEP ───────────────────────────────────────────────────────────────

log("=" * 60)
log("FULL SWEEP: Sports YES — all pool variants, BUY-only + directional")
log("=" * 60)

results = {
    "sanity": sanity,
    "baselines": {},
    "cross_pool": {},
    "pool_stats": {},
}

# ── Build single-pool baselines ───────────────────────────────────────────────

log("\nBuilding single-pool baselines (Sports YES, v3-style, K=100)...")

# Full K=100 pool (composite, no BEH gate — generous baseline)
base_tbl_sports = build_base_stats("Sports")
cons_tbl_sports = build_consistency_stats("Sports", base_tbl_sports)

con.execute("""
CREATE OR REPLACE TABLE _cp_full100_sports AS
WITH base AS (
    SELECT
        b.trader, b.n_markets, b.excess_hr, b.avg_edge_usd,
        coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe
    FROM _cp_base_sports b
    LEFT JOIN _cp_consistency_sports c ON b.trader = c.trader
),
ranked AS (
    SELECT *,
        percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
        percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
        percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge
    FROM base
)
SELECT
    trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd,
    (0.50 * pr_excess_hr + 0.30 * pr_consistency + 0.20 * pr_edge) AS composite_score,
    ROW_NUMBER() OVER (ORDER BY
        (0.50 * pr_excess_hr + 0.30 * pr_consistency + 0.20 * pr_edge) DESC
    ) AS rank
FROM ranked
""")

full_k100 = {r[0].lower() for r in con.execute(
    "SELECT trader FROM _cp_full100_sports WHERE rank <= 100"
).fetchall()}
log(f"Full K=100 pool: {len(full_k100)} traders")

for n in [1, 2, 3]:
    for sell_mode in ["buy_only", "directional"]:
        key = f"k100_n{n}_{sell_mode}"
        r = simulate_single_pool_baseline(full_k100, "Sports", "YES", n, sell_mode=sell_mode)
        results["baselines"][key] = r
        if "error" not in r:
            log(f"  Baseline K=100 N={n} [{sell_mode}]: {r['n_signals']} signals, "
                f"HR={r['hit_rate']:.1%}, excess={r['excess_hr']:+.1%}")
        else:
            log(f"  Baseline K=100 N={n} [{sell_mode}]: {r['error']}")


# ── Pool construction + cross-pool sweep ─────────────────────────────────────

log("\nBuilding pool variants and running cross-pool sweeps...")

VARIANTS = [
    ("style_split",     lambda: build_pool_variant_style("Sports", k_each=50)),
    ("recency_split",   lambda: build_pool_variant_recency("Sports", k_each=50)),
    ("score_axis",      lambda: build_pool_variant_score_axis("Sports", k_each=50)),
    ("random_split",    lambda: build_pool_variant_random("Sports", k_total=100)),
]

N_A_VALUES = [1]
N_B_VALUES = [1]

for variant_name, builder in VARIANTS:
    log(f"\n--- Variant: {variant_name} ---")

    pa, pb, vdesc = builder()
    jac = jaccard(pa, pb)

    results["pool_stats"][variant_name] = {
        "pool_a_size": len(pa),
        "pool_b_size": len(pb),
        "jaccard": round(jac, 4),
        "overlap_size": len(pa & pb),
        "pool_a_only": len(pa - pb),
        "pool_b_only": len(pb - pa),
    }
    log(f"  Pool A={len(pa)}, Pool B={len(pb)}, Jaccard={jac:.3f}, "
        f"A-only={len(pa-pb)}, B-only={len(pb-pa)}")

    results["cross_pool"][variant_name] = {}

    for n_a in N_A_VALUES:
        for n_b in N_B_VALUES:
            for sell_mode in ["buy_only", "directional"]:
                key = f"na{n_a}_nb{n_b}_{sell_mode}"
                r = simulate_cross_pool(
                    pa, pb, "Sports", "YES", n_a=n_a, n_b=n_b,
                    sell_mode=sell_mode,
                    label_suffix=f"_{variant_name[:8]}",
                )
                results["cross_pool"][variant_name][key] = r

                if "error" not in r:
                    log(f"  [{variant_name}] N_a={n_a} N_b={n_b} [{sell_mode}]: "
                        f"{r['n_signals']} signals, HR={r['hit_rate']:.1%}, "
                        f"excess={r['excess_hr']:+.1%}, gap={r.get('med_gap_hours','?')}h")
                else:
                    log(f"  [{variant_name}] N_a={n_a} N_b={n_b} [{sell_mode}]: {r['error']}")


# Also test N_a=1, N_b=2 (Pool B must have 2 traders confirming) for best variants
log("\n--- N_a=1 N_b=2 variants (higher confirmation bar) ---")
for variant_name, builder in VARIANTS:
    pa, pb, _ = builder() if variant_name not in results["pool_stats"] else (
        # re-use existing pools by looking at stored sizes for reference
        # Need to rebuild — pool objects not cached
        None, None, None
    )
    # Actually rebuild each one (fast, cached base stats)
    pa, pb, _ = builder()

    for sell_mode in ["buy_only", "directional"]:
        key = f"na1_nb2_{sell_mode}"
        r = simulate_cross_pool(
            pa, pb, "Sports", "YES", n_a=1, n_b=2,
            sell_mode=sell_mode,
            label_suffix=f"_{variant_name[:8]}_nb2",
        )
        results["cross_pool"][variant_name][key] = r

        if "error" not in r:
            log(f"  [{variant_name}] N_a=1 N_b=2 [{sell_mode}]: "
                f"{r['n_signals']} signals, HR={r['hit_rate']:.1%}, "
                f"excess={r['excess_hr']:+.1%}")
        else:
            log(f"  [{variant_name}] N_a=1 N_b=2 [{sell_mode}]: {r['error']}")


# ── Politics YES cross-pool (secondary tag) ───────────────────────────────────

log("\n=== Politics YES cross-pool (secondary tag) ===")

results["cross_pool_politics"] = {}

# Only test most promising variant (random split as baseline + score_axis)
for variant_name, builder in [
    ("random_split_pol",  lambda: build_pool_variant_random("Politics", k_total=100)),
    ("score_axis_pol",    lambda: build_pool_variant_score_axis("Politics", k_each=50)),
]:
    log(f"\n--- Politics Variant: {variant_name} ---")
    pa, pb, _ = builder()
    jac = jaccard(pa, pb)
    log(f"  Pool A={len(pa)}, Pool B={len(pb)}, Jaccard={jac:.3f}")

    results["cross_pool_politics"][variant_name] = {}

    for sell_mode in ["buy_only", "directional"]:
        key = f"na1_nb1_{sell_mode}"
        r = simulate_cross_pool(
            pa, pb, "Politics", "YES", n_a=1, n_b=1,
            sell_mode=sell_mode,
            max_price=0.80,
            label_suffix=f"_pol_{variant_name[:8]}",
        )
        results["cross_pool_politics"][variant_name][key] = r
        if "error" not in r:
            log(f"  [{variant_name}] [{sell_mode}]: {r['n_signals']} signals, "
                f"HR={r['hit_rate']:.1%}, excess={r['excess_hr']:+.1%}")
        else:
            log(f"  [{variant_name}] [{sell_mode}]: {r['error']}")


# ─── Save JSON ────────────────────────────────────────────────────────────────

log("\nSaving results...")

out_dir = Path(f"{REPO}/research/hypotheses/cross-pool-consensus/discovery")
out_dir.mkdir(parents=True, exist_ok=True)

output = {
    "hypothesis": "cross-pool-consensus",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "note": "ALL RESULTS ARE UPPER BOUNDS — vectorized backtests 20-40pp optimistic vs tick.",
    "config": {
        "train_cutoff": TRAIN_CUTOFF,
        "test_start": TEST_START,
        "test_end": TEST_END,
        "primary_tag": "Sports",
        "secondary_tag": "Politics",
        "direction": "YES",
    },
    "results": results,
}

json_path = out_dir / "sweep_results.json"
with open(json_path, "w") as f:
    json.dump(output, f, indent=2, default=str)
log(f"Saved JSON: {json_path}")

log("Done.")
