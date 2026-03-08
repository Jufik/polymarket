"""
Calibration-Filtered Consensus Pool (Strategy A — scorecard-v2-strategies)

Goal: Test whether filtering the consensus pool by calibration_gap improves
      performance vs the prior Politics NO K=50 approach (+6.2pp excess).

Calibration_gap = hit_rate - avg_entry_price (positive = beats implied probability)

Pool variants:
  A: Top-K by excess_hr only (baseline, replicates v1 K=50 Politics NO)
  B: Top-K by excess_hr, exclude calibration_gap < 0 (strict)
  C: Top-K by excess_hr, exclude calibration_gap < -0.05 (lenient)
  D: Top-K by excess_hr × (1 + calibration_gap) (calibration-weighted ranking)

Sweep: K = 25, 50, 100, 200 × N = 2, 3, 5 × direction = vol-weighted
Tags: Politics, Sports (>=4h hold), Crypto, Elections
Train: resolved_at < 2025-07-01
Test:  resolved_at >= 2025-07-01

CRITICAL RULES:
- Aggregate to MARKET level before computing any HR metric
- YES HR vs YES base rate, NO HR vs NO base rate (direction decomposition)
- Sports: hold_hours >= 4 filter to exclude in-play signals
- Gambling exclusion: slug NOT LIKE '%updown%' AND slug NOT LIKE '%up-or-down%'
- first_trade >= test_start (only copyable entries)
"""

import json
import sys
from datetime import datetime

TRAIN_END = "2025-07-01"
TEST_START = "2025-07-01"
# Sports has 161K test markets — include but note it's slow
# Elections has only 66 training markets (likely no traders will qualify)
TARGET_TAGS = ["Politics", "Crypto", "Elections", "Sports"]
LOG_PATH = "/mnt/nvme/git/polymarket/polymarket/tmp/calibration_pool.log"
RESULTS_PATH = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/discovery/calibration_filtered_pool_raw.json"

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")
from research.db import db as get_db


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# Clear log
with open(LOG_PATH, "w") as f:
    pass

log("=" * 60)
log("Calibration-Filtered Consensus Pool Sweep")
log("=" * 60)

con = get_db().con
log("DuckDB connection established")

# ─── Step 1: Gambling exclusion macro ────────────────────────────────────────
log("Step 1: Gambling exclusion macro...")
con.execute("""
CREATE OR REPLACE MACRO is_gambling(slug) AS (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
);
""")

# ─── Step 2: Market-tag lookup (non-gambling, target tags only) ───────────────
log("Step 2: Market-tag lookup...")
target_tags_sql = ", ".join(f"'{t}'" for t in TARGET_TAGS)
con.execute(f"""
CREATE OR REPLACE TABLE _cv2_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling(m.slug)
  AND et.label IN ({target_tags_sql})
GROUP BY m.condition_id, m.slug;
""")
n_mkts = con.execute("SELECT count(*) FROM _cv2_market_tags").fetchone()[0]
log(f"  Non-gambling markets in target tags: {n_mkts:,}")

# ─── Step 3: Tag base rates (YES wins) — TRAINING period ────────────────────
log("Step 3: Training-period tag base rates (YES wins)...")
con.execute(f"""
CREATE OR REPLACE TABLE _cv2_tag_br AS
SELECT
    mt.primary_tag AS tag,
    count(DISTINCT p.condition_id) AS n_markets,
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    1.0 - avg(CAST(p.yes_won AS DOUBLE)) AS no_base_rate
FROM maker_positions p
JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
GROUP BY mt.primary_tag
HAVING count(DISTINCT p.condition_id) >= 20;
""")
tag_brs = con.execute("SELECT tag, n_markets, yes_base_rate, no_base_rate FROM _cv2_tag_br ORDER BY tag").fetchall()
log("  Training base rates:")
for tag, n, yes_br, no_br in tag_brs:
    log(f"    {tag}: YES_BR={yes_br:.3f}, NO_BR={no_br:.3f} ({n:,} markets)")

# ─── Step 4: Test-period tag base rates (for excess reporting) ───────────────
log("Step 4: Test-period tag base rates...")
con.execute(f"""
CREATE OR REPLACE TABLE _cv2_tag_br_test AS
SELECT
    mt.primary_tag AS tag,
    count(DISTINCT p.condition_id) AS n_markets,
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    1.0 - avg(CAST(p.yes_won AS DOUBLE)) AS no_base_rate
FROM maker_positions p
JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
GROUP BY mt.primary_tag;
""")
tag_brs_test = con.execute("SELECT tag, n_markets, yes_base_rate, no_base_rate FROM _cv2_tag_br_test ORDER BY tag").fetchall()
log("  Test base rates:")
for tag, n, yes_br, no_br in tag_brs_test:
    log(f"    {tag}: YES_BR={yes_br:.3f}, NO_BR={no_br:.3f} ({n:,} markets)")

# ─── Step 5: Entry prices from yes_entry_data ────────────────────────────────
log("Step 5: Join entry prices from yes_entry_data...")
con.execute("""
CREATE OR REPLACE TABLE _cv2_entry_prices AS
SELECT
    trader,
    condition_id,
    CASE WHEN volume > 0 THEN price_x_vol / volume ELSE NULL END AS entry_price
FROM yes_entry_data
WHERE volume > 0;
""")
n_ep = con.execute("SELECT count(*) FROM _cv2_entry_prices").fetchone()[0]
log(f"  Entry price records: {n_ep:,}")

# ─── Step 6: Trader training scorecard ───────────────────────────────────────
log("Step 6: Trader training scorecard (YES positions, training period)...")
con.execute(f"""
CREATE OR REPLACE TABLE _cv2_scorecard AS
WITH base AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        p.condition_id,
        CAST(p.yes_won AS DOUBLE) AS correct,
        CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE NULL END AS conviction,
        ep.entry_price
    FROM maker_positions p
    JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
    LEFT JOIN _cv2_entry_prices ep ON p.trader = ep.trader AND p.condition_id = ep.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0
      AND p.net_usd IS NOT NULL
),
-- Also get NO positions for calibration on NO side
base_no AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        p.condition_id,
        CAST(p.yes_won AS DOUBLE) AS yes_won,
        -- NO position correct when YES loses
        CAST(1.0 - p.yes_won AS DOUBLE) AS correct,
        CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE NULL END AS conviction
    FROM maker_positions p
    JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.position = 'NO'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0
      AND p.net_usd IS NOT NULL
),
-- YES-side stats with entry price calibration
yes_stats AS (
    SELECT
        trader, tag,
        count(DISTINCT condition_id) AS n_yes_markets,
        avg(correct) AS yes_hit_rate,
        avg(CASE WHEN conviction IS NOT NULL THEN conviction END) AS avg_conviction,
        avg(CASE WHEN entry_price IS NOT NULL THEN entry_price END) AS avg_entry_price,
        -- calibration_gap: positive = trader beats implied probability
        avg(CASE WHEN entry_price IS NOT NULL THEN correct - entry_price ELSE NULL END) AS calibration_gap_yes
    FROM base
    GROUP BY trader, tag
    HAVING count(DISTINCT condition_id) >= 10
       AND avg(CASE WHEN conviction IS NOT NULL THEN conviction END) >= 0.90  -- exclude market makers
),
-- NO-side stats
no_stats AS (
    SELECT
        trader, tag,
        count(DISTINCT condition_id) AS n_no_markets,
        avg(correct) AS no_hit_rate
    FROM base_no
    GROUP BY trader, tag
    HAVING count(DISTINCT condition_id) >= 5
)
SELECT
    ys.*,
    ns.n_no_markets,
    ns.no_hit_rate,
    -- Total positions across both directions
    ys.n_yes_markets + COALESCE(ns.n_no_markets, 0) AS n_total_markets,
    br.yes_base_rate AS tag_yes_base_rate,
    -- Excess HR: YES HR vs YES base rate
    (ys.yes_hit_rate - br.yes_base_rate) AS excess_hr_yes,
    -- Primary excess_hr for ranking: YES excess (main signal)
    (ys.yes_hit_rate - br.yes_base_rate) AS excess_hr,
    -- Calibration-weighted ranking score
    (ys.yes_hit_rate - br.yes_base_rate) * (1.0 + COALESCE(ys.calibration_gap_yes, 0.0)) AS calib_weighted_score
FROM yes_stats ys
JOIN _cv2_tag_br br ON ys.tag = br.tag
LEFT JOIN no_stats ns ON ys.trader = ns.trader AND ys.tag = ns.tag
WHERE ys.n_yes_markets >= 20;   -- minimum 20 YES positions per tag
""")
n_sc = con.execute("SELECT count(*) FROM _cv2_scorecard").fetchone()[0]
log(f"  Scorecard records (trader-tag): {n_sc:,}")

# Per-tag scorecard summary
sc_by_tag = con.execute("""
SELECT tag,
    count(*) AS n_trader_tags,
    count(DISTINCT trader) AS n_traders,
    median(excess_hr) AS median_excess_hr,
    median(calibration_gap_yes) AS median_calib_gap,
    median(avg_entry_price) AS median_entry_price
FROM _cv2_scorecard
GROUP BY tag
ORDER BY n_traders DESC
""").fetchall()
log("  Scorecard by tag:")
for r in sc_by_tag:
    log(f"    {r[0]}: {r[2]:,} traders, excess_hr={r[3]:+.3f}, calib_gap={r[4]:+.3f}, entry_price={r[5]:.3f}")

# ─── Step 7: Stability bonus (window-based consistency) ──────────────────────
log("Step 7: Stability bonus (60-day windows)...")
con.execute(f"""
CREATE OR REPLACE TABLE _cv2_stability AS
WITH base AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        CAST(p.yes_won AS DOUBLE) AS correct,
        CAST(epoch_ms(CAST(p.resolved_at AS TIMESTAMP)) / 5184000000 AS BIGINT) AS window_id
    FROM maker_positions p
    JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND p.volume > 0
),
window_stats AS (
    SELECT trader, tag, window_id,
           count(*) AS n_pos,
           avg(correct) AS window_hr
    FROM base
    GROUP BY trader, tag, window_id
    HAVING n_pos >= 3
),
stability AS (
    SELECT trader, tag,
           count(*) AS n_windows,
           avg(window_hr) AS mean_window_hr,
           CASE WHEN count(*) >= 2
                THEN (avg(window_hr) / (coalesce(stddev(window_hr), 0.0) + 0.05))
                     * least(ln(1.0 + count(*)), 1.8)
                ELSE 0.0
           END AS stability_score
    FROM window_stats
    GROUP BY trader, tag
)
SELECT * FROM stability;
""")
log(f"  Stability records: {con.execute('SELECT count(*) FROM _cv2_stability').fetchone()[0]:,}")

# ─── Step 8: Build pool variants (A, B, C, D) for each K ────────────────────
log("Step 8: Build pool variants...")

def build_pool(con, pool_id: str, tag: str, k: int, filter_condition: str, rank_expr: str) -> str:
    """Build a pool table and return table name."""
    table_name = f"_cv2_pool_{pool_id}_{tag.lower()}_{k}"
    con.execute(f"""
    CREATE OR REPLACE TABLE {table_name} AS
    WITH eligible AS (
        SELECT sc.trader, sc.tag, sc.excess_hr, sc.calibration_gap_yes,
               sc.calib_weighted_score, sc.avg_entry_price, sc.yes_hit_rate,
               sc.avg_conviction
        FROM _cv2_scorecard sc
        WHERE sc.tag = '{tag}'
          {filter_condition}
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (ORDER BY {rank_expr} DESC) AS rn
        FROM eligible
    )
    SELECT * FROM ranked WHERE rn <= {k};
    """)
    n = con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
    return table_name, n


# ─── Step 9: Consensus sweep function ────────────────────────────────────────
def run_consensus_sweep(
    con, pool_table: str, tag: str, n_min: int, hold_hours_min: int = 0
) -> dict:
    """
    Compute vol-weighted consensus signals for test period.
    Returns market-level HR, signal count, direction decomposition.
    """
    # Sports: apply 4h hold minimum to exclude in-play
    hold_filter = f"AND date_diff('hour', signal_entry, resolved_at) >= {hold_hours_min}" if hold_hours_min > 0 else ""

    try:
        result = con.execute(f"""
        WITH pool_traders AS (
            SELECT trader FROM {pool_table}
        ),
        -- All test-period positions for pool traders in this tag
        test_positions AS (
            SELECT
                p.trader,
                p.condition_id,
                p.position,
                CAST(p.yes_won AS DOUBLE) AS yes_won,
                CAST(p.first_trade AS TIMESTAMP) AS first_trade_ts,
                CAST(p.resolved_at AS TIMESTAMP) AS resolved_at_ts,
                -- Entry price proxy
                CASE WHEN p.volume > 0 AND (
                    (p.position = 'YES' AND abs(p.net_yes) > 0.01)
                    OR (p.position = 'NO' AND abs(p.net_no) > 0.01)
                ) THEN abs(p.net_usd) / NULLIF(
                    CASE WHEN p.position = 'YES' THEN abs(p.net_yes)
                         ELSE abs(p.net_no) END, 0
                ) ELSE NULL END AS entry_price_proxy,
                abs(p.net_usd) AS abs_net_usd
            FROM maker_positions p
            JOIN _cv2_market_tags mt ON p.condition_id = mt.condition_id
            JOIN pool_traders pt ON p.trader = pt.trader
            WHERE mt.primary_tag = '{tag}'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'   -- only copyable
              AND p.volume > 0
        ),
        -- Vol-weighted direction per market: sum(abs_net_usd * sign) where YES=+1, NO=-1
        -- Signal fires at max(first_trade) — consensus trigger (Nth trader enters)
        market_consensus AS (
            SELECT
                condition_id,
                first(yes_won) AS yes_won,
                first(resolved_at_ts) AS resolved_at,
                max(first_trade_ts) AS signal_entry,   -- consensus trigger time
                count(DISTINCT trader) AS n_traders,
                -- Vol-weighted direction
                sum(CASE WHEN position = 'YES' THEN abs_net_usd
                         ELSE -abs_net_usd END) AS vol_direction,
                sum(abs_net_usd) AS total_vol,
                -- Volume-weighted confidence (abs of vol_direction / total_vol)
                abs(sum(CASE WHEN position = 'YES' THEN abs_net_usd
                              ELSE -abs_net_usd END)) / NULLIF(sum(abs_net_usd), 0) AS vol_confidence
            FROM test_positions
            GROUP BY condition_id
            HAVING count(DISTINCT trader) >= {n_min}
        ),
        -- Determine signal direction and correctness
        signals AS (
            SELECT
                condition_id,
                yes_won,
                signal_entry,
                resolved_at,
                n_traders,
                vol_direction,
                vol_confidence,
                date_diff('hour', signal_entry, resolved_at) AS hold_hours,
                -- Signal direction: YES when vol_direction > 0
                CASE WHEN vol_direction > 0 THEN 'YES' ELSE 'NO' END AS signal_direction,
                -- Correct when direction matches outcome
                CASE WHEN vol_direction > 0 THEN CAST(yes_won AS DOUBLE)
                     ELSE 1.0 - CAST(yes_won AS DOUBLE)
                END AS correct
            FROM market_consensus
            WHERE date_diff('hour', signal_entry, resolved_at) >= 0
              {hold_filter}
        )
        SELECT
            count(*) AS n_signals,
            avg(correct) AS hit_rate,
            median(hold_hours) / 24.0 AS median_hold_days,
            median(n_traders) AS median_n_traders,
            -- Direction decomposition
            sum(CASE WHEN signal_direction = 'YES' THEN 1 ELSE 0 END) AS n_yes_signals,
            sum(CASE WHEN signal_direction = 'NO' THEN 1 ELSE 0 END) AS n_no_signals,
            avg(CASE WHEN signal_direction = 'YES' THEN correct END) AS yes_hr,
            avg(CASE WHEN signal_direction = 'NO' THEN correct END) AS no_hr
        FROM signals
        """).fetchone()

        return {
            "n_signals": result[0] or 0,
            "hit_rate": round(result[1], 4) if result[1] is not None else None,
            "median_hold_days": round(result[2], 2) if result[2] is not None else None,
            "median_n_traders": result[3],
            "n_yes": result[4] or 0,
            "n_no": result[5] or 0,
            "yes_hr": round(result[6], 4) if result[6] is not None else None,
            "no_hr": round(result[7], 4) if result[7] is not None else None,
        }
    except Exception as e:
        log(f"    ERROR in consensus sweep: {e}")
        return {"error": str(e)}


# ─── Step 10: Main sweep ──────────────────────────────────────────────────────
log("Step 10: Running main sweep...")

# Pool variant definitions
POOL_VARIANTS = {
    "A": {
        "desc": "Top-K by excess_hr (baseline)",
        "filter": "",
        "rank": "excess_hr",
    },
    "B": {
        "desc": "Top-K by excess_hr, exclude calibration_gap < 0",
        "filter": "AND calibration_gap_yes >= 0.0",
        "rank": "excess_hr",
    },
    "C": {
        "desc": "Top-K by excess_hr, exclude calibration_gap < -5pp",
        "filter": "AND calibration_gap_yes >= -0.05",
        "rank": "excess_hr",
    },
    "D": {
        "desc": "Top-K by excess_hr × (1 + calibration_gap)",
        "filter": "",
        "rank": "calib_weighted_score",
    },
}

KS = [25, 50, 100, 200]
NS = [2, 3, 5]

# Sports need 4h hold to filter in-play; others 0h
HOLD_HOURS = {"Sports": 4, "Politics": 0, "Crypto": 0, "Elections": 0}

results = {
    "metadata": {
        "train_end": TRAIN_END,
        "test_start": TEST_START,
        "target_tags": TARGET_TAGS,
        "pool_variants": {k: v["desc"] for k, v in POOL_VARIANTS.items()},
        "k_values": KS,
        "n_values": NS,
    },
    "tag_base_rates_train": {r[0]: {"yes": round(r[2], 4), "no": round(r[3], 4)} for r in tag_brs},
    "tag_base_rates_test": {r[0]: {"yes": round(r[2], 4), "no": round(r[3], 4)} for r in tag_brs_test},
    "scorecard_summary": [
        {"tag": r[0], "n_traders": r[2], "median_excess_hr": round(r[3], 4),
         "median_calib_gap": round(r[4], 4), "median_entry_price": round(r[5], 4)}
        for r in sc_by_tag
    ],
    "sweep_results": [],
}

for tag in TARGET_TAGS:
    hold_h = HOLD_HOURS.get(tag, 0)
    hold_label = f">={hold_h}h hold" if hold_h > 0 else "no hold filter"
    log(f"\n  Tag: {tag} ({hold_label})")

    # Test base rates for excess computation
    test_br_row = {r[0]: r for r in tag_brs_test}
    train_br_row = {r[0]: r for r in tag_brs}

    if tag not in test_br_row:
        log(f"    SKIP: no test data for {tag}")
        continue

    test_yes_br = test_br_row[tag][2]
    test_no_br = test_br_row[tag][3]

    for pool_id, pool_def in POOL_VARIANTS.items():
        for k in KS:
            # Build pool
            pool_table, pool_size = build_pool(
                con, pool_id, tag, k,
                pool_def["filter"], pool_def["rank"]
            )
            if pool_size == 0:
                log(f"    Pool {pool_id} K={k}: EMPTY (0 traders after filter)")
                continue

            log(f"    Pool {pool_id} K={k}: {pool_size} traders")

            for n_min in NS:
                sweep = run_consensus_sweep(con, pool_table, tag, n_min, hold_h)

                # Compute excess HR vs test base rates
                yes_excess = round(sweep["yes_hr"] - test_yes_br, 4) if sweep.get("yes_hr") is not None else None
                no_excess = round(sweep["no_hr"] - test_no_br, 4) if sweep.get("no_hr") is not None else None
                overall_excess = None
                if sweep.get("n_signals", 0) > 0 and sweep.get("hit_rate") is not None:
                    # Weighted excess across directions
                    n_yes = sweep.get("n_yes", 0)
                    n_no = sweep.get("n_no", 0)
                    n_total = n_yes + n_no
                    if n_total > 0 and yes_excess is not None and no_excess is not None:
                        overall_excess = round(
                            (n_yes * yes_excess + n_no * no_excess) / n_total, 4
                        )

                row = {
                    "tag": tag,
                    "pool": pool_id,
                    "pool_desc": pool_def["desc"],
                    "k": k,
                    "actual_pool_size": pool_size,
                    "n_min": n_min,
                    "hold_filter_hours": hold_h,
                    **sweep,
                    "test_yes_base_rate": round(test_yes_br, 4),
                    "test_no_base_rate": round(test_no_br, 4),
                    "yes_excess_hr": yes_excess,
                    "no_excess_hr": no_excess,
                    "overall_excess_hr": overall_excess,
                }
                results["sweep_results"].append(row)

                n_sig = sweep.get("n_signals", 0)
                hr = sweep.get("hit_rate")
                if n_sig > 0 and hr is not None:
                    yes_exc_str = f"{yes_excess:+.3f}pp" if yes_excess is not None else "n/a"
                    no_exc_str = f"{no_excess:+.3f}pp" if no_excess is not None else "n/a"
                    log(f"      N>={n_min}: {n_sig} sigs, HR={hr:.3f}, "
                        f"YES({sweep.get('n_yes',0)}): {sweep.get('yes_hr')!r} {yes_exc_str}, "
                        f"NO({sweep.get('n_no',0)}): {sweep.get('no_hr')!r} {no_exc_str}")
                else:
                    log(f"      N>={n_min}: 0 signals")

# ─── Step 11: Find best configs ───────────────────────────────────────────────
log("\nStep 11: Top results by overall excess HR...")
# Filter to rows with enough signals
valid = [r for r in results["sweep_results"]
         if r.get("n_signals", 0) >= 10 and r.get("overall_excess_hr") is not None]
valid.sort(key=lambda r: r["overall_excess_hr"], reverse=True)

log("  Top 20 configs by overall excess HR (min 10 signals):")
for r in valid[:20]:
    log(f"    {r['tag']} Pool{r['pool']} K={r['k']} N>={r['n_min']}: "
        f"{r['n_signals']} sigs, HR={r['hit_rate']:.3f}, "
        f"excess={r['overall_excess_hr']:+.4f}")

results["top_configs"] = valid[:20]

# ─── Step 12: Pool A vs B/C/D comparison ─────────────────────────────────────
log("\nStep 12: Pool variant comparison for best K/N combos...")
comparison = {}
for tag in TARGET_TAGS:
    comparison[tag] = {}
    # Best K for this tag: 50 (matching v1 baseline)
    for k in [25, 50, 100]:
        for n_min in [2, 3]:
            key = f"K{k}_N{n_min}"
            comparison[tag][key] = {}
            for pool_id in POOL_VARIANTS:
                match = [r for r in results["sweep_results"]
                         if r["tag"] == tag and r["pool"] == pool_id
                         and r["k"] == k and r["n_min"] == n_min]
                if match:
                    r = match[0]
                    comparison[tag][key][pool_id] = {
                        "n_signals": r.get("n_signals", 0),
                        "hit_rate": r.get("hit_rate"),
                        "overall_excess_hr": r.get("overall_excess_hr"),
                        "pool_size": r.get("actual_pool_size", 0),
                    }

results["comparison_table"] = comparison

# ─── Save results ────────────────────────────────────────────────────────────
log("\nSaving results...")
with open(RESULTS_PATH, "w") as f:
    json.dump(results, f, indent=2, default=str)
log(f"Saved to {RESULTS_PATH}")
log("Sweep complete.")
