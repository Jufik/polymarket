"""
Market-level pattern analysis for Polymarket trading data.
Rewrite: better key-findings synthesis and interpretation.
Reads the same data, outputs insights/04_market_patterns.md
"""

import duckdb
import numpy as np
from pathlib import Path

BASE = Path("/Users/kiefferjulien/git/polymarket")
PNL_PATH = str(BASE / "data/derived/trader_market_pnl.parquet")
RESOLVED_PATH = str(BASE / "data/derived/markets_resolved.parquet")
MARKETS_PATH = str(BASE / "data/metadata/markets.parquet")
OUTPUT = BASE / "insights" / "04_market_patterns.md"

db = duckdb.connect()

# ---------------------------------------------------------------------------
# Load and set up tables
# ---------------------------------------------------------------------------
print("Loading data...")
db.execute(f"""
    CREATE TABLE pnl AS SELECT * FROM read_parquet('{PNL_PATH}');
    CREATE TABLE resolved AS SELECT * FROM read_parquet('{RESOLVED_PATH}');
    CREATE TABLE markets AS SELECT * FROM read_parquet('{MARKETS_PATH}');
""")

# Join PnL with resolved markets, strip timezones
db.execute("""
    CREATE TABLE pnl_resolved AS
    SELECT p.trader, p.condition_id, p.market_pnl, p.market_volume,
           p.trade_count,
           p.first_trade::TIMESTAMP AS first_trade,
           p.last_trade::TIMESTAMP AS last_trade
    FROM pnl p
    JOIN resolved r ON p.condition_id = r.condition_id
""")

# Trader totals (min 5 resolved markets)
db.execute("""
    CREATE TABLE trader_totals AS
    SELECT trader,
           SUM(market_pnl) AS total_pnl,
           COUNT(DISTINCT condition_id) AS n_markets,
           SUM(market_volume) AS total_volume,
           SUM(trade_count) AS total_trades
    FROM pnl_resolved
    GROUP BY trader
    HAVING COUNT(DISTINCT condition_id) >= 5
""")

n_traders = db.execute("SELECT count(*) FROM trader_totals").fetchone()[0]
p95 = db.execute("SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY total_pnl) FROM trader_totals").fetchone()[0]
p05 = db.execute("SELECT percentile_cont(0.05) WITHIN GROUP (ORDER BY total_pnl) FROM trader_totals").fetchone()[0]
median_pnl = db.execute("SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY total_pnl) FROM trader_totals").fetchone()[0]

db.execute(f"""
    CREATE TABLE skilled AS SELECT trader FROM trader_totals WHERE total_pnl >= {p95};
    CREATE TABLE unskilled AS SELECT trader FROM trader_totals WHERE total_pnl <= {p05};
""")

n_skilled = db.execute("SELECT count(*) FROM skilled").fetchone()[0]
n_unskilled = db.execute("SELECT count(*) FROM unskilled").fetchone()[0]
n_resolved_markets = db.execute("SELECT count(DISTINCT condition_id) FROM pnl_resolved").fetchone()[0]
n_all_traders = db.execute("SELECT count(DISTINCT trader) FROM pnl_resolved").fetchone()[0]

print(f"  {n_traders:,} active traders, {n_skilled:,} skilled, {n_unskilled:,} unskilled")
print(f"  PnL thresholds: skilled >= ${p95:,.2f}, unskilled <= ${p05:,.2f}")

# Market categories table
db.execute("""
    CREATE TABLE market_meta AS
    SELECT condition_id,
           CASE
               WHEN tags LIKE 'Sports%' THEN 'Sports'
               WHEN tags LIKE 'Crypto%' THEN 'Crypto'
               WHEN tags LIKE 'Politics%' THEN 'Politics'
               WHEN tags LIKE 'Pop Culture%' OR tags LIKE 'Pop-Culture%' THEN 'Pop Culture'
               WHEN tags LIKE 'Science%' THEN 'Science'
               WHEN tags LIKE 'Business%' THEN 'Business'
               WHEN tags LIKE 'AI%' THEN 'AI'
               WHEN tags LIKE 'All%' THEN 'General'
               WHEN tags LIKE 'Financials%' THEN 'Financials'
               WHEN tags LIKE 'Climate%' THEN 'Climate'
               WHEN tags LIKE 'Weather%' THEN 'Weather'
               WHEN tags LIKE 'Finance%' THEN 'Finance'
               WHEN tags LIKE 'Awards%' THEN 'Awards'
               WHEN tags LIKE 'Movies%' THEN 'Movies'
               WHEN tags LIKE 'Culture%' THEN 'Culture'
               WHEN tags LIKE 'Music%' THEN 'Music'
               WHEN tags LIKE 'Esports%' THEN 'Esports'
               WHEN tags LIKE 'Elections%' THEN 'Elections'
               WHEN tags LIKE 'Economy%' THEN 'Economy'
               WHEN tags LIKE 'Trump%' THEN 'Trump'
               ELSE COALESCE(NULLIF(SPLIT_PART(tags, ',', 1), ''), 'Unknown')
           END AS primary_category,
           CASE WHEN tags LIKE '%,%' THEN SPLIT_PART(tags, ',', 2) ELSE NULL END AS sub_category,
           neg_risk,
           resolved_at::TIMESTAMP AS market_resolved_at
    FROM markets
    WHERE condition_id IN (SELECT condition_id FROM resolved)
""")


# ===========================================================================
# 1. MARKET CONCENTRATION
# ===========================================================================
print("\n1. Market concentration...")

concentration = db.execute("""
    WITH market_counts AS (
        SELECT t.trader, t.n_markets, t.total_pnl,
               CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                    WHEN u.trader IS NOT NULL THEN 'unskilled'
                    ELSE 'middle' END AS tier
        FROM trader_totals t
        LEFT JOIN skilled s ON t.trader = s.trader
        LEFT JOIN unskilled u ON t.trader = u.trader
    )
    SELECT tier,
           count(*) as n,
           avg(n_markets) as avg_markets,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY n_markets) as median_markets,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY n_markets) as p25_markets,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY n_markets) as p75_markets,
           min(n_markets) as min_markets,
           max(n_markets) as max_markets
    FROM market_counts
    GROUP BY tier
    ORDER BY tier
""").df()

# Gini coefficients
gini_results = {}
for tier in ['skilled', 'unskilled', 'middle']:
    if tier == 'skilled':
        join = 'JOIN skilled s ON t.trader = s.trader'
    elif tier == 'unskilled':
        join = 'JOIN unskilled u ON t.trader = u.trader'
    else:
        join = 'LEFT JOIN skilled s ON t.trader = s.trader LEFT JOIN unskilled u ON t.trader = u.trader WHERE s.trader IS NULL AND u.trader IS NULL'
    vals = db.execute(f"SELECT t.n_markets FROM trader_totals t {join}").fetchnumpy()['n_markets']
    vals_sorted = np.sort(vals)
    n = len(vals_sorted)
    index = np.arange(1, n + 1)
    gini = (2 * np.sum(index * vals_sorted) - (n + 1) * np.sum(vals_sorted)) / (n * np.sum(vals_sorted))
    gini_results[tier] = round(float(gini), 4)

# Volume HHI
vol_concentration = db.execute("""
    WITH trader_market_share AS (
        SELECT p.trader,
               p.market_volume / NULLIF(t.total_volume, 0) AS vol_share,
               CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                    WHEN u.trader IS NOT NULL THEN 'unskilled'
                    ELSE 'middle' END AS tier
        FROM pnl_resolved p
        JOIN trader_totals t ON p.trader = t.trader
        LEFT JOIN skilled s ON p.trader = s.trader
        LEFT JOIN unskilled u ON p.trader = u.trader
    ),
    trader_hhi AS (
        SELECT trader, tier, SUM(vol_share * vol_share) AS hhi
        FROM trader_market_share
        GROUP BY trader, tier
    )
    SELECT tier,
           avg(hhi) as avg_hhi,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY hhi) as median_hhi
    FROM trader_hhi
    GROUP BY tier
    ORDER BY tier
""").df()

# ===========================================================================
# 2. CATEGORY EDGE (primary categories only, >1000 positions)
# ===========================================================================
print("\n2. Category edge...")

cat_edge = db.execute("""
    SELECT mc.primary_category,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n_positions,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl,
           SUM(p.market_pnl) AS total_pnl,
           AVG(ABS(p.market_pnl)) AS avg_abs_pnl
    FROM pnl_resolved p
    JOIN market_meta mc ON p.condition_id = mc.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
      AND mc.primary_category NOT IN ('Unknown', 'Hide From New')
    GROUP BY mc.primary_category, tier
    HAVING count(*) >= 1000
    ORDER BY mc.primary_category, tier
""").df()

cat_s = cat_edge[cat_edge['tier'] == 'skilled'].set_index('primary_category')
cat_u = cat_edge[cat_edge['tier'] == 'unskilled'].set_index('primary_category')

cat_rows = []
for cat in cat_s.index:
    if cat in cat_u.index:
        s = cat_s.loc[cat]
        u = cat_u.loc[cat]
        cat_rows.append({
            'category': cat,
            'skilled_wr': s['win_rate'],
            'unskilled_wr': u['win_rate'],
            'wr_edge': s['win_rate'] - u['win_rate'],
            'skilled_avg_pnl': s['avg_pnl'],
            'unskilled_avg_pnl': u['avg_pnl'],
            'pnl_edge': s['avg_pnl'] - u['avg_pnl'],
            'skilled_total': s['total_pnl'],
            'skilled_n': int(s['n_positions']),
            'unskilled_n': int(u['n_positions']),
        })

# Sort by PnL edge (the real measure of skill)
cat_rows.sort(key=lambda x: -x['pnl_edge'])

# Sub-category (min 500 positions per tier)
subcat_edge = db.execute("""
    SELECT mc.primary_category || ' > ' || mc.sub_category AS sub_cat,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n_positions,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl
    FROM pnl_resolved p
    JOIN market_meta mc ON p.condition_id = mc.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
      AND mc.sub_category IS NOT NULL
    GROUP BY sub_cat, tier
    HAVING count(*) >= 500
    ORDER BY sub_cat, tier
""").df()

sc_s = subcat_edge[subcat_edge['tier'] == 'skilled'].set_index('sub_cat')
sc_u = subcat_edge[subcat_edge['tier'] == 'unskilled'].set_index('sub_cat')
subcat_rows = []
for sc in sc_s.index:
    if sc in sc_u.index:
        s = sc_s.loc[sc]
        u = sc_u.loc[sc]
        subcat_rows.append({
            'sub': sc,
            'pnl_edge': s['avg_pnl'] - u['avg_pnl'],
            'skilled_wr': s['win_rate'],
            'unskilled_wr': u['win_rate'],
            'skilled_avg_pnl': s['avg_pnl'],
            'n': int(s['n_positions']),
        })
subcat_rows.sort(key=lambda x: -x['pnl_edge'])


# ===========================================================================
# 3. MARKET TIMING
# ===========================================================================
print("\n3. Market timing...")

timing = db.execute("""
    WITH market_first_last AS (
        SELECT condition_id,
               MIN(first_trade) AS market_open,
               MAX(last_trade) AS market_close
        FROM pnl_resolved
        GROUP BY condition_id
    )
    SELECT
        CASE WHEN s.trader IS NOT NULL THEN 'skilled'
             WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
        avg(CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
             ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as avg_entry,
        percentile_cont(0.25) WITHIN GROUP (ORDER BY
            CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
                 ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as p25_entry,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY
            CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
                 ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as median_entry,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY
            CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
                 ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as p75_entry,
        count(*) as n
    FROM pnl_resolved p
    JOIN market_first_last mfl ON p.condition_id = mfl.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
    GROUP BY tier
    ORDER BY tier
""").df()

timing_quintile = db.execute("""
    WITH market_first_last AS (
        SELECT condition_id,
               MIN(first_trade) AS market_open,
               MAX(last_trade) AS market_close
        FROM pnl_resolved
        GROUP BY condition_id
    ),
    trader_quintiles AS (
        SELECT trader, NTILE(5) OVER (ORDER BY total_pnl) AS pnl_quintile
        FROM trader_totals
    )
    SELECT tq.pnl_quintile,
           avg(CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
                ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as avg_entry,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY
               CASE WHEN mfl.market_close = mfl.market_open THEN 0.5
                    ELSE EPOCH(p.first_trade - mfl.market_open) / NULLIF(EPOCH(mfl.market_close - mfl.market_open), 0) END) as median_entry,
           count(*) as n
    FROM pnl_resolved p
    JOIN market_first_last mfl ON p.condition_id = mfl.condition_id
    JOIN trader_quintiles tq ON p.trader = tq.trader
    GROUP BY tq.pnl_quintile
    ORDER BY tq.pnl_quintile
""").df()


# ===========================================================================
# 4. MARKET SIZE EFFECT
# ===========================================================================
print("\n4. Market size effect...")

market_size = db.execute("""
    WITH market_vol AS (
        SELECT condition_id, SUM(market_volume) AS total_market_vol
        FROM pnl_resolved
        GROUP BY condition_id
    ),
    vol_tiers AS (
        SELECT condition_id, total_market_vol,
               CASE
                   WHEN total_market_vol < 100 THEN '1_micro (<$100)'
                   WHEN total_market_vol < 1000 THEN '2_small ($100-1K)'
                   WHEN total_market_vol < 10000 THEN '3_medium ($1K-10K)'
                   WHEN total_market_vol < 100000 THEN '4_large ($10K-100K)'
                   WHEN total_market_vol < 1000000 THEN '5_xlarge ($100K-1M)'
                   ELSE '6_mega (>$1M)'
               END AS vol_tier
        FROM market_vol
    )
    SELECT vt.vol_tier,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl,
           SUM(p.market_pnl) AS total_pnl
    FROM pnl_resolved p
    JOIN vol_tiers vt ON p.condition_id = vt.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
    GROUP BY vt.vol_tier, tier
    ORDER BY vt.vol_tier, tier
""").df()

ms_s = market_size[market_size['tier'] == 'skilled'].set_index('vol_tier')
ms_u = market_size[market_size['tier'] == 'unskilled'].set_index('vol_tier')
size_rows = []
for tier in sorted(ms_s.index):
    if tier in ms_u.index:
        s = ms_s.loc[tier]
        u = ms_u.loc[tier]
        size_rows.append({
            'tier': tier[2:],  # strip sort prefix
            'skilled_wr': s['win_rate'],
            'unskilled_wr': u['win_rate'],
            'wr_edge': s['win_rate'] - u['win_rate'],
            'skilled_avg_pnl': s['avg_pnl'],
            'unskilled_avg_pnl': u['avg_pnl'],
            'pnl_edge': s['avg_pnl'] - u['avg_pnl'],
            'skilled_n': int(s['n']),
        })

mkt_counts = db.execute("""
    WITH market_vol AS (
        SELECT condition_id, SUM(market_volume) AS total_market_vol
        FROM pnl_resolved
        GROUP BY condition_id
    )
    SELECT CASE
               WHEN total_market_vol < 100 THEN '1_micro'
               WHEN total_market_vol < 1000 THEN '2_small'
               WHEN total_market_vol < 10000 THEN '3_medium'
               WHEN total_market_vol < 100000 THEN '4_large'
               WHEN total_market_vol < 1000000 THEN '5_xlarge'
               ELSE '6_mega'
           END AS vol_tier,
           count(*) as n_markets,
           avg(total_market_vol) as avg_vol
    FROM market_vol
    GROUP BY vol_tier
    ORDER BY vol_tier
""").df()


# ===========================================================================
# 5. CONSENSUS MARKETS
# ===========================================================================
print("\n5. Consensus markets...")

consensus_dist = db.execute("""
    WITH market_correct AS (
        SELECT condition_id,
               COUNT(*) AS n_traders,
               SUM(CASE WHEN market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS correct_fraction,
               SUM(market_volume) AS total_vol
        FROM pnl_resolved
        GROUP BY condition_id
        HAVING COUNT(*) >= 10
    )
    SELECT
        CASE
            WHEN correct_fraction < 0.1 THEN '01_<10%'
            WHEN correct_fraction < 0.2 THEN '02_10-20%'
            WHEN correct_fraction < 0.3 THEN '03_20-30%'
            WHEN correct_fraction < 0.4 THEN '04_30-40%'
            WHEN correct_fraction < 0.5 THEN '05_40-50%'
            WHEN correct_fraction < 0.6 THEN '06_50-60%'
            WHEN correct_fraction < 0.7 THEN '07_60-70%'
            WHEN correct_fraction < 0.8 THEN '08_70-80%'
            WHEN correct_fraction < 0.9 THEN '09_80-90%'
            ELSE '10_90-100%'
        END AS bucket,
        count(*) as n_markets,
        avg(correct_fraction) as avg_correct,
        avg(n_traders) as avg_traders,
        avg(total_vol) as avg_vol
    FROM market_correct
    GROUP BY bucket
    ORDER BY bucket
""").df()

# Skilled edge by difficulty
cons_by_tier = db.execute("""
    WITH market_correct AS (
        SELECT condition_id,
               COUNT(*) AS n_traders,
               SUM(CASE WHEN market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS correct_fraction
        FROM pnl_resolved
        GROUP BY condition_id
        HAVING COUNT(*) >= 10
    ),
    bands AS (
        SELECT condition_id, correct_fraction,
               CASE
                   WHEN correct_fraction < 0.2 THEN '1_very_hard (<20%)'
                   WHEN correct_fraction < 0.4 THEN '2_hard (20-40%)'
                   WHEN correct_fraction < 0.6 THEN '3_medium (40-60%)'
                   WHEN correct_fraction < 0.8 THEN '4_easy (60-80%)'
                   ELSE '5_very_easy (80-100%)'
               END AS difficulty
        FROM market_correct
    )
    SELECT b.difficulty,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl,
           SUM(p.market_pnl) AS total_pnl
    FROM pnl_resolved p
    JOIN bands b ON p.condition_id = b.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
    GROUP BY b.difficulty, tier
    ORDER BY b.difficulty, tier
""").df()

cb_s = cons_by_tier[cons_by_tier['tier'] == 'skilled'].set_index('difficulty')
cb_u = cons_by_tier[cons_by_tier['tier'] == 'unskilled'].set_index('difficulty')
cons_rows = []
for d in sorted(cb_s.index):
    if d in cb_u.index:
        s = cb_s.loc[d]
        u = cb_u.loc[d]
        cons_rows.append({
            'difficulty': d[2:],
            'skilled_wr': s['win_rate'],
            'unskilled_wr': u['win_rate'],
            'wr_edge': s['win_rate'] - u['win_rate'],
            'skilled_avg_pnl': s['avg_pnl'],
            'unskilled_avg_pnl': u['avg_pnl'],
            'pnl_edge': s['avg_pnl'] - u['avg_pnl'],
            'skilled_total': s['total_pnl'],
            'unskilled_total': u['total_pnl'],
        })


# ===========================================================================
# 6. TIME-TO-RESOLUTION
# ===========================================================================
print("\n6. Time-to-resolution...")

ttr = db.execute("""
    WITH market_duration AS (
        SELECT p.condition_id,
               MIN(p.first_trade) AS market_first_trade,
               mc.market_resolved_at,
               EPOCH(mc.market_resolved_at - MIN(p.first_trade)) / 86400.0 AS days
        FROM pnl_resolved p
        JOIN market_meta mc ON p.condition_id = mc.condition_id
        WHERE mc.market_resolved_at IS NOT NULL
        GROUP BY p.condition_id, mc.market_resolved_at
    ),
    dur_tiers AS (
        SELECT condition_id, days,
               CASE
                   WHEN days < 1 THEN '1_<1 day'
                   WHEN days < 7 THEN '2_1-7 days'
                   WHEN days < 30 THEN '3_1-4 weeks'
                   WHEN days < 90 THEN '4_1-3 months'
                   WHEN days < 365 THEN '5_3-12 months'
                   ELSE '6_>1 year'
               END AS dur_tier
        FROM market_duration
        WHERE days > 0
    )
    SELECT dt.dur_tier,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl,
           SUM(p.market_pnl) AS total_pnl
    FROM pnl_resolved p
    JOIN dur_tiers dt ON p.condition_id = dt.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
    GROUP BY dt.dur_tier, tier
    ORDER BY dt.dur_tier, tier
""").df()

ttr_s = ttr[ttr['tier'] == 'skilled'].set_index('dur_tier')
ttr_u = ttr[ttr['tier'] == 'unskilled'].set_index('dur_tier')
ttr_rows = []
for d in sorted(ttr_s.index):
    if d in ttr_u.index:
        s = ttr_s.loc[d]
        u = ttr_u.loc[d]
        ttr_rows.append({
            'tier': d[2:],
            'skilled_wr': s['win_rate'],
            'unskilled_wr': u['win_rate'],
            'wr_edge': s['win_rate'] - u['win_rate'],
            'skilled_avg_pnl': s['avg_pnl'],
            'unskilled_avg_pnl': u['avg_pnl'],
            'pnl_edge': s['avg_pnl'] - u['avg_pnl'],
            'skilled_n': int(s['n']),
            'unskilled_n': int(u['n']),
        })

# Average duration preference
ttr_pref = db.execute("""
    WITH market_duration AS (
        SELECT p.condition_id,
               MIN(p.first_trade) AS market_first_trade,
               mc.market_resolved_at,
               EPOCH(mc.market_resolved_at - MIN(p.first_trade)) / 86400.0 AS days
        FROM pnl_resolved p
        JOIN market_meta mc ON p.condition_id = mc.condition_id
        WHERE mc.market_resolved_at IS NOT NULL
        GROUP BY p.condition_id, mc.market_resolved_at
    ),
    trader_dur AS (
        SELECT p.trader,
               AVG(md.days) AS avg_days,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY md.days) AS median_days,
               CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                    WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier
        FROM pnl_resolved p
        JOIN market_duration md ON p.condition_id = md.condition_id
        LEFT JOIN skilled s ON p.trader = s.trader
        LEFT JOIN unskilled u ON p.trader = u.trader
        WHERE md.days > 0
          AND (s.trader IS NOT NULL OR u.trader IS NOT NULL)
        GROUP BY p.trader, tier
    )
    SELECT tier, avg(avg_days) as avg_days, avg(median_days) as avg_median_days, count(*) as n
    FROM trader_dur
    GROUP BY tier
""").df()


# ===========================================================================
# 7. NEG RISK MARKETS
# ===========================================================================
print("\n7. Neg risk markets...")

neg_risk = db.execute("""
    SELECT mc.neg_risk,
           CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier,
           count(*) as n,
           SUM(CASE WHEN p.market_pnl > 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) AS win_rate,
           AVG(p.market_pnl) AS avg_pnl,
           SUM(p.market_pnl) AS total_pnl,
           AVG(p.market_volume) AS avg_position
    FROM pnl_resolved p
    JOIN markets mc ON p.condition_id = mc.condition_id
    LEFT JOIN skilled s ON p.trader = s.trader
    LEFT JOIN unskilled u ON p.trader = u.trader
    WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
    GROUP BY mc.neg_risk, tier
    ORDER BY mc.neg_risk, tier
""").df()

neg_mkt = db.execute("""
    WITH ms AS (
        SELECT mc.neg_risk, p.condition_id,
               COUNT(DISTINCT p.trader) as n_traders,
               SUM(p.market_volume) as total_vol
        FROM pnl_resolved p
        JOIN markets mc ON p.condition_id = mc.condition_id
        GROUP BY mc.neg_risk, p.condition_id
    )
    SELECT neg_risk, count(*) as n_markets,
           avg(n_traders) as avg_traders,
           avg(total_vol) as avg_vol,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_vol) as median_vol
    FROM ms
    GROUP BY neg_risk
""").df()

neg_pref = db.execute("""
    WITH tnr AS (
        SELECT p.trader,
               SUM(CASE WHEN mc.neg_risk THEN p.market_volume ELSE 0 END)
                   / NULLIF(SUM(p.market_volume), 0) AS nr_frac,
               CASE WHEN s.trader IS NOT NULL THEN 'skilled'
                    WHEN u.trader IS NOT NULL THEN 'unskilled' END AS tier
        FROM pnl_resolved p
        JOIN markets mc ON p.condition_id = mc.condition_id
        LEFT JOIN skilled s ON p.trader = s.trader
        LEFT JOIN unskilled u ON p.trader = u.trader
        WHERE (s.trader IS NOT NULL OR u.trader IS NOT NULL)
        GROUP BY p.trader, tier
    )
    SELECT tier,
           avg(nr_frac) as avg_frac,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY nr_frac) as median_frac
    FROM tnr
    GROUP BY tier
""").df()


# ===========================================================================
# ASSEMBLE REPORT
# ===========================================================================
print("\nAssembling report...")

# Derive key numbers for summary
skilled_entry = timing[timing['tier'] == 'skilled'].iloc[0]
unskilled_entry = timing[timing['tier'] == 'unskilled'].iloc[0]

best_size_pnl = max(size_rows, key=lambda x: x['pnl_edge'])
best_cons_pnl = max(cons_rows, key=lambda x: x['pnl_edge'])
best_ttr_pnl = max(ttr_rows, key=lambda x: x['pnl_edge'])

# Neg risk edge
nr_s_true = neg_risk[(neg_risk['neg_risk'] == True) & (neg_risk['tier'] == 'skilled')].iloc[0]
nr_u_true = neg_risk[(neg_risk['neg_risk'] == True) & (neg_risk['tier'] == 'unskilled')].iloc[0]
nr_s_false = neg_risk[(neg_risk['neg_risk'] == False) & (neg_risk['tier'] == 'skilled')].iloc[0]
nr_u_false = neg_risk[(neg_risk['neg_risk'] == False) & (neg_risk['tier'] == 'unskilled')].iloc[0]
nr_true_pnl_edge = nr_s_true['avg_pnl'] - nr_u_true['avg_pnl']
nr_false_pnl_edge = nr_s_false['avg_pnl'] - nr_u_false['avg_pnl']

report = f"""# 04 - Market-Level Patterns for Profitable Trading

> Analysis of market characteristics that predict profitability for skilled traders.
> Data: 70.9M trader-market positions across {n_resolved_markets:,} resolved markets from {n_all_traders:,} traders.

**Methodology**: Skilled traders = top 5% by total PnL across resolved markets (minimum 5 resolved markets traded).
- Skilled (top 5%): {n_skilled:,} traders (total PnL >= ${p95:,.0f})
- Unskilled (bottom 5%): {n_unskilled:,} traders (total PnL <= ${p05:,.0f})
- Median trader total PnL: ${median_pnl:,.2f}

**Key insight across all sections**: Skilled traders often have *lower* win rates than unskilled traders, but dramatically higher average PnL per position. They win bigger and lose smaller. The PnL edge (avg PnL gap) is the true measure of skill, not win rate.

---

## Key Findings Summary

1. **Category edge**: Largest PnL edges in **{cat_rows[0]['category']}** (${cat_rows[0]['pnl_edge']:,.0f}/position), **{cat_rows[1]['category']}** (${cat_rows[1]['pnl_edge']:,.0f}), and **{cat_rows[2]['category']}** (${cat_rows[2]['pnl_edge']:,.0f}). Low-frequency, high-information categories reward skilled analysis most.

2. **Market timing**: Skilled traders enter **earlier** -- median entry at {skilled_entry['median_entry']:.1%} of market lifetime vs {unskilled_entry['median_entry']:.1%} for unskilled. The 5pp gap represents meaningful alpha from earlier information processing.

3. **Market size**: Skilled-trader PnL edge scales with market volume. Biggest absolute edge in **{best_size_pnl['tier']}** (${best_size_pnl['pnl_edge']:,.0f}/position). Only markets >$100K show skilled traders with higher win rates than unskilled.

4. **Market difficulty**: In "hard" markets (where <40% of traders profit), skilled traders earn ${best_cons_pnl['pnl_edge']:,.0f}/position more than unskilled. The PnL edge is largest where the crowd gets it wrong.

5. **Resolution speed**: Biggest PnL edge in **{best_ttr_pnl['tier']}** markets (${best_ttr_pnl['pnl_edge']:,.0f}/position). Skilled traders average {ttr_pref[ttr_pref['tier']=='skilled'].iloc[0]['avg_days']:.0f} days to resolution vs {ttr_pref[ttr_pref['tier']=='unskilled'].iloc[0]['avg_days']:.0f} for unskilled -- slightly shorter hold periods.

6. **Market concentration**: Both skilled and unskilled have highly skewed participation (Gini ~0.82-0.86). But skilled traders have higher *average* market counts ({concentration[concentration['tier']=='skilled'].iloc[0]['avg_markets']:.0f} vs {concentration[concentration['tier']=='unskilled'].iloc[0]['avg_markets']:.0f}) despite similar medians (~29).

7. **Neg risk markets**: Multi-outcome neg_risk markets amplify both gains and losses. PnL edge is ${nr_true_pnl_edge:,.0f}/position in neg_risk vs ${nr_false_pnl_edge:,.0f} in standard binary markets. Unskilled traders lose almost 6x more per position in neg_risk markets.

---

## 1. Market Concentration

**How many markets do top traders participate in?**

| Tier | Count | Avg Markets | Median | P25 | P75 | Min | Max |
|------|-------|-------------|--------|-----|-----|-----|-----|
"""

for _, row in concentration.iterrows():
    report += f"| {row['tier']} | {int(row['n']):,} | {row['avg_markets']:.1f} | {row['median_markets']:.0f} | {row['p25_markets']:.0f} | {row['p75_markets']:.0f} | {int(row['min_markets'])} | {int(row['max_markets']):,} |\n"

report += f"""
**Gini coefficient of market participation** (0 = perfectly equal, 1 = extreme concentration):
- Skilled: {gini_results['skilled']}
- Middle: {gini_results['middle']}
- Unskilled: {gini_results['unskilled']}

All tiers show highly skewed participation -- a few traders are in thousands of markets while most stay in under 30. The skilled and unskilled Gini values are similar (0.82 vs 0.86), meaning both groups have power-law participation patterns. The middle tier (90% of traders) is less skewed (0.62) because it excludes the extreme tails.

**Volume concentration (HHI)** -- higher = more volume concentrated in fewer markets:

| Tier | Avg HHI | Median HHI |
|------|---------|------------|
"""

for _, row in vol_concentration.iterrows():
    report += f"| {row['tier']} | {row['avg_hhi']:.4f} | {row['median_hhi']:.4f} |\n"

report += """
HHI is similar across tiers (~0.25 median), indicating no significant difference in volume concentration strategy. Skilled traders do not systematically concentrate more or less than unskilled ones.

---

## 2. Category Edge

**Win rate and average PnL by category** (sorted by PnL edge = skilled avg PnL - unskilled avg PnL):

Note: Skilled traders often have *lower* win rates because they take contrarian positions in larger size. The PnL edge captures both win rate and bet sizing.

| Category | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N |
|----------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|
"""

for r in cat_rows[:25]:
    report += f"| {r['category']} | {r['skilled_wr']:.1%} | {r['unskilled_wr']:.1%} | {r['wr_edge']*100:+.1f}pp | ${r['skilled_avg_pnl']:,.2f} | ${r['unskilled_avg_pnl']:,.2f} | ${r['pnl_edge']:,.0f} | {r['skilled_n']:,} |\n"

report += """
**Top sub-categories by PnL edge** (min 500 positions per tier):

| Sub-category | Skilled WR | Unskilled WR | Skilled Avg PnL | PnL Edge | Skilled N |
|--------------|-----------|-------------|-----------------|----------|-----------|
"""

for r in subcat_rows[:15]:
    report += f"| {r['sub']} | {r['skilled_wr']:.1%} | {r['unskilled_wr']:.1%} | ${r['skilled_avg_pnl']:,.2f} | ${r['pnl_edge']:,.0f} | {r['n']:,} |\n"

report += """
---

## 3. Market Timing

**Do profitable traders enter markets earlier or later?**

Entry percentile: 0.0 = enters at the very first trade, 1.0 = enters at the very last trade.

| Tier | Avg Entry | P25 | Median | P75 | N Positions |
|------|-----------|-----|--------|-----|-------------|
"""

for _, row in timing.iterrows():
    report += f"| {row['tier']} | {row['avg_entry']:.3f} | {row['p25_entry']:.3f} | {row['median_entry']:.3f} | {row['p75_entry']:.3f} | {int(row['n']):,} |\n"

report += """
Skilled traders enter markets meaningfully earlier across the distribution. The median skilled trader enters at 85.7% of market lifetime vs 90.8% for unskilled. At P25, the gap widens: 58.6% vs 72.7%. This means skilled traders are more likely to be among the early participants in a market.

**Entry timing by PnL quintile** (Q1 = worst performers, Q5 = best):

| Quintile | Avg Entry | Median Entry | N Positions |
|----------|-----------|-------------|-------------|
"""

for _, row in timing_quintile.iterrows():
    report += f"| Q{int(row['pnl_quintile'])} | {row['avg_entry']:.3f} | {row['median_entry']:.3f} | {int(row['n']):,} |\n"

report += """
Interestingly, the relationship is U-shaped: Q1 (worst) and Q5 (best) both enter later than Q2-Q4. This reflects two distinct populations in the tails: Q1 are late FOMO traders chasing prices, while Q5 are high-volume informed traders who time entries strategically. The Q3 middle quintile enters earliest (median 0.772), suggesting casual early participants who break even.

---

## 4. Market Size Effect

**In which volume tier do skilled traders have the biggest edge?**

| Volume Tier | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N |
|-------------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|
"""

for r in size_rows:
    report += f"| {r['tier']} | {r['skilled_wr']:.1%} | {r['unskilled_wr']:.1%} | {r['wr_edge']*100:+.1f}pp | ${r['skilled_avg_pnl']:,.2f} | ${r['unskilled_avg_pnl']:,.2f} | ${r['pnl_edge']:,.0f} | {r['skilled_n']:,} |\n"

report += """
**Market count by volume tier:**

| Tier | N Markets | Avg Volume |
|------|-----------|------------|
"""

for _, row in mkt_counts.iterrows():
    report += f"| {row['vol_tier']} | {int(row['n_markets']):,} | ${row['avg_vol']:,.0f} |\n"

report += """
Key patterns:
- **Micro/small markets**: Unskilled traders have much higher win rates (78%/65% vs 15%/29%). These tiny markets likely have trivial outcomes where the "obvious" side wins but pays almost nothing. Skilled traders rarely bother.
- **Large markets ($10K-100K)**: Near parity in win rates, but skilled traders average +$46 vs -$45 per position -- a $91 edge from sizing and timing.
- **Mega markets (>$1M)**: Skilled traders earn $907/position vs -$1,363 for unskilled -- a $2,270 PnL edge. This is where skill pays off most in absolute dollars.
- The crossover happens at ~$100K volume: below this, unskilled win more often; above it, skilled traders dominate.

---

## 5. Consensus Markets

**Market difficulty = fraction of traders who end up profitable.**

"Hard" markets are those where few traders profit; "easy" markets are those where most do.

| Difficulty Bucket | N Markets | Avg Correct% | Avg Traders | Avg Volume |
|-------------------|-----------|-------------|-------------|------------|
"""

for _, row in consensus_dist.iterrows():
    report += f"| {row['bucket']} | {int(row['n_markets']):,} | {row['avg_correct']:.1%} | {row['avg_traders']:.0f} | ${row['avg_vol']:,.0f} |\n"

report += """
**Skilled vs unskilled by market difficulty:**

| Difficulty | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge |
|-----------|-----------|-------------|---------|-----------------|-------------------|----------|
"""

for r in cons_rows:
    report += f"| {r['difficulty']} | {r['skilled_wr']:.1%} | {r['unskilled_wr']:.1%} | {r['wr_edge']*100:+.1f}pp | ${r['skilled_avg_pnl']:,.2f} | ${r['unskilled_avg_pnl']:,.2f} | ${r['pnl_edge']:,.0f} |\n"

report += """
Key insights:
- **Very hard markets (<20% profitable)**: Only 1,636 markets but avg 1,036 traders each (high liquidity). These are the most contested markets. Skilled traders average $440/position even here, while unskilled lose $1,052.
- **The PnL edge is largest in hard markets**: Where the crowd gets it wrong, skilled analysis adds the most value.
- Win rates tell a counterintuitive story: unskilled traders have *higher* win rates in easy markets because they follow consensus on obvious outcomes. But their losses in hard markets wipe out those gains.
- The "very hard" bucket (<20% correct) contains large, contested markets where strong opinions collide -- exactly where information edges matter.

---

## 6. Time-to-Resolution

**Skilled vs unskilled by market duration:**

| Duration | Skilled WR | Unskilled WR | WR Edge | Skilled Avg PnL | Unskilled Avg PnL | PnL Edge | Skilled N | Unskilled N |
|----------|-----------|-------------|---------|-----------------|-------------------|----------|-----------|-------------|
"""

for r in ttr_rows:
    report += f"| {r['tier']} | {r['skilled_wr']:.1%} | {r['unskilled_wr']:.1%} | {r['wr_edge']*100:+.1f}pp | ${r['skilled_avg_pnl']:,.2f} | ${r['unskilled_avg_pnl']:,.2f} | ${r['pnl_edge']:,.0f} | {r['skilled_n']:,} | {r['unskilled_n']:,} |\n"

report += """
**Average market duration preference:**

| Tier | Avg Days to Resolution | Avg Median Days | N Traders |
|------|----------------------|-----------------|-----------|
"""

for _, row in ttr_pref.iterrows():
    report += f"| {row['tier']} | {row['avg_days']:.1f} | {row['avg_median_days']:.1f} | {int(row['n']):,} |\n"

report += """
Key insights:
- **Short-duration (<1 day)**: Most positions land here (4.6M skilled, 10.6M unskilled). These are the crypto/sports recurring markets. PnL edge is modest ($88/position) because outcomes are more random.
- **3-12 month markets**: Largest PnL edge per position. These are the political/macro markets where fundamental analysis and patience pay off.
- Unskilled traders are disproportionately concentrated in sub-day markets (10.6M vs 4.6M) -- they prefer the dopamine of fast resolution.
- Skilled traders slightly prefer shorter hold periods (avg 63 vs 67 days), but this likely reflects broader participation rather than a duration preference.

---

## 7. Neg Risk Markets

**neg_risk markets** are multi-outcome markets (e.g., "Who will win the Super Bowl?") using negative-risk accounting. Standard markets are binary YES/NO.

**Market-level statistics:**

| neg_risk | N Markets | Avg Traders | Avg Volume | Median Volume |
|----------|-----------|-------------|------------|---------------|
"""

for _, row in neg_mkt.iterrows():
    report += f"| {row['neg_risk']} | {int(row['n_markets']):,} | {row['avg_traders']:.0f} | ${row['avg_vol']:,.0f} | ${row['median_vol']:,.0f} |\n"

report += """
**Skilled vs unskilled by neg_risk:**

| neg_risk | Tier | N Positions | Win Rate | Avg PnL | Total PnL | Avg Position Size |
|----------|------|-------------|----------|---------|-----------|-------------------|
"""

for _, row in neg_risk.iterrows():
    report += f"| {row['neg_risk']} | {row['tier']} | {int(row['n']):,} | {row['win_rate']:.1%} | ${row['avg_pnl']:,.2f} | ${row['total_pnl']:,.0f} | ${row['avg_position']:,.2f} |\n"

report += """
**Neg risk allocation preference** (fraction of each trader's volume in neg_risk markets):

| Tier | Avg Fraction | Median Fraction |
|------|-------------|-----------------|
"""

for _, row in neg_pref.iterrows():
    report += f"| {row['tier']} | {row['avg_frac']:.1%} | {row['median_frac']:.1%} |\n"

report += f"""
Key insights:
- **Neg risk amplifies everything**: Unskilled traders lose ${abs(nr_u_true['avg_pnl']):,.0f}/position in neg_risk markets vs ${abs(nr_u_false['avg_pnl']):,.0f} in standard -- a 6x difference. Skilled traders earn ${nr_s_true['avg_pnl']:,.0f} vs ${nr_s_false['avg_pnl']:,.0f}.
- **Position sizes diverge**: Unskilled traders bet ${nr_u_true['avg_position']:,.0f} avg in neg_risk vs ${nr_u_false['avg_position']:,.0f} in standard. The larger sizing combined with lower skill creates outsized losses.
- **Both groups allocate ~44% to neg_risk** by volume, so the difference is not in allocation but in execution.
- Neg risk markets attract higher conviction bets (bigger positions) because traders feel they have edge in multi-outcome markets. But the complexity creates more opportunities for mispricing that skilled traders exploit.

---

## Actionable Implications for Strategy Design

1. **Focus on large/mega markets** (>$100K volume) -- this is where skilled analysis generates the highest absolute returns and where the skilled-trader edge is positive on a win-rate basis.

2. **Enter early** -- the earlier a signal identifies a market opportunity relative to the market lifecycle, the more alpha it captures. Target entry in the first 60% of market lifetime.

3. **Target "hard" markets** -- markets where consensus is split or where most traders lose are where analytical edge converts to the largest profits.

4. **Political/macro markets** (3-12 month horizon) offer the best PnL edge per position. Short-duration recurring markets (crypto up/down, sports) have smaller edges.

5. **Neg risk markets require extra caution** -- they amplify both skill and mistakes. Position sizing discipline is critical.

6. **Category specialization pays** -- categories like Biden/Politics, Celebrities, and Crypto have large PnL edges for skilled traders, suggesting domain knowledge matters.
"""

OUTPUT.write_text(report)
print(f"\nReport written to {OUTPUT}")
print(f"  Length: {len(report):,} chars")
