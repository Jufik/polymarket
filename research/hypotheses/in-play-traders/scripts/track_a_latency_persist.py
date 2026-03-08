"""Track A: Latency + Persistence + Tag analysis (continuation after step 3).

Fixes binder error in step 4b, completes steps 5-9.
Assumes _inplay_positions, _trader_inplay_stats already created or recreates them.
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


log("\n" + "=" * 60)
log(f"Track A Continuation (Latency+Persistence) — {time.strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 60)

from research.db import db as get_db  # noqa: E402

con = get_db().con
log("DuckDB loaded.")

# Check if previous tables exist
n_check = con.execute("""
    SELECT count() FROM information_schema.tables
    WHERE table_name IN ('_inplay_positions', '_trader_inplay_stats')
""").fetchone()[0]
log(f"Previous temp tables present: {n_check}/2")

if n_check < 2:
    log("Recreating tables from scratch...")
    # Rebuild gambling markets
    GAMBLING_PATTERNS = [
        'up-or-down', 'up_or_down',
        'above-or-below', 'above-below',
        'higher-or-lower', 'higher-lower',
        'will-bitcoin-', 'will-btc-',
        'will-eth-', 'will-ethereum-',
        'will-xrp-', 'will-sol-',
        '-1h-', '-24h-',
    ]
    gambling_cond_sql = " OR ".join(
        [f"lower(slug) LIKE '%{p}%'" for p in GAMBLING_PATTERNS]
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE _gambling_markets AS
        SELECT condition_id, slug FROM markets WHERE {gambling_cond_sql}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE _inplay_positions AS
        SELECT
            mp.trader, mp.condition_id, mp.position, mp.yes_won, mp.correct,
            mp.first_trade, mp.resolved_at, mp.volume, mp.net_usd,
            (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 AS hold_hours,
            CASE WHEN gm.condition_id IS NOT NULL THEN 1 ELSE 0 END AS is_gambling,
            CASE
                WHEN mp.position = 'YES' AND mp.yes_won = 1 THEN 1
                WHEN mp.position = 'YES' AND mp.yes_won = 0 THEN 0
                WHEN mp.position = 'NO'  AND mp.yes_won = 0 THEN 1
                WHEN mp.position = 'NO'  AND mp.yes_won = 1 THEN 0
                ELSE NULL
            END AS position_correct
        FROM maker_positions mp
        LEFT JOIN _gambling_markets gm ON mp.condition_id = gm.condition_id
        WHERE mp.resolved_at IS NOT NULL AND mp.first_trade IS NOT NULL
          AND mp.position IN ('YES', 'NO')
          AND (epoch(mp.resolved_at) - epoch(mp.first_trade)) >= 0
          AND (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 < 4.0
    """)
    con.execute("""
        CREATE OR REPLACE TABLE _trader_inplay_stats AS
        SELECT trader, count(*) AS n_inplay,
            count(*) FILTER (WHERE is_gambling = 0) AS n_non_gambling,
            count(*) FILTER (WHERE is_gambling = 1) AS n_gambling,
            avg(position_correct) AS hr_overall,
            avg(position_correct) FILTER (WHERE is_gambling = 0) AS hr_non_gambling,
            avg(position_correct) FILTER (WHERE is_gambling = 1) AS hr_gambling,
            median(volume) AS median_vol, sum(volume) AS total_vol,
            median(hold_hours) AS median_hold_hours,
            count(*) FILTER (WHERE position = 'YES') AS n_yes,
            count(*) FILTER (WHERE position = 'NO') AS n_no,
            count(*) FILTER (WHERE is_gambling = 1) * 1.0 / count(*) AS gambling_frac
        FROM _inplay_positions
        GROUP BY trader
        HAVING count(*) >= 50 AND avg(position_correct) >= 0.80 AND median(volume) >= 5.0
    """)
    n_elite = con.execute("SELECT count() FROM _trader_inplay_stats").fetchone()[0]
    log(f"Rebuilt: {n_elite:,} elite traders")

# ---------------------------------------------------------------------------
# Step 4b: Fixed Latency Analysis
# ---------------------------------------------------------------------------
log("\n[4b] Latency Analysis: Entry Gap (Fixed Query)...")

# Compute: for each elite YES position in a market,
# what was the time gap vs the last non-elite YES entry in the same market?
# Positive = elite entered AFTER others = market already had signal (copyable)
# Negative = elite entered BEFORE others = elite is the first signal

# Two-step: first compute last non-elite entry per market, then join to elite entries
con.execute("""
    CREATE OR REPLACE TABLE _last_non_elite_entry AS
    SELECT
        yed.condition_id,
        max(yed.first_trade) AS last_other_entry_time,
        count(DISTINCT yed.trader) AS n_other_traders
    FROM yes_entry_data yed
    WHERE yed.trader NOT IN (SELECT trader FROM _trader_inplay_stats)
    GROUP BY yed.condition_id
""")
log("Computed last non-elite entry per market.")

# Now get elite YES entries in in-play markets
con.execute("""
    CREATE OR REPLACE TABLE _elite_yes_inplay AS
    SELECT
        ip.trader,
        ip.condition_id,
        ip.first_trade AS elite_entry,
        ip.hold_hours,
        ip.volume AS elite_vol,
        ip.position_correct,
        ip.is_gambling
    FROM _inplay_positions ip
    WHERE ip.position = 'YES'
      AND ip.trader IN (SELECT trader FROM _trader_inplay_stats)
      AND ip.hold_hours < 4.0
""")
n_elite_yes = con.execute("SELECT count() FROM _elite_yes_inplay").fetchone()[0]
log(f"Elite YES in-play positions: {n_elite_yes:,}")

# Compute gap distribution
gap_dist = con.execute("""
    SELECT
        CASE
            WHEN lne.last_other_entry_time IS NULL THEN 'no_other_traders_in_mkt'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < -60 THEN 'elite_entered_1h+_before_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < -30 THEN 'elite_30-60min_before_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < -10 THEN 'elite_10-30min_before_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 0   THEN 'elite_0-10min_before_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 5   THEN 'within_5min_after_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 15  THEN '5-15min_after_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 60  THEN '15-60min_after_pool'
            ELSE 'over_1h_after_pool'
        END AS timing_bucket,
        count(*) AS n_positions,
        round(avg(ey.position_correct) * 100, 2) AS hr_pct,
        round(median((epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0), 1) AS median_gap_min
    FROM _elite_yes_inplay ey
    LEFT JOIN _last_non_elite_entry lne ON ey.condition_id = lne.condition_id
    GROUP BY 1
    ORDER BY n_positions DESC
""").fetchall()

log("\n--- Elite Trader Entry Timing vs Pool (YES positions) ---")
log("(positive gap = elite entered AFTER pool, negative = elite is first)")
log(f"{'Timing Bucket':<35} {'N_pos':>8} {'HR%':>7} {'Median_gap_min':>15}")
for row in gap_dist:
    log(f"{row[0]:<35} {row[1]:>8} {row[2]:>7} {str(row[3]):>15}")

# Median gap overall
median_gap = con.execute("""
    SELECT
        median((epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0) AS median_gap_min,
        avg((epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0) AS avg_gap_min,
        count(*) FILTER (
            WHERE (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 > 0
        ) * 100.0 / count(*) AS pct_after_pool
    FROM _elite_yes_inplay ey
    JOIN _last_non_elite_entry lne ON ey.condition_id = lne.condition_id
""").fetchone()

log(f"\nOverall latency stats (markets with other traders):")
log(f"  Median gap (elite - last_pool): {median_gap[0]:.1f} min")
log(f"  Average gap: {median_gap[1]:.1f} min")
log(f"  % positions where elite entered AFTER pool: {median_gap[2]:.1f}%")

# ---------------------------------------------------------------------------
# Step 4c: By is_gambling flag
# ---------------------------------------------------------------------------
log("\n[4c] Latency by gambling type...")
gamble_timing = con.execute("""
    SELECT
        ey.is_gambling,
        CASE
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 0 THEN 'before_pool'
            WHEN (epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0 < 15 THEN 'within_15min'
            ELSE 'over_15min_after'
        END AS timing,
        count(*) AS n,
        round(avg(ey.position_correct) * 100, 2) AS hr_pct,
        round(median((epoch(ey.elite_entry) - epoch(lne.last_other_entry_time)) / 60.0), 1) AS median_gap
    FROM _elite_yes_inplay ey
    JOIN _last_non_elite_entry lne ON ey.condition_id = lne.condition_id
    GROUP BY 1, 2
    ORDER BY 1, 2
""").fetchall()

log(f"\n{'Is_Gambling':<12} {'Timing':<20} {'N':>7} {'HR%':>7} {'Med_gap_min':>12}")
for row in gamble_timing:
    log(f"{str(row[0]):<12} {row[1]:<20} {row[2]:>7} {row[3]:>7} {str(row[4]):>12}")

# ---------------------------------------------------------------------------
# Step 5: Persistence Analysis
# ---------------------------------------------------------------------------
log("\n[5] Persistence Analysis: Train (pre-2025-07-01) vs Test (post-2025-07-01)...")

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

persistence = con.execute("""
    SELECT
        tr.trader,
        tr.n_positions AS train_n,
        round(tr.hr * 100, 2) AS train_hr_pct,
        round(tr.median_vol, 2) AS train_med_vol,
        te.n_positions AS test_n,
        round(te.hr * 100, 2) AS test_hr_pct,
        round(te.median_vol, 2) AS test_med_vol,
        round((te.hr - tr.hr) * 100, 2) AS hr_delta_pp
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train' AND tr.n_positions >= 20
    ORDER BY test_hr_pct DESC NULLS LAST, train_hr_pct DESC
    LIMIT 30
""").fetchall()

log("\n--- Train vs Test HR Persistence (elite traders, min 20 train positions) ---")
log(f"{'Trader':<14} {'Tr_N':>6} {'Tr_HR%':>8} {'Tr_Med':>7} {'Te_N':>6} {'Te_HR%':>8} {'Te_Med':>7} {'Delta_pp':>9}")
for row in persistence:
    log(f"{row[0][:12]:<14} {row[1]:>6} {row[2]:>8} {row[3]:>7} {str(row[4]):>6} {str(row[5]):>8} {str(row[6]):>7} {str(row[7]):>9}")

persist_summary = con.execute("""
    SELECT
        count(*) FILTER (WHERE te.hr >= 0.70) AS n_maintain_70pct,
        count(*) FILTER (WHERE te.hr >= 0.80) AS n_maintain_80pct,
        count(*) FILTER (WHERE te.hr >= 0.90) AS n_maintain_90pct,
        count(*) FILTER (WHERE te.hr >= 0.95) AS n_maintain_95pct,
        count(*) FILTER (WHERE te.hr IS NOT NULL) AS n_active_in_test,
        count(*) AS n_total_with_train
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train' AND tr.n_positions >= 20
""").fetchone()

log(f"\nPersistence summary (min 20 train positions):")
log(f"  Total with sufficient train data: {persist_summary[5]}")
log(f"  Active in test period: {persist_summary[4]}")
log(f"  Maintain >=70% HR in test: {persist_summary[0]}")
log(f"  Maintain >=80% HR in test: {persist_summary[1]}")
log(f"  Maintain >=90% HR in test: {persist_summary[2]}")
log(f"  Maintain >=95% HR in test: {persist_summary[3]}")

# HR distribution in test period
test_hr_dist = con.execute("""
    SELECT
        CASE
            WHEN te.hr IS NULL THEN 'not_active_in_test'
            WHEN te.hr >= 0.99 THEN '99-100%'
            WHEN te.hr >= 0.95 THEN '95-99%'
            WHEN te.hr >= 0.90 THEN '90-95%'
            WHEN te.hr >= 0.80 THEN '80-90%'
            WHEN te.hr >= 0.70 THEN '70-80%'
            ELSE '<70%'
        END AS test_hr_bucket,
        count(*) AS n_traders,
        round(avg(tr.hr) * 100, 2) AS avg_train_hr
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train' AND tr.n_positions >= 20
    GROUP BY 1
    ORDER BY n_traders DESC
""").fetchall()

log("\n--- Test HR Distribution (from train-active elite traders) ---")
log(f"{'Test_HR_Bucket':<22} {'N_traders':>10} {'Avg_Train_HR%':>14}")
for row in test_hr_dist:
    log(f"{row[0]:<22} {row[1]:>10} {row[2]:>14}")

# ---------------------------------------------------------------------------
# Step 6: Deep profile of 0x751a
# ---------------------------------------------------------------------------
log("\n[6] Profile of 0x751a (reference trader from data recon)...")

elite_0x751a = con.execute("""
    SELECT * FROM _trader_inplay_stats
    WHERE trader LIKE '0x751a%'
""").fetchall()
if elite_0x751a:
    log(f"0x751a IS in elite set: {elite_0x751a}")
else:
    # Check what HR they actually have
    stats_0x751a = con.execute("""
        SELECT
            count(*) AS n,
            avg(position_correct) AS hr,
            median(volume) AS med_vol,
            median(hold_hours) AS med_hold_h
        FROM _inplay_positions WHERE trader LIKE '0x751a%'
    """).fetchone()
    log(f"0x751a in-play stats: n={stats_0x751a[0]}, HR={stats_0x751a[1]}, med_vol={stats_0x751a[2]}, med_hold_h={stats_0x751a[3]}")

    # Maybe they're failing the median vol filter?
    inplay_0x = con.execute("""
        SELECT position, count(*), avg(position_correct) AS hr, median(volume) AS med_vol
        FROM _inplay_positions
        WHERE trader LIKE '0x751a%'
        GROUP BY position
    """).fetchall()
    log(f"0x751a in-play by position: {inplay_0x}")

    # Check all position data for 0x751a
    all_0x = con.execute("""
        SELECT count(*), avg(correct), median(volume)
        FROM maker_positions WHERE trader LIKE '0x751a%'
    """).fetchone()
    log(f"0x751a all maker_positions: n={all_0x[0]}, avg_correct={all_0x[1]}, med_vol={all_0x[2]}")

# ---------------------------------------------------------------------------
# Step 7: Tag-Specific HR for all elite traders
# ---------------------------------------------------------------------------
log("\n[7] Tag-specific HR for elite in-play traders (non-gambling)...")

tag_ng_hr = con.execute("""
    SELECT
        et.label AS tag,
        count(DISTINCT ip.trader) AS n_elite_traders,
        count(DISTINCT ip.condition_id) AS n_markets,
        count(*) AS n_positions,
        round(avg(ip.position_correct) * 100, 2) AS hr_pct,
        round(median(ip.volume), 2) AS median_vol,
        round(sum(ip.volume), 0) AS total_vol,
        count(*) FILTER (WHERE ip.first_trade >= TIMESTAMP '2025-07-01') AS n_test,
        round(avg(ip.position_correct) FILTER (WHERE ip.first_trade >= TIMESTAMP '2025-07-01') * 100, 2) AS test_hr_pct
    FROM _inplay_positions ip
    JOIN _trader_inplay_stats ts ON ip.trader = ts.trader
    JOIN markets m ON ip.condition_id = m.condition_id
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    WHERE ip.is_gambling = 0
    GROUP BY et.label
    HAVING count(*) >= 50
    ORDER BY test_hr_pct DESC NULLS LAST, hr_pct DESC
    LIMIT 25
""").fetchall()

log(f"\n{'Tag':<25} {'N_trdrs':>8} {'N_mkts':>7} {'N_pos':>7} {'All_HR%':>8} {'MedVol':>8} {'Total$':>12} {'N_test':>7} {'Test_HR%':>9}")
for row in tag_ng_hr:
    log(f"{str(row[0]):<25} {row[1]:>8} {row[2]:>7} {row[3]:>7} {row[4]:>8} {row[5]:>8} {row[6]:>12} {row[7]:>7} {str(row[8]):>9}")

# ---------------------------------------------------------------------------
# Step 8: Copyability Assessment
# ---------------------------------------------------------------------------
log("\n[8] Copyability Assessment...")

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
        -- Copyability score: test_HR * sqrt(test_n) * (1 - gambling_frac)
        CASE WHEN te.hr IS NOT NULL AND te.n_positions >= 5 THEN
            round(te.hr * sqrt(te.n_positions) * (1 - ts.gambling_frac) * 100, 2)
        ELSE 0 END AS copy_score
    FROM _trader_inplay_stats ts
    LEFT JOIN _trader_period_stats te ON ts.trader = te.trader AND te.period = 'test'
    ORDER BY copy_score DESC, test_hr DESC NULLS LAST
    LIMIT 20
""").fetchall()

log("\n--- Copyability Ranking (copy_score = test_HR * sqrt(test_N) * (1-gamble_frac)) ---")
log(f"{'Trader':<14} {'N_all':>5} {'HR_all%':>8} {'HR_NG%':>8} {'Gamb%':>6} {'Hold_min':>9} {'MedVol':>8} {'Total$':>10} {'Te_N':>5} {'Te_HR%':>8} {'CopyScore':>10}")
for row in copy_assess:
    log(f"{row[0][:12]:<14} {row[1]:>5} {row[2]:>8} {str(row[3]):>8} {row[4]:>6} {row[5]:>9} {row[6]:>8} {row[7]:>10} {str(row[8]):>5} {str(row[9]):>8} {row[10]:>10}")

# ---------------------------------------------------------------------------
# Step 9: Final Summary
# ---------------------------------------------------------------------------
log("\n" + "=" * 60)
log("FINAL SUMMARY")
log("=" * 60)

summary = con.execute("""
    SELECT
        count(*) AS total_elite,
        count(*) FILTER (WHERE gambling_frac >= 0.99) AS pure_gambling,
        count(*) FILTER (WHERE gambling_frac < 0.99) AS has_non_gambling,
        count(*) FILTER (WHERE n_non_gambling >= 5 AND hr_non_gambling >= 0.80) AS strong_ng_signal,
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

# Across-board persistence
persist_wide = con.execute("""
    SELECT
        count(*) FILTER (WHERE te.hr >= 0.70) AS maintain_70,
        count(*) FILTER (WHERE te.hr >= 0.80) AS maintain_80,
        count(*) FILTER (WHERE te.hr >= 0.90) AS maintain_90,
        count(*) FILTER (WHERE te.hr IS NOT NULL) AS active_test,
        count(*) AS total_train
    FROM _trader_period_stats tr
    LEFT JOIN _trader_period_stats te ON tr.trader = te.trader AND te.period = 'test'
    WHERE tr.period = 'train' AND tr.n_positions >= 10
""").fetchone()
log(f"\nOut-of-sample persistence (min 10 train positions):")
log(f"  Train-active traders: {persist_wide[4]}")
log(f"  Active in test: {persist_wide[3]} ({persist_wide[3]/persist_wide[4]*100:.1f}%)")
log(f"  Maintain >=70% HR: {persist_wide[0]}")
log(f"  Maintain >=80% HR: {persist_wide[1]}")
log(f"  Maintain >=90% HR: {persist_wide[2]}")

log(f"\nDone! Log at: {LOG}")
