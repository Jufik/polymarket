"""Composite scorecard pool — full 4-signal ranking vs HR-only.

Signals:
  1. excess_hr          (0.45) — HR minus tag-specific base rate
  2. consistency_sharpe (0.25) — monthly HR Sharpe (mean/std, >=6 months)
  3. avg_edge_usd       (0.15) — avg realized PnL per position
  4. bucket_excess_hr   (0.15) — HR within 10pp entry-price bucket minus population HR

Results go to:
  research/hypotheses/scorecard-v2-strategies/discovery/composite_scorecard_pool.md
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

import polars as pl

# ── constants ─────────────────────────────────────────────────────────────────
TRAIN_END = "2025-07-01"
TEST_START = "2025-07-01"

# Tags to evaluate (direction-decomposed)
TAGS = ["Politics", "Sports", "Crypto", "Elections"]

# Tag-specific base rates (training window) from previous research
# These are YES-win base rates per tag in the TRAINING window
TAG_BASE_RATES: dict[str, float] = {
    "Politics": 0.279,
    "Sports":   0.309,
    "Crypto":   0.265,
    "Elections": 0.311,
    "Esports":  0.486,
    "Tennis":   0.399,
    "NBA":      0.350,
    "NFL":      0.297,
    "Soccer":   0.292,
}

# Composite weights
W_EXCESS_HR    = 0.45
W_CONSISTENCY  = 0.25
W_EDGE_USD     = 0.15
W_BUCKET_HR    = 0.15

# Hold filter (>=4h for sports, 0 for others — applied at signal level)
SPORTS_HOLD_FILTER_H = 4

LOG_FILE = REPO / "tmp" / "composite_pool.log"

# ── helpers ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def priority_tag(label: str) -> str:
    """Map raw event_tags label to canonical research tag using priority rules."""
    priority_order = [
        "counter strike 2", "Dota 2", "League of Legends", "Valorant", "Esports",
        "NBA", "NFL", "MLB", "NHL", "NCAA Basketball", "Tennis", "Golf/PGA", "Soccer", "Sports",
        "Elections", "Politics",
        "Crypto", "Finance", "Economy",
        "Culture", "Tech", "Science", "Geopolitics", "AI", "Weather",
    ]
    # Return label if already in priority list; otherwise keep as-is
    return label if label in priority_order else label


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    LOG_FILE.write_text("")  # reset log

    from research.db import db
    d = db()

    log("=== Composite Scorecard Pool — Phase 4 Task ===")
    log(f"Train end: {TRAIN_END} | Test start: {TEST_START}")

    # ── Step 0a: build non-gambling market set ─────────────────────────────────
    log("\n[0a] Building non-gambling market set (slug filter via markets table)...")
    d.execute("""
        CREATE OR REPLACE TABLE _valid_markets AS
        SELECT condition_id
        FROM markets
        WHERE slug NOT LIKE '%updown%'
          AND slug NOT LIKE '%up-or-down%'
    """)
    n_valid = d.fetchone("SELECT count() FROM _valid_markets")["count_star()"]
    log(f"  Non-gambling markets: {n_valid:,}")

    # ── Step 0b: build market-tag lookup ──────────────────────────────────────
    log("\n[0b] Building market→tag lookup...")
    d.execute("""
        CREATE OR REPLACE TABLE _mkt_tags AS
        WITH ranked AS (
            SELECT
                m.condition_id,
                et.label,
                ROW_NUMBER() OVER (
                    PARTITION BY m.condition_id
                    ORDER BY
                        CASE et.label
                            WHEN 'counter strike 2' THEN 1
                            WHEN 'Dota 2'            THEN 2
                            WHEN 'League of Legends' THEN 3
                            WHEN 'Valorant'          THEN 4
                            WHEN 'Esports'           THEN 5
                            WHEN 'NBA'               THEN 6
                            WHEN 'NFL'               THEN 7
                            WHEN 'MLB'               THEN 8
                            WHEN 'NHL'               THEN 9
                            WHEN 'NCAA Basketball'   THEN 10
                            WHEN 'Tennis'            THEN 11
                            WHEN 'Golf/PGA'          THEN 12
                            WHEN 'Soccer'            THEN 13
                            WHEN 'Sports'            THEN 14
                            WHEN 'Elections'         THEN 15
                            WHEN 'Politics'          THEN 16
                            WHEN 'Crypto'            THEN 17
                            WHEN 'Finance'           THEN 18
                            WHEN 'Economy'           THEN 19
                            WHEN 'Culture'           THEN 20
                            WHEN 'Tech'              THEN 21
                            WHEN 'Science'           THEN 22
                            WHEN 'Geopolitics'       THEN 23
                            WHEN 'AI'                THEN 24
                            WHEN 'Weather'           THEN 25
                            ELSE 99
                        END
                ) AS rn
            FROM markets m
            JOIN events e ON m.event_id = e.id
            JOIN event_tags et ON e.id = et.event_id
            WHERE et.label IN (
                'counter strike 2','Dota 2','League of Legends','Valorant','Esports',
                'NBA','NFL','MLB','NHL','NCAA Basketball','Tennis','Golf/PGA','Soccer','Sports',
                'Elections','Politics','Crypto','Finance','Economy',
                'Culture','Tech','Science','Geopolitics','AI','Weather'
            )
        )
        SELECT condition_id, label AS tag FROM ranked WHERE rn = 1
    """)
    n_mkts = d.fetchone("SELECT count() FROM _mkt_tags")["count_star()"]
    log(f"  Tagged markets: {n_mkts:,}")

    # ── Step 1: tag-specific base rates (training window) ─────────────────────
    log("\n[1] Computing tag-specific base rates (training window)...")
    d.execute(f"""
        CREATE OR REPLACE TABLE _tag_base_rates AS
        SELECT
            mt.tag,
            avg(CAST(mp.correct AS DOUBLE)) AS base_rate,
            count(DISTINCT mp.condition_id) AS n_train_markets
        FROM maker_positions mp
        JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
        WHERE mp.position = 'YES'
          AND CAST(mp.resolved_at AS DATE) < '{TRAIN_END}'
          AND mp.volume > 0
          AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
        GROUP BY mt.tag
        HAVING count(DISTINCT mp.condition_id) >= 100
    """)
    base_rates_df = d.query("SELECT * FROM _tag_base_rates ORDER BY n_train_markets DESC")
    log("  Tag base rates (training):")
    for row in base_rates_df.to_dicts():
        log(f"    {row['tag']:20s}  base={row['base_rate']:.3f}  n={row['n_train_markets']:,}")

    # ── Step 2: per-trader raw metrics (training window) ─────────────────────
    log("\n[2] Computing per-trader raw metrics (training window)...")

    # 2a: excess_hr per trader (use dominant tag)
    d.execute(f"""
        CREATE OR REPLACE TABLE _trader_tag_stats AS
        WITH trader_tag AS (
            SELECT
                mp.trader,
                mt.tag,
                count(DISTINCT mp.condition_id) AS n_positions,
                avg(CAST(mp.correct AS DOUBLE)) AS hit_rate,
                avg(mp.realized_pnl) AS avg_pnl,
                -- market-maker exclusion
                avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) AS avg_conviction,
                -- dominant tag (most positions)
                ROW_NUMBER() OVER (PARTITION BY mp.trader ORDER BY count(DISTINCT mp.condition_id) DESC) AS tag_rank
            FROM maker_positions mp
            JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
            WHERE CAST(mp.resolved_at AS DATE) < '{TRAIN_END}'
              AND mp.volume > 0
              AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
            GROUP BY mp.trader, mt.tag
            HAVING count(DISTINCT mp.condition_id) >= 20
               AND avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) >= 0.90
        ),
        with_base AS (
            SELECT
                t.*,
                br.base_rate,
                (t.hit_rate - br.base_rate) AS excess_hr
            FROM trader_tag t
            JOIN _tag_base_rates br ON t.tag = br.tag
        )
        SELECT *
        FROM with_base
        WHERE tag_rank = 1
          AND excess_hr > 0
    """)
    n_traders = d.fetchone("SELECT count() FROM _trader_tag_stats")["count_star()"]
    log(f"  Qualified traders (excess_hr > 0, ≥20 positions, non-MM): {n_traders:,}")

    # 2b: consistency_sharpe — monthly HR (>=6 months, >=5 positions/month)
    log("\n[2b] Computing consistency_sharpe...")
    d.execute(f"""
        CREATE OR REPLACE TABLE _monthly_hr AS
        WITH dated AS (
            SELECT
                mp.trader,
                mp.condition_id,
                mp.correct,
                date_trunc('month', CAST(mp.resolved_at AS DATE)) AS month
            FROM maker_positions mp
            WHERE CAST(mp.resolved_at AS DATE) < '{TRAIN_END}'
              AND mp.volume > 0
              AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
        )
        SELECT
            trader,
            month,
            avg(CAST(correct AS DOUBLE)) AS monthly_hr,
            count(DISTINCT condition_id) AS n_pos
        FROM dated
        GROUP BY trader, month
        HAVING count(DISTINCT condition_id) >= 5
    """)

    d.execute("""
        CREATE OR REPLACE TABLE _consistency AS
        SELECT
            trader,
            count(*) AS n_months,
            avg(monthly_hr) AS mean_monthly_hr,
            stddev(monthly_hr) AS std_monthly_hr,
            avg(monthly_hr) / (stddev(monthly_hr) + 0.01) AS consistency_sharpe
        FROM _monthly_hr
        GROUP BY trader
        HAVING count(*) >= 6
    """)
    n_stable = d.fetchone("SELECT count() FROM _consistency")["count_star()"]
    log(f"  Traders with >=6 qualifying months: {n_stable:,}")

    # 2c: bucket_excess_hr — per 10pp entry-price bucket
    log("\n[2c] Computing bucket_excess_hr...")
    d.execute(f"""
        CREATE OR REPLACE TABLE _with_entry_price AS
        SELECT
            mp.trader,
            mp.condition_id,
            mp.position,
            mp.correct,
            CASE
                WHEN mp.position='YES' AND abs(mp.net_yes) > 0.01
                    THEN abs(mp.net_usd) / abs(mp.net_yes)
                WHEN mp.position='NO' AND abs(mp.net_no) > 0.01
                    THEN abs(mp.net_usd) / abs(mp.net_no)
                ELSE NULL
            END AS entry_price
        FROM maker_positions mp
        WHERE CAST(mp.resolved_at AS DATE) < '{TRAIN_END}'
          AND mp.volume > 0
          AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
    """)

    # Population HR per bucket
    d.execute("""
        CREATE OR REPLACE TABLE _pop_bucket_hr AS
        SELECT
            FLOOR(entry_price * 10) / 10 AS bucket,
            avg(CAST(correct AS DOUBLE)) AS pop_bucket_hr,
            count(*) AS pop_n
        FROM _with_entry_price
        WHERE entry_price IS NOT NULL
          AND entry_price BETWEEN 0.0 AND 1.0
        GROUP BY bucket
    """)

    # Trader HR per bucket
    d.execute("""
        CREATE OR REPLACE TABLE _trader_bucket_hr AS
        SELECT
            trader,
            FLOOR(entry_price * 10) / 10 AS bucket,
            avg(CAST(correct AS DOUBLE)) AS trader_bucket_hr,
            count(*) AS n_pos
        FROM _with_entry_price
        WHERE entry_price IS NOT NULL
          AND entry_price BETWEEN 0.0 AND 1.0
        GROUP BY trader, bucket
        HAVING count(*) >= 3
    """)

    # Weighted avg (trader_bucket_hr - pop_bucket_hr) across buckets
    d.execute("""
        CREATE OR REPLACE TABLE _bucket_excess AS
        WITH joined AS (
            SELECT
                t.trader,
                t.bucket,
                t.trader_bucket_hr,
                pb.pop_bucket_hr,
                t.n_pos,
                (t.trader_bucket_hr - pb.pop_bucket_hr) AS bucket_excess
            FROM _trader_bucket_hr t
            JOIN _pop_bucket_hr pb ON t.bucket = pb.bucket
        )
        SELECT
            trader,
            sum(bucket_excess * n_pos) / sum(n_pos) AS bucket_excess_hr,
            count(*) AS n_buckets
        FROM joined
        GROUP BY trader
        HAVING count(*) >= 2
    """)
    n_bucket = d.fetchone("SELECT count() FROM _bucket_excess")["count_star()"]
    log(f"  Traders with bucket_excess_hr computed: {n_bucket:,}")

    # ── Step 3: join all 4 components ─────────────────────────────────────────
    log("\n[3] Joining all 4 components...")
    d.execute("""
        CREATE OR REPLACE TABLE _raw_scorecard AS
        SELECT
            ts.trader,
            ts.tag,
            ts.n_positions,
            ts.hit_rate,
            ts.excess_hr,
            ts.avg_pnl AS avg_edge_usd,
            COALESCE(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            COALESCE(be.bucket_excess_hr, 0.0) AS bucket_excess_hr,
            -- flags
            c.n_months IS NOT NULL AS has_consistency,
            be.bucket_excess_hr IS NOT NULL AS has_bucket
        FROM _trader_tag_stats ts
        LEFT JOIN _consistency c ON ts.trader = c.trader
        LEFT JOIN _bucket_excess be ON ts.trader = be.trader
    """)
    n_sc = d.fetchone("SELECT count() FROM _raw_scorecard")["count_star()"]
    log(f"  Raw scorecard rows: {n_sc:,}")
    cov = d.query("""
        SELECT
            count(*) AS total,
            sum(CAST(has_consistency AS INT)) AS with_consistency,
            sum(CAST(has_bucket AS INT)) AS with_bucket
        FROM _raw_scorecard
    """).to_dicts()[0]
    log(f"  Coverage — consistency: {cov['with_consistency']:,}/{cov['total']:,} "
        f"({100*cov['with_consistency']//cov['total']}%), "
        f"bucket: {cov['with_bucket']:,}/{cov['total']:,} ({100*cov['with_bucket']//cov['total']}%)")

    # ── Step 4: percentile-rank normalize each component to [0, 1] ────────────
    log("\n[4] Percentile-rank normalizing components...")
    d.execute("""
        CREATE OR REPLACE TABLE _scored AS
        SELECT
            trader, tag, n_positions, hit_rate, excess_hr, avg_edge_usd,
            consistency_sharpe, bucket_excess_hr,
            has_consistency, has_bucket,
            -- percentile ranks [0, 1]
            percent_rank() OVER (PARTITION BY tag ORDER BY excess_hr)       AS pct_excess_hr,
            percent_rank() OVER (PARTITION BY tag ORDER BY consistency_sharpe) AS pct_consistency,
            percent_rank() OVER (PARTITION BY tag ORDER BY avg_edge_usd)    AS pct_edge_usd,
            percent_rank() OVER (PARTITION BY tag ORDER BY bucket_excess_hr) AS pct_bucket_hr,
            -- HR-only rank (baseline)
            percent_rank() OVER (PARTITION BY tag ORDER BY excess_hr)       AS hr_rank_pct
        FROM _raw_scorecard
    """)

    # Composite score
    d.execute(f"""
        CREATE OR REPLACE TABLE _composite AS
        SELECT *,
            ({W_EXCESS_HR} * pct_excess_hr
           + {W_CONSISTENCY} * pct_consistency
           + {W_EDGE_USD} * pct_edge_usd
           + {W_BUCKET_HR} * pct_bucket_hr) AS composite_score
        FROM _scored
    """)
    log("  Composite score computed.")

    # ── Step 5: pool size sweep — top-K by composite vs HR-only ───────────────
    log("\n[5] Pool size sweep + head-to-head vs HR-only...")

    results: list[dict] = []

    for tag in TAGS:
        log(f"\n  Tag: {tag}")
        tag_base = d.fetchone(f"SELECT base_rate FROM _tag_base_rates WHERE tag='{tag}'")
        if tag_base is None:
            log(f"    SKIP — no base rate for {tag}")
            continue
        base_rate = tag_base["base_rate"]

        n_tag_traders = d.fetchone(
            f"SELECT count() FROM _composite WHERE tag='{tag}'"
        )["count_star()"]
        log(f"    Pool: {n_tag_traders:,} traders, base_rate={base_rate:.3f}")

        for K in [25, 50, 100, 200]:
            if K > n_tag_traders:
                continue

            for N in [2, 3, 5]:
                for ranking in ["composite", "hr_only"]:
                    rank_col = "composite_score" if ranking == "composite" else "hr_rank_pct"

                    # Build top-K pool
                    pool_query = f"""
                        SELECT trader
                        FROM _composite
                        WHERE tag = '{tag}'
                        ORDER BY {rank_col} DESC
                        LIMIT {K}
                    """

                    hold_filter = (
                        f"AND date_diff('hour', mp.first_trade, mp.resolved_at) >= {SPORTS_HOLD_FILTER_H}"
                        if tag in ("Sports", "Tennis", "NBA", "NFL", "Soccer", "MLB", "NHL", "NCAA Basketball")
                        else ""
                    )

                    # Test-window signal: market-level aggregation (CRITICAL: only copyable entries)
                    sig_sql = f"""
                        WITH pool AS ({pool_query}),
                        test_pos AS (
                            SELECT
                                mp.trader,
                                mp.condition_id,
                                mp.position,
                                mp.correct,
                                mp.net_usd,
                                mp.first_trade,
                                mp.resolved_at,
                                mp.yes_won
                            FROM maker_positions mp
                            JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
                            JOIN pool p ON mp.trader = p.trader
                            WHERE mt.tag = '{tag}'
                              AND CAST(mp.resolved_at AS DATE) >= '{TEST_START}'
                              AND CAST(mp.first_trade AS DATE) >= '{TEST_START}'
                              AND mp.volume > 0
                              AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
                              {hold_filter}
                        ),
                        market_agg AS (
                            SELECT
                                condition_id,
                                any_value(yes_won) AS yes_won,
                                count(DISTINCT trader) AS n_qual,
                                count(DISTINCT CASE WHEN position='YES' THEN trader END) AS n_yes,
                                count(DISTINCT CASE WHEN position='NO' THEN trader END) AS n_no,
                                sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir,
                                max(first_trade) AS signal_entry,
                                any_value(resolved_at) AS resolved_at,
                                date_diff('hour', max(first_trade), any_value(resolved_at)) AS hold_hours
                            FROM test_pos
                            GROUP BY condition_id
                            HAVING count(DISTINCT trader) >= {N}
                        ),
                        signals AS (
                            SELECT *,
                                GREATEST(n_yes, n_no) * 1.0 / n_qual AS confidence,
                                CASE WHEN vol_dir > 0 THEN 1 ELSE 0 END AS yes_signal,
                                CASE WHEN vol_dir > 0 THEN yes_won ELSE (1 - yes_won) END AS signal_correct
                            FROM market_agg
                        )
                        SELECT
                            count(*) AS n_signals,
                            avg(CAST(signal_correct AS DOUBLE)) AS hit_rate,
                            median(hold_hours) AS med_hold_hours,
                            avg(CAST(n_qual AS DOUBLE)) AS avg_n_traders
                        FROM signals
                    """
                    row = d.fetchone(sig_sql)
                    if row is None or row["n_signals"] == 0:
                        continue

                    hr = row["hit_rate"]
                    n_sig = int(row["n_signals"])
                    hold_h = row["med_hold_hours"]
                    excess = hr - base_rate

                    # Compounding score
                    avg_edge = excess * 100  # approximate USD/signal at $100 stake
                    hold_days = (hold_h or 0) / 24
                    cs = (excess * avg_edge / max(hold_days, 0.083)) if hold_days > 0 else 0

                    results.append({
                        "tag": tag,
                        "K": K,
                        "N": N,
                        "ranking": ranking,
                        "n_signals": n_sig,
                        "hit_rate": round(hr, 4),
                        "excess_hr_pp": round(excess * 100, 2),
                        "base_rate": round(base_rate, 4),
                        "med_hold_h": round(hold_h, 1) if hold_h else None,
                        "cs": round(cs, 2),
                    })
                    log(f"    K={K:3d} N={N} {ranking:10s}: "
                        f"n={n_sig:4d}  HR={hr:.3f}  excess={excess*100:+.1f}pp  hold={hold_h:.0f}h  CS={cs:.1f}")

    # ── Step 6: head-to-head comparison (K=50) ────────────────────────────────
    log("\n[6] Head-to-head: Composite K=50 vs HR-only K=50...")
    for tag in TAGS:
        tag_row = d.fetchone(f"SELECT count() FROM _composite WHERE tag='{tag}'")
        if tag_row is None or tag_row["count_star()"] < 50:
            continue

        # Jaccard index of top-50 overlap
        jaccard_sql = f"""
            WITH comp50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY composite_score DESC LIMIT 50
            ),
            hr50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY hr_rank_pct DESC LIMIT 50
            ),
            intersection AS (
                SELECT trader FROM comp50 INTERSECT SELECT trader FROM hr50
            )
            SELECT
                (SELECT count() FROM intersection) AS overlap,
                (SELECT count() FROM comp50) + (SELECT count() FROM hr50)
                    - (SELECT count() FROM intersection) AS union_n
        """
        jrow = d.fetchone(jaccard_sql)
        overlap = jrow["overlap"]
        union_n = jrow["union_n"]
        jaccard = overlap / union_n if union_n > 0 else 0
        log(f"  {tag}: overlap={overlap}/50, Jaccard={jaccard:.3f}")

        # Profile difference: traders IN composite-50 but NOT in hr-50
        diff_sql = f"""
            WITH comp50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY composite_score DESC LIMIT 50
            ),
            hr50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY hr_rank_pct DESC LIMIT 50
            ),
            gained AS (
                SELECT trader FROM comp50 EXCEPT SELECT trader FROM hr50
            )
            SELECT
                avg(s.excess_hr) AS avg_excess_hr,
                avg(s.consistency_sharpe) AS avg_consistency,
                avg(s.avg_edge_usd) AS avg_edge_usd,
                avg(s.bucket_excess_hr) AS avg_bucket_excess,
                count(*) AS n
            FROM _composite s
            JOIN gained g ON s.trader = g.trader
        """
        gained_row = d.fetchone(diff_sql)

        lost_sql = f"""
            WITH comp50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY composite_score DESC LIMIT 50
            ),
            hr50 AS (
                SELECT trader FROM _composite WHERE tag='{tag}'
                ORDER BY hr_rank_pct DESC LIMIT 50
            ),
            lost AS (
                SELECT trader FROM hr50 EXCEPT SELECT trader FROM comp50
            )
            SELECT
                avg(s.excess_hr) AS avg_excess_hr,
                avg(s.consistency_sharpe) AS avg_consistency,
                avg(s.avg_edge_usd) AS avg_edge_usd,
                avg(s.bucket_excess_hr) AS avg_bucket_excess,
                count(*) AS n
            FROM _composite s
            JOIN lost l ON s.trader = l.trader
        """
        lost_row = d.fetchone(lost_sql)

        log(f"    Gained (composite adds): n={gained_row['n']}, "
            f"excess_hr={gained_row['avg_excess_hr']:.3f}, "
            f"consistency={gained_row['avg_consistency']:.2f}, "
            f"edge={gained_row['avg_edge_usd']:.2f}")
        log(f"    Lost  (composite drops): n={lost_row['n']}, "
            f"excess_hr={lost_row['avg_excess_hr']:.3f}, "
            f"consistency={lost_row['avg_consistency']:.2f}, "
            f"edge={lost_row['avg_edge_usd']:.2f}")

    # ── Step 7: walk-forward validation (3 folds) ─────────────────────────────
    log("\n[7] Walk-forward validation (6-month train, 3-month test, 3 folds)...")
    folds = [
        ("2024-01-01", "2024-07-01", "2024-07-01", "2024-10-01"),
        ("2024-04-01", "2024-10-01", "2024-10-01", "2025-01-01"),
        ("2024-07-01", "2025-01-01", "2025-01-01", "2025-04-01"),
    ]

    wf_results: list[dict] = []

    for train_start, train_end, test_start, test_end in folds:
        log(f"\n  Fold: train={train_start}→{train_end}, test={test_start}→{test_end}")

        # Base rates for this fold
        d.execute(f"""
            CREATE OR REPLACE TABLE _wf_base AS
            SELECT
                mt.tag,
                avg(CAST(mp.correct AS DOUBLE)) AS base_rate
            FROM maker_positions mp
            JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
            WHERE mp.position = 'YES'
              AND CAST(mp.resolved_at AS DATE) >= '{train_start}'
              AND CAST(mp.resolved_at AS DATE) < '{train_end}'
              AND mp.volume > 0
              AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
            GROUP BY mt.tag
            HAVING count(DISTINCT mp.condition_id) >= 50
        """)

        # Trader stats for this fold
        d.execute(f"""
            CREATE OR REPLACE TABLE _wf_stats AS
            WITH trader_tag AS (
                SELECT
                    mp.trader,
                    mt.tag,
                    count(DISTINCT mp.condition_id) AS n_positions,
                    avg(CAST(mp.correct AS DOUBLE)) AS hit_rate,
                    avg(mp.realized_pnl) AS avg_pnl,
                    avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) AS avg_conviction,
                    ROW_NUMBER() OVER (
                        PARTITION BY mp.trader
                        ORDER BY count(DISTINCT mp.condition_id) DESC
                    ) AS tag_rank
                FROM maker_positions mp
                JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
                WHERE CAST(mp.resolved_at AS DATE) >= '{train_start}'
                  AND CAST(mp.resolved_at AS DATE) < '{train_end}'
                  AND mp.volume > 0
                  AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
                GROUP BY mp.trader, mt.tag
                HAVING count(DISTINCT mp.condition_id) >= 10
                   AND avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) >= 0.90
            )
            SELECT
                t.trader, t.tag, t.n_positions, t.hit_rate, t.avg_pnl,
                (t.hit_rate - b.base_rate) AS excess_hr,
                b.base_rate
            FROM trader_tag t
            JOIN _wf_base b ON t.tag = b.tag
            WHERE t.tag_rank = 1 AND (t.hit_rate - b.base_rate) > 0
        """)

        # Consistency for fold
        d.execute(f"""
            CREATE OR REPLACE TABLE _wf_consistency AS
            WITH dated AS (
                SELECT
                    mp.trader,
                    mp.condition_id,
                    mp.correct,
                    date_trunc('month', CAST(mp.resolved_at AS DATE)) AS month
                FROM maker_positions mp
                WHERE CAST(mp.resolved_at AS DATE) >= '{train_start}'
                  AND CAST(mp.resolved_at AS DATE) < '{train_end}'
                  AND mp.volume > 0
                  AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
            ),
            monthly AS (
                SELECT
                    trader,
                    month,
                    avg(CAST(correct AS DOUBLE)) AS monthly_hr,
                    count(DISTINCT condition_id) AS n_pos
                FROM dated
                GROUP BY trader, month
                HAVING count(DISTINCT condition_id) >= 5
            )
            SELECT
                trader,
                avg(monthly_hr) / (stddev(monthly_hr) + 0.01) AS consistency_sharpe,
                count(*) AS n_months
            FROM monthly
            GROUP BY trader
            HAVING count(*) >= 3
        """)

        # Composite score for fold
        d.execute(f"""
            CREATE OR REPLACE TABLE _wf_composite AS
            WITH joined AS (
                SELECT
                    s.*,
                    COALESCE(c.consistency_sharpe, 0.0) AS consistency_sharpe
                FROM _wf_stats s
                LEFT JOIN _wf_consistency c ON s.trader = c.trader
            )
            SELECT *,
                percent_rank() OVER (PARTITION BY tag ORDER BY excess_hr)          AS pct_excess_hr,
                percent_rank() OVER (PARTITION BY tag ORDER BY consistency_sharpe)  AS pct_consistency,
                percent_rank() OVER (PARTITION BY tag ORDER BY avg_pnl)             AS pct_edge_usd,
                ({W_EXCESS_HR} * percent_rank() OVER (PARTITION BY tag ORDER BY excess_hr)
               + {W_CONSISTENCY} * percent_rank() OVER (PARTITION BY tag ORDER BY consistency_sharpe)
               + {W_EDGE_USD} * percent_rank() OVER (PARTITION BY tag ORDER BY avg_pnl)
                ) AS composite_score
            FROM joined
        """)

        # Evaluate on test window (K=50, N=3, composite vs hr-only)
        for tag in TAGS:
            tag_base_row = d.fetchone(f"SELECT base_rate FROM _wf_base WHERE tag='{tag}'")
            if tag_base_row is None:
                continue
            base_rate = tag_base_row["base_rate"]
            n_tag_wf = d.fetchone(
                f"SELECT count() FROM _wf_composite WHERE tag='{tag}'"
            )["count_star()"]
            if n_tag_wf < 10:
                continue

            K = min(50, n_tag_wf)
            N = 3

            hold_filter = (
                f"AND date_diff('hour', mp.first_trade, mp.resolved_at) >= {SPORTS_HOLD_FILTER_H}"
                if tag in ("Sports", "Tennis", "NBA", "NFL", "Soccer", "MLB", "NHL", "NCAA Basketball")
                else ""
            )

            for ranking in ["composite", "hr_only"]:
                rank_col = "composite_score" if ranking == "composite" else "pct_excess_hr"
                sig_sql = f"""
                    WITH pool AS (
                        SELECT trader FROM _wf_composite WHERE tag='{tag}'
                        ORDER BY {rank_col} DESC LIMIT {K}
                    ),
                    test_pos AS (
                        SELECT mp.condition_id, mp.trader, mp.position, mp.yes_won,
                               mp.net_usd, mp.first_trade, mp.resolved_at
                        FROM maker_positions mp
                        JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
                        JOIN pool p ON mp.trader = p.trader
                        WHERE mt.tag = '{tag}'
                          AND CAST(mp.resolved_at AS DATE) >= '{test_start}'
                          AND CAST(mp.resolved_at AS DATE) < '{test_end}'
                          AND CAST(mp.first_trade AS DATE) >= '{test_start}'
                          AND mp.volume > 0
                          AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
                          {hold_filter}
                    ),
                    market_agg AS (
                        SELECT
                            condition_id,
                            any_value(yes_won) AS yes_won,
                            count(DISTINCT trader) AS n_qual,
                            sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir,
                            max(first_trade) AS signal_entry,
                            date_diff('hour', max(first_trade), any_value(resolved_at)) AS hold_hours
                        FROM test_pos
                        GROUP BY condition_id
                        HAVING count(DISTINCT trader) >= {N}
                    )
                    SELECT
                        count(*) AS n_signals,
                        avg(CAST(CASE WHEN vol_dir > 0 THEN yes_won ELSE (1-yes_won) END AS DOUBLE)) AS hit_rate,
                        median(hold_hours) AS med_hold_hours
                    FROM market_agg
                """
                row = d.fetchone(sig_sql)
                if row is None or row["n_signals"] == 0:
                    continue

                hr = row["hit_rate"]
                excess = hr - base_rate
                wf_results.append({
                    "fold": f"{test_start[:7]}→{test_end[:7]}",
                    "tag": tag,
                    "K": K,
                    "N": N,
                    "ranking": ranking,
                    "n_signals": int(row["n_signals"]),
                    "hit_rate": round(hr, 4),
                    "excess_hr_pp": round(excess * 100, 2),
                    "base_rate": round(base_rate, 4),
                })
                log(f"    {tag} {ranking:10s}: n={row['n_signals']:4d}  HR={hr:.3f}  excess={excess*100:+.1f}pp")

    # ── Step 8: direction decomposition on best config ─────────────────────────
    log("\n[8] Direction decomposition for best configs (K=50, N=3)...")
    dir_results: list[dict] = []

    for tag in TAGS:
        tag_base_row = d.fetchone(f"SELECT base_rate FROM _tag_base_rates WHERE tag='{tag}'")
        if tag_base_row is None:
            continue
        base_rate_yes = tag_base_row["base_rate"]
        base_rate_no = 1 - base_rate_yes

        n_tag_traders = d.fetchone(
            f"SELECT count() FROM _composite WHERE tag='{tag}'"
        )["count_star()"]
        K = min(50, n_tag_traders)

        hold_filter = (
            f"AND date_diff('hour', mp.first_trade, mp.resolved_at) >= {SPORTS_HOLD_FILTER_H}"
            if tag in ("Sports", "Tennis", "NBA", "NFL", "Soccer")
            else ""
        )

        for ranking in ["composite", "hr_only"]:
            rank_col = "composite_score" if ranking == "composite" else "hr_rank_pct"
            dir_sql = f"""
                WITH pool AS (
                    SELECT trader FROM _composite WHERE tag='{tag}'
                    ORDER BY {rank_col} DESC LIMIT {K}
                ),
                test_pos AS (
                    SELECT mp.condition_id, mp.trader, mp.position, mp.yes_won,
                           mp.net_usd, mp.first_trade, mp.resolved_at
                    FROM maker_positions mp
                    JOIN _mkt_tags mt ON mp.condition_id = mt.condition_id
                    JOIN pool p ON mp.trader = p.trader
                    WHERE mt.tag = '{tag}'
                      AND CAST(mp.resolved_at AS DATE) >= '{TEST_START}'
                      AND CAST(mp.first_trade AS DATE) >= '{TEST_START}'
                      AND mp.volume > 0
                      AND mp.condition_id IN (SELECT condition_id FROM _valid_markets)
                      {hold_filter}
                ),
                market_agg AS (
                    SELECT
                        condition_id,
                        any_value(yes_won) AS yes_won,
                        count(DISTINCT trader) AS n_qual,
                        sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir
                    FROM test_pos
                    GROUP BY condition_id
                    HAVING count(DISTINCT trader) >= 3
                ),
                signals AS (
                    SELECT *,
                        CASE WHEN vol_dir > 0 THEN 'YES' ELSE 'NO' END AS direction
                    FROM market_agg
                )
                SELECT
                    direction,
                    count(*) AS n_signals,
                    avg(CAST(CASE WHEN direction='YES' THEN yes_won ELSE (1-yes_won) END AS DOUBLE)) AS hit_rate
                FROM signals
                GROUP BY direction
            """
            dir_df = d.query(dir_sql)
            for drow in dir_df.to_dicts():
                direction = drow["direction"]
                base = base_rate_yes if direction == "YES" else base_rate_no
                excess = drow["hit_rate"] - base
                dir_results.append({
                    "tag": tag,
                    "ranking": ranking,
                    "direction": direction,
                    "n_signals": drow["n_signals"],
                    "hit_rate": round(drow["hit_rate"], 4),
                    "base_rate": round(base, 4),
                    "excess_hr_pp": round(excess * 100, 2),
                })
                log(f"  {tag} {ranking:10s} {direction}: n={drow['n_signals']:4d}  "
                    f"HR={drow['hit_rate']:.3f}  excess={excess*100:+.1f}pp")

    # ── Step 9: write results to markdown ─────────────────────────────────────
    log("\n[9] Writing results markdown...")
    write_results(results, wf_results, dir_results)
    log(f"\nDone. Log: {LOG_FILE}")
    log(f"Results: {REPO}/research/hypotheses/scorecard-v2-strategies/discovery/composite_scorecard_pool.md")


def write_results(
    results: list[dict],
    wf_results: list[dict],
    dir_results: list[dict],
) -> None:
    import datetime

    out_path = (
        Path(__file__).parents[1]
        / "discovery"
        / "composite_scorecard_pool.md"
    )
    out_path.parent.mkdir(exist_ok=True)

    lines: list[str] = []
    lines.append("# Composite Scorecard Pool — Discovery Results")
    lines.append("")
    lines.append(f"**Date**: {datetime.date.today()}")
    lines.append("**Status**: VECTORIZED UPPER BOUNDS — tick-by-tick validation required")
    lines.append(f"**Train period**: before {TRAIN_END}")
    lines.append(f"**Test period**: {TEST_START} onwards")
    lines.append("")
    lines.append("## Scorecard Composition")
    lines.append("")
    lines.append("| Signal | Weight | Description |")
    lines.append("|--------|--------|-------------|")
    lines.append(f"| excess_hr | {W_EXCESS_HR} | HR vs tag-specific base rate (IC=0.744) |")
    lines.append(f"| consistency_sharpe | {W_CONSISTENCY} | Monthly HR Sharpe (≥6 months) |")
    lines.append(f"| avg_edge_usd | {W_EDGE_USD} | Average realized PnL per position |")
    lines.append(f"| bucket_excess_hr | {W_BUCKET_HR} | HR vs population in same 10pp price bucket |")
    lines.append("")
    lines.append("All components percentile-rank normalized to [0,1] within each tag.")
    lines.append("Composite = weighted sum. Compared against HR-only baseline.")
    lines.append("")

    # Main sweep results table
    lines.append("## Pool Sweep Results (Test Period)")
    lines.append("")
    lines.append("| Tag | K | N | Ranking | N Signals | HR | Excess HR | Med Hold h | CS |")
    lines.append("|-----|---|---|---------|-----------|-----|-----------|------------|-----|")
    for r in sorted(results, key=lambda x: (-x.get("cs", 0),)):
        hold_str = f"{r['med_hold_h']:.0f}h" if r.get("med_hold_h") else "—"
        lines.append(
            f"| {r['tag']} | {r['K']} | {r['N']} | {r['ranking']} "
            f"| {r['n_signals']} | {r['hit_rate']:.3f} | {r['excess_hr_pp']:+.1f}pp "
            f"| {hold_str} | {r['cs']:.1f} |"
        )
    lines.append("")

    # Head-to-head K=50 comparison
    lines.append("## Head-to-Head: Composite vs HR-Only (K=50, N=3)")
    lines.append("")
    lines.append("| Tag | Ranking | N Signals | HR | Excess HR |")
    lines.append("|-----|---------|-----------|-----|-----------|")
    k50 = [r for r in results if r["K"] == 50 and r["N"] == 3]
    for r in sorted(k50, key=lambda x: (x["tag"], x["ranking"])):
        lines.append(
            f"| {r['tag']} | {r['ranking']} "
            f"| {r['n_signals']} | {r['hit_rate']:.3f} | {r['excess_hr_pp']:+.1f}pp |"
        )
    lines.append("")

    # Direction decomposition
    lines.append("## Direction Decomposition (K=50, N=3)")
    lines.append("")
    lines.append("| Tag | Ranking | Direction | N Signals | HR | Base Rate | Excess HR |")
    lines.append("|-----|---------|-----------|-----------|-----|-----------|-----------|")
    for r in sorted(dir_results, key=lambda x: (x["tag"], x["ranking"], x["direction"])):
        lines.append(
            f"| {r['tag']} | {r['ranking']} | {r['direction']} "
            f"| {r['n_signals']} | {r['hit_rate']:.3f} "
            f"| {r['base_rate']:.3f} | {r['excess_hr_pp']:+.1f}pp |"
        )
    lines.append("")

    # Walk-forward results
    lines.append("## Walk-Forward Validation (3 Folds, K=50, N=3)")
    lines.append("")
    lines.append("| Fold | Tag | Ranking | N Signals | HR | Excess HR |")
    lines.append("|------|-----|---------|-----------|-----|-----------|")
    for r in sorted(wf_results, key=lambda x: (x["fold"], x["tag"], x["ranking"])):
        lines.append(
            f"| {r['fold']} | {r['tag']} | {r['ranking']} "
            f"| {r['n_signals']} | {r['hit_rate']:.3f} | {r['excess_hr_pp']:+.1f}pp |"
        )
    lines.append("")

    # Composite vs HR-only uplift summary
    lines.append("## Composite vs HR-Only Uplift (K=50, N=3)")
    lines.append("")
    lines.append("Uplift = composite_excess_hr - hr_only_excess_hr")
    lines.append("")
    lines.append("| Tag | HR-Only Excess | Composite Excess | Uplift |")
    lines.append("|-----|----------------|-----------------|--------|")
    for tag in TAGS:
        hr_row = next((r for r in k50 if r["tag"] == tag and r["ranking"] == "hr_only"), None)
        comp_row = next((r for r in k50 if r["tag"] == tag and r["ranking"] == "composite"), None)
        if hr_row and comp_row:
            uplift = comp_row["excess_hr_pp"] - hr_row["excess_hr_pp"]
            lines.append(
                f"| {tag} | {hr_row['excess_hr_pp']:+.1f}pp | "
                f"{comp_row['excess_hr_pp']:+.1f}pp | {uplift:+.1f}pp |"
            )
    lines.append("")

    lines.append("## Methodology Notes")
    lines.append("")
    lines.append("- All results are VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation)")
    lines.append("- Hold filter: Sports ≥4h, all others no filter")
    lines.append("- Market-level aggregation: each market counted once, vol-weighted direction")
    lines.append("- CRITICAL: only entries with first_trade >= test_start counted (copyable only)")
    lines.append("- Gambling exclusion: slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'")
    lines.append("- Market-maker exclusion: avg_conviction >= 0.90")
    lines.append("- Min 20 training positions per trader per dominant tag")
    lines.append("- Consistency: ≥6 months with ≥5 positions each (or 0.0 if absent)")
    lines.append("- Bucket excess HR: weighted avg (trader_hr - pop_hr) in 10pp price buckets")

    out_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
