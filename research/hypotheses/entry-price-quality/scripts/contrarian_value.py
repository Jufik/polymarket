"""
Contrarian Value Signal Research
=================================
Approaches:
1. Sure-thing penalty: fraction of correct positions entered at >0.80
2. Edge-per-dollar: avg_profit_when_correct = avg(1 - entry_price) for correct positions
3. Bargain hunter composite: excess_hr * avg_profit_when_correct
4. Price-conditional excess HR: bucket by entry price, compute bucket-level excess
5. Direction-aware: decompose by YES vs NO separately

Usage:
    PYTHONPATH=. uv run python research/hypotheses/entry-price-quality/scripts/contrarian_value.py
"""

import sys
import json
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

LOG_FILE = Path("/mnt/nvme/git/polymarket/polymarket/tmp/contrarian_value.log")
OUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/entry-price-quality/discovery")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg: str) -> None:
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("")  # reset log
    log("=== Contrarian Value Signal Research ===")
    log(f"Started at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    from research.db import db as db_fn
    db = db_fn()

    log("DB loaded. Checking available tables...")

    # Verify tables exist
    tables = db.query("SHOW TABLES").to_dicts()
    table_names = [t["name"] for t in tables]
    log(f"Available tables: {table_names}")

    # Check yes_entry_data schema
    log("\n--- yes_entry_data schema ---")
    schema = db.query("DESCRIBE yes_entry_data")
    log(schema.to_pandas().to_string())

    # Check maker_positions schema
    log("\n--- maker_positions schema ---")
    mp_schema = db.query("DESCRIBE maker_positions")
    log(mp_schema.to_pandas().to_string())

    # Check markets schema
    log("\n--- markets schema ---")
    if "markets" in table_names:
        m_schema = db.query("DESCRIBE markets")
        log(m_schema.to_pandas().to_string())

    # ============================================================
    # Step 1: Build base positions table with entry price proxy
    # ============================================================
    log("\n=== Building base positions with entry price ===")

    # For YES positions: entry price = price_x_vol / volume from yes_entry_data
    # For NO positions: we need to infer from maker_positions using abs(net_usd)/abs(net_no)
    # BUT memory note warns: net_yes/net_no are in token units (1e3 scale), so
    # NO entry price = abs(net_usd)/abs(net_no) is unreliable.
    # Better: for NO positions, infer YES price from yes_entry_data of same market, use 1-yes_price
    # Actually the task says: use yes_entry_data for YES positions (cost per YES share)
    # For NO positions: entry_price_proxy = abs(net_usd)/abs(net_no)
    # But the memory says net_no is in token units (1e3 scale relative to net_usd)
    # So the correct formula for NO would be: abs(net_usd) / (abs(net_no) / 1000)?
    # Let's first check the actual scale of net_no

    log("\n--- Checking net_no scale vs yes_entry_data ---")
    scale_check = db.query("""
        SELECT
            mp.trader, mp.condition_id, mp.position,
            mp.net_usd, mp.net_yes, mp.net_no,
            yed.price_x_vol, yed.volume,
            yed.price_x_vol / yed.volume AS yes_price_proxy,
            CASE WHEN ABS(mp.net_yes) > 0.01
                 THEN ABS(mp.net_usd) / ABS(mp.net_yes)
                 ELSE NULL END AS raw_yes_price_ratio,
            CASE WHEN ABS(mp.net_no) > 0.01
                 THEN ABS(mp.net_usd) / ABS(mp.net_no)
                 ELSE NULL END AS raw_no_price_ratio
        FROM maker_positions mp
        INNER JOIN yes_entry_data yed ON mp.trader = yed.trader AND mp.condition_id = yed.condition_id
        WHERE mp.position = 'YES'
            AND mp.resolved_at IS NOT NULL
            AND ABS(mp.net_usd) > 0.01
            AND ABS(mp.net_yes) > 0.01
        LIMIT 100
    """)
    log(f"YES position sample (n={len(scale_check)}):")
    log(scale_check.select(["net_usd","net_yes","yes_price_proxy","raw_yes_price_ratio"]).describe().to_pandas().to_string())

    # Check if raw ratio matches yes_entry_data price
    log("\nCorrelation between raw ratio and yes_entry_data price:")
    corr = scale_check.select(pl.corr("yes_price_proxy", "raw_yes_price_ratio")).to_dicts()
    log(str(corr))

    # ============================================================
    # Step 2: Build comprehensive base table
    # ============================================================
    log("\n=== Step 2: Building comprehensive base table ===")

    # Key insight: For YES positions, use yes_entry_data for clean entry price
    # For NO positions: the dispatch says use abs(net_usd)/abs(net_no) but memory warns net_no is in token units
    # Let's check if markets has slug for gambling exclusion

    has_markets = "markets" in table_names
    log(f"has_markets table: {has_markets}")

    if has_markets:
        mkt_cols = db.query("DESCRIBE markets").to_dicts()
        mkt_col_names = [c["column_name"] for c in mkt_cols]
        log(f"markets columns: {mkt_col_names}")

        # Check if slug exists
        has_slug = "slug" in mkt_col_names or "market_slug" in mkt_col_names
        log(f"has slug: {has_slug}")
        slug_col = "slug" if "slug" in mkt_col_names else ("market_slug" if "market_slug" in mkt_col_names else None)

    # Build base table with YES positions using yes_entry_data
    log("\nBuilding YES positions base table...")

    gambling_filter = ""
    if has_markets and slug_col:
        gambling_filter = f"AND m.{slug_col} NOT LIKE '%updown%' AND m.{slug_col} NOT LIKE '%up-or-down%'"
        markets_join = f"INNER JOIN markets m ON mp.condition_id = m.condition_id"
    else:
        gambling_filter = ""
        markets_join = ""

    yes_base_sql = f"""
    CREATE OR REPLACE TABLE _cv_yes_positions AS
    SELECT
        mp.trader,
        mp.condition_id,
        'YES' AS position,
        mp.correct,
        mp.net_usd,
        yed.price_x_vol / yed.volume AS entry_price
    FROM maker_positions mp
    INNER JOIN yes_entry_data yed ON mp.trader = yed.trader AND mp.condition_id = yed.condition_id
    {markets_join}
    WHERE mp.position = 'YES'
        AND mp.resolved_at IS NOT NULL
        AND mp.correct IN (0, 1)
        AND ABS(mp.net_usd) > 0.01
        AND yed.volume > 0
        AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99
        {gambling_filter}
    """

    db.execute(yes_base_sql)
    yes_cnt = db.query("SELECT count() AS n FROM _cv_yes_positions").to_dicts()[0]["n"]
    log(f"YES positions: {yes_cnt:,}")

    # Now build NO positions
    # For NO positions: dispatch says entry_price = abs(net_usd)/abs(net_no)
    # Memory warns net_no is in token units (1e3 scale vs net_usd)
    # Let's test both approaches:
    # approach A: net_usd/net_no direct
    # approach B: net_usd/(net_no/1000) -- but this would give values like 0.09/(90/1000) = 1.0 (impossible)
    # The memory says: net_usd=-0.09, net_yes=90 means $0.09 USDC for 90 tokens at $0.001/token
    # So YES price = net_usd/net_yes = 0.09/90 = 0.001 -- that's WAY too low
    # But yes_entry_data gives price_x_vol/volume which is 0.246 median -- reasonable
    # The scaling in yes_entry_data must use different units
    #
    # Better approach for NO: compute NO entry price from the YES side
    # If YES price = 0.30, then NO price ≈ 0.70
    # For NO traders in a market, get the YES entry price at the time they entered (not ideal)
    #
    # SIMPLEST: For NO positions, aggregate the yes_entry_data for the SAME market,
    # then NO entry price proxy = 1 - market_yes_price at that time
    # But we don't have per-trader timestamps in yes_entry_data
    #
    # Let's just use the market-level YES price from yes_entry_data as proxy for NO traders too
    # NO entry price ≈ 1 - avg(yes_price) in same market
    # This is approximate but avoids the scaling issue

    log("\nBuilding NO positions base table using market-level YES price complement...")

    no_base_sql = f"""
    CREATE OR REPLACE TABLE _cv_no_positions AS
    WITH market_yes_price AS (
        SELECT condition_id, AVG(price_x_vol / volume) AS avg_yes_price
        FROM yes_entry_data
        WHERE volume > 0
        GROUP BY condition_id
    )
    SELECT
        mp.trader,
        mp.condition_id,
        'NO' AS position,
        mp.correct,
        mp.net_usd,
        1.0 - myp.avg_yes_price AS entry_price  -- NO entry price = 1 - YES price
    FROM maker_positions mp
    INNER JOIN market_yes_price myp ON mp.condition_id = myp.condition_id
    {markets_join}
    WHERE mp.position = 'NO'
        AND mp.resolved_at IS NOT NULL
        AND mp.correct IN (0, 1)
        AND ABS(mp.net_usd) > 0.01
        AND myp.avg_yes_price BETWEEN 0.01 AND 0.99
        {gambling_filter}
    """

    db.execute(no_base_sql)
    no_cnt = db.query("SELECT count() AS n FROM _cv_no_positions").to_dicts()[0]["n"]
    log(f"NO positions: {no_cnt:,}")

    # Combine
    db.execute("""
    CREATE OR REPLACE TABLE _cv_all_positions AS
    SELECT * FROM _cv_yes_positions
    UNION ALL
    SELECT * FROM _cv_no_positions
    """)
    total_cnt = db.query("SELECT count() AS n FROM _cv_all_positions").to_dicts()[0]["n"]
    log(f"Total positions: {total_cnt:,}")

    # ============================================================
    # Step 3: Compute per-trader metrics
    # ============================================================
    log("\n=== Step 3: Computing per-trader metrics ===")

    log("Computing population base rates by entry price bucket...")
    # Population base rate per price bucket
    pop_bucket = db.query("""
    WITH buckets AS (
        SELECT
            CASE
                WHEN entry_price < 0.20 THEN '0.00-0.20'
                WHEN entry_price < 0.40 THEN '0.20-0.40'
                WHEN entry_price < 0.60 THEN '0.40-0.60'
                WHEN entry_price < 0.80 THEN '0.60-0.80'
                ELSE '0.80-1.00'
            END AS price_bucket,
            correct
        FROM _cv_all_positions
    )
    SELECT price_bucket,
           count(*) AS n_positions,
           AVG(correct) AS pop_hr
    FROM buckets
    GROUP BY price_bucket
    ORDER BY price_bucket
    """)
    log("\nPopulation HR by price bucket:")
    log(pop_bucket.to_pandas().to_string())

    # Population HR by price bucket (YES only)
    pop_bucket_yes = db.query("""
    WITH buckets AS (
        SELECT
            CASE
                WHEN entry_price < 0.20 THEN '0.00-0.20'
                WHEN entry_price < 0.40 THEN '0.20-0.40'
                WHEN entry_price < 0.60 THEN '0.40-0.60'
                WHEN entry_price < 0.80 THEN '0.60-0.80'
                ELSE '0.80-1.00'
            END AS price_bucket,
            correct
        FROM _cv_all_positions
        WHERE position = 'YES'
    )
    SELECT price_bucket,
           count(*) AS n_positions,
           AVG(correct) AS pop_hr
    FROM buckets
    GROUP BY price_bucket
    ORDER BY price_bucket
    """)
    log("\nPopulation HR by price bucket (YES only):")
    log(pop_bucket_yes.to_pandas().to_string())

    # ============================================================
    # Step 4: Per-trader scorecard
    # ============================================================
    log("\n=== Step 4: Per-trader scorecard ===")

    # Compute global population HR for excess_hr computation
    global_hr = db.query("SELECT AVG(correct) AS global_hr FROM _cv_all_positions").to_dicts()[0]["global_hr"]
    log(f"Global HR: {global_hr:.4f}")

    log("Computing per-trader metrics (min 20 positions)...")

    trader_sql = f"""
    CREATE OR REPLACE TABLE _cv_traders AS
    WITH price_buckets AS (
        SELECT *,
            CASE
                WHEN entry_price < 0.20 THEN '0.00-0.20'
                WHEN entry_price < 0.40 THEN '0.20-0.40'
                WHEN entry_price < 0.60 THEN '0.40-0.60'
                WHEN entry_price < 0.80 THEN '0.60-0.80'
                ELSE '0.80-1.00'
            END AS price_bucket
        FROM _cv_all_positions
    ),
    trader_overall AS (
        SELECT
            trader,
            count(*) AS n_positions,
            AVG(correct) AS hit_rate,
            AVG(correct) - {global_hr} AS excess_hr,

            -- Approach 1: Sure-thing ratio (high entry_price on correct positions)
            CASE WHEN SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) > 0
                 THEN SUM(CASE WHEN entry_price > 0.80 AND correct = 1 THEN 1.0 ELSE 0.0 END)
                      / SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END)
                 ELSE NULL END AS sure_thing_ratio,

            -- Approach 2: Edge-per-dollar (avg profit when correct = 1 - entry_price for correct trades)
            AVG(CASE WHEN correct = 1 THEN 1.0 - entry_price ELSE NULL END) AS avg_profit_when_correct,

            -- Average entry price overall
            AVG(entry_price) AS avg_entry_price,

            -- Count by bucket
            SUM(CASE WHEN price_bucket = '0.00-0.20' THEN 1 ELSE 0 END) AS n_cheap,
            SUM(CASE WHEN price_bucket = '0.80-1.00' THEN 1 ELSE 0 END) AS n_surething,
            SUM(CASE WHEN price_bucket = '0.00-0.20' AND correct = 1 THEN 1 ELSE 0 END) AS n_cheap_correct,
            SUM(CASE WHEN price_bucket = '0.80-1.00' AND correct = 1 THEN 1 ELSE 0 END) AS n_surething_correct,

            -- HR in cheap bucket (<0.40)
            SUM(CASE WHEN entry_price < 0.40 THEN 1 ELSE 0 END) AS n_cheap_40,
            AVG(CASE WHEN entry_price < 0.40 THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_cheap_40,

            -- HR in expensive bucket (>0.80)
            AVG(CASE WHEN entry_price > 0.80 THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_expensive_80

        FROM price_buckets
        GROUP BY trader
        HAVING n_positions >= 20
    ),
    -- Compute bucket-level HR for each trader (for conditional excess HR)
    trader_bucket_hr AS (
        SELECT
            trader,
            -- Per-bucket HR
            AVG(CASE WHEN price_bucket = '0.00-0.20' THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_b1,
            AVG(CASE WHEN price_bucket = '0.20-0.40' THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_b2,
            AVG(CASE WHEN price_bucket = '0.40-0.60' THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_b3,
            AVG(CASE WHEN price_bucket = '0.60-0.80' THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_b4,
            AVG(CASE WHEN price_bucket = '0.80-1.00' THEN CAST(correct AS DOUBLE) ELSE NULL END) AS hr_b5
        FROM price_buckets
        GROUP BY trader
    )
    SELECT
        t.*,
        b.hr_b1, b.hr_b2, b.hr_b3, b.hr_b4, b.hr_b5,
        -- Approach 3: Bargain hunter composite
        t.excess_hr * COALESCE(t.avg_profit_when_correct, 0) AS bargain_score
    FROM trader_overall t
    LEFT JOIN trader_bucket_hr b ON t.trader = b.trader
    """

    db.execute(trader_sql)
    n_traders = db.query("SELECT count() AS n FROM _cv_traders").to_dicts()[0]["n"]
    log(f"Traders with >= 20 positions: {n_traders:,}")

    # ============================================================
    # Approach 1: Sure-thing ratio analysis
    # ============================================================
    log("\n=== APPROACH 1: Sure-Thing Ratio ===")

    surething_dist = db.query("""
    SELECT
        CASE
            WHEN sure_thing_ratio IS NULL THEN 'no_correct'
            WHEN sure_thing_ratio < 0.10 THEN '0-10%'
            WHEN sure_thing_ratio < 0.30 THEN '10-30%'
            WHEN sure_thing_ratio < 0.50 THEN '30-50%'
            WHEN sure_thing_ratio < 0.70 THEN '50-70%'
            ELSE '70-100%'
        END AS st_bucket,
        count(*) AS n_traders,
        AVG(hit_rate) AS avg_hr,
        AVG(excess_hr) AS avg_excess_hr,
        AVG(n_positions) AS avg_n_positions,
        AVG(avg_entry_price) AS avg_entry_price
    FROM _cv_traders
    GROUP BY st_bucket
    ORDER BY st_bucket
    """)
    log("\nSure-thing ratio distribution:")
    log(surething_dist.to_pandas().to_string())

    # Correlation between sure_thing_ratio and excess_hr
    corr_st = db.query("""
    SELECT corr(sure_thing_ratio, excess_hr) AS corr_st_excess_hr,
           corr(sure_thing_ratio, hit_rate) AS corr_st_hit_rate,
           corr(avg_entry_price, excess_hr) AS corr_price_excess_hr
    FROM _cv_traders
    WHERE sure_thing_ratio IS NOT NULL
    """).to_dicts()[0]
    log(f"\nCorrelations with excess HR:")
    log(f"  sure_thing_ratio → excess_hr: {corr_st['corr_st_excess_hr']:.4f}")
    log(f"  sure_thing_ratio → hit_rate:  {corr_st['corr_st_hit_rate']:.4f}")
    log(f"  avg_entry_price → excess_hr:  {corr_st['corr_price_excess_hr']:.4f}")

    # ============================================================
    # Approach 2: Edge-per-dollar analysis
    # ============================================================
    log("\n=== APPROACH 2: Edge-Per-Dollar (avg_profit_when_correct) ===")

    epd_deciles = db.query("""
    WITH ranked AS (
        SELECT *,
            ntile(10) OVER (ORDER BY avg_profit_when_correct) AS decile
        FROM _cv_traders
        WHERE avg_profit_when_correct IS NOT NULL
    )
    SELECT
        decile,
        count(*) AS n_traders,
        AVG(avg_profit_when_correct) AS avg_profit_when_correct,
        AVG(avg_entry_price) AS avg_entry_price,
        AVG(hit_rate) AS avg_hr,
        AVG(excess_hr) AS avg_excess_hr,
        AVG(n_positions) AS avg_n_positions
    FROM ranked
    GROUP BY decile
    ORDER BY decile
    """)
    log("\nEdge-per-dollar decile table:")
    log(epd_deciles.to_pandas().to_string(float_format='{:.4f}'.format))

    # IC: avg_profit_when_correct → excess_hr
    corr_epd = db.query("""
    SELECT
        corr(avg_profit_when_correct, excess_hr) AS corr_epd_excess_hr,
        corr(avg_profit_when_correct, hit_rate) AS corr_epd_hit_rate
    FROM _cv_traders
    WHERE avg_profit_when_correct IS NOT NULL
    """).to_dicts()[0]
    log(f"\nIC (avg_profit_when_correct):")
    log(f"  → excess_hr: {corr_epd['corr_epd_excess_hr']:.4f}")
    log(f"  → hit_rate:  {corr_epd['corr_epd_hit_rate']:.4f}")

    # ============================================================
    # Approach 3: Bargain hunter composite
    # ============================================================
    log("\n=== APPROACH 3: Bargain Hunter Composite ===")

    bargain_deciles = db.query("""
    WITH ranked AS (
        SELECT *,
            ntile(10) OVER (ORDER BY bargain_score) AS decile
        FROM _cv_traders
        WHERE avg_profit_when_correct IS NOT NULL
    )
    SELECT
        decile,
        count(*) AS n_traders,
        AVG(bargain_score) AS avg_bargain_score,
        AVG(avg_profit_when_correct) AS avg_profit_when_correct,
        AVG(excess_hr) AS avg_excess_hr,
        AVG(hit_rate) AS avg_hr,
        AVG(avg_entry_price) AS avg_entry_price
    FROM ranked
    GROUP BY decile
    ORDER BY decile
    """)
    log("\nBargain hunter score decile table:")
    log(bargain_deciles.to_pandas().to_string(float_format='{:.4f}'.format))

    corr_bargain = db.query("""
    SELECT corr(bargain_score, excess_hr) AS corr_bargain_excess_hr
    FROM _cv_traders
    WHERE avg_profit_when_correct IS NOT NULL
    """).to_dicts()[0]
    log(f"\nIC (bargain_score → excess_hr): {corr_bargain['corr_bargain_excess_hr']:.4f}")

    # ============================================================
    # Approach 4: Price-conditional excess HR
    # ============================================================
    log("\n=== APPROACH 4: Price-Conditional Excess HR ===")

    # Get population HR per bucket
    pop_rates = db.query("""
    SELECT
        CASE
            WHEN entry_price < 0.20 THEN 1
            WHEN entry_price < 0.40 THEN 2
            WHEN entry_price < 0.60 THEN 3
            WHEN entry_price < 0.80 THEN 4
            ELSE 5
        END AS bucket_id,
        AVG(correct) AS pop_hr
    FROM _cv_all_positions
    GROUP BY bucket_id
    """).to_dicts()
    pop_hr_by_bucket = {r["bucket_id"]: r["pop_hr"] for r in pop_rates}
    log(f"\nPopulation HR by bucket: {pop_hr_by_bucket}")

    # For each trader, compute excess HR in each bucket
    excess_by_bucket = db.query(f"""
    WITH trader_bucket AS (
        SELECT
            trader,
            CASE
                WHEN entry_price < 0.20 THEN 1
                WHEN entry_price < 0.40 THEN 2
                WHEN entry_price < 0.60 THEN 3
                WHEN entry_price < 0.80 THEN 4
                ELSE 5
            END AS bucket_id,
            AVG(correct) AS trader_bucket_hr,
            count(*) AS n_in_bucket
        FROM _cv_all_positions
        GROUP BY trader, bucket_id
    )
    SELECT
        bucket_id,
        AVG(trader_bucket_hr) AS avg_trader_hr,
        AVG(n_in_bucket) AS avg_positions_in_bucket,
        count(*) AS n_trader_bucket_pairs
    FROM trader_bucket
    WHERE n_in_bucket >= 5
    GROUP BY bucket_id
    ORDER BY bucket_id
    """)
    log("\nTrader-level average HR per bucket (traders with >=5 in bucket):")
    log(excess_by_bucket.to_pandas().to_string(float_format='{:.4f}'.format))

    # Key question: do traders who are "cheap" buyers (lots of positions in buckets 1-2)
    # show higher excess HR?
    log("\nExcess HR in cheap bucket (bucket 1: <0.20) vs overall excess HR:")
    cheap_excess = db.query(f"""
    WITH cheap_traders AS (
        SELECT
            trader,
            AVG(correct) AS hr_in_cheap_bucket,
            count(*) AS n_cheap
        FROM _cv_all_positions
        WHERE entry_price < 0.20
        GROUP BY trader
        HAVING n_cheap >= 5
    ),
    overall AS (
        SELECT trader, excess_hr, hit_rate, n_positions, avg_profit_when_correct
        FROM _cv_traders
    ),
    joined AS (
        SELECT
            c.hr_in_cheap_bucket - {pop_hr_by_bucket.get(1, 0.15):.6f} AS excess_in_cheap,
            o.excess_hr, o.hit_rate, o.n_positions
        FROM cheap_traders c
        INNER JOIN overall o ON c.trader = o.trader
    ),
    ranked AS (
        SELECT *, ntile(5) OVER (ORDER BY excess_in_cheap) AS quintile_excess_cheap
        FROM joined
    )
    SELECT
        quintile_excess_cheap,
        count(*) AS n_traders,
        AVG(excess_in_cheap) AS avg_excess_in_cheap,
        AVG(excess_hr) AS avg_overall_excess_hr,
        AVG(hit_rate) AS avg_overall_hr,
        AVG(n_positions) AS avg_n_positions
    FROM ranked
    GROUP BY quintile_excess_cheap
    ORDER BY quintile_excess_cheap
    """)
    log(cheap_excess.to_pandas().to_string(float_format='{:.4f}'.format))

    # ============================================================
    # Approach 5: Direction-aware (YES vs NO separately)
    # ============================================================
    log("\n=== APPROACH 5: Direction-Aware Analysis (YES vs NO) ===")

    # YES position analysis
    yes_metrics = db.query(f"""
    WITH yes_trader AS (
        SELECT
            trader,
            count(*) AS n_yes,
            AVG(correct) AS yes_hr,
            AVG(entry_price) AS avg_yes_price,
            AVG(CASE WHEN correct = 1 THEN 1.0 - entry_price ELSE NULL END) AS yes_profit_when_correct
        FROM _cv_yes_positions
        GROUP BY trader
        HAVING count(*) >= 10
    ),
    ranked AS (
        SELECT *, ntile(10) OVER (ORDER BY avg_yes_price) AS price_decile
        FROM yes_trader
    )
    SELECT
        price_decile,
        count(*) AS n_traders,
        AVG(avg_yes_price) AS avg_entry_price,
        AVG(yes_hr) AS avg_yes_hr,
        AVG(yes_profit_when_correct) AS avg_profit_correct
    FROM ranked
    GROUP BY price_decile
    ORDER BY price_decile
    """)
    log("\nYES position traders by entry price decile:")
    log(yes_metrics.to_pandas().to_string(float_format='{:.4f}'.format))

    # Population HR for YES by price bucket
    pop_yes_hr = db.query("""
    SELECT
        CASE WHEN entry_price < 0.20 THEN '0-20'
             WHEN entry_price < 0.40 THEN '20-40'
             WHEN entry_price < 0.60 THEN '40-60'
             WHEN entry_price < 0.80 THEN '60-80'
             ELSE '80-100' END AS bucket,
        count(*) AS n, AVG(correct) AS pop_hr
    FROM _cv_yes_positions
    GROUP BY bucket ORDER BY bucket
    """)
    log("\nPopulation YES HR by entry price bucket:")
    log(pop_yes_hr.to_pandas().to_string(float_format='{:.4f}'.format))

    # NO position analysis
    no_metrics = db.query("""
    SELECT
        CASE WHEN entry_price < 0.20 THEN '0-20'
             WHEN entry_price < 0.40 THEN '20-40'
             WHEN entry_price < 0.60 THEN '40-60'
             WHEN entry_price < 0.80 THEN '60-80'
             ELSE '80-100' END AS bucket,
        count(*) AS n, AVG(correct) AS pop_hr
    FROM _cv_no_positions
    GROUP BY bucket ORDER BY bucket
    """)
    log("\nPopulation NO HR by entry price bucket:")
    log(no_metrics.to_pandas().to_string(float_format='{:.4f}'.format))

    # ============================================================
    # Concrete trader examples
    # ============================================================
    log("\n=== Concrete Trader Examples ===")

    # Top 10 bargain hunters (high bargain_score)
    top_bargain = db.query("""
    SELECT trader, n_positions, hit_rate, excess_hr, avg_profit_when_correct,
           bargain_score, avg_entry_price, sure_thing_ratio,
           n_cheap, n_surething
    FROM _cv_traders
    WHERE avg_profit_when_correct IS NOT NULL
    ORDER BY bargain_score DESC
    LIMIT 10
    """)
    log("\nTop 10 Bargain Hunters (highest bargain_score):")
    log(top_bargain.to_pandas().to_string(float_format='{:.4f}'.format))

    # Bottom 10 (sure-thing pilers)
    bottom_bargain = db.query("""
    SELECT trader, n_positions, hit_rate, excess_hr, avg_profit_when_correct,
           bargain_score, avg_entry_price, sure_thing_ratio,
           n_cheap, n_surething
    FROM _cv_traders
    WHERE avg_profit_when_correct IS NOT NULL AND n_positions >= 50
    ORDER BY sure_thing_ratio DESC, excess_hr ASC
    LIMIT 10
    """)
    log("\nTop 10 Sure-Thing Pilers (high sure_thing_ratio, low excess_hr):")
    log(bottom_bargain.to_pandas().to_string(float_format='{:.4f}'.format))

    # ============================================================
    # Summary statistics for IC comparison
    # ============================================================
    log("\n=== IC Summary (metric → excess_hr) ===")

    ic_summary = db.query("""
    SELECT
        corr(sure_thing_ratio, excess_hr) AS IC_sure_thing_ratio,
        corr(avg_profit_when_correct, excess_hr) AS IC_avg_profit_correct,
        corr(bargain_score, excess_hr) AS IC_bargain_score,
        corr(avg_entry_price, excess_hr) AS IC_avg_entry_price,
        -corr(avg_entry_price, excess_hr) AS IC_neg_entry_price,
        corr(hr_b1, excess_hr) AS IC_hr_in_cheap_bucket,
        corr(hr_b5, excess_hr) AS IC_hr_in_surething_bucket
    FROM _cv_traders
    WHERE avg_profit_when_correct IS NOT NULL
      AND sure_thing_ratio IS NOT NULL
    """)
    log("\nIC table (Pearson r, metric → excess_hr):")
    log(ic_summary.to_pandas().to_string(float_format='{:.4f}'.format))

    # ============================================================
    # Save results as JSON for report
    # ============================================================
    results = {
        "global_hr": global_hr,
        "n_traders": n_traders,
        "yes_count": yes_cnt,
        "no_count": no_cnt,
        "total_positions": total_cnt,
        "pop_bucket": pop_bucket.to_dicts(),
        "pop_bucket_yes": pop_bucket_yes.to_dicts(),
        "surething_dist": surething_dist.to_dicts(),
        "corr_surething": corr_st,
        "epd_deciles": epd_deciles.to_dicts(),
        "corr_epd": corr_epd,
        "bargain_deciles": bargain_deciles.to_dicts(),
        "corr_bargain": corr_bargain,
        "pop_yes_hr": pop_yes_hr.to_dicts(),
        "no_metrics": no_metrics.to_dicts(),
        "ic_summary": ic_summary.to_dicts()[0],
        "top_bargain_hunters": top_bargain.to_dicts(),
        "top_surething_pilers": bottom_bargain.to_dicts(),
    }

    out_json = OUT_DIR / "contrarian_value_results.json"
    with open(out_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"\nResults saved to {out_json}")
    log("=== DONE ===")


if __name__ == "__main__":
    main()
