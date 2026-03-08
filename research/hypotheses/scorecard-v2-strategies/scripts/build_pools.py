"""Build trader pools for scorecard-v2-strategies tick-by-tick validation.

Three pools:
  1. PoliticsComposite K=100, N=5  (YES+NO)
  2. CryptoHROnly K=50, N=2        (YES-only)
  3. SportsComposite K=25, N=3     (YES-only, >=4h hold)

Train cutoff: 2025-07-01 (all training data before this date).
Test period: 2025-07-01 onwards.

Usage:
    from research.hypotheses.scorecard_v2_strategies.scripts.build_pools import (
        build_politics_composite_pool,
        build_crypto_hr_pool,
        build_sports_composite_pool,
    )
    pool, tag_markets, gambling_markets = build_politics_composite_pool()
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

TRAIN_CUTOFF = "2025-07-01"
TEST_START = "2025-07-01"

# Gambling filter SQL macro — shared across all pool builders
GAMBLING_MACRO_SQL = """
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
"""

MARKET_TAGS_SQL = """
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
"""


def _get_db():
    from research.db import db as get_db
    return get_db().con


def _ensure_shared_tables(con) -> None:
    """Create shared lookup tables (idempotent)."""
    con.execute(GAMBLING_MACRO_SQL)
    con.execute(MARKET_TAGS_SQL)


def get_gambling_markets(con=None) -> set[str]:
    """Get set of condition_ids for gambling markets."""
    if con is None:
        con = _get_db()
    # Markets that match the gambling pattern
    rows = con.execute("""
        SELECT condition_id FROM markets WHERE is_gambling_market(slug)
    """).fetchall()
    return {r[0] for r in rows}


def get_tag_markets(tag_label: str, test_start: str = TEST_START, con=None) -> set[str]:
    """Get condition_ids for a tag in the test period (resolved >= test_start).

    Returns markets that resolved on or after test_start for the given tag.
    """
    if con is None:
        con = _get_db()
    rows = con.execute(f"""
        SELECT DISTINCT p.condition_id
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
    """).fetchall()
    return {r[0] for r in rows}


def _build_composite_pool(
    con,
    tag_label: str,
    k: int,
    train_cutoff: str = TRAIN_CUTOFF,
) -> set[str]:
    """Build composite-ranked pool (excess_hr + consistency_sharpe + avg_edge + bucket_excess_hr).

    Composite weights: 0.45 * excess_hr + 0.25 * consistency_sharpe
                     + 0.15 * avg_edge_usd + 0.15 * bucket_excess_hr

    All components percentile-rank normalized to [0,1] within this tag.
    Returns top-K trader addresses.
    """
    # Step 1: base training stats
    con.execute(f"""
    CREATE OR REPLACE TABLE _bp_train_{tag_label.lower()} AS
    WITH base AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(p.resolved_at AS DATE) AS resolved_date,
            CAST(p.first_trade AS DATE) AS entry_date,
            abs(p.net_usd) AS abs_net_usd,
            p.volume,
            CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction,
            p.net_usd
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
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
            median(b.net_usd) AS median_pnl
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 20
           AND avg(CASE WHEN b.volume > 0 THEN abs(b.net_usd) / b.volume ELSE 0 END) >= 0.90
           AND count(*) < 10000
    )
    SELECT
        ts.trader,
        ts.n_markets,
        ts.hit_rate,
        ts.avg_pos_size,
        ts.avg_conviction,
        ts.median_pnl AS avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    # Step 2: consistency_sharpe (monthly HR Sharpe, needs >=6 months with >=5 positions)
    con.execute(f"""
    CREATE OR REPLACE TABLE _bp_consistency_{tag_label.lower()} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _bp_train_{tag_label.lower()} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    ),
    sharpe AS (
        SELECT
            trader,
            count(*) AS n_months,
            avg(month_hr) AS mean_hr,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0
            END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
        HAVING count(*) >= 6
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: bucket_excess_hr (per 10pp price bucket)
    con.execute(f"""
    CREATE OR REPLACE TABLE _bp_bucket_{tag_label.lower()} AS
    WITH price_data AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _bp_train_{tag_label.lower()} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
          AND yed.volume > 0
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr
        FROM price_data
        GROUP BY price_bucket
    ),
    trader_bucket AS (
        SELECT
            pd.trader,
            pd.price_bucket,
            avg(pd.correct) AS bucket_hr,
            bp.pop_hr
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    ),
    trader_bucket_excess AS (
        SELECT
            trader,
            count(*) AS n_buckets,
            avg(bucket_hr - pop_hr) AS bucket_excess_hr
        FROM trader_bucket
        GROUP BY trader
        HAVING count(*) >= 2
    )
    SELECT * FROM trader_bucket_excess;
    """)

    # Step 4: composite score with percentile normalization
    con.execute(f"""
    CREATE OR REPLACE TABLE _bp_composite_{tag_label.lower()} AS
    WITH base AS (
        SELECT
            t.trader,
            t.excess_hr,
            t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _bp_train_{tag_label.lower()} t
        LEFT JOIN _bp_consistency_{tag_label.lower()} c ON t.trader = c.trader
        LEFT JOIN _bp_bucket_{tag_label.lower()} b ON t.trader = b.trader
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
        trader,
        excess_hr,
        consistency_sharpe,
        avg_edge_usd,
        bucket_excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) AS composite_score,
        ROW_NUMBER() OVER (ORDER BY (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) DESC) AS rank
    FROM ranked;
    """)

    rows = con.execute(f"""
        SELECT trader FROM _bp_composite_{tag_label.lower()}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


def _build_hr_only_pool(
    con,
    tag_label: str,
    k: int,
    train_cutoff: str = TRAIN_CUTOFF,
) -> set[str]:
    """Build HR-only ranked pool (top-K by excess_hr).

    Returns top-K trader addresses.
    """
    con.execute(f"""
    CREATE OR REPLACE TABLE _bp_hronly_{tag_label.lower()} AS
    WITH base AS (
        SELECT
            p.trader,
            count(DISTINCT p.condition_id) AS n_markets,
            avg(CAST(p.yes_won AS DOUBLE)) AS hit_rate,
            avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) AS avg_conviction
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
          AND p.net_usd IS NOT NULL
        GROUP BY p.trader
        HAVING count(DISTINCT p.condition_id) >= 20
           AND avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) >= 0.90
           AND count(*) < 10000
    ),
    tag_base AS (
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
        FROM maker_positions p
        JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag_label}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
    )
    SELECT
        b.trader,
        b.hit_rate,
        b.n_markets,
        (b.hit_rate - tb.base_rate) AS excess_hr,
        ROW_NUMBER() OVER (ORDER BY (b.hit_rate - tb.base_rate) DESC) AS rank
    FROM base b CROSS JOIN tag_base tb
    WHERE (b.hit_rate - tb.base_rate) > 0;
    """)

    rows = con.execute(f"""
        SELECT trader FROM _bp_hronly_{tag_label.lower()}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


def build_politics_composite_pool(
    k: int = 100,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Politics pool.

    Returns (pool, tag_markets, gambling_markets):
        pool: set of trader addresses (top-K composite score)
        tag_markets: set of condition_ids for Politics in test period
        gambling_markets: set of condition_ids to exclude
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_composite_pool(con, "Politics", k=k, train_cutoff=train_cutoff)
    tag_markets = get_tag_markets("Politics", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools] Politics composite K={k}: {len(pool)} traders, "
          f"{len(tag_markets)} markets, {len(gambling_markets)} gambling excluded "
          f"({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_crypto_hr_pool(
    k: int = 50,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
) -> tuple[set[str], set[str], set[str]]:
    """Build HR-only ranked Crypto pool.

    Returns (pool, tag_markets, gambling_markets):
        pool: set of trader addresses (top-K by excess_hr)
        tag_markets: set of condition_ids for Crypto in test period
        gambling_markets: set of condition_ids to exclude
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_hr_only_pool(con, "Crypto", k=k, train_cutoff=train_cutoff)
    tag_markets = get_tag_markets("Crypto", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools] Crypto hr_only K={k}: {len(pool)} traders, "
          f"{len(tag_markets)} markets, {len(gambling_markets)} gambling excluded "
          f"({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


def build_sports_composite_pool(
    k: int = 25,
    train_cutoff: str = TRAIN_CUTOFF,
    test_start: str = TEST_START,
) -> tuple[set[str], set[str], set[str]]:
    """Build composite-ranked Sports pool.

    Returns (pool, tag_markets, gambling_markets):
        pool: set of trader addresses (top-K composite score)
        tag_markets: set of condition_ids for Sports in test period
        gambling_markets: set of condition_ids to exclude
    """
    t0 = time.time()
    con = _get_db()
    _ensure_shared_tables(con)

    pool = _build_composite_pool(con, "Sports", k=k, train_cutoff=train_cutoff)
    tag_markets = get_tag_markets("Sports", test_start=test_start, con=con)
    gambling_markets = get_gambling_markets(con=con)

    print(f"[build_pools] Sports composite K={k}: {len(pool)} traders, "
          f"{len(tag_markets)} markets, {len(gambling_markets)} gambling excluded "
          f"({time.time()-t0:.1f}s)")
    return pool, tag_markets, gambling_markets


if __name__ == "__main__":
    print("Building all three pools...")
    p1, m1, g1 = build_politics_composite_pool()
    print(f"  Politics: {len(p1)} traders, {len(m1)} markets")

    p2, m2, g2 = build_crypto_hr_pool()
    print(f"  Crypto:   {len(p2)} traders, {len(m2)} markets")

    p3, m3, g3 = build_sports_composite_pool()
    print(f"  Sports:   {len(p3)} traders, {len(m3)} markets")

    print("Done.")
