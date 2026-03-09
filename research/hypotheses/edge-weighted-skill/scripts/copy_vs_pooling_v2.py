"""Copy vs Pooling v2 — Fix Fill Price Bug + 3-Month Extension.

Fixes from v1:
  Bug fix: Consensus fill price is now the Nth trader's actual entry price
           (chronologically ordered), not AVG across all pool traders.
           AVG was look-ahead bias — some traders who entered AFTER signal
           trigger were included in the price average.
  Extension: 3-month test window (Nov 2025, Dec 2025, Jan 2026).
             Training pool built on positions resolved before 2025-11-01.

Rules:
  - Market-level aggregation: 1 signal per condition_id per month
  - Direction: YES BUY only (NO analysis is a separate task)
  - Elections tag EXCLUDED (per Round 1 review)
  - Gambling markets excluded
  - Tag-specific base rates per test month
  - BUY-only (SELL is ambiguous per pitfalls/sell_is_exit.md)
  - Training causal: only positions resolved before train_end

Outputs:
  research/hypotheses/edge-weighted-skill/scripts/copy_vs_pooling_v2.py  (this file)
  research/hypotheses/edge-weighted-skill/discovery/copy_vs_pooling_v2_results.json
  research/hypotheses/edge-weighted-skill/discovery/copy_vs_pooling_v2_results.md
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJ = Path("/mnt/nvme/git/polymarket/polymarket")
sys.path.insert(0, str(PROJ))

LOG_PATH = PROJ / "tmp" / "copy_vs_pooling_v2.log"
DISCOVERY_DIR = PROJ / "research" / "hypotheses" / "edge-weighted-skill" / "discovery"
DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


with open(LOG_PATH, "w") as f:
    pass

log("=" * 70)
log(f"Copy vs Pooling v2 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 70)
log("Train: resolved before 2025-11-01")
log("Test:  Nov 2025, Dec 2025, Jan 2026 (3 months)")
log("Fix:   Consensus fill price = Nth trader's actual entry price (causal)")
log("Fix:   Elections tag excluded")

# ---------------------------------------------------------------------------
# Step 0: Load DuckDB
# ---------------------------------------------------------------------------
log("\n[0] Loading DuckDB...")
from research.db import db as get_db  # noqa: E402

t0 = time.time()
d = get_db()
con = d.con
log(f"DuckDB ready ({time.time() - t0:.1f}s)")

# Configuration
TRAIN_END = "2025-11-01"
TEST_MONTHS = [
    ("2025-11-01", "2025-12-01", "Nov2025"),
    ("2025-12-01", "2026-01-01", "Dec2025"),
    ("2026-01-01", "2026-02-01", "Jan2026"),
]
COPY_K_VALUES = [5, 10, 25, 50, 100]
POOL_K_VALUES = [50, 100, 200]
N_VALUES = [1, 2, 3]

# ---------------------------------------------------------------------------
# Step 1: Build Gambling Market Classification
# ---------------------------------------------------------------------------
log("\n[1] Building gambling market classification...")

GAMBLING_PATTERNS = [
    "up-or-down", "up_or_down",
    "above-or-below", "above-below",
    "higher-or-lower", "higher-lower",
    "will-bitcoin-", "will-btc-",
    "will-eth-", "will-ethereum-",
    "will-xrp-", "will-sol-",
    "-1h-", "-24h-",
]
gambling_cond_sql = " OR ".join([f"lower(slug) LIKE '%{p}%'" for p in GAMBLING_PATTERNS])

con.execute(f"""
    CREATE OR REPLACE TABLE _v2_gambling_markets AS
    SELECT condition_id FROM markets WHERE {gambling_cond_sql}
""")
n_gamble = con.execute("SELECT count(*) FROM _v2_gambling_markets").fetchone()[0]
log(f"Gambling markets: {n_gamble:,}")

# ---------------------------------------------------------------------------
# Step 2: Canonical Tag Assignment (Elections excluded)
# ---------------------------------------------------------------------------
log("\n[2] Building tag assignment (Elections excluded)...")

con.execute("""
    CREATE OR REPLACE TABLE _v2_market_tags AS
    SELECT
        m.condition_id,
        CASE
            WHEN et.label = 'Crypto'            THEN 1
            WHEN et.label = 'Sports'            THEN 2
            WHEN et.label = 'Soccer'            THEN 3
            WHEN et.label = 'Basketball'        THEN 4
            WHEN et.label = 'American Football' THEN 5
            WHEN et.label = 'Baseball'          THEN 6
            WHEN et.label = 'Tennis'            THEN 7
            WHEN et.label = 'Esports'           THEN 8
            WHEN et.label = 'Politics'          THEN 9
            WHEN et.label = 'Finance'           THEN 10
            WHEN et.label = 'Science'           THEN 11
            WHEN et.label = 'Culture'           THEN 12
            WHEN et.label = 'Technology'        THEN 13
            WHEN et.label = 'Weather'           THEN 14
            ELSE 999
        END AS tag_priority,
        et.label AS tag
    FROM markets m
    LEFT JOIN _v2_gambling_markets gm ON m.condition_id = gm.condition_id
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    WHERE gm.condition_id IS NULL
      AND et.label != 'Elections'          -- EXCLUDED per Round 1 review
    QUALIFY row_number() OVER (PARTITION BY m.condition_id ORDER BY tag_priority ASC) = 1
""")
n_tagged = con.execute("SELECT count(*) FROM _v2_market_tags").fetchone()[0]
log(f"Tagged non-gambling non-elections markets: {n_tagged:,}")

# ---------------------------------------------------------------------------
# Step 3: Build Training Dataset (resolved before TRAIN_END)
# ---------------------------------------------------------------------------
log(f"\n[3] Building training dataset (resolved < {TRAIN_END})...")

con.execute(f"""
    CREATE OR REPLACE TABLE _v2_train_base AS
    SELECT
        mp.trader,
        mp.condition_id,
        mp.yes_won,
        mp.first_trade,
        mp.resolved_at,
        mp.volume,
        yed.price_x_vol / NULLIF(yed.volume, 0) AS entry_price,
        (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 AS hold_hours,
        CASE
            WHEN mp.yes_won = 1 THEN 1
            WHEN mp.yes_won = 0 THEN 0
            ELSE NULL
        END AS correct,
        FLOOR((yed.price_x_vol / NULLIF(yed.volume, 0)) / 0.05) * 0.05 AS price_bucket,
        CASE
            WHEN (yed.price_x_vol / NULLIF(yed.volume, 0)) < 0.30 THEN 'longshot'
            WHEN (yed.price_x_vol / NULLIF(yed.volume, 0)) < 0.85 THEN 'mid'
            ELSE 'sure_thing'
        END AS price_regime,
        mt.tag
    FROM maker_positions mp
    INNER JOIN yes_entry_data yed
        ON mp.trader = yed.trader AND mp.condition_id = yed.condition_id
    LEFT JOIN _v2_gambling_markets gm ON mp.condition_id = gm.condition_id
    LEFT JOIN _v2_market_tags mt ON mp.condition_id = mt.condition_id
    WHERE
        gm.condition_id IS NULL
        AND mt.condition_id IS NOT NULL         -- must have a non-elections tag
        AND mp.position = 'YES'
        AND mp.resolved_at < TIMESTAMP '{TRAIN_END}'   -- resolved before test window
        AND mp.first_trade < TIMESTAMP '{TRAIN_END}'   -- entered during training
        AND mp.resolved_at IS NOT NULL
        AND mp.yes_won IS NOT NULL
        AND yed.volume > 0
        AND (yed.price_x_vol / NULLIF(yed.volume, 0)) >= 0.01
        AND (yed.price_x_vol / NULLIF(yed.volume, 0)) <  1.00
""")
n_train = con.execute("SELECT count(*) FROM _v2_train_base").fetchone()[0]
log(f"Training YES positions: {n_train:,}")

# ---------------------------------------------------------------------------
# Step 4: Price-Level Base Rates (Training)
# ---------------------------------------------------------------------------
log("\n[4] Computing training price-level base rates...")

con.execute("""
    CREATE OR REPLACE TABLE _v2_price_base_rates AS
    SELECT
        price_bucket,
        count(*) AS n_pos,
        AVG(CAST(correct AS DOUBLE)) AS base_hr
    FROM _v2_train_base
    WHERE correct IS NOT NULL
    GROUP BY price_bucket
    HAVING count(*) >= 100
""")
n_buckets = con.execute("SELECT count(*) FROM _v2_price_base_rates").fetchone()[0]
log(f"Price buckets: {n_buckets}")

# ---------------------------------------------------------------------------
# Step 5: Per-Trader Bucket Excess HR (Training)
# ---------------------------------------------------------------------------
log("\n[5] Per-trader bucket excess HR (training)...")

con.execute("""
    CREATE OR REPLACE TABLE _v2_trader_bucket_stats AS
    SELECT
        tb.trader,
        tb.price_bucket,
        count(*) AS n_pos,
        AVG(CAST(tb.correct AS DOUBLE)) AS trader_hr,
        pbr.base_hr,
        AVG(CAST(tb.correct AS DOUBLE)) - pbr.base_hr AS excess_hr
    FROM _v2_train_base tb
    JOIN _v2_price_base_rates pbr ON tb.price_bucket = pbr.price_bucket
    WHERE tb.correct IS NOT NULL
    GROUP BY tb.trader, tb.price_bucket, pbr.base_hr
    HAVING count(*) >= 5
""")

# ---------------------------------------------------------------------------
# Step 6: Global Edge-Weighted Scorecard
# ---------------------------------------------------------------------------
log("\n[6] Building edge-weighted scorecard...")

con.execute("""
    CREATE OR REPLACE TABLE _v2_trader_scorecard AS
    SELECT
        tb.trader,
        count(*) AS n_positions,
        AVG(CAST(tb.correct AS DOUBLE)) AS raw_hr,
        SUM(bs.excess_hr * bs.n_pos) / NULLIF(SUM(bs.n_pos), 0) AS bucket_excess_hr,
        SUM(bs.excess_hr * bs.n_pos) / NULLIF(SUM(bs.n_pos), 0) * LN(count(*) + 1) AS edge_score,
        AVG(tb.entry_price) AS avg_entry_price,
        AVG(ABS(mp.net_usd) / NULLIF(mp.volume, 0)) AS conviction_ratio,
        MEDIAN(tb.volume) AS median_vol,
        AVG(CASE WHEN tb.hold_hours < 4.0 THEN 1 ELSE 0 END) AS inplay_ratio,
        AVG(CASE WHEN tb.price_regime = 'longshot' THEN 1 ELSE 0 END) AS longshot_ratio,
        AVG(CASE WHEN tb.price_regime = 'sure_thing' THEN 1 ELSE 0 END) AS sure_thing_ratio
    FROM _v2_train_base tb
    JOIN _v2_trader_bucket_stats bs
        ON tb.trader = bs.trader AND tb.price_bucket = bs.price_bucket
    JOIN maker_positions mp
        ON tb.trader = mp.trader AND tb.condition_id = mp.condition_id
    WHERE tb.correct IS NOT NULL
    GROUP BY tb.trader
    HAVING
        count(*) >= 20
        AND AVG(ABS(mp.net_usd) / NULLIF(mp.volume, 0)) >= 0.50
""")
n_scored = con.execute("SELECT count(*) FROM _v2_trader_scorecard").fetchone()[0]
log(f"Scored traders: {n_scored:,}")

# Ranked lists
ranked_edge = con.execute("""
    SELECT trader, edge_score, raw_hr, bucket_excess_hr, inplay_ratio
    FROM _v2_trader_scorecard ORDER BY edge_score DESC
""").fetchall()

ranked_hr = con.execute("""
    SELECT trader, edge_score, raw_hr, bucket_excess_hr, inplay_ratio
    FROM _v2_trader_scorecard ORDER BY raw_hr * LN(n_positions + 1) DESC
""").fetchall()

ranked_inplay = con.execute("""
    SELECT trader, edge_score, raw_hr, bucket_excess_hr, inplay_ratio
    FROM _v2_trader_scorecard
    WHERE inplay_ratio >= 0.50
    ORDER BY edge_score DESC
""").fetchall()

ALL_K = [5, 10, 25, 50, 100, 200]
edge_pools = {k: {r[0] for r in ranked_edge[:k]} for k in ALL_K}
hr_pools   = {k: {r[0] for r in ranked_hr[:k]}   for k in ALL_K}
inplay_pools = {k: {r[0] for r in ranked_inplay[:k]} for k in ALL_K}

log(f"Edge pools: {[f'K={k}:{len(edge_pools[k])}' for k in COPY_K_VALUES]}")
log(f"In-play specialists: {len(ranked_inplay)}")

# Show top 10
top10 = con.execute("""
    SELECT trader, n_positions, round(raw_hr*100,1), round(bucket_excess_hr*100,2),
           round(edge_score,3), round(inplay_ratio*100,1)
    FROM _v2_trader_scorecard ORDER BY edge_score DESC LIMIT 10
""").fetchall()
log("\n--- Top 10 by Edge Score ---")
for r in top10:
    log(f"  {r[0][:12]}  N={r[1]:>5}  HR={r[2]:>5}%  Exc={r[3]:>6}pp  ES={r[4]:>6.3f}  InPlay={r[5]:>5}%")

# ---------------------------------------------------------------------------
# Step 7: Test Position Tables (per month, with causal entry filter)
# ---------------------------------------------------------------------------
log("\n[7] Building test position tables (3 months)...")

for start, end, label in TEST_MONTHS:
    con.execute(f"""
        CREATE OR REPLACE TABLE _v2_test_{label} AS
        SELECT
            mp.trader,
            mp.condition_id,
            mp.yes_won,
            mp.first_trade,
            mp.resolved_at,
            mp.volume,
            yed.price_x_vol / NULLIF(yed.volume, 0) AS entry_price,
            (epoch(mp.resolved_at) - epoch(mp.first_trade)) / 3600.0 AS hold_hours,
            CASE WHEN mp.yes_won = 1 THEN 1 WHEN mp.yes_won = 0 THEN 0 ELSE NULL END AS correct,
            CASE
                WHEN (yed.price_x_vol / NULLIF(yed.volume, 0)) < 0.30 THEN 'longshot'
                WHEN (yed.price_x_vol / NULLIF(yed.volume, 0)) < 0.85 THEN 'mid'
                ELSE 'sure_thing'
            END AS price_regime,
            mt.tag
        FROM maker_positions mp
        INNER JOIN yes_entry_data yed
            ON mp.trader = yed.trader AND mp.condition_id = yed.condition_id
        LEFT JOIN _v2_gambling_markets gm ON mp.condition_id = gm.condition_id
        LEFT JOIN _v2_market_tags mt ON mp.condition_id = mt.condition_id
        WHERE
            gm.condition_id IS NULL
            AND mt.condition_id IS NOT NULL
            AND mp.position = 'YES'
            AND mp.first_trade  >= TIMESTAMP '{start}'    -- CAUSAL: entry in test month
            AND mp.first_trade  <  TIMESTAMP '{end}'
            AND mp.resolved_at IS NOT NULL
            AND mp.yes_won IS NOT NULL
            AND yed.volume > 0
            AND (yed.price_x_vol / NULLIF(yed.volume, 0)) >= 0.01
            AND (yed.price_x_vol / NULLIF(yed.volume, 0)) <  1.00
    """)
    n_pos = con.execute(f"SELECT count(*) FROM _v2_test_{label}").fetchone()[0]
    n_mkt = con.execute(f"SELECT count(DISTINCT condition_id) FROM _v2_test_{label}").fetchone()[0]
    log(f"  {label}: {n_pos:,} positions across {n_mkt:,} markets")

# ---------------------------------------------------------------------------
# Step 8: Per-month base rates
# ---------------------------------------------------------------------------
log("\n[8] Computing per-month base rates...")

month_base_rates = {}
for _, _, label in TEST_MONTHS:
    overall = con.execute(f"""
        SELECT AVG(CAST(correct AS DOUBLE))
        FROM _v2_test_{label} WHERE correct IS NOT NULL
    """).fetchone()[0] or 0.0
    regime = con.execute(f"""
        SELECT price_regime, AVG(CAST(correct AS DOUBLE))
        FROM _v2_test_{label}
        WHERE correct IS NOT NULL
        GROUP BY price_regime
    """).fetchall()
    regime_dict = {r[0]: r[1] or 0.0 for r in regime}
    month_base_rates[label] = {"overall": overall, "regime": regime_dict}
    log(f"  {label}: overall={overall*100:.1f}%  "
        f"longshot={regime_dict.get('longshot',0)*100:.1f}%  "
        f"mid={regime_dict.get('mid',0)*100:.1f}%  "
        f"sure={regime_dict.get('sure_thing',0)*100:.1f}%")

# ---------------------------------------------------------------------------
# Step 9: Simulation helpers
# ---------------------------------------------------------------------------

def simulate_copy_month(pool: set[str], label: str) -> dict | None:
    """N=1 copy: first pool trader's entry = signal. Causal fill price."""
    pool_list = ", ".join(f"'{t}'" for t in pool) if pool else ""
    if not pool_list:
        return None

    result = con.execute(f"""
        WITH first_entries AS (
            SELECT condition_id, min(first_trade) AS min_first_trade
            FROM _v2_test_{label}
            WHERE trader IN ({pool_list}) AND correct IS NOT NULL
            GROUP BY condition_id
        ),
        signals AS (
            SELECT
                tp.condition_id,
                fe.min_first_trade AS signal_entry,
                first(tp.entry_price ORDER BY tp.first_trade ASC) AS signal_price,
                any_value(tp.correct) AS correct,
                any_value(tp.price_regime) AS price_regime,
                (epoch(any_value(tp.resolved_at)) - epoch(fe.min_first_trade)) / 3600.0 AS hold_hours
            FROM _v2_test_{label} tp
            JOIN first_entries fe ON tp.condition_id = fe.condition_id
            WHERE tp.trader IN ({pool_list}) AND tp.correct IS NOT NULL
            GROUP BY tp.condition_id, fe.min_first_trade
        )
        SELECT
            count(*) AS n_signals,
            AVG(CAST(correct AS DOUBLE)) AS hr,
            count(*) FILTER (WHERE price_regime = 'longshot') AS n_long,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'longshot') AS hr_long,
            count(*) FILTER (WHERE price_regime = 'mid') AS n_mid,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'mid') AS hr_mid,
            count(*) FILTER (WHERE price_regime = 'sure_thing') AS n_sure,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'sure_thing') AS hr_sure,
            MEDIAN(signal_price) AS median_fill,
            AVG(signal_price) AS avg_fill,
            AVG(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS avg_pnl,
            SUM(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS total_pnl,
            MEDIAN(hold_hours) AS median_hold,
            AVG(hold_hours) AS avg_hold
        FROM signals
    """).fetchone()

    if not result or not result[0]:
        return None
    base = month_base_rates[label]["overall"]
    return _pack_result(result, base, month_base_rates[label]["regime"])


def simulate_consensus_month(pool: set[str], n_thresh: int, label: str) -> dict | None:
    """Consensus N>=n_thresh: fill price = Nth trader's actual entry price (causal).

    Key fix: We rank pool traders by first_trade per market and take the
    Nth-ranked trader's entry_price as signal_price — NOT AVG across all traders.
    """
    pool_list = ", ".join(f"'{t}'" for t in pool) if pool else ""
    if not pool_list:
        return None

    result = con.execute(f"""
        WITH ranked_entries AS (
            -- Rank each pool trader's entry into each market chronologically
            SELECT
                condition_id,
                trader,
                first_trade,
                entry_price,
                correct,
                price_regime,
                resolved_at,
                hold_hours,
                row_number() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS entry_rank,
                count(*) OVER (PARTITION BY condition_id) AS total_pool_entries
            FROM _v2_test_{label}
            WHERE trader IN ({pool_list}) AND correct IS NOT NULL
        ),
        nth_entries AS (
            -- The Nth unique trader's entry = signal trigger
            -- entry_rank = n_thresh → this is the exact signal moment
            SELECT
                condition_id,
                entry_price AS signal_price,      -- CAUSAL: Nth trader's actual price
                first_trade AS signal_time,
                correct,
                price_regime,
                resolved_at,
                (epoch(resolved_at) - epoch(first_trade)) / 3600.0 AS hold_hours
            FROM ranked_entries
            WHERE entry_rank = {n_thresh}          -- exactly the Nth entry
              AND total_pool_entries >= {n_thresh} -- market has enough pool traders
        )
        SELECT
            count(*) AS n_signals,
            AVG(CAST(correct AS DOUBLE)) AS hr,
            count(*) FILTER (WHERE price_regime = 'longshot') AS n_long,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'longshot') AS hr_long,
            count(*) FILTER (WHERE price_regime = 'mid') AS n_mid,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'mid') AS hr_mid,
            count(*) FILTER (WHERE price_regime = 'sure_thing') AS n_sure,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'sure_thing') AS hr_sure,
            MEDIAN(signal_price) AS median_fill,
            AVG(signal_price) AS avg_fill,
            AVG(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS avg_pnl,
            SUM(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS total_pnl,
            MEDIAN(hold_hours) AS median_hold,
            AVG(hold_hours) AS avg_hold
        FROM nth_entries
    """).fetchone()

    if not result or not result[0]:
        return None
    base = month_base_rates[label]["overall"]
    return _pack_result(result, base, month_base_rates[label]["regime"])


def simulate_inplay_month(pool: set[str], n_thresh: int, label: str) -> dict | None:
    """In-play N>=n_thresh: only positions with hold_hours < 4h. Causal Nth price."""
    pool_list = ", ".join(f"'{t}'" for t in pool) if pool else ""
    if not pool_list:
        return None

    result = con.execute(f"""
        WITH ranked_entries AS (
            SELECT
                condition_id,
                trader,
                first_trade,
                entry_price,
                correct,
                price_regime,
                resolved_at,
                hold_hours,
                row_number() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS entry_rank,
                count(*) OVER (PARTITION BY condition_id) AS total_pool_entries
            FROM _v2_test_{label}
            WHERE trader IN ({pool_list})
              AND correct IS NOT NULL
              AND hold_hours < 4.0         -- in-play filter on position hold time
        ),
        nth_entries AS (
            SELECT
                condition_id,
                entry_price AS signal_price,
                first_trade AS signal_time,
                correct,
                price_regime,
                resolved_at,
                (epoch(resolved_at) - epoch(first_trade)) / 3600.0 AS hold_hours
            FROM ranked_entries
            WHERE entry_rank = {n_thresh}
              AND total_pool_entries >= {n_thresh}
        )
        SELECT
            count(*) AS n_signals,
            AVG(CAST(correct AS DOUBLE)) AS hr,
            count(*) FILTER (WHERE price_regime = 'longshot') AS n_long,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'longshot') AS hr_long,
            count(*) FILTER (WHERE price_regime = 'mid') AS n_mid,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'mid') AS hr_mid,
            count(*) FILTER (WHERE price_regime = 'sure_thing') AS n_sure,
            AVG(CAST(correct AS DOUBLE)) FILTER (WHERE price_regime = 'sure_thing') AS hr_sure,
            MEDIAN(signal_price) AS median_fill,
            AVG(signal_price) AS avg_fill,
            AVG(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS avg_pnl,
            SUM(CASE WHEN correct=1 THEN (100.0/NULLIF(signal_price,0)-100.0) ELSE -100.0 END) AS total_pnl,
            MEDIAN(hold_hours) AS median_hold,
            AVG(hold_hours) AS avg_hold
        FROM nth_entries
    """).fetchone()

    if not result or not result[0]:
        return None
    base = month_base_rates[label]["overall"]
    return _pack_result(result, base, month_base_rates[label]["regime"])


def _pack_result(result: tuple, base: float, regime_base: dict) -> dict:
    n_signals = int(result[0] or 0)
    hr        = float(result[1] or 0)
    n_long    = int(result[2] or 0)
    hr_long   = float(result[3] or 0)
    n_mid     = int(result[4] or 0)
    hr_mid    = float(result[5] or 0)
    n_sure    = int(result[6] or 0)
    hr_sure   = float(result[7] or 0)
    med_fill  = float(result[8] or 0)
    avg_fill  = float(result[9] or 0)
    avg_pnl   = float(result[10] or 0)
    total_pnl = float(result[11] or 0)
    med_hold  = float(result[12] or 0)
    avg_hold  = float(result[13] or 0)

    excess_hr  = hr - base
    hold_days  = max(med_hold / 24.0, 0.1)
    cs         = (excess_hr * 100) * abs(avg_pnl) / hold_days

    return {
        "n_signals":        n_signals,
        "hr":               round(hr, 4),
        "excess_hr":        round(excess_hr, 4),
        "base_rate":        round(base, 4),
        "n_longshot":       n_long,
        "hr_longshot":      round(hr_long, 4),
        "excess_hr_long":   round(hr_long - regime_base.get("longshot", base), 4),
        "n_mid":            n_mid,
        "hr_mid":           round(hr_mid, 4),
        "excess_hr_mid":    round(hr_mid - regime_base.get("mid", base), 4),
        "n_sure":           n_sure,
        "hr_sure":          round(hr_sure, 4),
        "excess_hr_sure":   round(hr_sure - regime_base.get("sure_thing", base), 4),
        "median_fill":      round(med_fill, 4),
        "avg_fill":         round(avg_fill, 4),
        "avg_pnl":          round(avg_pnl, 2),
        "total_pnl":        round(total_pnl, 2),
        "median_hold_h":    round(med_hold, 2),
        "avg_hold_h":       round(avg_hold, 2),
        "compounding_score": round(cs, 3),
    }


# ---------------------------------------------------------------------------
# Step 10: Run all strategies across 3 months
# ---------------------------------------------------------------------------
log("\n[10] Running sweeps across 3 months...")

all_results = {}   # strategy_key -> {month_label -> result_dict}

def run_strategy(name: str, fn, pool: set[str], **kwargs) -> None:
    """Run a strategy function across all test months, store results."""
    if not pool:
        return
    all_results[name] = {}
    for _, _, label in TEST_MONTHS:
        r = fn(pool=pool, label=label, **kwargs)
        if r:
            all_results[name][label] = r

# --- Part A: Copy N=1 ---
log("\n--- Part A: Copy N=1 ---")
for k in COPY_K_VALUES:
    key = f"edge_copy_k{k}"
    run_strategy(key, simulate_copy_month, edge_pools[k])
    for _, _, lbl in TEST_MONTHS:
        r = all_results.get(key, {}).get(lbl)
        if r:
            log(f"  {key} {lbl}: sigs={r['n_signals']:4d} HR={r['hr']*100:.1f}% "
                f"(+{r['excess_hr']*100:.1f}pp) PnL=${r['total_pnl']:,.0f}")

# HR baseline at selected K values for comparison
for k in [25, 100]:
    key = f"hr_copy_k{k}"
    run_strategy(key, simulate_copy_month, hr_pools[k])
    for _, _, lbl in TEST_MONTHS:
        r = all_results.get(key, {}).get(lbl)
        if r:
            log(f"  {key} {lbl}: sigs={r['n_signals']:4d} HR={r['hr']*100:.1f}% "
                f"(+{r['excess_hr']*100:.1f}pp) PnL=${r['total_pnl']:,.0f}")

# --- Part B: Consensus N>=2 ---
log("\n--- Part B: Consensus (N>=2, causal Nth price) ---")
for k in POOL_K_VALUES:
    for n in N_VALUES:
        key = f"edge_consensus_k{k}_n{n}"
        run_strategy(key, simulate_consensus_month, edge_pools[k], n_thresh=n)
        for _, _, lbl in TEST_MONTHS:
            r = all_results.get(key, {}).get(lbl)
            if r:
                log(f"  {key} {lbl}: sigs={r['n_signals']:4d} HR={r['hr']*100:.1f}% "
                    f"(+{r['excess_hr']*100:.1f}pp) PnL=${r['total_pnl']:,.0f} "
                    f"fill={r['avg_fill']:.3f}")

# --- Part C: In-Play Track ---
log("\n--- Part C: In-Play (hold < 4h) ---")
for k in [10, 25, 50, 100]:
    key = f"inplay_copy_k{k}_n1"
    run_strategy(key, simulate_inplay_month, inplay_pools[k], n_thresh=1)
    for _, _, lbl in TEST_MONTHS:
        r = all_results.get(key, {}).get(lbl)
        if r:
            log(f"  {key} {lbl}: sigs={r['n_signals']:4d} HR={r['hr']*100:.1f}% "
                f"(+{r['excess_hr']*100:.1f}pp) PnL=${r['total_pnl']:,.0f} "
                f"sure={r['n_sure']}/{r['hr_sure']*100:.0f}%")

# --- Part D: In-Play from General Edge Pool ---
log("\n--- Part D: General Edge Pool — In-Play subset ---")
for k in [25, 50, 100]:
    key = f"edge_inplay_k{k}_n1"
    run_strategy(key, simulate_inplay_month, edge_pools[k], n_thresh=1)
    for _, _, lbl in TEST_MONTHS:
        r = all_results.get(key, {}).get(lbl)
        if r:
            log(f"  {key} {lbl}: sigs={r['n_signals']:4d} HR={r['hr']*100:.1f}% "
                f"(+{r['excess_hr']*100:.1f}pp) PnL=${r['total_pnl']:,.0f}")

# ---------------------------------------------------------------------------
# Step 11: Aggregate across months (mean, variance)
# ---------------------------------------------------------------------------
log("\n[11] Aggregating across months...")

def aggregate_months(strategy_key: str) -> dict:
    """Compute mean and variance of PnL / HR / signals across test months."""
    month_data = all_results.get(strategy_key, {})
    if not month_data:
        return {}
    months = list(month_data.values())
    fields = ["n_signals", "hr", "excess_hr", "total_pnl", "avg_pnl",
              "median_fill", "median_hold_h", "compounding_score"]
    agg = {}
    for f in fields:
        vals = [m[f] for m in months if f in m]
        if vals:
            avg = sum(vals) / len(vals)
            var = sum((v - avg) ** 2 for v in vals) / len(vals) if len(vals) > 1 else 0
            agg[f"{f}_mean"] = round(avg, 4)
            agg[f"{f}_std"]  = round(var ** 0.5, 4)
            agg[f"{f}_min"]  = round(min(vals), 4)
            agg[f"{f}_max"]  = round(max(vals), 4)
    agg["n_months"] = len(months)
    agg["per_month"] = {lbl: r for lbl, r in month_data.items()}
    return agg

aggregated = {key: aggregate_months(key) for key in all_results}

# ---------------------------------------------------------------------------
# Step 12: Head-to-Head Summary (3-month averages)
# ---------------------------------------------------------------------------
log("\n[12] Head-to-head summary (3-month averages)...")
log("=" * 100)
log(f"{'Strategy':<38} {'K':>5} {'N':>3} {'AvgHR%':>8} {'ExcHR':>8} {'AvgPnL/mo':>12} "
    f"{'PnL_std':>10} {'AvgSigs':>8} {'AvgHold':>9} {'AvgCS':>8}")
log("-" * 100)

SUMMARY_STRATEGIES = [
    ("edge_copy_k5",         5,   1, "Edge Copy"),
    ("edge_copy_k10",       10,   1, "Edge Copy"),
    ("edge_copy_k25",       25,   1, "Edge Copy"),
    ("edge_copy_k50",       50,   1, "Edge Copy"),
    ("edge_copy_k100",     100,   1, "Edge Copy"),
    ("hr_copy_k25",         25,   1, "HR Baseline"),
    ("hr_copy_k100",       100,   1, "HR Baseline"),
    ("edge_consensus_k50_n1",  50,  1, "Consensus"),
    ("edge_consensus_k50_n2",  50,  2, "Consensus"),
    ("edge_consensus_k50_n3",  50,  3, "Consensus"),
    ("edge_consensus_k100_n1",100,  1, "Consensus"),
    ("edge_consensus_k100_n2",100,  2, "Consensus"),
    ("edge_consensus_k100_n3",100,  3, "Consensus"),
    ("edge_consensus_k200_n1",200,  1, "Consensus"),
    ("edge_consensus_k200_n2",200,  2, "Consensus"),
    ("edge_consensus_k200_n3",200,  3, "Consensus"),
    ("inplay_copy_k10_n1",  10,   1, "InPlay"),
    ("inplay_copy_k25_n1",  25,   1, "InPlay"),
    ("inplay_copy_k50_n1",  50,   1, "InPlay"),
    ("inplay_copy_k100_n1",100,   1, "InPlay"),
    ("edge_inplay_k25_n1",  25,   1, "EdgeInPlay"),
    ("edge_inplay_k50_n1",  50,   1, "EdgeInPlay"),
    ("edge_inplay_k100_n1",100,   1, "EdgeInPlay"),
]

summary_rows = []
for key, k, n, stype in SUMMARY_STRATEGIES:
    agg = aggregated.get(key, {})
    if not agg:
        continue
    hr_mean    = agg.get("hr_mean", 0)
    exc_mean   = agg.get("excess_hr_mean", 0)
    pnl_mean   = agg.get("total_pnl_mean", 0)
    pnl_std    = agg.get("total_pnl_std", 0)
    sigs_mean  = agg.get("n_signals_mean", 0)
    hold_mean  = agg.get("median_hold_h_mean", 0)
    cs_mean    = agg.get("compounding_score_mean", 0)
    log(f"{key:<38} {k:>5} {n:>3} {hr_mean*100:>7.1f}% {exc_mean*100:>+7.1f}pp "
        f"${pnl_mean:>10,.0f} ±${pnl_std:>8,.0f} {sigs_mean:>8.0f} "
        f"{hold_mean:>8.1f}h {cs_mean:>8.2f}")
    summary_rows.append({
        "strategy": key, "type": stype, "k": k, "n": n,
        "hr_mean": round(hr_mean, 4),
        "excess_hr_mean": round(exc_mean, 4),
        "pnl_mean": round(pnl_mean, 2),
        "pnl_std": round(pnl_std, 2),
        "pnl_min": round(agg.get("total_pnl_min", 0), 2),
        "pnl_max": round(agg.get("total_pnl_max", 0), 2),
        "sigs_mean": round(sigs_mean, 1),
        "hold_mean": round(hold_mean, 2),
        "cs_mean": round(cs_mean, 3),
        "n_months": agg.get("n_months", 0),
        "per_month": agg.get("per_month", {}),
    })

# ---------------------------------------------------------------------------
# Step 13: Save JSON
# ---------------------------------------------------------------------------
log("\n[13] Saving JSON results...")

results_json = {
    "meta": {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "label": "VECTORIZED UPPER BOUNDS (20-40pp tick degradation expected)",
        "train_end": TRAIN_END,
        "test_months": [lbl for _, _, lbl in TEST_MONTHS],
        "n_scored_traders": n_scored,
        "n_edge_pool": len(ranked_edge),
        "n_inplay_pool": len(ranked_inplay),
        "month_base_rates": month_base_rates,
        "bugs_fixed": [
            "Consensus fill price = Nth trader's actual entry price (not AVG over all pool traders)",
            "Elections tag excluded",
            "Training period extended: resolved before 2025-11-01",
        ],
    },
    "summary_rows": summary_rows,
    "aggregated": aggregated,
}

json_path = DISCOVERY_DIR / "copy_vs_pooling_v2_results.json"
with open(json_path, "w") as f:
    json.dump(results_json, f, indent=2)
log(f"JSON saved: {json_path}")

# ---------------------------------------------------------------------------
# Step 14: Markdown Report
# ---------------------------------------------------------------------------
log("\n[14] Writing markdown report...")

months_lbl = [lbl for _, _, lbl in TEST_MONTHS]

def pnl_per_month_str(key: str) -> str:
    """Format PnL per month as compact string."""
    agg = aggregated.get(key, {})
    pm = agg.get("per_month", {})
    parts = []
    for lbl in months_lbl:
        r = pm.get(lbl)
        parts.append(f"${r['total_pnl']:+,.0f}" if r else "N/A")
    return " / ".join(parts)

md = f"""# Copy vs Pooling v2 — 3-Month Analysis with Causal Fill Price

**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Label**: VECTORIZED UPPER BOUNDS (expect 20-40pp tick degradation; more for in-play)
**Training**: resolved before {TRAIN_END}
**Test**: {', '.join(months_lbl)}

---

## Bug Fixes from v1

1. **Consensus fill price (critical)**: Previously used `AVG(entry_price)` across ALL pool traders
   in a market. This is look-ahead bias — traders entering AFTER signal trigger were included.
   **Fix**: Fill price = entry price of the **Nth unique pool trader** (chronologically ordered).
   For N=2 this is the 2nd trader's actual price; for N=1 it's the 1st trader's price.

2. **Elections tag excluded**: Elections tag removed from tag assignment per Round 1 review.

3. **Training period**: Now uses positions resolved before {TRAIN_END}
   (previously used all data before 2026-01-01, overlapping with Nov/Dec test months).

---

## Dataset

| Metric | Value |
|--------|-------|
| Scored traders (≥20 pos, conviction ≥0.50, train period) | **{n_scored:,}** |
| In-play specialists (≥50% in-play) | **{len(ranked_inplay):,}** |
| Test months | **{', '.join(months_lbl)}** |

### Per-Month Base Rates (YES, non-gambling, non-Elections)

| Month | Overall | Longshot | Mid | Sure-thing |
|-------|---------|----------|-----|------------|
"""
for lbl in months_lbl:
    br = month_base_rates[lbl]
    md += (f"| {lbl} | {br['overall']*100:.1f}% "
           f"| {br['regime'].get('longshot',0)*100:.1f}% "
           f"| {br['regime'].get('mid',0)*100:.1f}% "
           f"| {br['regime'].get('sure_thing',0)*100:.1f}% |\n")

md += """
---

## Part A: Edge-Weighted Copy (N=1)

Pool ranked by `bucket_excess_hr × ln(n_positions + 1)`. Fill price = 1st pool trader's actual entry.

| Strategy | K | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals | Avg Hold |
|----------|---|--------|---------------|-------------------|-------------|----------|
"""
for row in summary_rows:
    if row["type"] in ("Edge Copy", "HR Baseline"):
        md += (f"| {row['strategy']:<38} | {row['k']:>5} "
               f"| {row['hr_mean']*100:>6.1f}% | {row['excess_hr_mean']*100:>+6.1f}pp "
               f"| {pnl_per_month_str(row['strategy'])} "
               f"| {row['sigs_mean']:>7.0f} | {row['hold_mean']:>6.1f}h |\n")

md += """
---

## Part B: Consensus Pooling (N>=2, causal Nth price)

Fill price = the Nth pool trader's actual entry price (no look-ahead).

| Strategy | K | N | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals | Avg Hold | Avg Fill |
|----------|---|---|--------|---------------|-------------------|-------------|----------|----------|
"""
for row in summary_rows:
    if row["type"] == "Consensus":
        # Get avg fill price across months
        agg = aggregated.get(row["strategy"], {})
        pm = agg.get("per_month", {})
        fills = [pm[lbl]["avg_fill"] for lbl in months_lbl if lbl in pm]
        avg_fill_str = f"{sum(fills)/len(fills):.3f}" if fills else "N/A"
        md += (f"| {row['strategy']:<38} | {row['k']:>5} | {row['n']:>3} "
               f"| {row['hr_mean']*100:>6.1f}% | {row['excess_hr_mean']*100:>+6.1f}pp "
               f"| {pnl_per_month_str(row['strategy'])} "
               f"| {row['sigs_mean']:>7.0f} | {row['hold_mean']:>6.1f}h | {avg_fill_str} |\n")

md += """
---

## Part C: In-Play Dedicated Track (hold < 4h)

| Strategy | K | HR avg | Excess HR avg | PnL (Nov/Dec/Jan) | Avg Signals |
|----------|---|--------|---------------|-------------------|-------------|
"""
for row in summary_rows:
    if row["type"] in ("InPlay", "EdgeInPlay"):
        md += (f"| {row['strategy']:<38} | {row['k']:>5} "
               f"| {row['hr_mean']*100:>6.1f}% | {row['excess_hr_mean']*100:>+6.1f}pp "
               f"| {pnl_per_month_str(row['strategy'])} "
               f"| {row['sigs_mean']:>7.0f} |\n")

md += """
---

## Part D: Head-to-Head Summary (3-month averages, UPPER BOUNDS)

| Strategy | K | N | HR% | Excess HR | PnL/mo avg | PnL std | CS avg | Sigs/mo | Hold |
|----------|---|---|-----|-----------|-----------|---------|--------|---------|------|
"""
# Top candidates sorted by avg PnL
top_candidates = [
    ("edge_copy_k50",          "Edge Copy",     50,  1),
    ("edge_copy_k100",         "Edge Copy",    100,  1),
    ("hr_copy_k25",            "HR Baseline",   25,  1),
    ("edge_consensus_k200_n2", "Consensus",    200,  2),
    ("edge_consensus_k100_n2", "Consensus",    100,  2),
    ("edge_consensus_k50_n2",  "Consensus",     50,  2),
    ("inplay_copy_k10_n1",     "InPlay",        10,  1),
    ("inplay_copy_k25_n1",     "InPlay",        25,  1),
    ("edge_inplay_k25_n1",     "EdgeInPlay",    25,  1),
    ("edge_inplay_k50_n1",     "EdgeInPlay",    50,  1),
]
for key, stype, k, n in top_candidates:
    row = next((r for r in summary_rows if r["strategy"] == key), None)
    if not row:
        continue
    md += (f"| {key:<38} | {k:>5} | {n:>3} "
           f"| {row['hr_mean']*100:>6.1f}% | {row['excess_hr_mean']*100:>+6.1f}pp "
           f"| ${row['pnl_mean']:>8,.0f} | ±${row['pnl_std']:>6,.0f} "
           f"| {row['cs_mean']:>6.2f} | {row['sigs_mean']:>7.0f} | {row['hold_mean']:>5.1f}h |\n")

md += f"""
---

## Per-Month Detail

### Edge Copy K=50 (best volume strategy)
"""
for lbl in months_lbl:
    r = aggregated.get("edge_copy_k50", {}).get("per_month", {}).get(lbl)
    if r:
        md += (f"- **{lbl}**: {r['n_signals']} signals, HR={r['hr']*100:.1f}% "
               f"(+{r['excess_hr']*100:.1f}pp), PnL=${r['total_pnl']:+,.0f}, "
               f"fill={r['avg_fill']:.3f}, hold={r['median_hold_h']:.1f}h\n")

md += "\n### Consensus K=200 N=2 (best consensus candidate)\n"
for lbl in months_lbl:
    r = aggregated.get("edge_consensus_k200_n2", {}).get("per_month", {}).get(lbl)
    if r:
        md += (f"- **{lbl}**: {r['n_signals']} signals, HR={r['hr']*100:.1f}% "
               f"(+{r['excess_hr']*100:.1f}pp), PnL=${r['total_pnl']:+,.0f}, "
               f"fill={r['avg_fill']:.3f} (Nth trigger price), hold={r['median_hold_h']:.1f}h\n")

md += "\n### In-Play K=25 N=1\n"
for lbl in months_lbl:
    r = aggregated.get("inplay_copy_k25_n1", {}).get("per_month", {}).get(lbl)
    if r:
        md += (f"- **{lbl}**: {r['n_signals']} signals, HR={r['hr']*100:.1f}% "
               f"(+{r['excess_hr']*100:.1f}pp), PnL=${r['total_pnl']:+,.0f}, "
               f"sure={r['n_sure']}({r['hr_sure']*100:.0f}%), hold={r['median_hold_h']:.1f}h\n")

md += f"""
---

## Key Observations

### Fill Price Bug Impact
The consensus fill price fix changes the PnL picture for N>=2 strategies.
v1 used AVG(entry_price) across all pool traders — including traders who entered AFTER
the signal fired. Traders who enter later typically get better (lower) prices on losing
markets and worse (higher) prices on winning markets, creating artificial optimism.
The causal Nth-price should give more conservative PnL estimates.

### 3-Month Stability
PnL variance across months (std/mean ratio) indicates strategy robustness:
- High variance = regime-dependent, may be luck in single month
- Low variance = consistent signal worth validating tick-by-tick

### Recommended Next Steps
1. **Tick-by-tick validation**: Top 3 strategies from this analysis
2. **NO-direction sweep**: Add NO signal analysis (separate task #13)
3. **Pool re-ranking**: Monthly re-rank using trailing 6-month data

---

## Limitations (CRITICAL)

1. **VECTORIZED UPPER BOUNDS**: 20-40pp optimistic vs tick-by-tick
2. **In-play gap LARGER**: 50-60pp expected for in-play strategies (latency)
3. **Fill approximation**: Nth trader's recorded entry_price, not actual market fill
4. **YES-only**: NO direction not included (Task #13)
5. **Single-market, no capital constraint**: Infinite capital assumed

*All results are UPPER BOUNDS. Tick validation required before any deployment decision.*
"""

md_path = DISCOVERY_DIR / "copy_vs_pooling_v2_results.md"
with open(md_path, "w") as f:
    f.write(md)
log(f"Markdown saved: {md_path}")

log(f"\n{'='*70}")
log("COMPLETE")
log(f"{'='*70}")
log(f"\nOutputs:")
log(f"  JSON: {json_path}")
log(f"  MD:   {md_path}")
log(f"  Log:  {LOG_PATH}")
