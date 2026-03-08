"""
Strategy 3: Elite Copy with Scorecard Filtering
Phase 1: Build Elite Pool + Vectorized Copy Signal

Train/test split: 2025-12-05
Train: all positions with resolved_at < 2025-12-05
Test: all positions with resolved_at >= 2025-12-05

Table in DuckDB: maker_positions (NOT maker_positions_resolved_corrected)
Columns: trader, condition_id, position, correct, yes_won, net_usd, volume,
         first_trade, resolved_at, net_yes, net_no

CRITICAL RULES:
- Aggregate to market level before computing metrics
- Exclude gambling markets
- Market-maker exclusion: avg(abs(net_usd)/volume) >= 0.90
- Tag-specific base rates
- first_trade filter for test window (only copyable entries)
"""

import json
import sys
from datetime import datetime

TRAIN_END = "2025-12-05"
TEST_START = "2025-12-05"

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")
from research.db import db as get_db

log_path = "/mnt/nvme/git/polymarket/polymarket/tmp/phase1_elite_pool.log"
results_path = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-strategies/results/phase1_results.json"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")

# Clear log
with open(log_path, "w") as f:
    pass

log("Starting Phase 1: Elite Pool Construction")
con = get_db().con
log("DuckDB connection established")

# ─── Step 1: Gambling filter macro ──────────────────────────────────────────
log("Step 1: Creating gambling filter macro...")
con.execute("""
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
""")
log("  Gambling macro created")

# ─── Step 2: Non-gambling markets with primary tag ──────────────────────────
log("Step 2: Build market-tag lookup (non-gambling only)...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag,
    first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
""")
n = con.execute("SELECT count(*) FROM _s3_market_tags").fetchone()[0]
log(f"  Non-gambling markets: {n:,}")

# ─── Step 3: Tag-specific base rates (TRAINING period) ───────────────────────
log("Step 3: Compute tag-specific base rates (training period)...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_tag_base_rates AS
SELECT
    mt.primary_tag AS tag,
    count(DISTINCT p.condition_id) AS n_markets,
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate
FROM maker_positions p
JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
GROUP BY mt.primary_tag
HAVING count(DISTINCT p.condition_id) >= 50
ORDER BY n_markets DESC;
""")
tag_rates = con.execute("SELECT tag, n_markets, yes_base_rate FROM _s3_tag_base_rates ORDER BY n_markets DESC LIMIT 20").fetchall()
log(f"  Tag base rates computed ({len(tag_rates)} tags):")
for tag, n, br in tag_rates:
    log(f"    {tag}: {br:.3f} ({n:,} markets)")

# ─── Step 4: Trader training stats (non-gambling, YES positions, TRAIN) ──────
log("Step 4: Compute trader training stats...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_trader_train AS
WITH base AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        p.condition_id,
        CAST(p.resolved_at AS DATE) AS resolved_date,
        CAST(p.first_trade AS DATE) AS entry_date,
        CAST(p.yes_won AS DOUBLE) AS correct,
        p.net_usd,
        abs(p.net_usd) AS abs_net_usd,
        p.volume,
        CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0
      AND p.net_usd IS NOT NULL
),
trader_conviction AS (
    SELECT trader, tag, avg(conviction) AS avg_conviction
    FROM base
    GROUP BY trader, tag
),
trader_stats AS (
    SELECT
        b.trader,
        b.tag,
        count(*) AS n_positions,
        count(DISTINCT b.condition_id) AS n_markets,
        sum(b.correct) AS n_correct,
        avg(b.correct) AS hit_rate,
        avg(b.abs_net_usd) AS avg_position_size,
        count(DISTINCT date_trunc('month', b.resolved_date)) AS n_months_active
    FROM base b
    GROUP BY b.trader, b.tag
)
SELECT
    ts.*,
    tc.avg_conviction,
    br.yes_base_rate AS tag_base_rate,
    (ts.hit_rate - br.yes_base_rate) AS excess_hr
FROM trader_stats ts
JOIN trader_conviction tc ON ts.trader = tc.trader AND ts.tag = tc.tag
JOIN _s3_tag_base_rates br ON ts.tag = br.tag
-- Bot guard
WHERE ts.n_positions < 10000;
""")
n_records = con.execute("SELECT count(*) FROM _s3_trader_train").fetchone()[0]
log(f"  Trader-tag training records: {n_records:,}")

# ─── Step 5: Window-based consistency ───────────────────────────────────────
log("Step 5: Compute window-based consistency (60-day windows)...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_trader_windows AS
WITH base AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        CAST(p.yes_won AS DOUBLE) AS correct,
        CAST(p.resolved_at AS DATE) AS resolved_date,
        -- 60-day window bucket (days since epoch / 60)
        CAST(epoch_ms(CAST(p.resolved_at AS TIMESTAMP)) / 5184000000 AS BIGINT) AS window_id
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0
),
window_stats AS (
    SELECT
        trader, tag, window_id,
        count(*) AS n_pos,
        avg(correct) AS window_hr
    FROM base
    GROUP BY trader, tag, window_id
    HAVING n_pos >= 3
),
trader_consistency AS (
    SELECT
        trader, tag,
        count(*) AS n_windows,
        avg(window_hr) AS mean_window_hr,
        CASE WHEN count(*) >= 2 THEN stddev(window_hr) ELSE NULL END AS std_window_hr,
        CASE WHEN count(*) >= 2
             THEN (avg(window_hr) / (coalesce(stddev(window_hr), 0.0) + 0.05))
                  * least(ln(1.0 + count(*)), 1.8)
             ELSE 0.0
        END AS stability_score
    FROM window_stats
    GROUP BY trader, tag
)
SELECT * FROM trader_consistency;
""")
n_window = con.execute("SELECT count(*) FROM _s3_trader_windows").fetchone()[0]
log(f"  Trader-window records: {n_window:,}")

# ─── Step 6: Apply elite gates ───────────────────────────────────────────────
log("Step 6: Apply elite gates...")
pool_stats = {}

all_traders = con.execute("SELECT count(DISTINCT trader) FROM _s3_trader_train").fetchone()[0]
pool_stats["all_traders_any_tag"] = all_traders
log(f"  All traders with training data: {all_traders:,}")

g1 = con.execute("""
    SELECT count(DISTINCT trader) FROM _s3_trader_train
    WHERE n_markets >= 20 AND avg_conviction >= 0.90
""").fetchone()[0]
pool_stats["gate1_min_markets_noMM"] = g1
log(f"  Gate 1 (n_markets>=20, conviction>=0.90): {g1:,}")

g2 = con.execute("""
    SELECT count(DISTINCT trader) FROM _s3_trader_train
    WHERE n_markets >= 20 AND avg_conviction >= 0.90 AND excess_hr >= 0.15
""").fetchone()[0]
pool_stats["gate2_excess_hr_15pp"] = g2
log(f"  Gate 2 (+ excess_hr>=0.15): {g2:,}")

g3 = con.execute("""
    SELECT count(DISTINCT t.trader) FROM _s3_trader_train t
    JOIN _s3_trader_windows w ON t.trader = w.trader AND t.tag = w.tag
    WHERE t.n_markets >= 20 AND t.avg_conviction >= 0.90 AND t.excess_hr >= 0.15
      AND t.n_months_active >= 6 AND w.n_windows >= 3
""").fetchone()[0]
pool_stats["gate3_min_activity"] = g3
log(f"  Gate 3 (+ months>=6, windows>=3): {g3:,}")

g4 = con.execute("""
    SELECT count(DISTINCT t.trader) FROM _s3_trader_train t
    JOIN _s3_trader_windows w ON t.trader = w.trader AND t.tag = w.tag
    WHERE t.n_markets >= 20 AND t.avg_conviction >= 0.90 AND t.excess_hr >= 0.15
      AND t.n_months_active >= 6 AND w.n_windows >= 3
      AND w.stability_score >= 4.0
""").fetchone()[0]
pool_stats["gate4_stability_4"] = g4
log(f"  Gate 4 (+ stability>=4.0): {g4:,}")

# ─── Step 7: Build elite pool ───────────────────────────────────────────────
log("Step 7: Build elite pool table...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_elite_pool AS
SELECT
    t.trader,
    t.tag,
    t.n_markets,
    t.hit_rate,
    t.excess_hr,
    t.avg_conviction,
    t.n_months_active,
    w.n_windows,
    w.stability_score,
    t.avg_position_size,
    t.tag_base_rate
FROM _s3_trader_train t
JOIN _s3_trader_windows w ON t.trader = w.trader AND t.tag = w.tag
WHERE t.n_markets >= 20
  AND t.avg_conviction >= 0.90
  AND t.excess_hr >= 0.15
  AND t.n_months_active >= 6
  AND w.n_windows >= 3
  AND w.stability_score >= 4.0;
""")

elite_total = con.execute("SELECT count(*) FROM _s3_elite_pool").fetchone()[0]
elite_traders = con.execute("SELECT count(DISTINCT trader) FROM _s3_elite_pool").fetchone()[0]
pool_stats["elite_pool_records"] = elite_total
pool_stats["elite_pool_traders"] = elite_traders
log(f"  Elite pool: {elite_traders:,} traders, {elite_total:,} trader-tag records")

elite_by_tag = con.execute("""
SELECT tag, count(DISTINCT trader) AS n_traders,
       median(hit_rate) AS median_hr,
       median(excess_hr) AS median_excess_hr,
       median(stability_score) AS median_stability,
       first(tag_base_rate) AS base_rate
FROM _s3_elite_pool
GROUP BY tag
ORDER BY n_traders DESC
""").fetchall()

pool_stats["elite_by_tag"] = [
    {"tag": r[0], "n_traders": r[1], "median_hr": round(r[2], 4),
     "median_excess_hr": round(r[3], 4), "median_stability": round(r[4], 4),
     "tag_base_rate": round(r[5], 4)}
    for r in elite_by_tag
]
log("  Per-tag elite pool:")
for r in elite_by_tag[:15]:
    log(f"    {r[0]}: {r[1]} traders, HR={r[2]:.3f}, excess={r[3]:+.3f}, stab={r[4]:.2f}")

# ─── Step 8: Vectorized Elite Copy Signal (TEST period) ─────────────────────
log("Step 8: Vectorized elite copy signal in TEST period...")
con.execute(f"""
CREATE OR REPLACE TABLE _s3_test_signals AS
WITH test_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        mt.primary_tag AS tag,
        CAST(p.yes_won AS DOUBLE) AS correct,
        CAST(p.resolved_at AS DATE) AS resolved_date,
        CAST(p.first_trade AS DATE) AS entry_date,
        date_diff('day', p.first_trade, p.resolved_at) AS hold_days,
        abs(p.net_usd) AS abs_net_usd
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    JOIN _s3_elite_pool ep ON p.trader = ep.trader AND mt.primary_tag = ep.tag
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.volume > 0
),
-- Aggregate to MARKET level
market_signals AS (
    SELECT
        condition_id,
        tag,
        first(correct) AS market_correct,
        first(resolved_date) AS resolved_date,
        count(DISTINCT trader) AS n_elite_traders,
        max(entry_date) AS signal_entry,
        date_diff('day', CAST(max(entry_date) AS TIMESTAMP), CAST(first(resolved_date) AS TIMESTAMP)) AS hold_days,
        sum(abs_net_usd) AS total_elite_usd
    FROM test_positions
    GROUP BY condition_id, tag
)
SELECT * FROM market_signals WHERE hold_days >= 0;
""")

n_signals = con.execute("SELECT count(*) FROM _s3_test_signals").fetchone()[0]
log(f"  Test signals (market-level): {n_signals:,}")

if n_signals > 0:
    metrics = con.execute("""
    SELECT
        count(*) AS n_signals,
        avg(market_correct) AS hit_rate,
        median(hold_days) AS median_hold_days,
        median(n_elite_traders) AS median_n_traders
    FROM _s3_test_signals
    """).fetchone()
    log(f"  Overall: HR={metrics[1]:.4f}, hold={metrics[2]:.1f}d, median_traders={metrics[3]:.1f}")

signals_by_tag = con.execute("""
SELECT
    tag,
    count(*) AS n_signals,
    avg(market_correct) AS hit_rate,
    median(hold_days) AS median_hold_days
FROM _s3_test_signals
GROUP BY tag
ORDER BY n_signals DESC
""").fetchall()

pool_stats["test_signals_by_tag"] = [
    {"tag": r[0], "n_signals": r[1], "hit_rate": round(r[2], 4), "median_hold": r[3]}
    for r in signals_by_tag
]
log("  Signals by tag:")
for r in signals_by_tag:
    log(f"    {r[0]}: {r[1]} signals, HR={r[2]:.3f}, hold={r[3]}d")

# ─── Step 9: Consensus N threshold sweep ─────────────────────────────────────
log("Step 9: Consensus N threshold sweep...")
consensus_by_n = {}
for N in [1, 2, 3, 4, 5]:
    result = con.execute(f"""
    SELECT count(*) AS n_signals,
           avg(market_correct) AS hit_rate,
           median(hold_days) AS median_hold
    FROM _s3_test_signals
    WHERE n_elite_traders >= {N}
    """).fetchone()
    consensus_by_n[f"N>={N}"] = {
        "n_signals": result[0],
        "hit_rate": round(result[1], 4) if result[0] > 0 else None,
        "median_hold": result[2]
    }
    if result[0] > 0:
        log(f"  N>={N}: {result[0]} signals, HR={result[1]:.4f}, hold={result[2]}d")
    else:
        log(f"  N>={N}: 0 signals")

pool_stats["consensus_by_n"] = consensus_by_n

# ─── Step 10: Per-tag consensus N sweep ──────────────────────────────────────
log("Step 10: Per-tag consensus N=2 breakdown...")
tag_consensus = con.execute("""
SELECT
    tag,
    count(*) AS n_signals,
    avg(market_correct) AS hit_rate,
    median(hold_days) AS median_hold,
    first(tag) AS tag_base_rate
FROM _s3_test_signals
WHERE n_elite_traders >= 2
GROUP BY tag
ORDER BY n_signals DESC
""").fetchall()

pool_stats["tag_consensus_n2"] = [
    {"tag": r[0], "n_signals": r[1], "hit_rate": round(r[2], 4), "median_hold": r[3]}
    for r in tag_consensus
]
log("  Consensus N>=2 by tag:")
for r in tag_consensus:
    log(f"    {r[0]}: {r[1]} signals, HR={r[2]:.3f}, hold={r[3]}d")

# ─── Step 11: Broader pool comparison ────────────────────────────────────────
log("Step 11: Broader copy pool comparison...")

# Top-decile (HR only, relaxed gates)
con.execute(f"""
CREATE OR REPLACE TABLE _s3_top_decile_pool AS
WITH ranked AS (
    SELECT
        trader, tag, hit_rate, excess_hr, n_markets,
        PERCENT_RANK() OVER (PARTITION BY tag ORDER BY hit_rate) AS hr_rank
    FROM _s3_trader_train
    WHERE n_markets >= 10 AND avg_conviction >= 0.90
)
SELECT * FROM ranked WHERE hr_rank >= 0.90;
""")
n_top_decile = con.execute("SELECT count(DISTINCT trader) FROM _s3_top_decile_pool").fetchone()[0]
log(f"  Top-decile pool: {n_top_decile:,} traders")

top_decile_signals = con.execute(f"""
WITH test_pos AS (
    SELECT
        p.condition_id,
        mt.primary_tag AS tag,
        first(CAST(p.yes_won AS DOUBLE)) AS market_correct,
        count(DISTINCT p.trader) AS n_traders,
        date_diff('day', max(p.first_trade), first(p.resolved_at)) AS hold_days
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    JOIN _s3_top_decile_pool dp ON p.trader = dp.trader AND mt.primary_tag = dp.tag
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.volume > 0
    GROUP BY p.condition_id, mt.primary_tag
    HAVING hold_days >= 0
)
SELECT count(*) AS n_signals, avg(market_correct) AS hit_rate, median(hold_days) AS median_hold
FROM test_pos
""").fetchone()

pool_stats["top_decile_test"] = {
    "n_traders": n_top_decile,
    "n_signals": top_decile_signals[0],
    "hit_rate": round(top_decile_signals[1], 4) if top_decile_signals[0] > 0 else None,
    "median_hold": top_decile_signals[2]
}
log(f"  Top-decile test: {top_decile_signals[0]} signals, HR={top_decile_signals[1]:.4f}")

# Top-quintile (top 20%, even broader)
con.execute(f"""
CREATE OR REPLACE TABLE _s3_top_quintile_pool AS
WITH ranked AS (
    SELECT
        trader, tag, hit_rate, excess_hr, n_markets,
        PERCENT_RANK() OVER (PARTITION BY tag ORDER BY hit_rate) AS hr_rank
    FROM _s3_trader_train
    WHERE n_markets >= 5 AND avg_conviction >= 0.90
)
SELECT * FROM ranked WHERE hr_rank >= 0.80;
""")
n_quintile = con.execute("SELECT count(DISTINCT trader) FROM _s3_top_quintile_pool").fetchone()[0]

quintile_signals = con.execute(f"""
WITH test_pos AS (
    SELECT
        p.condition_id,
        mt.primary_tag AS tag,
        first(CAST(p.yes_won AS DOUBLE)) AS market_correct,
        date_diff('day', max(p.first_trade), first(p.resolved_at)) AS hold_days
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    JOIN _s3_top_quintile_pool qp ON p.trader = qp.trader AND mt.primary_tag = qp.tag
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.volume > 0
    GROUP BY p.condition_id, mt.primary_tag
    HAVING date_diff('day', max(p.first_trade), first(p.resolved_at)) >= 0
)
SELECT count(*) AS n_signals, avg(market_correct) AS hit_rate, median(hold_days) AS median_hold
FROM test_pos
""").fetchone()

pool_stats["top_quintile_test"] = {
    "n_traders": n_quintile,
    "n_signals": quintile_signals[0],
    "hit_rate": round(quintile_signals[1], 4) if quintile_signals[0] > 0 else None,
    "median_hold": quintile_signals[2]
}
log(f"  Top-quintile test: {quintile_signals[0]} signals, HR={quintile_signals[1]:.4f}")

# ─── Step 12: Entry price analysis ───────────────────────────────────────────
log("Step 12: Entry price distribution for elite traders (TEST period)...")
try:
    price_analysis = con.execute(f"""
    SELECT
        ep.tag,
        count(*) AS n_entries,
        median(yed.price_x_vol / yed.volume) AS median_entry_price,
        avg(yed.price_x_vol / yed.volume) AS avg_entry_price,
        sum(CASE WHEN (yed.price_x_vol / yed.volume) < 0.30 THEN 1 ELSE 0 END) AS n_below_30,
        sum(CASE WHEN (yed.price_x_vol / yed.volume) BETWEEN 0.30 AND 0.60 THEN 1 ELSE 0 END) AS n_30_60,
        sum(CASE WHEN (yed.price_x_vol / yed.volume) > 0.60 THEN 1 ELSE 0 END) AS n_above_60
    FROM yes_entry_data yed
    JOIN _s3_elite_pool ep ON yed.trader = ep.trader
    JOIN _s3_market_tags mt ON yed.condition_id = mt.condition_id AND ep.tag = mt.primary_tag
    WHERE CAST(yed.first_trade AS DATE) >= '{TEST_START}'
      AND yed.volume > 0
    GROUP BY ep.tag
    ORDER BY n_entries DESC
    """).fetchall()

    pool_stats["entry_price_by_tag"] = [
        {
            "tag": r[0], "n_entries": r[1],
            "median_entry_price": round(r[2], 4) if r[2] else None,
            "avg_entry_price": round(r[3], 4) if r[3] else None,
            "n_below_30": r[4], "n_30_60": r[5], "n_above_60": r[6]
        }
        for r in price_analysis
    ]
    log("  Entry price by tag:")
    for r in price_analysis[:10]:
        total = r[4] + r[5] + r[6]
        if total > 0:
            log(f"    {r[0]}: median={r[2]:.3f}, <30%: {r[4]/total:.1%}, 30-60%: {r[5]/total:.1%}, >60%: {r[6]/total:.1%}")
except Exception as e:
    log(f"  Entry price error: {e}")
    pool_stats["entry_price_error"] = str(e)

# ─── Step 13: Entry price filter impact ──────────────────────────────────────
log("Step 13: Entry price ceiling filter impact...")
price_filter_results = {}
for ceil in [0.40, 0.50, 0.60, 0.70, 0.80, None]:
    try:
        if ceil is not None:
            price_cond = f"AND (yed.price_x_vol / yed.volume) <= {ceil}"
        else:
            price_cond = ""

        result = con.execute(f"""
        WITH elite_test AS (
            SELECT
                p.condition_id,
                mt.primary_tag AS tag,
                CAST(p.yes_won AS DOUBLE) AS correct,
                date_diff('day', p.first_trade, p.resolved_at) AS hold_days
            FROM maker_positions p
            JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
            JOIN _s3_elite_pool ep ON p.trader = ep.trader AND mt.primary_tag = ep.tag
            JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
            WHERE p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
              AND p.volume > 0
              AND hold_days >= 0
              {price_cond}
        ),
        mkt AS (
            SELECT condition_id, tag,
                   first(correct) AS market_correct,
                   count(DISTINCT condition_id) AS n
            FROM elite_test GROUP BY condition_id, tag
        )
        SELECT count(*) AS n_signals, avg(market_correct) AS hr FROM mkt
        """).fetchone()

        key = f"price_ceil_{ceil}" if ceil else "no_filter"
        price_filter_results[key] = {
            "n_signals": result[0],
            "hit_rate": round(result[1], 4) if result[0] > 0 else None
        }
        log(f"  Price ceil {ceil}: {result[0]} signals, HR={result[1]:.4f}" if result[0] > 0 else f"  Price ceil {ceil}: 0 signals")
    except Exception as e:
        log(f"  Price ceil {ceil} error: {e}")
        price_filter_results[f"error_{ceil}"] = str(e)

pool_stats["price_filter_impact"] = price_filter_results

# ─── Step 14: Elite market selector (Pivot C) ────────────────────────────────
log("Step 14: Elite market selector (Pivot C)...")
try:
    elite_mkt = con.execute(f"""
    WITH elite_markets AS (
        SELECT DISTINCT p.condition_id, mt.primary_tag AS tag
        FROM maker_positions p
        JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _s3_elite_pool ep ON p.trader = ep.trader AND mt.primary_tag = ep.tag
        WHERE p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
    )
    SELECT
        'elite_selected' AS group_name,
        count(DISTINCT p.condition_id) AS n_markets,
        avg(CAST(p.yes_won AS DOUBLE)) AS yes_win_rate
    FROM maker_positions p
    JOIN elite_markets em ON p.condition_id = em.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    UNION ALL
    SELECT
        'all_markets' AS group_name,
        count(DISTINCT p.condition_id) AS n_markets,
        avg(CAST(p.yes_won AS DOUBLE)) AS yes_win_rate
    FROM maker_positions p
    JOIN _s3_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    """).fetchall()

    pool_stats["elite_market_selector"] = [
        {"group": r[0], "n_markets": r[1], "yes_win_rate": round(r[2], 4)}
        for r in elite_mkt
    ]
    for r in elite_mkt:
        log(f"  {r[0]}: {r[1]:,} markets, YES win rate={r[2]:.4f}")
except Exception as e:
    log(f"  Elite market selector error: {e}")
    pool_stats["elite_market_selector_error"] = str(e)

# ─── Save results ─────────────────────────────────────────────────────────────
log("Saving results...")
with open(results_path, "w") as f:
    json.dump(pool_stats, f, indent=2, default=str)
log(f"Results saved to {results_path}")
log("Phase 1 complete!")
