"""Track A: Ultra-HR In-Play Traders Discovery.

Research Questions:
1. How many traders like 0x751a exist? (>=50 in-play positions, >=80% HR, median vol >=5, not pure gambling)
2. Are these traders ONLY in gambling markets?
3. If any trade non-gambling markets: what's their HR on non-gambling in-play entries?
4. Latency analysis: time gap between their entry and the previous pool trader entry (copyability window)
5. Persistence: train (pre-2025-07-01) vs test (post-2025-07-01)

All results labeled UPPER BOUNDS (vectorized, no tick simulation).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJ = Path("/mnt/nvme/git/polymarket/polymarket")
sys.path.insert(0, str(PROJ))

LOG = PROJ / "tmp" / "track_a_ultra_hr.log"


def log(msg: str) -> None:
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")


log("=" * 60)
log(f"Track A Ultra-HR Discovery — {time.strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 60)

# ---------------------------------------------------------------------------
# Step 0: Load DuckDB
# ---------------------------------------------------------------------------
log("\n[0] Loading DuckDB...")
from research.db import db as get_db  # noqa: E402

con = get_db().con
log("DuckDB loaded.")

# Quick schema check
cols = con.execute("PRAGMA table_info(maker_positions)").fetchall()
col_names = [c[1] for c in cols]
log(f"maker_positions columns: {col_names}")

# ---------------------------------------------------------------------------
# Step 1: Identify ultra-HR in-play traders
# ---------------------------------------------------------------------------
log("\n[1] Finding ultra-HR in-play traders (>= 50 positions, >= 80% HR, median vol >= $5)")

# In-play = hold < 4h (240 min)
# Gambling market filter: slugs containing 'up-or-down' OR 'above-below' OR 'higher-lower'
# We identify gambling markets via markets table slug
# CRITICAL: phantom signal filter — first_trade is the entry time,
# resolved_at is resolution. For in-play, the "phantom" issue is less relevant
# since in-play by definition means first_trade is recent.
# But we still need to ensure we're measuring per-market correctly.

# First, check if we have a slug column in markets
mkt_cols = con.execute("PRAGMA table_info(markets)").fetchall()
mkt_col_names = [c[1] for c in mkt_cols]
log(f"markets columns: {mkt_col_names}")

# Check event_tags columns
et_cols = con.execute("PRAGMA table_info(event_tags)").fetchall()
et_col_names = [c[1] for c in et_cols]
log(f"event_tags columns: {et_col_names}")

# Sample a few rows of markets to understand slug format
sample = con.execute("""
    SELECT condition_id, slug, question
    FROM markets
    WHERE lower(slug) LIKE '%up-or-down%' OR lower(slug) LIKE '%above-or-below%'
    LIMIT 5
""").fetchall()
log(f"Gambling market sample (slug-based): {sample}")

# Check if 'question' or 'slug' column exists in markets
has_slug = 'slug' in mkt_col_names
has_question = 'question' in mkt_col_names
log(f"has_slug={has_slug}, has_question={has_question}")

# ---------------------------------------------------------------------------
# Step 1a: Define gambling markets via slug pattern
# ---------------------------------------------------------------------------
log("\n[1a] Classifying gambling vs non-gambling markets...")

# Gambling patterns from background: "Up or Down", "above/below", "higher/lower"
# Also include "will-btc", "will-eth", "will-xrp" type crypto price prediction markets
GAMBLING_PATTERNS = [
    'up-or-down', 'up_or_down',
    'above-or-below', 'above-below',
    'higher-or-lower', 'higher-lower',
    'will-bitcoin-', 'will-btc-',
    'will-eth-', 'will-ethereum-',
    'will-xrp-', 'will-sol-',
    '-1h-', '-24h-',  # hourly/daily crypto markers in slug
]

# Build gambling market set from slug patterns
gambling_cond_sql = " OR ".join(
    [f"lower(slug) LIKE '%{p}%'" for p in GAMBLING_PATTERNS]
)

con.execute(f"""
    CREATE OR REPLACE TABLE _gambling_markets AS
    SELECT condition_id, slug
    FROM markets
    WHERE {gambling_cond_sql}
""")
n_gamble = con.execute("SELECT count() FROM _gambling_markets").fetchone()[0]
log(f"Gambling markets identified by slug: {n_gamble:,}")

# Also count non-gambling
n_total_mkt = con.execute("SELECT count() FROM markets").fetchone()[0]
log(f"Total markets: {n_total_mkt:,}, Non-gambling: {n_total_mkt - n_gamble:,}")

# ---------------------------------------------------------------------------
# Step 1b: Compute per-trader in-play stats (aggregated at MARKET level first)
# ---------------------------------------------------------------------------
log("\n[1b] Computing per-trader in-play stats (market-level aggregation)...")
# NOTE: Per the vectorized counting unit rule, we must aggregate at market level.
# Each position in maker_positions is already one trader × one market (unique pair),
# so no double-counting issue here — maker_positions primary key is (trader, condition_id).
# But we must still aggregate MARKET-level stats correctly.

# Hold time < 4h = 240 minutes
# date_diff returns integer days, so use epoch seconds for sub-day precision
# hold_hours = (epoch(resolved_at) - epoch(first_trade)) / 3600

# Step: label each position as gambling or not, in-play or not
con.execute("""
    CREATE OR REPLACE TABLE _inplay_positions AS
    SELECT
        mp.trader,
        mp.condition_id,
        mp.position,
        mp.yes_won,
        mp.correct,
        mp.first_trade,
        mp.resolved_at,
        mp.volume,
        mp.net_usd,
        -- hold in hours
        (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 AS hold_hours,
        -- gambling flag
        CASE WHEN gm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS is_gambling,
        -- correct for our copy: YES traders where yes_won=1, or NO traders where yes_won=0
        CASE
            WHEN mp.position = 'YES' AND mp.yes_won = 1 THEN 1
            WHEN mp.position = 'YES' AND mp.yes_won = 0 THEN 0
            WHEN mp.position = 'NO'  AND mp.yes_won = 0 THEN 1
            WHEN mp.position = 'NO'  AND mp.yes_won = 1 THEN 0
            ELSE NULL
        END AS position_correct
    FROM maker_positions mp
    LEFT JOIN _gambling_markets gm ON mp.condition_id = gm.condition_id
    WHERE
        mp.resolved_at IS NOT NULL
        AND mp.first_trade IS NOT NULL
        AND mp.position IN ('YES', 'NO')
        -- hold_hours >= 0 (sanity)
        AND (epoch(mp.resolved_at) - epoch(mp.first_trade)) >= 0
        -- in-play: hold < 4h
        AND (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 < 4.0
        -- filter bots: min $5 median requires checking later in aggregation
""")
n_inplay = con.execute("SELECT count() FROM _inplay_positions").fetchone()[0]
log(f"Total in-play positions (hold < 4h, YES/NO): {n_inplay:,}")

# ---------------------------------------------------------------------------
# Step 1c: Aggregate per-trader stats
# ---------------------------------------------------------------------------
log("\n[1c] Aggregating per-trader in-play stats...")

con.execute("""
    CREATE OR REPLACE TABLE _trader_inplay_stats AS
    SELECT
        trader,
        count(*) AS n_inplay,
        count(*) FILTER (WHERE is_gambling = 0) AS n_non_gambling,
        count(*) FILTER (WHERE is_gambling = 1) AS n_gambling,
        -- Overall HR
        avg(position_correct) AS hr_overall,
        avg(position_correct) FILTER (WHERE is_gambling = 0) AS hr_non_gambling,
        avg(position_correct) FILTER (WHERE is_gambling = 1) AS hr_gambling,
        -- Volume stats
        median(volume) AS median_vol,
        sum(volume) AS total_vol,
        -- Hold time
        median(hold_hours) AS median_hold_hours,
        -- Position split
        count(*) FILTER (WHERE position = 'YES') AS n_yes,
        count(*) FILTER (WHERE position = 'NO') AS n_no,
        -- Gambling fraction
        count(*) FILTER (WHERE is_gambling = 1) * 1.0 / count(*) AS gambling_frac
    FROM _inplay_positions
    GROUP BY trader
    HAVING
        count(*) >= 50             -- min 50 in-play positions
        AND avg(position_correct) >= 0.80  -- min 80% HR
        AND median(volume) >= 5.0   -- min $5 median position size (bot filter)
""")

n_elite = con.execute("SELECT count() FROM _trader_inplay_stats").fetchone()[0]
log(f"Elite in-play traders (>=50 positions, >=80% HR, median vol >=$5): {n_elite:,}")

# Show top traders
top_traders = con.execute("""
    SELECT trader, n_inplay, n_gambling, n_non_gambling,
           round(hr_overall * 100, 2) AS hr_overall_pct,
           round(hr_gambling * 100, 2) AS hr_gambling_pct,
           round(hr_non_gambling * 100, 2) AS hr_non_gambling_pct,
           round(median_vol, 2) AS median_vol,
           round(total_vol, 0) AS total_vol,
           round(gambling_frac * 100, 1) AS gambling_frac_pct,
           round(median_hold_hours, 2) AS median_hold_h,
           n_yes, n_no
    FROM _trader_inplay_stats
    ORDER BY hr_overall DESC, n_inplay DESC
    LIMIT 30
""").fetchall()

log("\n--- Top 30 Elite In-Play Traders ---")
log(f"{'Trader':<14} {'N':>6} {'N_gamb':>7} {'N_nonG':>7} {'HR%':>7} {'HR_G%':>7} {'HR_NG%':>8} {'Med$':>7} {'Total$':>10} {'Gamb%':>6} {'Hold_h':>7} {'nY':>5} {'nN':>5}")
for row in top_traders:
    log(f"{row[0][:12]:<14} {row[1]:>6} {row[2]:>7} {row[3]:>7} {row[4]:>7} {str(row[5]):>7} {str(row[6]):>8} {row[7]:>7} {row[8]:>10} {row[9]:>6} {row[10]:>7} {row[11]:>5} {row[12]:>5}")

# ---------------------------------------------------------------------------
# Step 2: Gambling vs Non-Gambling Analysis
# ---------------------------------------------------------------------------
log("\n[2] Gambling vs Non-Gambling Breakdown...")

dist = con.execute("""
    SELECT
        CASE
            WHEN gambling_frac >= 0.99 THEN 'pure_gambling'
            WHEN gambling_frac >= 0.70 THEN 'mostly_gambling'
            WHEN gambling_frac >= 0.30 THEN 'mixed'
            WHEN gambling_frac > 0.01  THEN 'mostly_non_gambling'
            ELSE 'pure_non_gambling'
        END AS category,
        count(*) AS n_traders,
        round(avg(hr_overall) * 100, 2) AS avg_hr_pct,
        round(avg(hr_gambling) * 100, 2) AS avg_hr_gambling_pct,
        round(avg(hr_non_gambling) * 100, 2) AS avg_hr_non_gambling_pct,
        round(median(median_vol), 2) AS median_median_vol,
        round(sum(total_vol), 0) AS total_vol_sum
    FROM _trader_inplay_stats
    GROUP BY 1
    ORDER BY avg_hr_pct DESC
""").fetchall()

log("\n--- Elite Trader Distribution: Gambling vs Non-Gambling ---")
log(f"{'Category':<22} {'N_traders':>10} {'HR%':>7} {'HR_G%':>7} {'HR_NG%':>8} {'MedVol':>8} {'TotalVol':>12}")
for row in dist:
    log(f"{row[0]:<22} {row[1]:>10} {row[2]:>7} {str(row[3]):>7} {str(row[4]):>8} {row[5]:>8} {row[6]:>12}")

# Count pure-gambling vs non
n_pure_gamble = con.execute("""
    SELECT count() FROM _trader_inplay_stats WHERE gambling_frac >= 0.99
""").fetchone()[0]
n_non_gamble_traders = con.execute("""
    SELECT count() FROM _trader_inplay_stats WHERE gambling_frac < 0.99
""").fetchone()[0]
log(f"\nPure gambling traders (>99% gambling mkts): {n_pure_gamble}")
log(f"Traders with non-gambling activity: {n_non_gamble_traders}")

# ---------------------------------------------------------------------------
# Step 3: Non-Gambling HR Analysis for mixed/non-gambling traders
# ---------------------------------------------------------------------------
log("\n[3] Non-Gambling HR Analysis (traders with >=5 non-gambling in-play positions)...")

non_gamble_hr = con.execute("""
    SELECT
        trader,
        n_non_gambling,
        n_gambling,
        round(hr_non_gambling * 100, 2) AS hr_non_gambling_pct,
        round(hr_gambling * 100, 2) AS hr_gambling_pct,
        round(median_vol, 2) AS median_vol,
        round(total_vol, 0) AS total_vol,
        round(median_hold_hours, 2) AS hold_h
    FROM _trader_inplay_stats
    WHERE n_non_gambling >= 5
    ORDER BY hr_non_gambling DESC, n_non_gambling DESC
    LIMIT 20
""").fetchall()

log("\n--- Traders with Non-Gambling In-Play Activity (>=5 non-gamble positions) ---")
log(f"{'Trader':<14} {'N_NG':>6} {'N_G':>6} {'HR_NG%':>8} {'HR_G%':>7} {'MedVol':>8} {'Total$':>10} {'Hold_h':>7}")
for row in non_gamble_hr:
    log(f"{row[0][:12]:<14} {row[1]:>6} {row[2]:>6} {row[3]:>8} {str(row[4]):>7} {row[5]:>8} {row[6]:>10} {row[7]:>7}")

# What tags are non-gambling elite traders active in?
log("\n[3b] Tag distribution of non-gambling in-play positions from elite traders...")
# Get the traders with non-gambling activity
non_gamble_traders = con.execute("""
    SELECT trader FROM _trader_inplay_stats WHERE n_non_gambling >= 5
""").fetchall()
ng_trader_list = [r[0] for r in non_gamble_traders]
log(f"Number of elite traders with >=5 non-gambling in-play positions: {len(ng_trader_list)}")

if len(ng_trader_list) > 0:
    # Get tag distribution for these traders' non-gambling positions
    tag_dist = con.execute("""
        SELECT
            et.label AS tag,
            count(*) AS n_positions,
            count(DISTINCT ip.condition_id) AS n_markets,
            count(DISTINCT ip.trader) AS n_traders,
            round(avg(ip.position_correct) * 100, 2) AS hr_pct,
            round(median(ip.volume), 2) AS median_vol
        FROM _inplay_positions ip
        JOIN _trader_inplay_stats ts ON ip.trader = ts.trader
        JOIN markets m ON ip.condition_id = m.condition_id
        JOIN events e ON m.event_id = e.id
        JOIN event_tags et ON e.id = et.event_id
        WHERE
            ip.is_gambling = 0
            AND ts.n_non_gambling >= 5
        GROUP BY et.label
        ORDER BY n_positions DESC
        LIMIT 20
    """).fetchall()

    log(f"\n{'Tag':<25} {'N_pos':>7} {'N_mkts':>7} {'N_trdrs':>8} {'HR%':>7} {'MedVol':>8}")
    for row in tag_dist:
        log(f"{str(row[0]):<25} {row[1]:>7} {row[2]:>7} {row[3]:>8} {row[4]:>7} {row[5]:>8}")

# ---------------------------------------------------------------------------
# Step 4: Latency Analysis — Copyability Window
# ---------------------------------------------------------------------------
log("\n[4] Latency Analysis: Copyability Window...")
log("Computing time gap between elite trader entry and next-earlier pool trader entry...")

# For each market where an elite trader entered, find:
# - The elite trader's first_trade time
# - The timestamp of the PREVIOUS trade in the same market from the broader population
# This uses yes_entry_data which has per-trader entry times for YES positions.
# For a proper latency analysis, we need the trades table.

# First, let's do a simpler version: in maker_positions, for markets where elite traders
# entered, what's the time gap between the elite's first_trade and the market's resolution?
# This tells us "how late" they entered (how much time remained).

# Get time-to-resolution distribution for elite traders
time_to_res = con.execute("""
    SELECT
        CASE
            WHEN hold_hours < 0.5 THEN '<30min'
            WHEN hold_hours < 1.0 THEN '30-60min'
            WHEN hold_hours < 2.0 THEN '1-2h'
            WHEN hold_hours < 3.0 THEN '2-3h'
            ELSE '3-4h'
        END AS bucket,
        count(*) AS n_positions,
        round(avg(position_correct) * 100, 2) AS hr_pct,
        round(median(volume), 2) AS median_vol
    FROM _inplay_positions ip
    JOIN _trader_inplay_stats ts ON ip.trader = ts.trader
    GROUP BY 1
    ORDER BY 1
""").fetchall()

log("\n--- Elite Trader Time-to-Resolution Distribution ---")
log(f"{'Bucket':<12} {'N_pos':>8} {'HR%':>7} {'MedVol':>8}")
for row in time_to_res:
    log(f"{row[0]:<12} {row[1]:>8} {row[2]:>7} {row[3]:>8}")

# Now do actual latency analysis using yes_entry_data
# For each market where an elite trader entered YES, find:
# a) Elite trader's entry time
# b) The LATEST entry time of any OTHER trader (from broader maker pool)
# c) Gap = elite's entry - previous_last_entry (positive = elite entered after others, negative = first)
log("\n[4b] Computing actual entry time gap via yes_entry_data...")

# Get elite traders
elite_traders_list = con.execute("SELECT trader FROM _trader_inplay_stats").fetchall()
elite_set = [r[0] for r in elite_traders_list]
log(f"Elite traders to analyze: {len(elite_set)}")

# For yes_entry_data, get yes markets where elite traders entered
# Compute gap between elite's entry and last non-elite entry in same market
latency_df = con.execute("""
    WITH elite_yes_entries AS (
        -- Elite trader YES entries in markets
        SELECT
            yed.condition_id,
            yed.trader AS elite_trader,
            yed.first_trade AS elite_entry,
            ip.hold_hours,
            ip.volume AS elite_vol
        FROM yes_entry_data yed
        JOIN _trader_inplay_stats ts ON yed.trader = ts.trader
        JOIN _inplay_positions ip ON yed.trader = ip.trader AND yed.condition_id = ip.condition_id
            AND ip.position = 'YES'
        WHERE ip.hold_hours < 4.0
    ),
    all_other_entries AS (
        -- All other traders' YES entries in same markets
        SELECT
            yed.condition_id,
            yed.trader,
            yed.first_trade AS other_entry
        FROM yes_entry_data yed
        WHERE yed.condition_id IN (SELECT DISTINCT condition_id FROM elite_yes_entries)
          AND yed.trader NOT IN (SELECT elite_trader FROM elite_yes_entries WHERE condition_id = yed.condition_id)
    ),
    last_other_entry AS (
        SELECT
            condition_id,
            max(other_entry) AS last_other_entry_time,
            count(DISTINCT trader) AS n_other_traders
        FROM all_other_entries
        GROUP BY condition_id
    )
    SELECT
        ee.elite_trader,
        ee.condition_id,
        ee.elite_entry,
        loe.last_other_entry_time,
        -- Positive: elite entered AFTER last pool trader (can copy)
        -- Negative: elite entered BEFORE any pool trader (elite is first, not copyable)
        (epoch(ee.elite_entry) - epoch(loe.last_other_entry_time)) / 60.0 AS gap_minutes,
        loe.n_other_traders,
        ee.hold_hours,
        ee.elite_vol
    FROM elite_yes_entries ee
    LEFT JOIN last_other_entry loe ON ee.condition_id = loe.condition_id
""").pl()

log(f"\nLatency analysis rows: {len(latency_df)}")

if len(latency_df) > 0:
    # Distribution of gap_minutes
    gap_dist = con.execute("""
        SELECT
            CASE
                WHEN gap_minutes IS NULL THEN 'no_other_traders'
                WHEN gap_minutes < -60 THEN 'elite_1h_earlier'
                WHEN gap_minutes < -30 THEN 'elite_30-60min_earlier'
                WHEN gap_minutes < -10 THEN 'elite_10-30min_earlier'
                WHEN gap_minutes < 0   THEN 'elite_0-10min_earlier'
                WHEN gap_minutes < 5   THEN 'within_5min_after'
                WHEN gap_minutes < 15  THEN '5-15min_after'
                WHEN gap_minutes < 60  THEN '15-60min_after'
                ELSE 'over_1h_after'
            END AS bucket,
            count(*) AS n,
            round(median(gap_minutes), 1) AS median_gap_min
        FROM (
            SELECT
                (epoch(ee.elite_entry) - epoch(loe.last_other_entry_time)) / 60.0 AS gap_minutes
            FROM yes_entry_data yed
            JOIN _trader_inplay_stats ts ON yed.trader = ts.trader
            JOIN _inplay_positions ip ON yed.trader = ip.trader AND yed.condition_id = ip.condition_id
                AND ip.position = 'YES' AND ip.hold_hours < 4.0
            LEFT JOIN (
                SELECT condition_id, max(first_trade) AS last_other_entry_time
                FROM yes_entry_data
                WHERE trader NOT IN (SELECT trader FROM _trader_inplay_stats)
                GROUP BY condition_id
            ) loe ON yed.condition_id = loe.condition_id
        )
        GROUP BY 1
        ORDER BY n DESC
    """).fetchall()

    log("\n--- Gap Between Elite Entry and Last Pool Trader Entry ---")
    log("(positive = elite entered AFTER pool, meaning pool could copy)")
    log(f"{'Bucket':<30} {'Count':>8} {'Median_gap_min':>15}")
    for row in gap_dist:
        log(f"{row[0]:<30} {row[1]:>8} {str(row[2]):>15}")

# ---------------------------------------------------------------------------
# Step 5: Persistence Analysis (Train vs Test)
# ---------------------------------------------------------------------------
log("\n[5] Persistence Analysis: Train (pre-2025-07-01) vs Test (post-2025-07-01)...")

# Compute per-trader stats split by period
# CRITICAL: phantom signal filter — only count positions where first_trade >= period_start
con.execute("""
    CREATE OR REPLACE TABLE _trader_period_stats AS
    SELECT
        trader,
        CASE WHEN first_trade < TIMESTAMP '2025-07-01' THEN 'train' ELSE 'test' END AS period,
        count(*) AS n_positions,
        avg(position_correct) AS hr,
        median(volume) AS median_vol,
        sum(volume) AS total_vol,
        count(*) FILTER (WHERE is_gambling = 0) AS n_non_gambling,
        avg(position_correct) FILTER (WHERE is_gambling = 0) AS hr_non_gambling
    FROM _inplay_positions
    WHERE trader IN (SELECT trader FROM _trader_inplay_stats)
    GROUP BY trader, period
""")

# Pivot to train/test side by side
persistence = con.execute("""
    SELECT
        tr.trader,
        tr.n_positions AS train_n,
        round(tr.hr * 100, 2) AS train_hr_pct,
        round(tr.median_vol, 2) AS train_med_vol,
        te.n_positions AS test_n,
        round(te.hr * 100, 2) AS test_hr_pct,
        round(te.median_vol, 2) AS test_med_vol,
        -- HR drop: negative = degraded
        round((te.hr - tr.hr) * 100, 2) AS hr_delta_pp
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train'
      AND tr.n_positions >= 20  -- need min train data
    ORDER BY test_hr_pct DESC NULLS LAST, train_hr_pct DESC
    LIMIT 30
""").fetchall()

log("\n--- Train vs Test HR Persistence (elite traders, min 20 train positions) ---")
log(f"{'Trader':<14} {'Tr_N':>6} {'Tr_HR%':>8} {'Tr_Med':>7} {'Te_N':>6} {'Te_HR%':>8} {'Te_Med':>7} {'Delta_pp':>9}")
for row in persistence:
    log(f"{row[0][:12]:<14} {row[1]:>6} {row[2]:>8} {row[3]:>7} {str(row[4]):>6} {str(row[5]):>8} {str(row[6]):>7} {str(row[7]):>9}")

# Summary: how many maintain >70% HR in test?
persist_summary = con.execute("""
    SELECT
        count(*) FILTER (WHERE te.hr >= 0.70) AS n_maintain_70pct,
        count(*) FILTER (WHERE te.hr >= 0.80) AS n_maintain_80pct,
        count(*) FILTER (WHERE te.hr >= 0.90) AS n_maintain_90pct,
        count(*) FILTER (WHERE te.hr IS NOT NULL) AS n_active_in_test,
        count(*) AS n_total_with_train
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train' AND tr.n_positions >= 20
""").fetchone()

log(f"\nPersistence summary (min 20 train positions):")
log(f"  Total with sufficient train data: {persist_summary[4]}")
log(f"  Active in test period: {persist_summary[3]}")
log(f"  Maintain >=70% HR in test: {persist_summary[0]}")
log(f"  Maintain >=80% HR in test: {persist_summary[1]}")
log(f"  Maintain >=90% HR in test: {persist_summary[2]}")

# ---------------------------------------------------------------------------
# Step 6: Deeper profile of 0x751a and top-5 similar traders
# ---------------------------------------------------------------------------
log("\n[6] Deep profile of 0x751a and top comparable traders...")

# Check if 0x751a is in our elite set
elite_0x751a = con.execute("""
    SELECT * FROM _trader_inplay_stats
    WHERE trader LIKE '0x751a%'
""").fetchall()
log(f"0x751a in elite set: {len(elite_0x751a) > 0}")
if elite_0x751a:
    row = elite_0x751a[0]
    log(f"  0x751a stats: n={row[1]}, HR={round(row[3]*100,2)}%, total_vol=${row[9]:,.0f}")

# Top 5 traders by test HR with sufficient test data
top_test = con.execute("""
    SELECT
        ts.trader,
        ts.n_inplay,
        round(ts.hr_overall * 100, 2) AS hr_overall_pct,
        round(ts.hr_gambling * 100, 2) AS hr_gambling_pct,
        round(ts.hr_non_gambling * 100, 2) AS hr_non_gambling_pct,
        ts.n_gambling,
        ts.n_non_gambling,
        round(ts.median_vol, 2) AS median_vol,
        round(ts.total_vol, 0) AS total_vol,
        round(ts.gambling_frac * 100, 1) AS gambling_frac_pct,
        round(ts.median_hold_hours, 2) AS median_hold_h,
        te.n_positions AS test_n,
        round(te.hr * 100, 2) AS test_hr_pct
    FROM _trader_inplay_stats ts
    LEFT JOIN _trader_period_stats te ON ts.trader = te.trader AND te.period = 'test'
    WHERE te.n_positions >= 10  -- active in test
    ORDER BY te.hr DESC, ts.n_inplay DESC
    LIMIT 10
""").fetchall()

log("\n--- Top 10 Traders by Test-Period HR (>=10 test positions) ---")
log(f"{'Trader':<14} {'N_all':>6} {'HR_all%':>8} {'HR_G%':>7} {'HR_NG%':>8} {'N_G':>5} {'N_NG':>5} {'MedVol':>8} {'Total$':>10} {'G%':>5} {'Hold_h':>7} {'Te_N':>5} {'Te_HR%':>8}")
for row in top_test:
    log(f"{row[0][:12]:<14} {row[1]:>6} {row[2]:>8} {str(row[3]):>7} {str(row[4]):>8} {row[5]:>5} {row[6]:>5} {row[7]:>8} {row[8]:>10} {row[9]:>5} {row[10]:>7} {str(row[11]):>5} {str(row[12]):>8}")

# ---------------------------------------------------------------------------
# Step 7: Tag-Specific HR for Top Traders
# ---------------------------------------------------------------------------
log("\n[7] Tag-specific HR for elite in-play traders...")

tag_trader_hr = con.execute("""
    SELECT
        et.label AS tag,
        count(DISTINCT ip.trader) AS n_elite_traders,
        count(DISTINCT ip.condition_id) AS n_markets,
        count(*) AS n_positions,
        round(avg(ip.position_correct) * 100, 2) AS hr_pct,
        round(median(ip.volume), 2) AS median_vol,
        round(sum(ip.volume), 0) AS total_vol
    FROM _inplay_positions ip
    JOIN _trader_inplay_stats ts ON ip.trader = ts.trader
    JOIN markets m ON ip.condition_id = m.condition_id
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    GROUP BY et.label
    HAVING count(DISTINCT ip.trader) >= 2  -- need at least 2 elite traders in tag
    ORDER BY hr_pct DESC, n_positions DESC
    LIMIT 25
""").fetchall()

log(f"\n{'Tag':<25} {'N_trdrs':>8} {'N_mkts':>7} {'N_pos':>7} {'HR%':>7} {'MedVol':>8} {'Total$':>12}")
for row in tag_trader_hr:
    log(f"{str(row[0]):<25} {row[1]:>8} {row[2]:>7} {row[3]:>7} {row[4]:>7} {row[5]:>8} {row[6]:>12}")

# ---------------------------------------------------------------------------
# Step 8: Non-gambling specific tag breakdown
# ---------------------------------------------------------------------------
log("\n[8] Non-gambling in-play positions by tag (all elite traders)...")

non_gamble_tags = con.execute("""
    SELECT
        et.label AS tag,
        count(DISTINCT ip.trader) AS n_elite_traders,
        count(DISTINCT ip.condition_id) AS n_markets,
        count(*) AS n_positions,
        round(avg(ip.position_correct) * 100, 2) AS hr_pct,
        round(median(ip.volume), 2) AS median_vol,
        round(sum(ip.volume), 0) AS total_vol,
        -- Train vs test breakdown
        count(*) FILTER (WHERE ip.first_trade >= TIMESTAMP '2025-07-01') AS n_test_positions
    FROM _inplay_positions ip
    JOIN _trader_inplay_stats ts ON ip.trader = ts.trader
    JOIN markets m ON ip.condition_id = m.condition_id
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    WHERE ip.is_gambling = 0
    GROUP BY et.label
    HAVING count(*) >= 10
    ORDER BY hr_pct DESC, n_positions DESC
    LIMIT 20
""").fetchall()

log(f"\n{'Tag':<25} {'N_trdrs':>8} {'N_mkts':>7} {'N_pos':>7} {'HR%':>7} {'MedVol':>8} {'Total$':>12} {'N_test':>7}")
for row in non_gamble_tags:
    log(f"{str(row[0]):<25} {row[1]:>8} {row[2]:>7} {row[3]:>7} {row[4]:>7} {row[5]:>8} {row[6]:>12} {row[7]:>7}")

# ---------------------------------------------------------------------------
# Step 9: Copyability Assessment
# ---------------------------------------------------------------------------
log("\n[9] Copyability Assessment Summary...")

# For each elite trader, assess copyability score:
# - Test HR (persistence)
# - Volume (tradeable size)
# - Non-gambling fraction (signal independence)
# - Hold time (enough window to copy)
copy_assess = con.execute("""
    SELECT
        ts.trader,
        ts.n_inplay,
        round(ts.hr_overall * 100, 2) AS hr_all_pct,
        round(ts.hr_non_gambling * 100, 2) AS hr_ng_pct,
        round(ts.gambling_frac * 100, 1) AS gamble_frac_pct,
        round(ts.median_hold_hours * 60, 0) AS median_hold_min,
        round(ts.median_vol, 2) AS median_vol,
        round(ts.total_vol, 0) AS total_vol,
        te.n_positions AS test_n,
        round(te.hr * 100, 2) AS test_hr,
        -- Copyability score: HR in test * non-gambling fraction * log(median_hold_hours)
        CASE WHEN te.hr IS NOT NULL THEN
            round(te.hr * (1 - ts.gambling_frac) * log(ts.median_hold_hours + 1) * 100, 2)
        ELSE 0 END AS copy_score
    FROM _trader_inplay_stats ts
    LEFT JOIN _trader_period_stats te ON ts.trader = te.trader AND te.period = 'test'
    ORDER BY copy_score DESC, test_hr DESC NULLS LAST
    LIMIT 20
""").fetchall()

log("\n--- Copyability Assessment (sorted by copy_score) ---")
log(f"{'Trader':<14} {'N':>5} {'HR_all%':>8} {'HR_NG%':>8} {'Gamb%':>6} {'Hold_min':>9} {'MedVol':>8} {'TotalVol':>10} {'Te_N':>5} {'Te_HR%':>8} {'CopyScore':>10}")
for row in copy_assess:
    log(f"{row[0][:12]:<14} {row[1]:>5} {row[2]:>8} {str(row[3]):>8} {row[4]:>6} {row[5]:>9} {row[6]:>8} {row[7]:>10} {str(row[8]):>5} {str(row[9]):>8} {row[10]:>10}")

# ---------------------------------------------------------------------------
# Final Summary
# ---------------------------------------------------------------------------
log("\n" + "=" * 60)
log("FINAL SUMMARY")
log("=" * 60)

# Count elite traders by category
summary = con.execute("""
    SELECT
        count(*) AS total_elite,
        count(*) FILTER (WHERE gambling_frac >= 0.99) AS pure_gambling,
        count(*) FILTER (WHERE gambling_frac < 0.99) AS has_non_gambling,
        count(*) FILTER (WHERE n_non_gambling >= 5 AND hr_non_gambling >= 0.80) AS strong_non_gambling_signal,
        round(avg(hr_overall) * 100, 2) AS avg_hr,
        round(median(median_vol), 2) AS median_vol,
        round(sum(total_vol), 0) AS total_vol
    FROM _trader_inplay_stats
""").fetchone()

log(f"\nTotal elite traders found: {summary[0]}")
log(f"  Pure gambling traders (>99% gambling mkts): {summary[1]}")
log(f"  Traders with non-gambling in-play activity: {summary[2]}")
log(f"  Strong non-gambling signal (>=5 pos, >=80% HR): {summary[3]}")
log(f"  Average HR across all elite traders: {summary[4]}%")
log(f"  Median position size (median of medians): ${summary[5]}")
log(f"  Total volume across all elite in-play positions: ${summary[6]:,.0f}")

log(f"\nDone! Results written to: {LOG}")
log(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
