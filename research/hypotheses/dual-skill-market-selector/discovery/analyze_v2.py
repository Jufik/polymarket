"""Dual-Skill Market Selector Analysis (v2 — fixed resolution semantics).

Fix: markets_resolved has 2 rows per market (YES + NO tokens).
Must filter to outcome='YES' and use token_won to get market-level YES base rate.
Or use maker_positions.correct directly for direction-aware HR.

For consensus signals:
- Sports YES: correct = yes_won (1 if YES side won)
- Politics NO: correct = 1 - yes_won (1 if NO side won)
"""

from __future__ import annotations

import sys
import time
import importlib.util

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from research.db import db

TRAIN_CUTOFF = "2025-07-01"
TEST_START = "2025-07-01"


def main():
    t0 = time.time()
    d = db()
    con = d.con

    # ── Setup ─────────────────────────────────────────────────────────────────
    con.execute("""
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
    """)

    con.execute("""
    CREATE OR REPLACE TABLE _ds_market_tags AS
    WITH tag_ranked AS (
        SELECT m.condition_id, m.slug, m.event_id,
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
        SELECT condition_id, slug, event_id,
               arg_min(label, tag_priority) AS primary_tag
        FROM tag_ranked
        GROUP BY condition_id, slug, event_id
    )
    SELECT * FROM tag_assigned;
    """)

    # Create a market-level yes_won view (1 row per market)
    con.execute("""
    CREATE OR REPLACE TABLE _ds_market_yes_won AS
    SELECT condition_id, token_won AS yes_won, resolved_at
    FROM markets_resolved
    WHERE outcome = 'YES';
    """)

    print(f"Setup done in {time.time()-t0:.1f}s")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1: Identify dual-skill traders
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 1: Identify Dual-Skill Traders")
    print("=" * 70)

    # YES direction BEH
    con.execute("""
    CREATE OR REPLACE TABLE _ds_yes_beh AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE p.position = 'YES'
          AND p.resolved_at < TIMESTAMP '2025-07-01'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr, count(*) AS n_pop
        FROM price_data GROUP BY price_bucket HAVING count(*) >= 50
    ),
    trader_bucket AS (
        SELECT pd.trader, pd.price_bucket,
               avg(pd.correct) AS bucket_hr, bp.pop_hr,
               count(*) AS n_in_bucket
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    )
    SELECT
        trader,
        sum(n_in_bucket) AS n_yes_positions,
        count(*) AS n_buckets,
        sum((bucket_hr - pop_hr) * n_in_bucket) / sum(n_in_bucket) AS yes_beh
    FROM trader_bucket
    GROUP BY trader
    HAVING sum(n_in_bucket) >= 10 AND count(*) >= 2;
    """)

    # NO direction BEH
    con.execute("""
    CREATE OR REPLACE TABLE _ds_no_beh AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            p.correct::DOUBLE AS correct,
            CAST(
                floor(
                    COALESCE(
                        1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)),
                        abs(p.net_usd) / NULLIF(p.net_no, 0)
                    ) * 10
                ) * 0.10
            AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE p.position = 'NO'
          AND p.resolved_at < TIMESTAMP '2025-07-01'
          AND p.volume > 0
          AND COALESCE(
                1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)),
                abs(p.net_usd) / NULLIF(p.net_no, 0)
              ) BETWEEN 0.05 AND 0.95
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr, count(*) AS n_pop
        FROM price_data GROUP BY price_bucket HAVING count(*) >= 50
    ),
    trader_bucket AS (
        SELECT pd.trader, pd.price_bucket,
               avg(pd.correct) AS bucket_hr, bp.pop_hr,
               count(*) AS n_in_bucket
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    )
    SELECT
        trader,
        sum(n_in_bucket) AS n_no_positions,
        count(*) AS n_buckets,
        sum((bucket_hr - pop_hr) * n_in_bucket) / sum(n_in_bucket) AS no_beh
    FROM trader_bucket
    GROUP BY trader
    HAVING sum(n_in_bucket) >= 10 AND count(*) >= 2;
    """)

    con.execute("""
    CREATE OR REPLACE TABLE _ds_dual_skill AS
    SELECT
        y.trader,
        y.n_yes_positions,
        y.yes_beh,
        n.n_no_positions,
        n.no_beh,
        y.n_yes_positions + n.n_no_positions AS total_positions,
        (y.yes_beh + n.no_beh) / 2.0 AS avg_beh
    FROM _ds_yes_beh y
    JOIN _ds_no_beh n ON y.trader = n.trader
    WHERE y.yes_beh >= 0.02 AND n.no_beh >= 0.02;
    """)

    dual_count = con.execute("SELECT count() FROM _ds_dual_skill").fetchone()[0]
    yes_count = con.execute("SELECT count() FROM _ds_yes_beh WHERE yes_beh >= 0.02").fetchone()[0]
    no_count = con.execute("SELECT count() FROM _ds_no_beh WHERE no_beh >= 0.02").fetchone()[0]

    print(f"\nYES-skilled (BEH >= 0.02, >= 10 positions): {yes_count:,}")
    print(f"NO-skilled  (BEH >= 0.02, >= 10 positions): {no_count:,}")
    print(f"Dual-skilled (both):                        {dual_count:,}")

    stats = d.query("""
        SELECT
            min(total_positions) AS min_pos,
            avg(total_positions)::INT AS avg_pos,
            median(total_positions)::INT AS med_pos,
            max(total_positions) AS max_pos,
            round(avg(yes_beh), 4) AS avg_yes_beh,
            round(avg(no_beh), 4) AS avg_no_beh,
            round(avg(avg_beh), 4) AS avg_overall_beh
        FROM _ds_dual_skill
    """)
    print(f"\nDual-skill distribution:")
    print(stats)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2: Market Selection Quality (OOS)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 2: Market Selection Quality (OOS: Jul 2025+)")
    print("=" * 70)

    # Markets entered by dual-skill traders OOS
    con.execute("""
    CREATE OR REPLACE TABLE _ds_entered_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.first_trade >= TIMESTAMP '2025-07-01'
      AND p.volume > 0;
    """)
    entered_count = con.execute("SELECT count() FROM _ds_entered_markets").fetchone()[0]
    print(f"\nMarkets with dual-skill entry (OOS): {entered_count:,}")

    # Market quality comparison using market-level yes_won
    quality = d.query("""
    WITH market_info AS (
        SELECT
            mt.condition_id,
            mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            myw.yes_won,
            sum(p.volume) AS total_volume,
            count(DISTINCT p.trader) AS n_traders
        FROM _ds_market_tags mt
        JOIN maker_positions p ON mt.condition_id = p.condition_id
        JOIN _ds_market_yes_won myw ON mt.condition_id = myw.condition_id
        LEFT JOIN _ds_entered_markets dm ON mt.condition_id = dm.condition_id
        WHERE p.resolved_at >= TIMESTAMP '2025-07-01'
          AND p.volume > 0
        GROUP BY mt.condition_id, mt.primary_tag, dm.condition_id, myw.yes_won
    )
    SELECT
        dual_entered,
        count() AS n_markets,
        round(avg(total_volume), 0)::BIGINT AS avg_volume,
        round(avg(n_traders), 1) AS avg_traders,
        round(avg(yes_won::DOUBLE), 4) AS yes_base_rate
    FROM market_info
    GROUP BY dual_entered
    ORDER BY dual_entered
    """)
    print("\nMarket quality (0=no dual entry, 1=dual entered):")
    print(quality)

    # Per-tag
    tag_quality = d.query("""
    WITH market_info AS (
        SELECT
            mt.condition_id,
            mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            myw.yes_won,
            sum(p.volume) AS total_volume,
            count(DISTINCT p.trader) AS n_traders
        FROM _ds_market_tags mt
        JOIN maker_positions p ON mt.condition_id = p.condition_id
        JOIN _ds_market_yes_won myw ON mt.condition_id = myw.condition_id
        LEFT JOIN _ds_entered_markets dm ON mt.condition_id = dm.condition_id
        WHERE p.resolved_at >= TIMESTAMP '2025-07-01'
          AND p.volume > 0
        GROUP BY mt.condition_id, mt.primary_tag, dm.condition_id, myw.yes_won
    )
    SELECT
        primary_tag,
        dual_entered,
        count() AS n_markets,
        round(avg(total_volume), 0)::BIGINT AS avg_vol,
        round(avg(n_traders), 0)::INT AS avg_traders,
        round(avg(yes_won::DOUBLE), 4) AS yes_br
    FROM market_info
    WHERE primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY primary_tag, dual_entered
    ORDER BY primary_tag, dual_entered
    """)
    print("\nPer-tag market quality:")
    print(tag_quality)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3: Population HR on dual-skill-entered markets
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 3: Population HR on Dual-Skill-Entered Markets (OOS)")
    print("=" * 70)

    pop_hr = d.query("""
    SELECT
        CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
        p.position,
        count() AS n_positions,
        round(avg(p.correct::DOUBLE), 4) AS hr,
        round(avg(p.net_usd), 2) AS avg_pnl
    FROM maker_positions p
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
    WHERE p.resolved_at >= TIMESTAMP '2025-07-01'
      AND p.volume > 0
    GROUP BY 1, p.position
    ORDER BY 1, p.position
    """)
    print("\nPopulation HR by dual-entry status and position:")
    print(pop_hr)

    # Sports-specific
    pop_hr_sports = d.query("""
    SELECT
        CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
        p.position,
        count() AS n_positions,
        round(avg(p.correct::DOUBLE), 4) AS hr,
        round(avg(p.net_usd), 2) AS avg_pnl
    FROM maker_positions p
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
    WHERE p.resolved_at >= TIMESTAMP '2025-07-01'
      AND p.volume > 0
      AND mt.primary_tag = 'Sports'
    GROUP BY 1, p.position
    ORDER BY 1, p.position
    """)
    print("\nSports population HR:")
    print(pop_hr_sports)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4: Pre-filter for Sports YES K=25 N>=2
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 4: Pre-filter for Sports YES Consensus (K=25, N>=2)")
    print("=" * 70)

    _v3_path = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"
    _spec = importlib.util.spec_from_file_location("build_pools_v3", _v3_path)
    _v3_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_v3_mod)

    sports_yes_pool, sports_markets, gambling = _v3_mod.build_sports_yes_pool_v3(k=25)

    dual_traders = {r[0].lower() for r in con.execute(
        "SELECT trader FROM _ds_dual_skill"
    ).fetchall()}

    overlap_sports = sports_yes_pool & dual_traders
    print(f"\nSports YES pool (K=25): {len(sports_yes_pool)} traders")
    print(f"Dual-skill pool: {len(dual_traders)} traders")
    print(f"Overlap: {len(overlap_sports)} traders ({len(overlap_sports)/len(sports_yes_pool)*100:.1f}% of pool)")

    # Build consensus and evaluate with/without dual-skill filter
    con.execute("""
    CREATE OR REPLACE TABLE _ds_sports_pool AS
    SELECT UNNEST(?) AS trader
    """, [list(sports_yes_pool)])

    con.execute("""
    CREATE OR REPLACE TABLE _ds_sports_consensus AS
    WITH oos_entries AS (
        SELECT
            p.condition_id,
            lower(p.trader) AS trader
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _ds_sports_pool pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND p.volume > 0
          AND p.resolved_at >= TIMESTAMP '2025-07-01'
    ),
    consensus AS (
        SELECT
            condition_id,
            count(DISTINCT trader) AS n_pool_traders
        FROM oos_entries
        GROUP BY condition_id
    )
    SELECT
        c.condition_id,
        c.n_pool_traders,
        myw.yes_won
    FROM consensus c
    JOIN _ds_market_yes_won myw ON c.condition_id = myw.condition_id
    WHERE c.n_pool_traders >= 2;
    """)

    # Dual-skill entered Sports markets (OOS)
    con.execute("""
    CREATE OR REPLACE TABLE _ds_sports_dual_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND p.volume > 0
      AND p.first_trade >= TIMESTAMP '2025-07-01';
    """)

    n_dual_sports = con.execute("SELECT count() FROM _ds_sports_dual_markets").fetchone()[0]
    n_consensus = con.execute("SELECT count() FROM _ds_sports_consensus").fetchone()[0]
    print(f"\nSports consensus signals (N>=2): {n_consensus}")
    print(f"Sports markets with dual-skill entry (OOS): {n_dual_sports}")

    sports_filter = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_pool_traders,
            sc.yes_won,
            CASE WHEN sdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS has_dual
        FROM _ds_sports_consensus sc
        LEFT JOIN _ds_sports_dual_markets sdm ON sc.condition_id = sdm.condition_id
    )
    SELECT
        'All signals' AS filter_type,
        count() AS n_markets,
        round(avg(yes_won::DOUBLE), 4) AS yes_hr,
        sum(yes_won::INT) AS yes_wins,
        count() - sum(yes_won::INT) AS no_wins
    FROM base
    UNION ALL
    SELECT
        'Dual-skill filtered',
        count(),
        round(avg(yes_won::DOUBLE), 4),
        sum(yes_won::INT),
        count() - sum(yes_won::INT)
    FROM base WHERE has_dual = 1
    UNION ALL
    SELECT
        'NOT dual-filtered',
        count(),
        round(avg(yes_won::DOUBLE), 4),
        sum(yes_won::INT),
        count() - sum(yes_won::INT)
    FROM base WHERE has_dual = 0
    """)
    print("\nSports YES consensus with dual-skill filter:")
    print(sports_filter)

    # OOS Sports YES base rate for context
    sports_br = d.query("""
    SELECT round(avg(yes_won::DOUBLE), 4) AS sports_yes_br, count() AS n
    FROM _ds_market_yes_won myw
    JOIN _ds_market_tags mt ON myw.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND myw.resolved_at >= TIMESTAMP '2025-07-01'
    """)
    print(f"\nSports OOS YES base rate: {sports_br}")

    # By consensus level
    by_consensus = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_pool_traders,
            sc.yes_won,
            CASE WHEN sdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS has_dual
        FROM _ds_sports_consensus sc
        LEFT JOIN _ds_sports_dual_markets sdm ON sc.condition_id = sdm.condition_id
    )
    SELECT
        n_pool_traders AS N,
        count() AS n_all,
        round(avg(yes_won::DOUBLE), 4) AS hr_all,
        sum(has_dual) AS n_dual,
        CASE WHEN sum(has_dual) > 0
            THEN round(avg(CASE WHEN has_dual = 1 THEN yes_won::DOUBLE END), 4)
            ELSE NULL END AS hr_dual,
        count() - sum(has_dual) AS n_no_dual,
        CASE WHEN count() - sum(has_dual) > 0
            THEN round(avg(CASE WHEN has_dual = 0 THEN yes_won::DOUBLE END), 4)
            ELSE NULL END AS hr_no_dual
    FROM base
    GROUP BY n_pool_traders
    ORDER BY n_pool_traders
    """)
    print("\nBy consensus level:")
    print(by_consensus)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 5: Pre-filter for Politics NO K=100 N>=2
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 5: Pre-filter for Politics NO Consensus (K=100, N>=2)")
    print("=" * 70)

    politics_no_pool, politics_markets, _ = _v3_mod.build_politics_no_pool(k=100)

    overlap_politics = politics_no_pool & dual_traders
    print(f"\nPolitics NO pool (K=100): {len(politics_no_pool)} traders")
    print(f"Overlap with dual-skill: {len(overlap_politics)} ({len(overlap_politics)/len(politics_no_pool)*100:.1f}%)")

    con.execute("""
    CREATE OR REPLACE TABLE _ds_politics_pool AS
    SELECT UNNEST(?) AS trader
    """, [list(politics_no_pool)])

    # NO consensus: we want markets where N>=2 pool traders took NO positions
    # HR = % where NO won = (1 - yes_won)
    con.execute("""
    CREATE OR REPLACE TABLE _ds_politics_no_consensus AS
    WITH oos_entries AS (
        SELECT
            p.condition_id,
            lower(p.trader) AS trader
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _ds_politics_pool pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Politics'
          AND p.position = 'NO'
          AND p.volume > 0
          AND p.resolved_at >= TIMESTAMP '2025-07-01'
    ),
    consensus AS (
        SELECT
            condition_id,
            count(DISTINCT trader) AS n_pool_traders
        FROM oos_entries
        GROUP BY condition_id
    )
    SELECT
        c.condition_id,
        c.n_pool_traders,
        myw.yes_won
    FROM consensus c
    JOIN _ds_market_yes_won myw ON c.condition_id = myw.condition_id
    WHERE c.n_pool_traders >= 2;
    """)

    # Politics dual-skill markets
    con.execute("""
    CREATE OR REPLACE TABLE _ds_politics_dual_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND p.volume > 0
      AND p.first_trade >= TIMESTAMP '2025-07-01';
    """)

    politics_filter = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_pool_traders,
            sc.yes_won,
            CASE WHEN pdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS has_dual
        FROM _ds_politics_no_consensus sc
        LEFT JOIN _ds_politics_dual_markets pdm ON sc.condition_id = pdm.condition_id
    )
    SELECT
        'All signals' AS filter_type,
        count() AS n_markets,
        round(avg(CASE WHEN yes_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr,
        sum(CASE WHEN yes_won = 0 THEN 1 ELSE 0 END) AS no_wins,
        sum(yes_won::INT) AS yes_wins
    FROM base
    UNION ALL
    SELECT
        'Dual-skill filtered',
        count(),
        round(avg(CASE WHEN yes_won = 0 THEN 1.0 ELSE 0.0 END), 4),
        sum(CASE WHEN yes_won = 0 THEN 1 ELSE 0 END),
        sum(yes_won::INT)
    FROM base WHERE has_dual = 1
    UNION ALL
    SELECT
        'NOT dual-filtered',
        count(),
        round(avg(CASE WHEN yes_won = 0 THEN 1.0 ELSE 0.0 END), 4),
        sum(CASE WHEN yes_won = 0 THEN 1 ELSE 0 END),
        sum(yes_won::INT)
    FROM base WHERE has_dual = 0
    """)
    print("\nPolitics NO consensus with dual-skill filter:")
    print(politics_filter)

    # Politics OOS NO base rate
    pol_br = d.query("""
    SELECT round(1.0 - avg(yes_won::DOUBLE), 4) AS politics_no_br, count() AS n
    FROM _ds_market_yes_won myw
    JOIN _ds_market_tags mt ON myw.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND myw.resolved_at >= TIMESTAMP '2025-07-01'
    """)
    print(f"\nPolitics OOS NO base rate: {pol_br}")

    # By consensus level
    pol_by_consensus = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_pool_traders,
            sc.yes_won,
            CASE WHEN pdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS has_dual
        FROM _ds_politics_no_consensus sc
        LEFT JOIN _ds_politics_dual_markets pdm ON sc.condition_id = pdm.condition_id
    )
    SELECT
        n_pool_traders AS N,
        count() AS n_all,
        round(avg(CASE WHEN yes_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr_all,
        sum(has_dual) AS n_dual,
        CASE WHEN sum(has_dual) > 0
            THEN round(avg(CASE WHEN has_dual = 1 AND yes_won = 0 THEN 1.0
                               WHEN has_dual = 1 AND yes_won = 1 THEN 0.0
                               ELSE NULL END), 4)
            ELSE NULL END AS no_hr_dual,
        count() - sum(has_dual) AS n_no_dual,
        CASE WHEN count() - sum(has_dual) > 0
            THEN round(avg(CASE WHEN has_dual = 0 AND yes_won = 0 THEN 1.0
                               WHEN has_dual = 0 AND yes_won = 1 THEN 0.0
                               ELSE NULL END), 4)
            ELSE NULL END AS no_hr_no_dual
    FROM base
    GROUP BY n_pool_traders
    ORDER BY n_pool_traders
    """)
    print("\nBy consensus level:")
    print(pol_by_consensus)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 6: Hold times
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 6: Hold Time Analysis")
    print("=" * 70)

    hold = d.query("""
    WITH market_info AS (
        SELECT
            mt.condition_id,
            mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            date_diff('day', min(p.first_trade), max(p.resolved_at)) AS hold_days
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
        WHERE p.resolved_at >= TIMESTAMP '2025-07-01'
          AND p.volume > 0
        GROUP BY mt.condition_id, mt.primary_tag, dm.condition_id
    )
    SELECT
        primary_tag,
        dual_entered,
        count() AS n_markets,
        round(avg(hold_days), 1) AS avg_hold,
        median(hold_days)::INT AS med_hold
    FROM market_info
    WHERE primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY primary_tag, dual_entered
    ORDER BY primary_tag, dual_entered
    """)
    print("\nHold time comparison:")
    print(hold)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 7: Timing — dual-skill entry vs pool consensus formation
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 7: Timing (Dual-Skill Entry vs Pool Consensus)")
    print("=" * 70)

    timing = d.query("""
    WITH pool_entries AS (
        SELECT
            p.condition_id,
            min(p.first_trade) AS pool_first_entry,
            count(DISTINCT lower(p.trader)) AS n_pool_traders
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _ds_sports_pool pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND p.volume > 0
          AND p.resolved_at >= TIMESTAMP '2025-07-01'
        GROUP BY p.condition_id
        HAVING count(DISTINCT lower(p.trader)) >= 2
    ),
    dual_entries AS (
        SELECT
            p.condition_id,
            min(p.first_trade) AS dual_first_entry
        FROM _ds_dual_skill ds
        JOIN maker_positions p ON ds.trader = p.trader
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = 'Sports'
          AND p.volume > 0
          AND p.first_trade >= TIMESTAMP '2025-07-01'
        GROUP BY p.condition_id
    )
    SELECT
        count() AS n_overlap_markets,
        sum(CASE WHEN de.dual_first_entry <= pe.pool_first_entry THEN 1 ELSE 0 END) AS dual_entered_first,
        sum(CASE WHEN de.dual_first_entry > pe.pool_first_entry THEN 1 ELSE 0 END) AS pool_entered_first,
        round(avg(date_diff('hour', de.dual_first_entry, pe.pool_first_entry)), 1) AS avg_hours_dual_ahead,
        median(date_diff('hour', de.dual_first_entry, pe.pool_first_entry))::INT AS med_hours_dual_ahead
    FROM pool_entries pe
    JOIN dual_entries de ON pe.condition_id = de.condition_id
    """)
    print("\nTiming (Sports YES pool vs dual-skill, positive = dual ahead):")
    print(timing)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 8: Dual-skill consensus as standalone signal
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 8: Dual-Skill Consensus as Standalone Signal")
    print("=" * 70)

    # How well does N>=K dual-skill traders entering a Sports market predict YES?
    standalone = d.query("""
    WITH dual_entries AS (
        SELECT
            p.condition_id,
            count(DISTINCT lower(ds.trader)) AS n_dual
        FROM _ds_dual_skill ds
        JOIN maker_positions p ON ds.trader = p.trader
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND p.volume > 0
          AND p.resolved_at >= TIMESTAMP '2025-07-01'
        GROUP BY p.condition_id
    )
    SELECT
        n_dual,
        count() AS n_markets,
        round(avg(myw.yes_won::DOUBLE), 4) AS yes_hr
    FROM dual_entries de
    JOIN _ds_market_yes_won myw ON de.condition_id = myw.condition_id
    WHERE myw.resolved_at >= TIMESTAMP '2025-07-01'
    GROUP BY n_dual
    HAVING count() >= 5
    ORDER BY n_dual
    """)
    print("\nSports YES: dual-skill consensus as standalone signal:")
    print(standalone)

    # Same for Politics NO
    standalone_pol = d.query("""
    WITH dual_entries AS (
        SELECT
            p.condition_id,
            count(DISTINCT lower(ds.trader)) AS n_dual
        FROM _ds_dual_skill ds
        JOIN maker_positions p ON ds.trader = p.trader
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = 'Politics'
          AND p.position = 'NO'
          AND p.volume > 0
          AND p.resolved_at >= TIMESTAMP '2025-07-01'
        GROUP BY p.condition_id
    )
    SELECT
        n_dual,
        count() AS n_markets,
        round(avg(CASE WHEN myw.yes_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr
    FROM dual_entries de
    JOIN _ds_market_yes_won myw ON de.condition_id = myw.condition_id
    WHERE myw.resolved_at >= TIMESTAMP '2025-07-01'
    GROUP BY n_dual
    HAVING count() >= 5
    ORDER BY n_dual
    """)
    print("\nPolitics NO: dual-skill consensus as standalone signal:")
    print(standalone_pol)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 9: BEH threshold sensitivity for filter quality
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 9: BEH Threshold Sensitivity")
    print("=" * 70)

    for beh in [0.02, 0.05, 0.10, 0.15, 0.20]:
        row = con.execute(f"""
            SELECT count() AS n
            FROM _ds_yes_beh y
            JOIN _ds_no_beh n ON y.trader = n.trader
            WHERE y.yes_beh >= {beh} AND n.no_beh >= {beh}
        """).fetchone()
        print(f"  BEH >= {beh:.2f}: {row[0]:>5} traders")

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"Total analysis time: {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
