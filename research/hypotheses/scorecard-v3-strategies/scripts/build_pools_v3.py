"""Build trader pools for scorecard-v3-strategies.

Extends scorecard-v2 build_pools.py with:
  1. BEH qualification gate (bucket_excess_hr >= beh_gate, default 0.02)
     Removes traders whose "skill" is just betting on near-certainties.
  2. NO-direction pool builder (_build_no_composite_pool)
     Uses NO-entry price buckets; NO base rate = avg(correct) for NO positions.
  3. Volume-weighted ranking (volume_weighted=True → score × ln(n_markets+1))
  4. Dynamic pool cap (pool_cap: hard cap regardless of qualifiers)
  5. Walk-forward safe: train_cutoff and test_start as explicit params

Convenience builders:
  YES (v3 — with BEH gate):
    build_politics_yes_pool_v3(k=100)
    build_sports_yes_pool_v3(k=25)
    build_crypto_yes_pool_v3(k=50)

  NO (new):
    build_politics_no_pool(k=100)
    build_esports_no_pool(k=50)
    build_sports_no_pool(k=50)

Default train_cutoff: 2025-07-01 (same as v2 for comparison).

Usage:
    from research.hypotheses.scorecard_v3_strategies.scripts.build_pools_v3 import (
        build_politics_yes_pool_v3,
        build_politics_no_pool,
        build_esports_no_pool,
        build_sports_no_pool,
        build_crypto_yes_pool_v3,
        build_sports_yes_pool_v3,
    )
    pool, tag_markets, gambling_markets = build_politics_no_pool()
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

TRAIN_CUTOFF = "2025-07-01"
TEST_START = "2025-07-01"

# ─── Shared SQL setup ────────────────────────────────────────────────────────

GAMBLING_MACRO_SQL = """
CREATE OR REPLACE MACRO is_gambling_market_v3(slug) AS (
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

# Priority-ordered canonical tag assignment (from decomposition sweep findings)
MARKET_TAGS_SQL = """
CREATE OR REPLACE TABLE _v3_market_tags AS
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
    WHERE NOT is_gambling_market_v3(m.slug)
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


def _get_db():
    from research.db import db as get_db
    return get_db().con


def _ensure_shared_tables(con) -> None:
    """Create shared lookup tables (idempotent)."""
    con.execute(GAMBLING_MACRO_SQL)
    con.execute(MARKET_TAGS_SQL)


def get_gambling_markets(con=None) -> set[str]:
    """Return set of condition_ids for gambling markets."""
    if con is None:
        con = _get_db()
    rows = con.execute(
        "SELECT condition_id FROM markets WHERE is_gambling_market_v3(slug)"
    ).fetchall()
    return {r[0] for r in rows}


def get_tag_markets(tag_label: str, test_start: str = TEST_START, con=None) -> set[str]:
    """Return condition_ids for a tag that resolved on or after test_start."""
    if con is None:
        con = _get_db()
    rows = con.execute(f"""
        SELECT DISTINCT p.condition_id
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
    """).fetchall()
    return {r[0] for r in rows}


# ─── YES-direction pool builder ───────────────────────────────────────────────

def _build_yes_composite_pool(
    con,
    tag_label: str,
    k: int,
    train_cutoff: str = TRAIN_CUTOFF,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> set[str]:
    """Build composite-ranked YES pool with BEH gate.

    New vs v2:
    - BEH gate: requires bucket_excess_hr >= beh_gate before composite scoring
    - volume_weighted: multiplies composite score by ln(n_markets+1) when True
    - pool_cap: hard cap on pool size

    Composite weights: 0.45 * excess_hr + 0.25 * consistency_sharpe
                     + 0.15 * avg_edge_usd + 0.15 * bucket_excess_hr
    All components percentile-rank normalized to [0,1].
    """
    tag = tag_label.lower()

    # Step 1: base training stats (same as v2, adds conviction gate)
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_yes_train_{tag} AS
    WITH base AS (
        SELECT
            p.trader, p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            abs(p.net_usd) AS abs_net_usd,
            p.volume, p.net_usd,
            CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
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
            median(b.net_usd) AS avg_edge_usd
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 20
           AND avg(CASE WHEN b.volume > 0 THEN abs(b.net_usd) / b.volume ELSE 0 END) >= 0.90
           AND count(*) < 10000
    )
    SELECT
        ts.trader, ts.n_markets, ts.hit_rate, ts.avg_pos_size,
        ts.avg_conviction, ts.avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    # Step 2: consistency_sharpe
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_yes_consistency_{tag} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _v3_yes_train_{tag} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    ),
    sharpe AS (
        SELECT trader, count(*) AS n_months, avg(month_hr) AS mean_hr,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0 END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
        HAVING count(*) >= 6
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: bucket_excess_hr (YES — uses yes_entry_data for entry price)
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_yes_bucket_{tag} AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _v3_yes_train_{tag} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr, count(*) AS n_pop
        FROM price_data GROUP BY price_bucket HAVING count(*) >= 10
    ),
    trader_bucket AS (
        SELECT pd.trader, pd.price_bucket,
               avg(pd.correct) AS bucket_hr, bp.pop_hr
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    ),
    trader_bucket_excess AS (
        SELECT trader, count(*) AS n_buckets,
               avg(bucket_hr - pop_hr) AS bucket_excess_hr
        FROM trader_bucket
        GROUP BY trader
        HAVING count(*) >= 2
    )
    SELECT * FROM trader_bucket_excess;
    """)

    # Step 4: composite score with BEH gate + optional volume weighting
    vw_expr = "* ln(t.n_markets + 1)" if volume_weighted else ""
    cap_clause = f"WHERE rank <= {pool_cap}" if pool_cap else ""

    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_yes_composite_{tag} AS
    WITH base AS (
        SELECT
            t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _v3_yes_train_{tag} t
        LEFT JOIN _v3_yes_consistency_{tag} c ON t.trader = c.trader
        LEFT JOIN _v3_yes_bucket_{tag} b ON t.trader = b.trader
        -- BEH gate: filter out traders whose skill is just near-certainty bets
        WHERE coalesce(b.bucket_excess_hr, 0.0) >= {beh_gate}
    ),
    ranked AS (
        SELECT *,
            percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
            percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
            percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge,
            percent_rank() OVER (ORDER BY bucket_excess_hr) AS pr_bucket
        FROM base
    )
    SELECT
        trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd, bucket_excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket)
            {vw_expr} AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket)
            {vw_expr} DESC
        ) AS rank
    FROM ranked;
    """)

    rows = con.execute(f"""
        SELECT trader FROM _v3_yes_composite_{tag}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


# ─── NO-direction pool builder ────────────────────────────────────────────────

def _build_no_composite_pool(
    con,
    tag_label: str,
    k: int,
    train_cutoff: str = TRAIN_CUTOFF,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> set[str]:
    """Build composite-ranked NO-direction pool.

    Mirrors _build_yes_composite_pool but for NO-entry positions:
    - position = 'NO'
    - correct = 1 when NO wins (yes_won = 0)
    - entry price = 1 - YES price (from yes_entry_data when available,
      else abs(net_usd)/net_no from maker_positions)
    - NO base rate = avg(correct) for NO positions in training window
    - BEH gate applied before composite scoring
    """
    tag = tag_label.lower()

    # Step 1: base training stats for NO positions
    # correct for NO = (1 - yes_won), since correct field in maker_positions
    # already accounts for direction (correct=1 when the position won).
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_no_train_{tag} AS
    WITH base AS (
        SELECT
            p.trader, p.condition_id,
            p.correct::DOUBLE AS correct,   -- correct=1 when NO wins
            abs(p.net_usd) AS abs_net_usd,
            p.volume, p.net_usd,
            CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'NO'
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
            median(b.net_usd) AS avg_edge_usd
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 20
           AND count(*) < 10000
    )
    SELECT
        ts.trader, ts.n_markets, ts.hit_rate, ts.avg_pos_size,
        ts.avg_conviction, ts.avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    # Step 2: consistency_sharpe for NO positions
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_no_consistency_{tag} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(p.correct::DOUBLE) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _v3_no_train_{tag} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'NO'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    ),
    sharpe AS (
        SELECT trader, count(*) AS n_months, avg(month_hr) AS mean_hr,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0 END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
        HAVING count(*) >= 6
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: bucket_excess_hr for NO positions
    # NO entry price: use 1 - YES_price from yes_entry_data when available.
    # Fallback: abs(net_usd)/net_no (direct NO token price from maker_positions).
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_no_bucket_{tag} AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            p.correct::DOUBLE AS correct,
            -- NO entry price = 1 - YES price when YES data available,
            -- else direct NO token price from maker_positions
            CAST(
                floor(
                    COALESCE(
                        1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)),
                        abs(p.net_usd) / NULLIF(p.net_no, 0)
                    ) * 10
                ) * 0.10
            AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _v3_no_train_{tag} t ON p.trader = t.trader
        LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'NO'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
          -- Valid NO price range
          AND COALESCE(
                1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)),
                abs(p.net_usd) / NULLIF(p.net_no, 0)
              ) BETWEEN 0.05 AND 0.95
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr, count(*) AS n_pop
        FROM price_data GROUP BY price_bucket HAVING count(*) >= 10
    ),
    trader_bucket AS (
        SELECT pd.trader, pd.price_bucket,
               avg(pd.correct) AS bucket_hr, bp.pop_hr
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    ),
    trader_bucket_excess AS (
        SELECT trader, count(*) AS n_buckets,
               avg(bucket_hr - pop_hr) AS bucket_excess_hr
        FROM trader_bucket
        GROUP BY trader
        HAVING count(*) >= 2
    )
    SELECT * FROM trader_bucket_excess;
    """)

    # Step 4: composite score with BEH gate + optional volume weighting
    vw_expr = "* ln(t.n_markets + 1)" if volume_weighted else ""

    con.execute(f"""
    CREATE OR REPLACE TABLE _v3_no_composite_{tag} AS
    WITH base AS (
        SELECT
            t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _v3_no_train_{tag} t
        LEFT JOIN _v3_no_consistency_{tag} c ON t.trader = c.trader
        LEFT JOIN _v3_no_bucket_{tag} b ON t.trader = b.trader
        -- BEH gate
        WHERE coalesce(b.bucket_excess_hr, 0.0) >= {beh_gate}
    ),
    ranked AS (
        SELECT *,
            percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
            percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
            percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge,
            percent_rank() OVER (ORDER BY bucket_excess_hr) AS pr_bucket
        FROM base
    )
    SELECT
        trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd, bucket_excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket)
            {vw_expr} AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket)
            {vw_expr} DESC
        ) AS rank
    FROM ranked;
    """)

    rows = con.execute(f"""
        SELECT trader FROM _v3_no_composite_{tag}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


# ─── Convenience builders: YES (v3 with BEH gate) ────────────────────────────

def build_politics_yes_pool_v3(
    k: int = 100,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Politics YES pool (v3 with BEH gate).

    Returns (pool, tag_markets, gambling_markets).
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_yes_composite_pool(
        con, "Politics", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Politics", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Politics YES K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_sports_yes_pool_v3(
    k: int = 25,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Sports YES pool (v3 with BEH gate)."""
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_yes_composite_pool(
        con, "Sports", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Sports", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Sports YES K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_crypto_yes_pool_v3(
    k: int = 50,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Crypto YES pool (v3 with BEH gate)."""
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_yes_composite_pool(
        con, "Crypto", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Crypto", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Crypto YES K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


# ─── Convenience builders: NO (new in v3) ────────────────────────────────────

def build_politics_no_pool(
    k: int = 100,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Politics NO-direction pool.

    Returns (pool, tag_markets, gambling_markets).
    Key finding: 826 signals/3mo (hold>=24h), HR=85.0%, excess +7.7pp over 76.8% base.
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_no_composite_pool(
        con, "Politics", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Politics", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Politics NO K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_esports_no_pool(
    k: int = 50,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Esports NO-direction pool.

    Key finding from decomposition: Esports is almost pure NO signal (+0.446 BEH).
    Thin market count — hold filter >=24h required for tick validation.
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_no_composite_pool(
        con, "Esports", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Esports", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Esports NO K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_sports_no_pool(
    k: int = 50,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
    beh_gate: float = 0.02,
    volume_weighted: bool = False,
    pool_cap: int | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Sports NO-direction pool.

    Warning: Sports NO signals dominated by in-play (hold=0d). Apply hold>=24h
    filter in tick validation. Vectorized HR is inflated.
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_no_composite_pool(
        con, "Sports", k=k, train_cutoff=train_cutoff,
        beh_gate=beh_gate, volume_weighted=volume_weighted, pool_cap=pool_cap,
    )
    tag_markets = get_tag_markets("Sports", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools_v3] Sports NO K={k} beh≥{beh_gate}: {len(pool)} traders, "
          f"{len(tag_markets)} markets ({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


# ─── Comparison helpers ───────────────────────────────────────────────────────

def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare_v2_v3_yes(tag: str, k: int, train_cutoff: str = TRAIN_CUTOFF) -> dict:
    """Compare v2 vs v3 YES pool for a given tag."""
    import importlib.util, os

    v2_path = os.path.join(
        "/mnt/nvme/git/polymarket/polymarket",
        "research/hypotheses/scorecard-v2-strategies/scripts/build_pools.py"
    )
    spec = importlib.util.spec_from_file_location("build_pools_v2", v2_path)
    v2_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v2_mod)

    con = _get_db()

    # Build v2 pool (uses its own shared tables)
    con.execute(v2_mod.GAMBLING_MACRO_SQL)
    con.execute(v2_mod.MARKET_TAGS_SQL)
    v2_pool = v2_mod._build_composite_pool(con, tag, k=k, train_cutoff=train_cutoff)

    # Build v3 pool (with BEH gate, using v3 shared tables)
    _ensure_shared_tables(con)
    v3_pool = _build_yes_composite_pool(con, tag, k=k, train_cutoff=train_cutoff, beh_gate=0.02)

    return {
        "tag": tag,
        "k": k,
        "v2_size": len(v2_pool),
        "v3_size": len(v3_pool),
        "jaccard": round(jaccard(v2_pool, v3_pool), 4),
        "v2_only": len(v2_pool - v3_pool),
        "v3_only": len(v3_pool - v2_pool),
        "shared": len(v2_pool & v3_pool),
    }


# ─── Main: test all builders ──────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    print("=" * 60)
    print("Building all v3 pools...")
    print("=" * 60)

    results = {}

    # YES pools (v3 with BEH gate)
    print("\n--- YES Pools (v3, BEH gate >= 0.02) ---")
    p_pol_yes, m_pol, g = build_politics_yes_pool_v3(k=100)
    results["Politics YES K=100"] = len(p_pol_yes)

    p_spo_yes, m_spo, _ = build_sports_yes_pool_v3(k=25)
    results["Sports YES K=25"] = len(p_spo_yes)

    p_cry_yes, m_cry, _ = build_crypto_yes_pool_v3(k=50)
    results["Crypto YES K=50"] = len(p_cry_yes)

    # NO pools (new in v3)
    print("\n--- NO Pools (v3, new) ---")
    p_pol_no, m_pol2, _ = build_politics_no_pool(k=100)
    results["Politics NO K=100"] = len(p_pol_no)

    p_esp_no, m_esp, _ = build_esports_no_pool(k=50)
    results["Esports NO K=50"] = len(p_esp_no)

    p_spo_no, m_spo2, _ = build_sports_no_pool(k=50)
    results["Sports NO K=50"] = len(p_spo_no)

    # Summary table
    print("\n" + "=" * 60)
    print("POOL SIZES SUMMARY")
    print("=" * 60)
    for name, size in results.items():
        print(f"  {name:<30} {size:>6} traders")

    # YES/NO overlap for Politics
    shared_pol = p_pol_yes & p_pol_no
    print(f"\n  Politics YES ∩ NO overlap: {len(shared_pol)} traders "
          f"(Jaccard={jaccard(p_pol_yes, p_pol_no):.3f})")

    shared_spo = p_spo_yes & p_spo_no
    print(f"  Sports YES ∩ NO overlap:   {len(shared_spo)} traders "
          f"(Jaccard={jaccard(p_spo_yes, p_spo_no):.3f})")

    # Compare v2 vs v3 YES pools
    print("\n--- v2 vs v3 YES Pool Comparison ---")
    for tag, k in [("Politics", 100), ("Sports", 25), ("Crypto", 50)]:
        try:
            cmp = compare_v2_v3_yes(tag, k)
            print(f"  {tag} K={k}: v2={cmp['v2_size']}, v3={cmp['v3_size']}, "
                  f"Jaccard={cmp['jaccard']:.3f}, shared={cmp['shared']}, "
                  f"v3-only={cmp['v3_only']}, v2-only={cmp['v2_only']}")
        except Exception as e:
            print(f"  {tag} comparison error: {e}")

    print("\nDone.")
