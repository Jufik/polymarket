"""
Dynamic Entry Quality Research
===============================
Analyzes time-aware and hold-time-aware entry quality signals for trader scorecards.

Approaches:
1. Entry timing relative to market lifecycle (early vs late entrants)
2. Average payoff on correct trades (core metric)
3. Payoff asymmetry (risk/reward ratio)
4. Hold time x entry price interaction (2D grid)
5. Bargain hunting consistency
6. Tag-specific entry quality
"""

import sys
sys.path.insert(0, '/mnt/nvme/git/polymarket/polymarket')

import duckdb
from research.db import db
import json
from pathlib import Path

LOG = Path('/mnt/nvme/git/polymarket/polymarket/tmp/dynamic_entry.log')
LOG.parent.mkdir(exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open(LOG, 'a') as f:
        f.write(msg + '\n')

log("=== Dynamic Entry Quality Research ===")

conn = db().con

# ──────────────────────────────────────────────────────────────────────────────
# Base CTE: positions with entry price proxy
# ──────────────────────────────────────────────────────────────────────────────
BASE_SQL = """
CREATE OR REPLACE TABLE _ep_base AS
SELECT * FROM (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.net_usd,
        p.net_yes,
        p.net_no,
        p.first_trade,
        p.resolved_at,
        -- entry price proxy
        CASE
            WHEN p.position = 'YES' AND abs(p.net_yes) > 0.01 THEN abs(p.net_usd) / abs(p.net_yes)
            WHEN p.position = 'NO'  AND abs(p.net_no)  > 0.01 THEN abs(p.net_usd) / abs(p.net_no)
        END AS entry_price,
        date_diff('hour', p.first_trade, p.resolved_at) AS hold_hours,
        m.created_at AS market_created_at
    FROM maker_positions p
    JOIN markets m ON p.condition_id = m.condition_id
    WHERE p.resolved_at IS NOT NULL
      AND p.position IN ('YES', 'NO')
      AND abs(p.net_usd) > 0.01
      AND m.slug NOT LIKE '%updown%'
      AND m.slug NOT LIKE '%up-or-down%'
) sub
WHERE entry_price BETWEEN 0.01 AND 0.99
"""

log("Building base table (entry prices + hold times)...")
conn.execute(BASE_SQL)
n2 = conn.execute("SELECT count(*) FROM _ep_base").fetchone()[0]
log(f"After gambling exclusion: {n2:,} positions")

# Qualified traders (min 20 positions)
conn.execute("""
CREATE OR REPLACE TABLE _ep_traders AS
SELECT trader, count(*) AS n_positions
FROM _ep_base
WHERE entry_price IS NOT NULL
GROUP BY trader
HAVING n_positions >= 20
""")
n_traders = conn.execute("SELECT count(*) FROM _ep_traders").fetchone()[0]
log(f"Qualified traders (>=20 positions): {n_traders:,}")

# Filter base to qualified traders
conn.execute("""
CREATE OR REPLACE TABLE _ep AS
SELECT b.*,
    date_diff('hour', b.market_created_at, b.first_trade) AS entry_lag_hours
FROM _ep_base b
JOIN _ep_traders t ON b.trader = t.trader
WHERE b.entry_price IS NOT NULL
""")
n_ep = conn.execute("SELECT count(*) FROM _ep").fetchone()[0]
log(f"Final filtered positions: {n_ep:,}")

results = {}

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 2: Average payoff on correct trades — decile analysis
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 2: Average Payoff on Correct Trades ===")

payoff_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) AS avg_payoff_correct,
        avg(entry_price) AS avg_entry_price
    FROM _ep
    GROUP BY trader
    HAVING count(*) >= 20 AND avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) IS NOT NULL
),
deciled AS (
    SELECT *,
        ntile(10) OVER (ORDER BY avg_payoff_correct) AS payoff_decile
    FROM trader_stats
)
SELECT
    payoff_decile,
    count(*) AS n_traders,
    round(median(avg_payoff_correct), 4) AS med_avg_payoff,
    round(median(avg_entry_price), 4) AS med_entry_price,
    round(median(hit_rate), 4) AS med_hit_rate,
    round(median(n_positions), 0) AS med_positions
FROM deciled
GROUP BY payoff_decile
ORDER BY payoff_decile
"""

payoff_rows = conn.execute(payoff_sql).fetchall()
payoff_cols = ['payoff_decile', 'n_traders', 'med_avg_payoff', 'med_entry_price', 'med_hit_rate', 'med_positions']
results['avg_payoff_deciles'] = [dict(zip(payoff_cols, row)) for row in payoff_rows]
log("Avg payoff correct decile vs HR:")
for r in results['avg_payoff_deciles']:
    log(f"  D{r['payoff_decile']:2d}: payoff={r['med_avg_payoff']:.3f}, ep={r['med_entry_price']:.3f}, HR={r['med_hit_rate']:.3f}, n_traders={r['n_traders']}")

# IC: correlation avg_payoff_correct -> hit_rate
ic_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) AS avg_payoff_correct
    FROM _ep
    GROUP BY trader
    HAVING count(*) >= 20 AND avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) IS NOT NULL
)
SELECT corr(avg_payoff_correct, hit_rate) AS ic_payoff_hr
FROM trader_stats
"""
ic_val = conn.execute(ic_sql).fetchone()[0]
results['payoff_hr_ic'] = round(ic_val, 4) if ic_val else None
log(f"IC (avg_payoff_correct -> hit_rate): {results['payoff_hr_ic']}")

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 3: Payoff asymmetry (risk/reward ratio)
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 3: Payoff Asymmetry ===")

asym_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) AS avg_payoff_correct,
        avg(CASE WHEN correct = 0 THEN entry_price END) AS avg_loss_incorrect,
        -- risk/reward ratio: profit when right / loss when wrong
        CASE WHEN avg(CASE WHEN correct = 0 THEN entry_price END) > 0
             THEN avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END)
                  / avg(CASE WHEN correct = 0 THEN entry_price END)
        END AS rr_ratio
    FROM _ep
    GROUP BY trader
    HAVING count(*) >= 20
      AND avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) IS NOT NULL
      AND avg(CASE WHEN correct = 0 THEN entry_price END) IS NOT NULL
),
deciled AS (
    SELECT *,
        ntile(10) OVER (ORDER BY rr_ratio) AS rr_decile
    FROM trader_stats
    WHERE rr_ratio IS NOT NULL
)
SELECT
    rr_decile,
    count(*) AS n_traders,
    round(median(rr_ratio), 3) AS med_rr_ratio,
    round(median(avg_payoff_correct), 3) AS med_payoff_correct,
    round(median(avg_loss_incorrect), 3) AS med_loss_incorrect,
    round(median(hit_rate), 3) AS med_hit_rate
FROM deciled
GROUP BY rr_decile
ORDER BY rr_decile
"""

asym_rows = conn.execute(asym_sql).fetchall()
asym_cols = ['rr_decile','n_traders','med_rr_ratio','med_payoff_correct','med_loss_incorrect','med_hit_rate']
results['rr_ratio_deciles'] = [dict(zip(asym_cols, row)) for row in asym_rows]
log("Risk/Reward ratio decile vs HR:")
for r in results['rr_ratio_deciles']:
    log(f"  D{r['rr_decile']:2d}: rr={r['med_rr_ratio']:.3f}, payoff={r['med_payoff_correct']:.3f}, loss={r['med_loss_incorrect']:.3f}, HR={r['med_hit_rate']:.3f}")

# IC rr_ratio -> hit_rate
ic_rr_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        CASE WHEN avg(CASE WHEN correct = 0 THEN entry_price END) > 0
             THEN avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END)
                  / avg(CASE WHEN correct = 0 THEN entry_price END)
        END AS rr_ratio
    FROM _ep
    GROUP BY trader
    HAVING count(*) >= 20
)
SELECT corr(rr_ratio, hit_rate) AS ic_rr_hr
FROM trader_stats WHERE rr_ratio IS NOT NULL
"""
ic_rr = conn.execute(ic_rr_sql).fetchone()[0]
results['rr_hr_ic'] = round(ic_rr, 4) if ic_rr else None
log(f"IC (rr_ratio -> hit_rate): {results['rr_hr_ic']}")

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 4: 2D grid — price bucket x hold time bucket -> HR
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 4: 2D Grid — Price Bucket x Hold Time -> HR ===")

grid_sql = """
WITH bucketed AS (
    SELECT
        correct,
        CASE
            WHEN entry_price < 0.10 THEN 'A_<0.10'
            WHEN entry_price < 0.25 THEN 'B_0.10-0.25'
            WHEN entry_price < 0.40 THEN 'C_0.25-0.40'
            WHEN entry_price < 0.55 THEN 'D_0.40-0.55'
            WHEN entry_price < 0.70 THEN 'E_0.55-0.70'
            WHEN entry_price < 0.85 THEN 'F_0.70-0.85'
            ELSE 'G_0.85+'
        END AS price_bucket,
        CASE
            WHEN hold_hours < 1   THEN 'A_<1h'
            WHEN hold_hours < 24  THEN 'B_1-24h'
            WHEN hold_hours < 72  THEN 'C_1-3d'
            WHEN hold_hours < 168 THEN 'D_3-7d'
            WHEN hold_hours < 720 THEN 'E_7-30d'
            ELSE                       'F_30d+'
        END AS hold_bucket
    FROM _ep
    WHERE hold_hours >= 0
)
SELECT
    price_bucket,
    hold_bucket,
    count(*) AS n,
    round(avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END), 3) AS hit_rate
FROM bucketed
GROUP BY price_bucket, hold_bucket
ORDER BY price_bucket, hold_bucket
"""

grid_rows = conn.execute(grid_sql).fetchall()
grid_cols = ['price_bucket','hold_bucket','n','hit_rate']
results['price_hold_grid'] = [dict(zip(grid_cols, row)) for row in grid_rows]
log("Price x Hold 2D grid (n, HR):")
for r in results['price_hold_grid']:
    log(f"  {r['price_bucket']} x {r['hold_bucket']}: n={r['n']:,}, HR={r['hit_rate']:.3f}")

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 1: Entry timing relative to market lifecycle
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 1: Entry Timing Relative to Market Lifecycle ===")

timing_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        median(entry_lag_hours) AS med_entry_lag_hours,
        avg(entry_price) AS avg_entry_price,
        -- early = entered within first 24h of market life
        avg(CASE WHEN entry_lag_hours < 24 THEN 1.0 ELSE 0.0 END) AS frac_early_entry,
        -- very early = entered within first 1h
        avg(CASE WHEN entry_lag_hours < 1 THEN 1.0 ELSE 0.0 END) AS frac_very_early
    FROM _ep
    WHERE entry_lag_hours >= 0
    GROUP BY trader
    HAVING count(*) >= 20
),
deciled AS (
    SELECT *,
        ntile(10) OVER (ORDER BY med_entry_lag_hours) AS lag_decile
    FROM trader_stats
)
SELECT
    lag_decile,
    count(*) AS n_traders,
    round(median(med_entry_lag_hours), 1) AS med_lag_hours,
    round(median(hit_rate), 4) AS med_hit_rate,
    round(median(avg_entry_price), 4) AS med_entry_price,
    round(median(frac_early_entry), 3) AS med_frac_early
FROM deciled
GROUP BY lag_decile
ORDER BY lag_decile
"""

timing_rows = conn.execute(timing_sql).fetchall()
timing_cols = ['lag_decile','n_traders','med_lag_hours','med_hit_rate','med_entry_price','med_frac_early']
results['entry_timing_deciles'] = [dict(zip(timing_cols, row)) for row in timing_rows]
log("Entry lag decile vs HR:")
for r in results['entry_timing_deciles']:
    log(f"  D{r['lag_decile']:2d}: lag={r['med_lag_hours']:.0f}h, HR={r['med_hit_rate']:.3f}, ep={r['med_entry_price']:.3f}, frac_early={r['med_frac_early']:.3f}")

# IC entry_lag -> hit_rate
ic_lag_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        median(entry_lag_hours) AS med_entry_lag_hours
    FROM _ep WHERE entry_lag_hours >= 0
    GROUP BY trader
    HAVING count(*) >= 20
)
SELECT corr(med_entry_lag_hours, hit_rate) AS ic
FROM trader_stats
"""
ic_lag = conn.execute(ic_lag_sql).fetchone()[0]
results['entry_lag_hr_ic'] = round(ic_lag, 4) if ic_lag else None
log(f"IC (entry_lag_hours -> hit_rate): {results['entry_lag_hr_ic']}")

# Cross: early entry + cheap price + correct
cross_sql = """
SELECT
    CASE WHEN entry_lag_hours < 24 THEN 'early' ELSE 'late' END AS entry_timing,
    CASE WHEN entry_price < 0.40 THEN 'cheap' WHEN entry_price > 0.75 THEN 'expensive' ELSE 'mid' END AS price_bucket,
    count(*) AS n,
    round(avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END), 3) AS hit_rate
FROM _ep
WHERE entry_lag_hours >= 0
GROUP BY 1, 2
ORDER BY 1, 2
"""
cross_rows = conn.execute(cross_sql).fetchall()
cross_cols = ['entry_timing','price_bucket','n','hit_rate']
results['early_cheap_cross'] = [dict(zip(cross_cols, row)) for row in cross_rows]
log("Early/Late x Cheap/Mid/Expensive cross:")
for r in results['early_cheap_cross']:
    log(f"  {r['entry_timing']} x {r['price_bucket']}: n={r['n']:,}, HR={r['hit_rate']:.3f}")

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 5: Bargain hunting consistency
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 5: Bargain Hunting Consistency ===")

bargain_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN entry_price < 0.40 THEN 1.0 ELSE 0.0 END) AS cheap_entry_ratio,
        avg(CASE WHEN entry_price > 0.75 THEN 1.0 ELSE 0.0 END) AS expensive_entry_ratio
    FROM _ep
    GROUP BY trader
    HAVING count(*) >= 20
),
deciled_cheap AS (
    SELECT *,
        ntile(5) OVER (ORDER BY cheap_entry_ratio) AS cheap_quintile,
        ntile(5) OVER (ORDER BY expensive_entry_ratio) AS expensive_quintile
    FROM trader_stats
)
SELECT
    cheap_quintile,
    count(*) AS n_traders,
    round(median(cheap_entry_ratio), 3) AS med_cheap_ratio,
    round(median(expensive_entry_ratio), 3) AS med_exp_ratio,
    round(median(hit_rate), 4) AS med_hit_rate
FROM deciled_cheap
GROUP BY cheap_quintile
ORDER BY cheap_quintile
"""
bargain_rows = conn.execute(bargain_sql).fetchall()
bargain_cols = ['cheap_quintile','n_traders','med_cheap_ratio','med_exp_ratio','med_hit_rate']
results['bargain_consistency'] = [dict(zip(bargain_cols, row)) for row in bargain_rows]
log("Cheap entry ratio quintile vs HR:")
for r in results['bargain_consistency']:
    log(f"  Q{r['cheap_quintile']}: cheap_ratio={r['med_cheap_ratio']:.3f}, exp_ratio={r['med_exp_ratio']:.3f}, HR={r['med_hit_rate']:.3f}")

# IC cheap_entry_ratio -> HR
ic_cheap_sql = """
WITH trader_stats AS (
    SELECT trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN entry_price < 0.40 THEN 1.0 ELSE 0.0 END) AS cheap_entry_ratio
    FROM _ep GROUP BY trader HAVING count(*) >= 20
)
SELECT corr(cheap_entry_ratio, hit_rate) AS ic
FROM trader_stats
"""
ic_cheap = conn.execute(ic_cheap_sql).fetchone()[0]
results['cheap_ratio_hr_ic'] = round(ic_cheap, 4) if ic_cheap else None
log(f"IC (cheap_entry_ratio -> hit_rate): {results['cheap_ratio_hr_ic']}")

# IC expensive_entry_ratio -> HR (expect negative)
ic_exp_sql = """
WITH trader_stats AS (
    SELECT trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN entry_price > 0.75 THEN 1.0 ELSE 0.0 END) AS expensive_entry_ratio
    FROM _ep GROUP BY trader HAVING count(*) >= 20
)
SELECT corr(expensive_entry_ratio, hit_rate) AS ic
FROM trader_stats
"""
ic_exp = conn.execute(ic_exp_sql).fetchone()[0]
results['expensive_ratio_hr_ic'] = round(ic_exp, 4) if ic_exp else None
log(f"IC (expensive_entry_ratio -> hit_rate): {results['expensive_ratio_hr_ic']}")

# ──────────────────────────────────────────────────────────────────────────────
# APPROACH 6: Tag-specific entry quality
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Approach 6: Tag-Specific Entry Quality ===")

# Need to join with event_tags/markets for tag
tag_sql = """
WITH tagged AS (
    SELECT
        e.entry_price,
        e.correct,
        e.condition_id,
        m.event_id
    FROM _ep e
    JOIN markets m ON e.condition_id = m.condition_id
),
tag_joined AS (
    SELECT
        t.entry_price,
        t.correct,
        et.tag_id
    FROM tagged t
    JOIN event_tags et ON t.event_id = et.event_id
)
SELECT
    tag_id,
    count(*) AS n,
    round(avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END), 3) AS base_hr,
    round(avg(entry_price), 3) AS avg_entry_price,
    round(avg(CASE WHEN correct = 1 THEN entry_price END), 3) AS avg_ep_correct,
    round(avg(CASE WHEN correct = 0 THEN entry_price END), 3) AS avg_ep_incorrect,
    -- avg entry price for winners vs losers
    round(avg(CASE WHEN correct = 1 THEN entry_price END) -
          avg(CASE WHEN correct = 0 THEN entry_price END), 3) AS ep_correct_minus_incorrect
FROM tag_joined
GROUP BY tag_id
HAVING count(*) >= 1000
ORDER BY n DESC
LIMIT 20
"""
tag_rows = conn.execute(tag_sql).fetchall()
tag_cols = ['tag_id','n','base_hr','avg_entry_price','avg_ep_correct','avg_ep_incorrect','ep_correct_minus_incorrect']
results['tag_entry_quality'] = [dict(zip(tag_cols, row)) for row in tag_rows]
log("Top 20 tags: entry price correct vs incorrect:")
for r in results['tag_entry_quality']:
    log(f"  tag={r['tag_id']}: n={r['n']:,}, HR={r['base_hr']:.3f}, ep_correct={r['avg_ep_correct']:.3f}, ep_wrong={r['avg_ep_incorrect']:.3f}, diff={r['ep_correct_minus_incorrect']:.3f}")

# ──────────────────────────────────────────────────────────────────────────────
# COMPOSITE: best combined signal
# avg_payoff_correct + cheap_entry_ratio composite IC vs hit_rate
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Composite Signal: Payoff + Cheap Ratio ===")

composite_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) AS avg_payoff_correct,
        avg(CASE WHEN entry_price < 0.40 THEN 1.0 ELSE 0.0 END) AS cheap_entry_ratio,
        CASE WHEN avg(CASE WHEN correct = 0 THEN entry_price END) > 0
             THEN avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END)
                  / avg(CASE WHEN correct = 0 THEN entry_price END)
        END AS rr_ratio,
        median(entry_lag_hours) AS med_lag_hours
    FROM _ep
    WHERE entry_lag_hours >= 0
    GROUP BY trader
    HAVING count(*) >= 20
      AND avg(CASE WHEN correct = 1 THEN (1.0 - entry_price) END) IS NOT NULL
),
normed AS (
    SELECT *,
        -- z-score normalize (approx via rank)
        (avg_payoff_correct - avg(avg_payoff_correct) OVER ()) / nullif(stddev(avg_payoff_correct) OVER (), 0) AS z_payoff,
        (cheap_entry_ratio - avg(cheap_entry_ratio) OVER ()) / nullif(stddev(cheap_entry_ratio) OVER (), 0) AS z_cheap
    FROM trader_stats
    WHERE rr_ratio IS NOT NULL
),
composite AS (
    SELECT *,
        0.6 * z_payoff + 0.4 * z_cheap AS composite_score
    FROM normed
)
SELECT
    corr(avg_payoff_correct, hit_rate) AS ic_payoff,
    corr(cheap_entry_ratio, hit_rate) AS ic_cheap,
    corr(rr_ratio, hit_rate) AS ic_rr,
    corr(med_lag_hours, hit_rate) AS ic_lag,
    corr(composite_score, hit_rate) AS ic_composite
FROM composite
"""
comp_row = conn.execute(composite_sql).fetchone()
results['composite_ic'] = {
    'ic_payoff': round(comp_row[0], 4) if comp_row[0] else None,
    'ic_cheap_ratio': round(comp_row[1], 4) if comp_row[1] else None,
    'ic_rr_ratio': round(comp_row[2], 4) if comp_row[2] else None,
    'ic_lag': round(comp_row[3], 4) if comp_row[3] else None,
    'ic_composite': round(comp_row[4], 4) if comp_row[4] else None,
}
log("Composite IC summary:")
for k, v in results['composite_ic'].items():
    log(f"  {k}: {v}")

# ──────────────────────────────────────────────────────────────────────────────
# HOLD TIME + CHEAP ENTRY combined as trader-level filter
# ──────────────────────────────────────────────────────────────────────────────
log("\n=== Hold Time x Cheap Entry Filter ===")

htce_sql = """
WITH trader_stats AS (
    SELECT
        trader,
        count(*) AS n_positions,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        -- "quality trade" = cheap entry + held >= 24h + correct
        avg(CASE WHEN entry_price < 0.40 AND hold_hours >= 24 AND correct = 1 THEN 1.0 ELSE 0.0 END) AS qual_trade_rate,
        -- fraction of cheap long-hold positions
        avg(CASE WHEN entry_price < 0.40 AND hold_hours >= 24 THEN 1.0 ELSE 0.0 END) AS cheap_longhold_frac
    FROM _ep
    WHERE hold_hours >= 0
    GROUP BY trader
    HAVING count(*) >= 20
),
deciled AS (
    SELECT *,
        ntile(10) OVER (ORDER BY qual_trade_rate) AS qual_decile
    FROM trader_stats
)
SELECT
    qual_decile,
    count(*) AS n_traders,
    round(median(qual_trade_rate), 4) AS med_qual_rate,
    round(median(cheap_longhold_frac), 3) AS med_cheap_lh_frac,
    round(median(hit_rate), 4) AS med_hit_rate
FROM deciled
GROUP BY qual_decile
ORDER BY qual_decile
"""
htce_rows = conn.execute(htce_sql).fetchall()
htce_cols = ['qual_decile','n_traders','med_qual_rate','med_cheap_lh_frac','med_hit_rate']
results['hold_cheap_quality'] = [dict(zip(htce_cols, row)) for row in htce_rows]
log("Quality trade rate (cheap+held>=24h+correct) decile vs HR:")
for r in results['hold_cheap_quality']:
    log(f"  D{r['qual_decile']:2d}: qual_rate={r['med_qual_rate']:.4f}, cheap_lh={r['med_cheap_lh_frac']:.3f}, HR={r['med_hit_rate']:.3f}")

ic_qual_sql = """
WITH trader_stats AS (
    SELECT trader,
        avg(CASE WHEN correct = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
        avg(CASE WHEN entry_price < 0.40 AND hold_hours >= 24 AND correct = 1 THEN 1.0 ELSE 0.0 END) AS qual_trade_rate
    FROM _ep WHERE hold_hours >= 0
    GROUP BY trader HAVING count(*) >= 20
)
SELECT corr(qual_trade_rate, hit_rate) AS ic
FROM trader_stats
"""
ic_qual = conn.execute(ic_qual_sql).fetchone()[0]
results['qual_trade_hr_ic'] = round(ic_qual, 4) if ic_qual else None
log(f"IC (qual_trade_rate -> hit_rate): {results['qual_trade_hr_ic']}")

# ──────────────────────────────────────────────────────────────────────────────
# Save results
# ──────────────────────────────────────────────────────────────────────────────
out_path = Path('/mnt/nvme/git/polymarket/polymarket/research/hypotheses/entry-price-quality/discovery/dynamic_entry_results.json')
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2)
log(f"\nResults saved to {out_path}")
log("=== DONE ===")
