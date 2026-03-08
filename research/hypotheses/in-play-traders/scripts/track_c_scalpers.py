"""
Track C: Scalper/Hedger Alpha Analysis
======================================
Hypothesis: Traders who BUY then SELL within a short window (scalpers/hedgers)
may contain directional alpha. Their entry direction (the initial BUY) may
predict outcome because:
1. They have information about short-term price movement
2. They're sophisticated enough to hedge/exit — indicating higher skill
3. The buy-sell spread generates risk-free profit if timed right

Key SELL-semantics caveat:
- SELL YES = bearish (exiting YES or split-entry into NO)
- SELL NO = bullish (exiting NO or split-entry into YES)
- We focus on BUY entry direction, and use the subsequent SELL as a proxy for
  "scalper" classification. We do NOT use the SELL as a signal direction.

markets_resolved schema:
- condition_id, resolution_value, winner_outcome, resolved_at, asset_id, outcome, token_won
- 2 rows per market (YES token and NO token)
- yes_won derived as: token_won=1 WHERE outcome='YES'

token_market_map: asset_id -> condition_id, outcome (YES/NO), winner (0/1)

Sample: Full 2025 (Jan-Dec trades)

Outputs to: research/hypotheses/in-play-traders/discovery/track_c_results.md
"""

import sys
import os
import logging
from datetime import datetime

# Setup logging
log_path = "/mnt/nvme/git/polymarket/polymarket/tmp/track_c_scalpers.log"
os.makedirs(os.path.dirname(log_path), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")
from research.db import db as get_db

log.info("Connecting to DuckDB...")
d = get_db()
con = d.con

log.info("DuckDB connected.")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0: Build yes_won lookup table (condition_id -> yes_won)
# markets_resolved has 2 rows per condition: one for YES token, one for NO token
# yes_won = 1 if the YES token won (token_won=1 WHERE outcome='YES')
# ─────────────────────────────────────────────────────────────────────────────
log.info("Building yes_won lookup from markets_resolved...")
con.execute("""
CREATE OR REPLACE TABLE _tc_market_resolution AS
SELECT DISTINCT
    condition_id,
    MAX(CASE WHEN outcome = 'YES' THEN token_won ELSE NULL END) AS yes_won,
    MAX(resolved_at) AS resolved_at
FROM markets_resolved
WHERE token_won IS NOT NULL
GROUP BY condition_id
HAVING yes_won IS NOT NULL
""")
cnt = con.execute("SELECT COUNT(*) FROM _tc_market_resolution").fetchone()[0]
log.info(f"Resolved markets: {cnt:,}")

# Verify
sample = con.execute("SELECT * FROM _tc_market_resolution LIMIT 5").fetchdf()
log.info(f"Resolution sample:\n{sample}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Identify scalpers from 2025 trades
# Scalper = maker who BUYs then SELLs same (condition_id, asset_id) within 24h
# side=1 = BUY, side=2 = SELL
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 1: Building buy and sell position tables (2025)")
log.info("=" * 60)

PARQUET_BASE = "/mnt/nvme/git/polymarket/polymarket/data/research/trades"

log.info("Building BUY table from 2025 trades...")
con.execute(f"""
CREATE OR REPLACE TABLE _tc_buys AS
SELECT
    maker,
    condition_id,
    asset_id,
    MIN(timestamp) AS first_buy_ts,
    SUM(CASE WHEN side = 1 THEN amount_usd ELSE 0 END) AS total_buy_usd,
    COUNT(CASE WHEN side = 1 THEN 1 END) AS n_buys,
    SUM(CASE WHEN side = 1 THEN price * amount_usd ELSE 0 END) /
        NULLIF(SUM(CASE WHEN side = 1 THEN amount_usd ELSE 0 END), 0) AS avg_buy_price
FROM read_parquet(['{PARQUET_BASE}/trades_202501.parquet',
                   '{PARQUET_BASE}/trades_202502.parquet',
                   '{PARQUET_BASE}/trades_202503.parquet',
                   '{PARQUET_BASE}/trades_202504.parquet',
                   '{PARQUET_BASE}/trades_202505.parquet',
                   '{PARQUET_BASE}/trades_202506.parquet',
                   '{PARQUET_BASE}/trades_202507.parquet',
                   '{PARQUET_BASE}/trades_202508.parquet',
                   '{PARQUET_BASE}/trades_202509.parquet',
                   '{PARQUET_BASE}/trades_202510.parquet',
                   '{PARQUET_BASE}/trades_202511.parquet',
                   '{PARQUET_BASE}/trades_202512.parquet'])
WHERE side = 1
  AND maker != ''
  AND maker != '0x0000000000000000000000000000000000000000'
  AND amount_usd >= 0.5
GROUP BY maker, condition_id, asset_id
""")
cnt_buys = con.execute("SELECT COUNT(*) FROM _tc_buys").fetchone()[0]
log.info(f"BUY positions: {cnt_buys:,}")

log.info("Building SELL table from 2025 trades...")
con.execute(f"""
CREATE OR REPLACE TABLE _tc_sells AS
SELECT
    maker,
    condition_id,
    asset_id,
    MIN(timestamp) AS first_sell_ts,
    SUM(CASE WHEN side = 2 THEN amount_usd ELSE 0 END) AS total_sell_usd,
    COUNT(CASE WHEN side = 2 THEN 1 END) AS n_sells,
    SUM(CASE WHEN side = 2 THEN price * amount_usd ELSE 0 END) /
        NULLIF(SUM(CASE WHEN side = 2 THEN amount_usd ELSE 0 END), 0) AS avg_sell_price
FROM read_parquet(['{PARQUET_BASE}/trades_202501.parquet',
                   '{PARQUET_BASE}/trades_202502.parquet',
                   '{PARQUET_BASE}/trades_202503.parquet',
                   '{PARQUET_BASE}/trades_202504.parquet',
                   '{PARQUET_BASE}/trades_202505.parquet',
                   '{PARQUET_BASE}/trades_202506.parquet',
                   '{PARQUET_BASE}/trades_202507.parquet',
                   '{PARQUET_BASE}/trades_202508.parquet',
                   '{PARQUET_BASE}/trades_202509.parquet',
                   '{PARQUET_BASE}/trades_202510.parquet',
                   '{PARQUET_BASE}/trades_202511.parquet',
                   '{PARQUET_BASE}/trades_202512.parquet'])
WHERE side = 2
  AND maker != ''
  AND maker != '0x0000000000000000000000000000000000000000'
GROUP BY maker, condition_id, asset_id
""")
cnt_sells = con.execute("SELECT COUNT(*) FROM _tc_sells").fetchone()[0]
log.info(f"SELL positions: {cnt_sells:,}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Find scalpers (BUY then SELL within 24h)
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 2: Join BUY+SELL to find scalpers")
log.info("=" * 60)

log.info("Joining to find scalp positions...")
con.execute("""
CREATE OR REPLACE TABLE _tc_scalpers_raw AS
SELECT
    b.maker,
    b.condition_id,
    b.asset_id,
    b.first_buy_ts,
    b.avg_buy_price,
    b.total_buy_usd,
    b.n_buys,
    s.first_sell_ts,
    s.avg_sell_price,
    s.total_sell_usd,
    s.n_sells,
    -- Spread: sell_price - buy_price (positive = profit)
    (s.avg_sell_price - b.avg_buy_price) AS spread,
    -- Time from first buy to first sell (hours)
    CAST(date_diff('minute', b.first_buy_ts, s.first_sell_ts) AS DOUBLE) / 60.0 AS scalp_hours,
    -- Profit estimate (tokens approximated from USD/price)
    (s.avg_sell_price - b.avg_buy_price) * LEAST(
        b.total_buy_usd / NULLIF(b.avg_buy_price, 0),
        s.total_sell_usd / NULLIF(s.avg_sell_price, 0)
    ) AS est_scalp_profit
FROM _tc_buys b
INNER JOIN _tc_sells s
    ON b.maker = s.maker
    AND b.condition_id = s.condition_id
    AND b.asset_id = s.asset_id
    AND s.first_sell_ts > b.first_buy_ts
    AND CAST(date_diff('minute', b.first_buy_ts, s.first_sell_ts) AS DOUBLE) <= 24 * 60
""")
cnt_scalpers = con.execute("SELECT COUNT(*) FROM _tc_scalpers_raw").fetchone()[0]
log.info(f"Scalp positions (BUY then SELL within 24h): {cnt_scalpers:,}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Scalper characteristics
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 3: Scalper characteristics")
log.info("=" * 60)

pop = con.execute("""
SELECT
    COUNT(*) AS n_scalp_positions,
    COUNT(DISTINCT maker) AS n_unique_scalpers,
    COUNT(DISTINCT condition_id) AS n_unique_markets,
    ROUND(MEDIAN(scalp_hours), 2) AS median_scalp_hours,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct,
    ROUND(AVG(spread) * 100, 2) AS avg_spread_pct,
    ROUND(SUM(total_sell_usd) / 1e6, 2) AS total_sell_vol_usd_M,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_buy_size,
    ROUND(MEDIAN(est_scalp_profit), 4) AS median_scalp_profit,
    ROUND(SUM(est_scalp_profit) / 1e6, 4) AS total_estimated_profit_M
FROM _tc_scalpers_raw
WHERE scalp_hours >= 0
""").fetchdf()
log.info(f"Population:\n{pop.to_string()}")

spread_dist = con.execute("""
SELECT
    CASE
        WHEN spread < -0.10 THEN 'a. negative large (<-10%)'
        WHEN spread < -0.02 THEN 'b. negative medium (-10% to -2%)'
        WHEN spread < 0.00  THEN 'c. negative small (-2% to 0%)'
        WHEN spread < 0.02  THEN 'd. near zero (0% to 2%)'
        WHEN spread < 0.05  THEN 'e. small profit (2-5%)'
        WHEN spread < 0.10  THEN 'f. medium profit (5-10%)'
        ELSE                     'g. large profit (>10%)'
    END AS spread_bucket,
    COUNT(*) AS n,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
FROM _tc_scalpers_raw
WHERE scalp_hours >= 0
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Spread distribution:\n{spread_dist.to_string()}")

time_dist = con.execute("""
SELECT
    CASE
        WHEN scalp_hours < 0.25 THEN 'a. <15min'
        WHEN scalp_hours < 1.0  THEN 'b. 15min-1h'
        WHEN scalp_hours < 4.0  THEN 'c. 1-4h'
        WHEN scalp_hours < 8.0  THEN 'd. 4-8h'
        ELSE                         'e. 8-24h'
    END AS time_bucket,
    COUNT(*) AS n,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol
FROM _tc_scalpers_raw
WHERE scalp_hours >= 0
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Time distribution:\n{time_dist.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Join with resolution outcomes
# Use token_market_map to get YES/NO direction of the bought asset
# Use _tc_market_resolution for yes_won and resolved_at
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 4: Join with resolution outcomes")
log.info("=" * 60)

con.execute("""
CREATE OR REPLACE TABLE _tc_scalpers_resolved AS
SELECT
    sc.maker,
    sc.condition_id,
    sc.asset_id,
    sc.first_buy_ts,
    sc.avg_buy_price,
    sc.total_buy_usd,
    sc.n_buys,
    sc.scalp_hours,
    sc.spread,
    sc.est_scalp_profit,
    tm.outcome AS bought_side,   -- YES or NO
    mr.yes_won,
    mr.resolved_at,
    -- Did the BUY direction win?
    CASE
        WHEN tm.outcome = 'YES' AND mr.yes_won = 1 THEN 1
        WHEN tm.outcome = 'NO'  AND mr.yes_won = 0 THEN 1
        ELSE 0
    END AS buy_direction_won,
    -- Hold time from buy to resolution (hours)
    CAST(date_diff('minute', sc.first_buy_ts, mr.resolved_at) AS DOUBLE) / 60.0 AS hold_to_resolution_hours
FROM _tc_scalpers_raw sc
JOIN token_market_map tm ON sc.asset_id = tm.asset_id
JOIN _tc_market_resolution mr ON sc.condition_id = mr.condition_id
WHERE sc.scalp_hours >= 0
  AND mr.yes_won IS NOT NULL
  AND tm.outcome IS NOT NULL
""")
cnt_resolved = con.execute("SELECT COUNT(*) FROM _tc_scalpers_resolved").fetchone()[0]
log.info(f"Resolved scalp positions: {cnt_resolved:,}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: Scalper hit rate at BUY entry
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 5: Scalper hit rate at BUY entry")
log.info("=" * 60)

scalper_hr_overall = con.execute("""
SELECT
    COUNT(*) AS n_positions,
    COUNT(DISTINCT maker) AS n_scalpers,
    COUNT(DISTINCT condition_id) AS n_markets,
    ROUND(AVG(buy_direction_won) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_buy_vol,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct
FROM _tc_scalpers_resolved
""").fetchdf()
log.info(f"Overall scalper HR:\n{scalper_hr_overall.to_string()}")

hr_by_side = con.execute("""
SELECT
    bought_side,
    COUNT(*) AS n,
    ROUND(AVG(buy_direction_won) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol
FROM _tc_scalpers_resolved
GROUP BY bought_side
ORDER BY bought_side
""").fetchdf()
log.info(f"HR by bought side:\n{hr_by_side.to_string()}")

hr_by_time = con.execute("""
SELECT
    CASE
        WHEN scalp_hours < 0.25 THEN 'a. <15min'
        WHEN scalp_hours < 1.0  THEN 'b. 15min-1h'
        WHEN scalp_hours < 4.0  THEN 'c. 1-4h'
        WHEN scalp_hours < 8.0  THEN 'd. 4-8h'
        ELSE                         'e. 8-24h'
    END AS scalp_time,
    COUNT(*) AS n,
    ROUND(AVG(buy_direction_won) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol
FROM _tc_scalpers_resolved
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"HR by scalp time:\n{hr_by_time.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: Serial scalper profiles
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 6: Serial scalper profiles (>=10 scalps)")
log.info("=" * 60)

serial_scalpers = con.execute("""
SELECT
    maker,
    COUNT(*) AS n_scalps,
    COUNT(DISTINCT condition_id) AS n_markets,
    ROUND(AVG(buy_direction_won) * 100, 2) AS entry_hr_pct,
    ROUND(MEDIAN(scalp_hours), 2) AS median_scalp_hours,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct,
    ROUND(SUM(est_scalp_profit), 2) AS total_est_profit,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol,
    ROUND(SUM(total_buy_usd), 0) AS total_vol
FROM _tc_scalpers_resolved
GROUP BY maker
HAVING COUNT(*) >= 10
ORDER BY n_scalps DESC
LIMIT 30
""").fetchdf()
log.info(f"Top 30 serial scalpers:\n{serial_scalpers.to_string()}")

scalper_dist = con.execute("""
WITH trader_stats AS (
    SELECT
        maker,
        COUNT(*) AS n_scalps,
        AVG(buy_direction_won) AS entry_hr,
        SUM(total_buy_usd) AS total_vol
    FROM _tc_scalpers_resolved
    GROUP BY maker
)
SELECT
    CASE
        WHEN n_scalps >= 100 THEN 'e. 100+ scalps'
        WHEN n_scalps >= 50  THEN 'd. 50-99 scalps'
        WHEN n_scalps >= 20  THEN 'c. 20-49 scalps'
        WHEN n_scalps >= 10  THEN 'b. 10-19 scalps'
        ELSE                      'a. 1-9 scalps'
    END AS activity_tier,
    COUNT(*) AS n_traders,
    SUM(n_scalps) AS total_scalps,
    ROUND(AVG(entry_hr) * 100, 2) AS avg_hr_pct,
    ROUND(SUM(total_vol) / 1e6, 2) AS total_vol_M
FROM trader_stats
GROUP BY 1
ORDER BY 1 DESC
""").fetchdf()
log.info(f"Scalper activity distribution:\n{scalper_dist.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7: Scalper vs non-scalper HR (using maker_positions)
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 7: Scalper vs non-scalper HR (maker_positions baseline)")
log.info("=" * 60)

# Unique (maker, condition_id) pairs that are scalpers in 2025
con.execute("""
CREATE OR REPLACE TABLE _tc_scalper_ids AS
SELECT DISTINCT maker, condition_id
FROM _tc_scalpers_raw
WHERE scalp_hours >= 0
""")

# Use maker_positions correct field for HR comparison
scalper_vs_holder = con.execute("""
WITH mp_labeled AS (
    SELECT
        mp.trader,
        mp.condition_id,
        mp.position,
        mp.correct,
        mp.volume,
        mp.yes_won,
        CASE WHEN sc.maker IS NOT NULL THEN 1 ELSE 0 END AS is_scalper
    FROM maker_positions mp
    LEFT JOIN _tc_scalper_ids sc
        ON mp.trader = sc.maker AND mp.condition_id = sc.condition_id
    WHERE mp.volume >= 0.5
      AND mp.correct IS NOT NULL
)
SELECT
    is_scalper,
    position,
    COUNT(*) AS n_positions,
    COUNT(DISTINCT trader) AS n_traders,
    COUNT(DISTINCT condition_id) AS n_markets,
    ROUND(AVG(correct) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(volume), 2) AS median_vol_usd
FROM mp_labeled
GROUP BY is_scalper, position
ORDER BY is_scalper, position
""").fetchdf()
log.info(f"Scalper vs non-scalper HR:\n{scalper_vs_holder.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8: Scalp profitability
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 8: Profitability analysis")
log.info("=" * 60)

profitability = con.execute("""
SELECT
    CASE
        WHEN spread > 0.05  THEN 'a. large profit (>5%)'
        WHEN spread > 0.01  THEN 'b. small profit (1-5%)'
        WHEN spread > 0.00  THEN 'c. near breakeven (0-1%)'
        WHEN spread > -0.02 THEN 'd. small loss (-2% to 0%)'
        ELSE                     'e. loss (>2%)'
    END AS profit_bucket,
    COUNT(*) AS n,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol,
    ROUND(MEDIAN(est_scalp_profit), 4) AS median_profit,
    ROUND(SUM(est_scalp_profit), 0) AS total_profit
FROM _tc_scalpers_resolved
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Profitability:\n{profitability.to_string()}")

monthly_vol = con.execute("""
SELECT
    DATE_TRUNC('month', first_buy_ts)::DATE AS month,
    COUNT(*) AS n_scalps,
    COUNT(DISTINCT maker) AS n_scalpers,
    ROUND(SUM(total_buy_usd) / 1e6, 2) AS buy_vol_M,
    ROUND(SUM(est_scalp_profit) / 1000, 1) AS est_profit_K,
    ROUND(AVG(buy_direction_won) * 100, 2) AS entry_hr_pct
FROM _tc_scalpers_resolved
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Monthly volume:\n{monthly_vol.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9: Timing — when do scalpers enter relative to resolution?
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 9: Timing vs resolution")
log.info("=" * 60)

timing = con.execute("""
SELECT
    CASE
        WHEN hold_to_resolution_hours < 1   THEN 'a. <1h to resolution'
        WHEN hold_to_resolution_hours < 4   THEN 'b. 1-4h to resolution'
        WHEN hold_to_resolution_hours < 24  THEN 'c. 4-24h to resolution'
        WHEN hold_to_resolution_hours < 72  THEN 'd. 1-3d to resolution'
        ELSE                                     'e. >3d to resolution'
    END AS time_to_resolution,
    COUNT(*) AS n,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct,
    ROUND(AVG(buy_direction_won) * 100, 2) AS entry_hr_pct,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol
FROM _tc_scalpers_resolved
WHERE hold_to_resolution_hours >= 0
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Timing vs resolution:\n{timing.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 10: Tag breakdown
# trader_volumes has tag labels per trader — use as proxy
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 10: Tag breakdown")
log.info("=" * 60)

tv_cols = con.execute("DESCRIBE trader_volumes").fetchall()
log.info(f"trader_volumes columns: {[c[0] for c in tv_cols]}")

# Try joining scalpers with markets -> event_tags for tag lookup
try:
    # First check events columns
    events_cols = [c[0] for c in con.execute("DESCRIBE events").fetchall()]
    log.info(f"events columns: {events_cols}")
    et_cols = [c[0] for c in con.execute("DESCRIBE event_tags").fetchall()]
    log.info(f"event_tags columns: {et_cols}")

    tag_breakdown = con.execute("""
        SELECT
            et.tag,
            COUNT(*) AS n_scalps,
            COUNT(DISTINCT sc.condition_id) AS n_markets,
            COUNT(DISTINCT sc.maker) AS n_scalpers,
            ROUND(AVG(sc.buy_direction_won) * 100, 2) AS entry_hr_pct,
            ROUND(MEDIAN(sc.scalp_hours), 2) AS median_scalp_hours,
            ROUND(MEDIAN(sc.spread) * 100, 2) AS median_spread_pct,
            ROUND(MEDIAN(sc.total_buy_usd), 2) AS median_vol,
            ROUND(SUM(sc.total_buy_usd) / 1e6, 2) AS total_vol_M,
            ROUND(SUM(sc.est_scalp_profit) / 1000, 1) AS est_profit_K
        FROM _tc_scalpers_resolved sc
        JOIN markets m ON sc.condition_id = m.condition_id
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.tag IS NOT NULL
        GROUP BY et.tag
        HAVING n_scalps >= 100
        ORDER BY n_scalps DESC
        LIMIT 25
    """).fetchdf()
    log.info(f"Tag breakdown:\n{tag_breakdown.to_string()}")
except Exception as e:
    log.warning(f"Tag breakdown failed: {e}")
    tag_breakdown = None

# ─────────────────────────────────────────────────────────────────────────────
# STEP 11: Copyability assessment (price-gated)
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 11: Copyability (price gate 0.10-0.80)")
log.info("=" * 60)

copyability = con.execute("""
SELECT
    CASE
        WHEN avg_buy_price BETWEEN 0.10 AND 0.80 THEN 'b. in_gate (0.10-0.80)'
        WHEN avg_buy_price < 0.10               THEN 'a. below_gate (<0.10 long-shot)'
        ELSE                                         'c. above_gate (>0.80 near-resolution)'
    END AS price_gate,
    bought_side,
    COUNT(*) AS n,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct_all,
    ROUND(AVG(buy_direction_won) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol,
    ROUND(MEDIAN(spread) * 100, 2) AS median_spread_pct
FROM _tc_scalpers_resolved
GROUP BY 1, 2
ORDER BY 1, 2
""").fetchdf()
log.info(f"Copyability by price gate:\n{copyability.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 12: Top scalpers by entry HR (the "smart scalper" pool)
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 12: High-edge scalper pool (>=10 scalps, HR >= 55%)")
log.info("=" * 60)

top_by_hr = con.execute("""
SELECT
    maker,
    COUNT(*) AS n_scalps,
    COUNT(DISTINCT condition_id) AS n_markets,
    ROUND(AVG(buy_direction_won) * 100, 2) AS entry_hr_pct,
    ROUND(MEDIAN(scalp_hours), 2) AS med_scalp_h,
    ROUND(MEDIAN(spread) * 100, 2) AS med_spread_pct,
    ROUND(SUM(est_scalp_profit), 2) AS total_profit,
    ROUND(MEDIAN(total_buy_usd), 2) AS median_vol
FROM _tc_scalpers_resolved
GROUP BY maker
HAVING COUNT(*) >= 10
ORDER BY entry_hr_pct DESC
LIMIT 50
""").fetchdf()
log.info(f"Top 50 scalpers by entry HR:\n{top_by_hr.to_string()}")

# Pool stats for copyable scalpers (HR >= 55%, >=10 scalps, price-gated)
pool_stats = con.execute("""
WITH high_edge AS (
    SELECT maker
    FROM _tc_scalpers_resolved
    GROUP BY maker
    HAVING COUNT(*) >= 10
       AND AVG(buy_direction_won) >= 0.55
)
SELECT
    bought_side,
    COUNT(*) AS n_signals,
    COUNT(DISTINCT sc.maker) AS n_scalpers,
    COUNT(DISTINCT sc.condition_id) AS n_markets,
    ROUND(AVG(sc.buy_direction_won) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(sc.total_buy_usd), 2) AS median_vol,
    ROUND(MEDIAN(sc.scalp_hours), 2) AS median_scalp_h
FROM _tc_scalpers_resolved sc
JOIN high_edge he ON sc.maker = he.maker
WHERE sc.avg_buy_price BETWEEN 0.10 AND 0.80
GROUP BY bought_side
ORDER BY bought_side
""").fetchdf()
log.info(f"High-edge scalper pool (HR>=55%, >=10 scalps, price-gated):\n{pool_stats.to_string()}")

# Profit quintile vs entry HR (do good scalpers also have edge at entry?)
decile_analysis = con.execute("""
WITH trader_stats AS (
    SELECT
        maker,
        COUNT(*) AS n_scalps,
        AVG(buy_direction_won) AS entry_hr,
        MEDIAN(spread) AS med_spread,
        SUM(est_scalp_profit) AS total_profit,
        MEDIAN(total_buy_usd) AS med_vol
    FROM _tc_scalpers_resolved
    GROUP BY maker
    HAVING COUNT(*) >= 5
),
ranked AS (
    SELECT *,
        NTILE(5) OVER (ORDER BY total_profit) AS profit_quintile,
        NTILE(5) OVER (ORDER BY med_spread) AS spread_quintile
    FROM trader_stats
)
SELECT
    profit_quintile,
    COUNT(*) AS n_traders,
    ROUND(AVG(entry_hr) * 100, 2) AS avg_entry_hr_pct,
    ROUND(AVG(med_spread) * 100, 2) AS avg_spread_pct,
    ROUND(AVG(n_scalps), 1) AS avg_n_scalps,
    ROUND(AVG(med_vol), 2) AS avg_med_vol,
    ROUND(SUM(total_profit), 0) AS total_profit
FROM ranked
GROUP BY profit_quintile
ORDER BY profit_quintile
""").fetchdf()
log.info(f"Profit quintile vs entry HR:\n{decile_analysis.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 13: Scalper edge persistence on HELD positions
# Do traders who scalp also do better on their non-scalped positions?
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 13: Edge persistence on held positions")
log.info("=" * 60)

held_analysis = con.execute("""
WITH scalper_traders AS (
    SELECT DISTINCT maker
    FROM _tc_scalpers_raw
    WHERE scalp_hours >= 0
    GROUP BY maker
    HAVING COUNT(*) >= 5
),
scalped_markets AS (
    SELECT DISTINCT maker, condition_id
    FROM _tc_scalpers_raw
    WHERE scalp_hours >= 0
),
labeled_positions AS (
    SELECT
        mp.trader,
        mp.condition_id,
        mp.position,
        mp.correct,
        mp.volume,
        CASE
            WHEN sm.maker IS NOT NULL THEN 'scalped'
            ELSE 'held'
        END AS position_type
    FROM maker_positions mp
    JOIN scalper_traders st ON mp.trader = st.maker
    LEFT JOIN scalped_markets sm ON mp.trader = sm.maker AND mp.condition_id = sm.condition_id
    WHERE mp.volume >= 0.5
      AND mp.correct IS NOT NULL
)
SELECT
    position_type,
    position,
    COUNT(*) AS n,
    COUNT(DISTINCT trader) AS n_traders,
    ROUND(AVG(correct) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(volume), 2) AS median_vol
FROM labeled_positions
GROUP BY position_type, position
ORDER BY position_type, position
""").fetchdf()
log.info(f"Held vs scalped positions:\n{held_analysis.to_string()}")

# Non-scalper baseline
non_scalper_baseline = con.execute("""
WITH scalper_traders AS (
    SELECT DISTINCT maker
    FROM _tc_scalpers_raw
    GROUP BY maker
    HAVING COUNT(*) >= 5
)
SELECT
    position,
    COUNT(*) AS n,
    COUNT(DISTINCT trader) AS n_traders,
    ROUND(AVG(correct) * 100, 2) AS hr_pct,
    ROUND(MEDIAN(volume), 2) AS median_vol
FROM maker_positions
WHERE trader NOT IN (SELECT maker FROM scalper_traders)
  AND volume >= 0.5
  AND correct IS NOT NULL
GROUP BY position
ORDER BY position
""").fetchdf()
log.info(f"Non-scalper baseline HR:\n{non_scalper_baseline.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 14: Correlation between scalp profitability and held-position HR
# ─────────────────────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STEP 14: Correlation: scalp profitability vs held HR")
log.info("=" * 60)

corr_analysis = con.execute("""
WITH scalp_stats AS (
    SELECT
        maker,
        AVG(buy_direction_won) AS scalp_entry_hr,
        MEDIAN(spread) AS med_spread,
        SUM(est_scalp_profit) AS total_scalp_profit,
        COUNT(*) AS n_scalps
    FROM _tc_scalpers_resolved
    GROUP BY maker
    HAVING COUNT(*) >= 10
),
scalped_market_ids AS (
    SELECT DISTINCT maker, condition_id
    FROM _tc_scalpers_raw
    WHERE scalp_hours >= 0
),
held_stats AS (
    SELECT
        mp.trader,
        AVG(mp.correct) AS held_hr,
        COUNT(*) AS n_held
    FROM maker_positions mp
    WHERE mp.position = 'YES'
      AND mp.volume >= 0.5
      AND mp.correct IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM scalped_market_ids sm
          WHERE sm.maker = mp.trader AND sm.condition_id = mp.condition_id
      )
    GROUP BY mp.trader
    HAVING COUNT(*) >= 5
)
SELECT
    NTILE(5) OVER (ORDER BY ss.med_spread) AS spread_quintile,
    COUNT(*) AS n_traders,
    ROUND(AVG(ss.scalp_entry_hr) * 100, 2) AS avg_scalp_entry_hr,
    ROUND(AVG(hs.held_hr) * 100, 2) AS avg_held_hr,
    ROUND(AVG(ss.med_spread) * 100, 2) AS avg_spread_pct,
    ROUND(AVG(ss.n_scalps), 1) AS avg_n_scalps
FROM scalp_stats ss
JOIN held_stats hs ON ss.maker = hs.trader
GROUP BY 1
ORDER BY 1
""").fetchdf()
log.info(f"Spread quintile vs held HR:\n{corr_analysis.to_string()}")

# ─────────────────────────────────────────────────────────────────────────────
# Write results
# ─────────────────────────────────────────────────────────────────────────────
log.info("Writing results...")

output_path = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/in-play-traders/discovery/track_c_results.md"

def df_to_md(df):
    try:
        return df.to_markdown(index=False)
    except Exception:
        return df.to_string()

results_content = f"""# Track C: Scalper/Hedger Alpha Analysis

**Date**: {datetime.now().strftime('%Y-%m-%d')}
**Sample**: Full 2025 (Jan-Dec), raw trades parquet, 2025 positions resolved
**Status**: VECTORIZED UPPER BOUND

---

## Summary

Scalpers = makers who BUY then SELL the same (condition_id, asset_id) within 24h.
This analysis tests if their BUY entry direction predicts market outcome.

Key caveats applied:
- SELL trades are NOT used as signals — only the initial BUY direction is evaluated
- Price gate [0.10, 0.80] filters in-the-money contamination
- Markets resolved with `token_won=1 WHERE outcome='YES'` → `yes_won`

---

## 1. Scalper Population (2025 Full Year)

{df_to_md(pop)}

### Spread Distribution (sell_price - buy_price)

{df_to_md(spread_dist)}

### Scalp Time Distribution

{df_to_md(time_dist)}

---

## 2. Scalper Hit Rate at BUY Entry (Overall)

{df_to_md(scalper_hr_overall)}

### By Position Side (YES vs NO)

{df_to_md(hr_by_side)}

### By Scalp Time Window

{df_to_md(hr_by_time)}

---

## 3. Serial Scalper Profiles

Top 30 by scalp count (>=10 scalps):

{df_to_md(serial_scalpers)}

### Activity Tier Distribution

{df_to_md(scalper_dist)}

---

## 4. Scalper vs Non-Scalper HR (maker_positions)

{df_to_md(scalper_vs_holder)}

### Non-Scalper Population Baseline

{df_to_md(non_scalper_baseline)}

---

## 5. Scalp Profitability

{df_to_md(profitability)}

### Monthly Volume and Profitability

{df_to_md(monthly_vol)}

---

## 6. Timing: Entry vs Resolution

{df_to_md(timing)}

---

## 7. Tag Breakdown

"""

if tag_breakdown is not None:
    results_content += df_to_md(tag_breakdown)
else:
    results_content += "_Tag breakdown unavailable_"

results_content += f"""

---

## 8. Copyability Assessment (Price-Gated: 0.10-0.80)

{df_to_md(copyability)}

---

## 9. High-Edge Scalper Pool

Top 50 by entry HR (>=10 scalps):

{df_to_md(top_by_hr)}

### Copy Pool: HR >= 55%, >=10 scalps, price-gated

{df_to_md(pool_stats)}

### Profit Quintile vs Entry HR

{df_to_md(decile_analysis)}

---

## 10. Edge Persistence: Scalper Held Positions

Do traders classified as scalpers also do better on their held positions?

{df_to_md(held_analysis)}

---

## 11. Spread Quintile vs Held HR

Do high-spread scalpers (profitable scalpers) also have higher held-position HR?

{df_to_md(corr_analysis)}

---

## 12. Assessment and Conclusions

_(To be filled after reviewing numbers above)_

"""

with open(output_path, "w") as f:
    f.write(results_content)

log.info(f"Results written to: {output_path}")
log.info("DONE")
