"""Dual-Skill Market Selector Analysis.

Hypothesis: Traders with positive bucket-excess-HR on BOTH YES and NO entries
are highly selective about which markets they enter. Their market entry could
serve as a "market quality" pre-filter for existing consensus strategies.

Steps:
1. Identify dual-skill traders (BEH >= 0.02 on YES AND NO, min 10 positions each)
2. Compare market quality: dual-skill-entered vs not
3. Test as pre-filter for Sports YES (K=25, N=2) and Politics NO (K=100, N=2)
4. Measure overlap with existing pools
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from research.db import db

TRAIN_CUTOFF = "2025-07-01"
TEST_START = "2025-07-01"


def main():
    t0 = time.time()
    d = db()
    con = d.con

    # ── Setup: gambling filter + market tags ──────────────────────────────────
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

    print(f"Setup done in {time.time()-t0:.1f}s")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1: Identify dual-skill traders
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 1: Identify Dual-Skill Traders")
    print("=" * 70)

    # Compute per-trader, per-direction bucket-excess-HR
    con.execute("""
    CREATE OR REPLACE TABLE _ds_yes_beh AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            mt.primary_tag,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '2025-07-01'
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

    con.execute("""
    CREATE OR REPLACE TABLE _ds_no_beh AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            p.correct::DOUBLE AS correct,
            mt.primary_tag,
            -- NO entry price = 1 - YES price or direct NO price
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
          AND CAST(p.resolved_at AS DATE) < '2025-07-01'
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

    # Identify dual-skill traders
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

    # Distribution stats
    stats = d.query("""
        SELECT
            min(total_positions) AS min_pos,
            avg(total_positions)::INT AS avg_pos,
            median(total_positions)::INT AS med_pos,
            max(total_positions) AS max_pos,
            avg(yes_beh) AS avg_yes_beh,
            avg(no_beh) AS avg_no_beh,
            avg(avg_beh) AS avg_overall_beh,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY avg_beh) AS beh_p25,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY avg_beh) AS beh_p75
        FROM _ds_dual_skill
    """)
    print(f"\nDual-skill distribution:")
    for col in stats.columns:
        val = stats[col][0]
        print(f"  {col}: {val:.4f}" if isinstance(val, float) else f"  {col}: {val}")

    # Per-tag breakdown of dual-skill traders
    tag_breakdown = d.query("""
        SELECT
            mt.primary_tag,
            count(DISTINCT ds.trader) AS n_dual_traders,
            count(DISTINCT p.condition_id) AS n_markets_entered,
            avg(p.correct::DOUBLE) AS avg_hr
        FROM _ds_dual_skill ds
        JOIN maker_positions p ON ds.trader = p.trader
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE CAST(p.resolved_at AS DATE) < '2025-07-01'
          AND p.volume > 0
        GROUP BY mt.primary_tag
        ORDER BY n_markets_entered DESC
    """)
    print(f"\nDual-skill traders per tag:")
    print(tag_breakdown)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2: Market selection quality
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 2: Market Selection Quality (OOS: Jul 2025+)")
    print("=" * 70)

    # Get markets where dual-skill traders entered during test period
    con.execute("""
    CREATE OR REPLACE TABLE _ds_entered_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE CAST(p.first_trade AS DATE) >= '2025-07-01'
      AND p.volume > 0;
    """)

    entered_count = con.execute("SELECT count() FROM _ds_entered_markets").fetchone()[0]
    print(f"\nMarkets with dual-skill entry (OOS): {entered_count:,}")

    # Compare resolved market quality: dual-skill entered vs not
    quality = d.query("""
    WITH all_oos_markets AS (
        SELECT DISTINCT p.condition_id, mt.primary_tag
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
    ),
    market_stats AS (
        SELECT
            m.condition_id,
            m.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            mr.token_won,
            mr.resolved_at,
            -- Market-level stats
            sum(p.volume) AS total_volume,
            count(DISTINCT p.trader) AS n_traders,
            avg(p.correct::DOUBLE) AS market_avg_hr
        FROM all_oos_markets m
        JOIN maker_positions p ON m.condition_id = p.condition_id
        JOIN markets_resolved mr ON m.condition_id = mr.condition_id
        LEFT JOIN _ds_entered_markets dm ON m.condition_id = dm.condition_id
        WHERE p.volume > 0 AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
        GROUP BY m.condition_id, m.primary_tag, dm.condition_id, mr.token_won, mr.resolved_at
    )
    SELECT
        dual_entered,
        count() AS n_markets,
        avg(total_volume) AS avg_volume,
        avg(n_traders) AS avg_traders,
        avg(token_won::DOUBLE) AS pct_yes_won,
        avg(market_avg_hr) AS avg_market_hr
    FROM market_stats
    GROUP BY dual_entered
    ORDER BY dual_entered
    """)
    print("\nMarket quality comparison (0 = no dual-skill entry, 1 = dual-skill entered):")
    print(quality)

    # Per-tag comparison
    tag_quality = d.query("""
    WITH all_oos_markets AS (
        SELECT DISTINCT p.condition_id, mt.primary_tag
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
    ),
    market_stats AS (
        SELECT
            m.condition_id,
            m.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            mr.token_won,
            sum(p.volume) AS total_volume,
            count(DISTINCT p.trader) AS n_traders
        FROM all_oos_markets m
        JOIN maker_positions p ON m.condition_id = p.condition_id
        JOIN markets_resolved mr ON m.condition_id = mr.condition_id
        LEFT JOIN _ds_entered_markets dm ON m.condition_id = dm.condition_id
        WHERE p.volume > 0 AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
        GROUP BY m.condition_id, m.primary_tag, dm.condition_id, mr.token_won
    )
    SELECT
        primary_tag,
        dual_entered,
        count() AS n_markets,
        avg(total_volume)::INT AS avg_volume,
        avg(n_traders)::INT AS avg_traders,
        round(avg(token_won::DOUBLE), 3) AS pct_yes_won
    FROM market_stats
    WHERE primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY primary_tag, dual_entered
    ORDER BY primary_tag, dual_entered
    """)
    print("\nPer-tag market quality:")
    print(tag_quality)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3: Population HR on dual-skill-entered vs not
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 3: Population HR on Dual-Skill-Entered Markets")
    print("=" * 70)

    pop_hr = d.query("""
    WITH oos_positions AS (
        SELECT
            p.trader, p.condition_id, p.position,
            p.correct::DOUBLE AS correct,
            p.net_usd,
            mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
    )
    SELECT
        dual_entered,
        count() AS n_positions,
        count(DISTINCT condition_id) AS n_markets,
        count(DISTINCT trader) AS n_traders,
        avg(correct) AS hr,
        avg(net_usd) AS avg_pnl
    FROM oos_positions
    GROUP BY dual_entered
    ORDER BY dual_entered
    """)
    print("\nPopulation HR comparison:")
    print(pop_hr)

    # Per-tag, per-direction population HR
    pop_hr_detail = d.query("""
    WITH oos_positions AS (
        SELECT
            p.trader, p.condition_id, p.position,
            p.correct::DOUBLE AS correct,
            p.net_usd,
            mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
    )
    SELECT
        primary_tag,
        position,
        dual_entered,
        count() AS n_positions,
        round(avg(correct), 4) AS hr,
        round(avg(net_usd), 2) AS avg_pnl
    FROM oos_positions
    WHERE primary_tag IN ('Sports', 'Politics')
    GROUP BY primary_tag, position, dual_entered
    ORDER BY primary_tag, position, dual_entered
    """)
    print("\nPer-tag, per-direction population HR:")
    print(pop_hr_detail)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4: Pre-filter for Sports YES consensus (K=25, N>=2)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 4: Pre-filter for Sports YES Consensus (K=25, N>=2)")
    print("=" * 70)

    # Build Sports YES pool K=25
    import importlib.util
    _v3_path = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"
    _spec = importlib.util.spec_from_file_location("build_pools_v3", _v3_path)
    _v3_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_v3_mod)

    sports_yes_pool, sports_markets, gambling = _v3_mod.build_sports_yes_pool_v3(k=25)
    print(f"Sports YES pool: {len(sports_yes_pool)} traders")

    # Get dual-skill trader set
    dual_traders = {r[0].lower() for r in con.execute(
        "SELECT trader FROM _ds_dual_skill"
    ).fetchall()}
    print(f"Dual-skill traders: {len(dual_traders)}")

    # Overlap
    overlap_sports = sports_yes_pool & dual_traders
    print(f"Sports YES pool ∩ Dual-skill: {len(overlap_sports)} traders")

    # Consensus signals with and without dual-skill filter
    # Build consensus on OOS markets
    con.execute(f"""
    CREATE OR REPLACE TABLE _ds_sports_consensus AS
    WITH pool_list AS (
        SELECT UNNEST(?) AS trader
    ),
    oos_entries AS (
        SELECT
            p.condition_id,
            p.trader,
            p.first_trade
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN pool_list pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND p.volume > 0
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    ),
    consensus AS (
        SELECT
            condition_id,
            count(DISTINCT lower(trader)) AS n_traders
        FROM oos_entries
        GROUP BY condition_id
    )
    SELECT
        c.condition_id,
        c.n_traders,
        mr.token_won
    FROM consensus c
    JOIN markets_resolved mr ON c.condition_id = mr.condition_id
    WHERE c.n_traders >= 2;
    """, [list(sports_yes_pool)])

    # Get dual-skill-entered market set (for Sports)
    con.execute("""
    CREATE OR REPLACE TABLE _ds_sports_dual_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND p.volume > 0
      AND CAST(p.first_trade AS DATE) >= '2025-07-01';
    """)

    sports_filter_results = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_traders,
            sc.token_won,
            CASE WHEN sdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_filtered
        FROM _ds_sports_consensus sc
        LEFT JOIN _ds_sports_dual_markets sdm ON sc.condition_id = sdm.condition_id
    )
    SELECT
        'All signals' AS filter,
        count() AS n_signals,
        count(DISTINCT condition_id) AS n_markets,
        round(avg(token_won::DOUBLE), 4) AS yes_hr,
        sum(CASE WHEN token_won = 1 THEN 1 ELSE 0 END) AS yes_wins,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base
    UNION ALL
    SELECT
        'Dual-skill filtered' AS filter,
        count() AS n_signals,
        count(DISTINCT condition_id) AS n_markets,
        round(avg(token_won::DOUBLE), 4) AS yes_hr,
        sum(CASE WHEN token_won = 1 THEN 1 ELSE 0 END) AS yes_wins,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base WHERE dual_filtered = 1
    UNION ALL
    SELECT
        'NOT dual-filtered' AS filter,
        count() AS n_signals,
        count(DISTINCT condition_id) AS n_markets,
        round(avg(token_won::DOUBLE), 4) AS yes_hr,
        sum(CASE WHEN token_won = 1 THEN 1 ELSE 0 END) AS yes_wins,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base WHERE dual_filtered = 0
    """)
    print("\nSports YES consensus (N>=2) with dual-skill filter:")
    print(sports_filter_results)

    # By consensus threshold
    threshold_results = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_traders,
            sc.token_won,
            CASE WHEN sdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_filtered
        FROM _ds_sports_consensus sc
        LEFT JOIN _ds_sports_dual_markets sdm ON sc.condition_id = sdm.condition_id
    )
    SELECT
        n_traders AS consensus,
        count() AS n_all,
        round(avg(token_won::DOUBLE), 4) AS hr_all,
        sum(dual_filtered) AS n_filtered,
        CASE WHEN sum(dual_filtered) > 0
             THEN round(avg(CASE WHEN dual_filtered = 1 THEN token_won::DOUBLE END), 4)
             ELSE NULL END AS hr_filtered,
        count() - sum(dual_filtered) AS n_excluded,
        CASE WHEN count() - sum(dual_filtered) > 0
             THEN round(avg(CASE WHEN dual_filtered = 0 THEN token_won::DOUBLE END), 4)
             ELSE NULL END AS hr_excluded
    FROM base
    GROUP BY n_traders
    ORDER BY n_traders
    """)
    print("\nBy consensus threshold:")
    print(threshold_results)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 5: Pre-filter for Politics NO consensus (K=100, N>=2)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 5: Pre-filter for Politics NO Consensus (K=100, N>=2)")
    print("=" * 70)

    politics_no_pool, politics_markets, _ = _v3_mod.build_politics_no_pool(k=100)
    print(f"Politics NO pool: {len(politics_no_pool)} traders")

    overlap_politics = politics_no_pool & dual_traders
    print(f"Politics NO pool ∩ Dual-skill: {len(overlap_politics)} traders")

    # Consensus signals
    con.execute(f"""
    CREATE OR REPLACE TABLE _ds_politics_no_consensus AS
    WITH pool_list AS (
        SELECT UNNEST(?) AS trader
    ),
    oos_entries AS (
        SELECT
            p.condition_id,
            p.trader,
            p.first_trade
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN pool_list pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Politics'
          AND p.position = 'NO'
          AND p.volume > 0
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    ),
    consensus AS (
        SELECT
            condition_id,
            count(DISTINCT lower(trader)) AS n_traders
        FROM oos_entries
        GROUP BY condition_id
    )
    SELECT
        c.condition_id,
        c.n_traders,
        mr.token_won
    FROM consensus c
    JOIN markets_resolved mr ON c.condition_id = mr.condition_id
    WHERE c.n_traders >= 2;
    """, [list(politics_no_pool)])

    # Dual-skill markets for Politics
    con.execute("""
    CREATE OR REPLACE TABLE _ds_politics_dual_markets AS
    SELECT DISTINCT p.condition_id
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND p.volume > 0
      AND CAST(p.first_trade AS DATE) >= '2025-07-01';
    """)

    politics_filter_results = d.query("""
    WITH base AS (
        SELECT
            sc.condition_id,
            sc.n_traders,
            sc.token_won,
            CASE WHEN pdm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_filtered
        FROM _ds_politics_no_consensus sc
        LEFT JOIN _ds_politics_dual_markets pdm ON sc.condition_id = pdm.condition_id
    )
    SELECT
        'All signals' AS filter,
        count() AS n_signals,
        round(avg(CASE WHEN token_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base
    UNION ALL
    SELECT
        'Dual-skill filtered' AS filter,
        count() AS n_signals,
        round(avg(CASE WHEN token_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base WHERE dual_filtered = 1
    UNION ALL
    SELECT
        'NOT dual-filtered' AS filter,
        count() AS n_signals,
        round(avg(CASE WHEN token_won = 0 THEN 1.0 ELSE 0.0 END), 4) AS no_hr,
        sum(CASE WHEN token_won = 0 THEN 1 ELSE 0 END) AS no_wins
    FROM base WHERE dual_filtered = 0
    """)
    print("\nPolitics NO consensus (N>=2) with dual-skill filter:")
    print(politics_filter_results)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 6: Hold time comparison
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 6: Hold Time & Resolution Speed")
    print("=" * 70)

    hold_times = d.query("""
    WITH oos_markets AS (
        SELECT DISTINCT p.condition_id, mt.primary_tag,
            CASE WHEN dm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS dual_entered,
            date_diff('day', min(p.first_trade), max(p.resolved_at)) AS hold_days
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        LEFT JOIN _ds_entered_markets dm ON p.condition_id = dm.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
        GROUP BY p.condition_id, mt.primary_tag, dm.condition_id
    )
    SELECT
        primary_tag,
        dual_entered,
        count() AS n_markets,
        avg(hold_days)::INT AS avg_hold_days,
        median(hold_days)::INT AS med_hold_days,
        percentile_cont(0.25) WITHIN GROUP (ORDER BY hold_days)::INT AS p25_hold,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY hold_days)::INT AS p75_hold
    FROM oos_markets
    WHERE primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY primary_tag, dual_entered
    ORDER BY primary_tag, dual_entered
    """)
    print("\nHold time comparison (days):")
    print(hold_times)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 7: Dual-skill traders as direct signals
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 7: Dual-Skill Traders as Direct Signals")
    print("=" * 70)

    # How do dual-skill traders themselves perform OOS?
    dual_oos = d.query("""
    SELECT
        p.position,
        mt.primary_tag,
        count() AS n_positions,
        round(avg(p.correct::DOUBLE), 4) AS hr,
        round(avg(p.net_usd), 2) AS avg_pnl,
        count(DISTINCT p.condition_id) AS n_markets,
        count(DISTINCT p.trader) AS n_traders
    FROM _ds_dual_skill ds
    JOIN maker_positions p ON ds.trader = p.trader
    JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
    WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
      AND p.volume > 0
    GROUP BY p.position, mt.primary_tag
    HAVING count() >= 20
    ORDER BY mt.primary_tag, p.position
    """)
    print("\nDual-skill trader OOS performance:")
    print(dual_oos)

    # Dual-skill consensus (N >= 2 dual-skill traders in same market)
    dual_consensus = d.query("""
    WITH dual_entries AS (
        SELECT
            p.condition_id,
            mt.primary_tag,
            p.position,
            count(DISTINCT lower(p.trader)) AS n_dual_traders,
            mr.token_won
        FROM _ds_dual_skill ds
        JOIN maker_positions p ON ds.trader = p.trader
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN markets_resolved mr ON p.condition_id = mr.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
          AND p.volume > 0
        GROUP BY p.condition_id, mt.primary_tag, p.position, mr.token_won
    )
    SELECT
        primary_tag,
        position,
        n_dual_traders,
        count() AS n_markets,
        round(avg(CASE
            WHEN position = 'YES' THEN token_won::DOUBLE
            ELSE 1.0 - token_won::DOUBLE
        END), 4) AS direction_hr
    FROM dual_entries
    WHERE primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY primary_tag, position, n_dual_traders
    HAVING count() >= 5
    ORDER BY primary_tag, position, n_dual_traders
    """)
    print("\nDual-skill consensus (by N dual traders in market):")
    print(dual_consensus)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 8: Tighter BEH thresholds
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 8: BEH Threshold Sensitivity")
    print("=" * 70)

    for beh_thresh in [0.02, 0.05, 0.10, 0.15, 0.20]:
        row = con.execute(f"""
            SELECT
                count() AS n,
                avg(total_positions)::INT AS avg_pos,
                avg(yes_beh) AS avg_yes_beh,
                avg(no_beh) AS avg_no_beh
            FROM (
                SELECT y.trader,
                    y.n_yes_positions + n.n_no_positions AS total_positions,
                    y.yes_beh, n.no_beh
                FROM _ds_yes_beh y
                JOIN _ds_no_beh n ON y.trader = n.trader
                WHERE y.yes_beh >= {beh_thresh} AND n.no_beh >= {beh_thresh}
            )
        """).fetchone()
        print(f"  BEH >= {beh_thresh:.2f}: {row[0]:>5} traders, avg_pos={row[1]}, "
              f"avg_yes_beh={row[2]:.3f}, avg_no_beh={row[3]:.3f}")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 9: Sports YES base rate context
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 9: Sports Base Rates (OOS context)")
    print("=" * 70)

    base_rates = d.query("""
    SELECT
        mt.primary_tag,
        count(DISTINCT mr.condition_id) AS n_markets,
        round(avg(mr.token_won::DOUBLE), 4) AS yes_base_rate,
        round(1.0 - avg(mr.token_won::DOUBLE), 4) AS no_base_rate
    FROM markets_resolved mr
    JOIN _ds_market_tags mt ON mr.condition_id = mt.condition_id
    WHERE CAST(mr.resolved_at AS DATE) >= '2025-07-01'
      AND mt.primary_tag IN ('Sports', 'Politics', 'Crypto', 'Esports')
    GROUP BY mt.primary_tag
    ORDER BY mt.primary_tag
    """)
    print("\nOOS base rates:")
    print(base_rates)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 10: Timing analysis - do dual-skill traders enter BEFORE consensus?
    # ══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("STEP 10: Timing - Dual-Skill Entry vs Pool Entry")
    print("=" * 70)

    timing = d.query(f"""
    WITH pool_list AS (
        SELECT UNNEST(?) AS trader
    ),
    pool_entries AS (
        SELECT
            p.condition_id,
            min(p.first_trade) AS pool_first_entry,
            count(DISTINCT lower(p.trader)) AS n_pool_traders
        FROM maker_positions p
        JOIN _ds_market_tags mt ON p.condition_id = mt.condition_id
        JOIN pool_list pl ON lower(p.trader) = pl.trader
        WHERE mt.primary_tag = 'Sports'
          AND p.position = 'YES'
          AND p.volume > 0
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
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
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
        GROUP BY p.condition_id
    )
    SELECT
        count() AS n_markets,
        sum(CASE WHEN de.dual_first_entry <= pe.pool_first_entry THEN 1 ELSE 0 END) AS dual_first,
        sum(CASE WHEN de.dual_first_entry > pe.pool_first_entry THEN 1 ELSE 0 END) AS pool_first,
        avg(date_diff('hour', de.dual_first_entry, pe.pool_first_entry)) AS avg_hours_dual_before_pool,
        median(date_diff('hour', de.dual_first_entry, pe.pool_first_entry)) AS med_hours_dual_before_pool
    FROM pool_entries pe
    JOIN dual_entries de ON pe.condition_id = de.condition_id
    """, [list(sports_yes_pool)])
    print("\nTiming (Sports YES, dual-skill vs pool first entry):")
    print(timing)

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"Total analysis time: {elapsed:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
