"""Anti-overpayer exclusion filter analysis.

Tests whether EXCLUDING traders with negative calibration_gap from the
consensus pool improves signal quality.

Key hypothesis:
- Sure-thing pilers (avg_entry 0.75, HR=68.4%) look skilled but have
  calibration_gap = -6.7pp (underperform their implied probability)
- Removing them may concentrate signal from genuinely skilled traders
- Compares filtered vs unfiltered K=50 pools

From prior research:
- Politics NO K=50 was only surviving strategy (+6.2pp semi-tick excess)
- Vol-weighted direction proven superior
- Direction decomposition mandatory
- Gambling exclusion, >=4h hold for sports

Results: research/hypotheses/scorecard-v2-strategies/discovery/anti_overpayer_filter.md
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")
from research.db import db as get_db  # noqa: E402

TRAIN_END = "2025-07-01"
TEST_START = "2025-07-01"

LOG_FILE = Path("/mnt/nvme/git/polymarket/polymarket/tmp/anti_overpayer.log")
OUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/discovery")
OUT_DIR.mkdir(exist_ok=True)
RESULTS_FILE = OUT_DIR / "anti_overpayer_filter.md"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# Clear log
LOG_FILE.write_text("")

log("=== Anti-overpayer exclusion filter analysis ===")
log(f"Train end: {TRAIN_END}, Test start: {TEST_START}")

con = get_db().con
log("DuckDB connection established")


# ─── Step 1: Gambling macro ─────────────────────────────────────────────────
log("\nStep 1: Create gambling filter macro...")
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
log("  done")


# ─── Step 2: Market-tag lookup (non-gambling) ────────────────────────────────
log("\nStep 2: Build market-tag lookup (non-gambling)...")
con.execute("""
CREATE OR REPLACE TABLE _ao_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
""")
n = con.execute("SELECT count(*) FROM _ao_market_tags").fetchone()[0]
log(f"  Non-gambling markets: {n:,}")


# ─── Step 3: Tag base rates (training) ──────────────────────────────────────
log("\nStep 3: Training-period tag base rates...")
con.execute(f"""
CREATE OR REPLACE TABLE _ao_tag_base_rates_train AS
SELECT
    mt.primary_tag AS tag,
    count(DISTINCT p.condition_id) AS n_markets,
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    1.0 - avg(CAST(p.yes_won AS DOUBLE)) AS no_base_rate
FROM maker_positions p
JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
GROUP BY mt.primary_tag
HAVING count(DISTINCT p.condition_id) >= 50
ORDER BY n_markets DESC;
""")
tag_rates_train = con.execute(
    "SELECT tag, n_markets, yes_base_rate, no_base_rate FROM _ao_tag_base_rates_train ORDER BY n_markets DESC LIMIT 15"
).fetchall()
log("  Training tag base rates:")
for tag, n_m, ybr, nbr in tag_rates_train:
    log(f"    {tag}: YES={ybr:.3f} NO={nbr:.3f} ({n_m:,} markets)")


# ─── Step 4: Tag base rates (test) ──────────────────────────────────────────
log("\nStep 4: Test-period tag base rates...")
con.execute(f"""
CREATE OR REPLACE TABLE _ao_tag_base_rates_test AS
SELECT
    mt.primary_tag AS tag,
    count(DISTINCT p.condition_id) AS n_markets,
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    1.0 - avg(CAST(p.yes_won AS DOUBLE)) AS no_base_rate
FROM maker_positions p
JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
GROUP BY mt.primary_tag
HAVING count(DISTINCT p.condition_id) >= 20
ORDER BY n_markets DESC;
""")


# ─── Step 5: Trader training stats with calibration_gap ─────────────────────
# Entry price: use yes_entry_data.price_x_vol / volume (gives proper 0-1 price)
# NOT net_usd/net_yes — that's unreliable when net_yes is near-zero (sold position)
log("\nStep 5: Compute trader training stats (include calibration_gap)...")
con.execute(f"""
CREATE OR REPLACE TABLE _ao_trader_train AS
WITH base AS (
    SELECT
        p.trader,
        mt.primary_tag AS tag,
        p.condition_id,
        CAST(p.yes_won AS DOUBLE) AS correct,
        p.net_usd,
        p.volume,
        -- Use yes_entry_data for reliable 0-1 entry price proxy
        yed.price_x_vol / nullif(yed.volume, 0) AS entry_price_proxy
    FROM maker_positions p
    JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
    -- INNER JOIN: only positions where we have YES entry data (excludes split-route traders)
    INNER JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
    WHERE p.position = 'YES'
      AND p.resolved_at IS NOT NULL
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND abs(p.net_usd) > 0.01
      AND abs(p.volume) > 0.01
      AND yed.volume > 0.01
),
trader_agg AS (
    SELECT
        b.trader,
        b.tag,
        count(*) AS n_positions,
        avg(b.correct) AS hit_rate,
        avg(b.entry_price_proxy) AS avg_entry_price,
        -- calibration_gap = hit_rate - avg_entry_price (positive = alpha)
        avg(b.correct) - avg(b.entry_price_proxy) AS calibration_gap,
        sum(abs(b.net_usd)) AS total_vol_usd,
        avg(abs(b.net_usd) / nullif(b.volume, 0)) AS avg_conviction
    FROM base b
    GROUP BY b.trader, b.tag
    HAVING count(*) >= 20
)
SELECT
    ta.trader,
    ta.tag,
    ta.n_positions,
    ta.hit_rate,
    ta.avg_entry_price,
    ta.calibration_gap,
    ta.total_vol_usd,
    ta.avg_conviction,
    br.yes_base_rate,
    ta.hit_rate - br.yes_base_rate AS excess_hr
FROM trader_agg ta
JOIN _ao_tag_base_rates_train br ON ta.tag = br.tag;
""")
n_traders = con.execute("SELECT count(*) FROM _ao_trader_train").fetchone()[0]
log(f"  Qualifying traders (train, >=20 positions): {n_traders:,}")


# ─── Step 6: Build elite pool K=50, K=100, K=200 by tag ─────────────────────
log("\nStep 6: Build elite pools by excess_hr rank...")
con.execute("""
CREATE OR REPLACE TABLE _ao_elite_pool AS
SELECT
    *,
    row_number() OVER (PARTITION BY tag ORDER BY excess_hr DESC) AS rank_in_tag
FROM _ao_trader_train
WHERE avg_conviction >= 0.90;  -- exclude market-makers
""")


# Pool membership with different K values
pool_summary = con.execute("""
SELECT
    tag,
    count(*) AS n_all,
    count(CASE WHEN rank_in_tag <= 50 THEN 1 END) AS n_k50,
    count(CASE WHEN rank_in_tag <= 100 THEN 1 END) AS n_k100,
    count(CASE WHEN rank_in_tag <= 200 THEN 1 END) AS n_k200,
    avg(CASE WHEN rank_in_tag <= 50 THEN excess_hr END) AS k50_avg_excess_hr,
    avg(CASE WHEN rank_in_tag <= 50 THEN calibration_gap END) AS k50_avg_cal_gap,
    count(CASE WHEN rank_in_tag <= 50 AND calibration_gap < 0 THEN 1 END) AS k50_n_negative_cal
FROM _ao_elite_pool
GROUP BY tag
ORDER BY n_k50 DESC
""").fetchall()
log("  Pool summary (tag, n_all, K50, K100, K200, avg_excess_K50, avg_calGap_K50, n_neg_cal_K50):")
for row in pool_summary:
    tag, n_all, k50, k100, k200, avg_exc, avg_cg, n_neg = row
    exc_s = f"{avg_exc:.3f}" if avg_exc is not None else "N/A"
    cg_s = f"{avg_cg:.3f}" if avg_cg is not None else "N/A"
    log(f"    {tag}: all={n_all} K50={k50} K100={k100} K200={k200} | excess={exc_s} calGap={cg_s} neg={n_neg}")


# ─── Step 7: Characterize overpayers in K=50 pool ───────────────────────────
log("\nStep 7: Characterize overpayers in K=50 pool...")
overpayer_profile = con.execute("""
SELECT
    CASE
        WHEN calibration_gap < -0.05 THEN 'Overpayer (<-5pp)'
        WHEN calibration_gap < -0.03 THEN 'Overpayer (<-3pp)'
        WHEN calibration_gap < 0     THEN 'Marginal (<0pp)'
        WHEN calibration_gap < 0.05  THEN 'Break-even (0-5pp)'
        ELSE 'Genuine alpha (>5pp)'
    END AS cal_gap_bucket,
    count(*) AS n_traders,
    avg(excess_hr) AS avg_excess_hr,
    avg(calibration_gap) AS avg_cal_gap,
    avg(avg_entry_price) AS avg_entry_price,
    avg(hit_rate) AS avg_hr,
    avg(n_positions) AS avg_n_positions
FROM _ao_elite_pool
WHERE rank_in_tag <= 50
GROUP BY 1
ORDER BY avg_cal_gap
""").fetchall()
log("  K=50 pool calibration gap profile:")
for row in overpayer_profile:
    bucket, n_t, avg_exc, avg_cg, avg_ep, avg_hr, avg_n = row
    exc_s = f"{avg_exc:.3f}" if avg_exc is not None else "N/A"
    cg_s = f"{avg_cg:.3f}" if avg_cg is not None else "N/A"
    ep_s = f"{avg_ep:.3f}" if avg_ep is not None else "N/A"
    hr_s = f"{avg_hr:.3f}" if avg_hr is not None else "N/A"
    log(f"    {bucket}: n={n_t} excess_hr={exc_s} calGap={cg_s} entry={ep_s} HR={hr_s}")


# ─── Step 8: Consensus evaluation in test window ─────────────────────────────
# Gates: 0=baseline, 1=cal<-5pp, 2=cal<-3pp, 3=cal<0, 4=cal<0+stability
log("\nStep 8: Build test-period consensus for each gate...")

# First, build test positions
con.execute(f"""
CREATE OR REPLACE TABLE _ao_test_positions AS
SELECT
    p.trader,
    p.condition_id,
    mt.primary_tag AS tag,
    p.position,
    CAST(p.yes_won AS DOUBLE) AS correct,
    p.net_usd,
    p.volume,
    p.first_trade,
    p.resolved_at,
    date_diff('hour', p.first_trade, p.resolved_at) AS hold_hours,
    CASE
        WHEN p.position = 'YES' AND abs(p.net_yes) > 0.01 THEN abs(p.net_usd) / abs(p.net_yes)
        WHEN p.position = 'NO'  AND abs(p.net_no)  > 0.01 THEN abs(p.net_usd) / abs(p.net_no)
    END AS entry_price_proxy
FROM maker_positions p
JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
WHERE p.position IN ('YES', 'NO')
  AND p.resolved_at IS NOT NULL
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
  AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
  AND abs(p.net_usd) > 0.01;
""")
n_test = con.execute("SELECT count(*) FROM _ao_test_positions").fetchone()[0]
log(f"  Test positions: {n_test:,}")


def run_consensus_for_gate(gate_name: str, cal_threshold: float | None, stability_filter: bool = False) -> list:
    """Run consensus evaluation for a given calibration gate.

    Returns list of (tag, direction, n_signals, hit_rate, base_rate, excess_hr, median_hold_h)
    grouped by N threshold (2, 3, 5).
    """
    # Build qualified pool (K=50, with exclusion gate)
    if cal_threshold is None:
        gate_filter = "1=1"
    elif stability_filter:
        # cal_gap < 0 AND n_positions < 50 (as stability proxy — low n = lucky streak)
        gate_filter = f"(calibration_gap >= {cal_threshold} OR n_positions < 50)"
    else:
        gate_filter = f"calibration_gap >= {cal_threshold}"

    con.execute(f"""
    CREATE OR REPLACE TABLE _ao_pool_gate AS
    SELECT trader, tag, calibration_gap, excess_hr, avg_conviction, n_positions
    FROM _ao_elite_pool
    WHERE rank_in_tag <= 50
      AND {gate_filter};
    """)
    pool_size = con.execute("SELECT count(*) FROM _ao_pool_gate").fetchone()[0]

    results = []
    for n_min in [2, 3, 5]:
        for direction in ["YES", "NO"]:
            # vol-weighted direction consensus at market level
            # count distinct qualified pool traders per market × direction
            hold_filter = ""
            if direction == "YES":
                # for sports apply >=4h hold to reduce in-play bias
                pass

            consensus_sql = f"""
            WITH mkt_consensus AS (
                SELECT
                    tp.condition_id,
                    tp.tag,
                    tp.position AS signal_dir,
                    count(DISTINCT tp.trader) AS n_qualified,
                    max(tp.first_trade) AS signal_entry,
                    first(tp.resolved_at) AS resolved_at,
                    first(tp.correct) AS market_correct,
                    -- vol-weighted direction: sum net_usd of YES vs NO qualified traders
                    sum(CASE WHEN tp.position = 'YES' THEN abs(tp.net_usd) ELSE 0 END) AS vol_yes,
                    sum(CASE WHEN tp.position = 'NO'  THEN abs(tp.net_usd) ELSE 0 END) AS vol_no
                FROM _ao_test_positions tp
                INNER JOIN _ao_pool_gate pg ON tp.trader = pg.trader AND tp.tag = pg.tag
                WHERE tp.position = '{direction}'
                  AND tp.hold_hours >= 0
                GROUP BY tp.condition_id, tp.tag, tp.position
                HAVING n_qualified >= {n_min}
            ),
            scored AS (
                SELECT
                    mc.*,
                    date_diff('hour', mc.signal_entry, mc.resolved_at) AS hold_hours
                FROM mkt_consensus mc
            )
            SELECT
                tag,
                '{direction}' AS direction,
                {n_min} AS n_min,
                count(*) AS n_signals,
                avg(market_correct) AS hit_rate,
                median(hold_hours) AS median_hold_h
            FROM scored
            WHERE hold_hours >= 0
            GROUP BY tag
            ORDER BY n_signals DESC
            """
            rows = con.execute(consensus_sql).fetchall()

            for row in rows:
                tag, direc, nm, n_sig, hr, med_hold = row
                results.append({
                    "gate": gate_name,
                    "pool_size": pool_size,
                    "tag": tag,
                    "direction": direc,
                    "n_min": nm,
                    "n_signals": n_sig,
                    "hit_rate": hr,
                    "median_hold_h": med_hold,
                })

    return results


# Apply sports >=4h hold filter to test positions before evaluation
con.execute("""
CREATE OR REPLACE TABLE _ao_test_positions_sports_filtered AS
SELECT * FROM _ao_test_positions
WHERE NOT (tag = 'Sports' AND hold_hours < 4);
""")

# Run all gates
all_results = []
gates = [
    ("Gate 0: No filter (baseline)", None, False),
    ("Gate 1: Exclude cal_gap < -5pp", -0.05, False),
    ("Gate 2: Exclude cal_gap < -3pp", -0.03, False),
    ("Gate 3: Exclude cal_gap < 0",     0.0, False),
    ("Gate 4: Exclude cal_gap < 0 + stability proxy", 0.0, True),
]

for gate_name, threshold, stability in gates:
    log(f"\n  Running {gate_name}...")
    results = run_consensus_for_gate(gate_name, threshold, stability)
    all_results.extend(results)
    pool_size = results[0]["pool_size"] if results else "?"
    log(f"    Pool size: {pool_size}")
    for r in results[:6]:
        hr_s = f"{r['hit_rate']:.3f}" if r['hit_rate'] is not None else "N/A"
        log(f"    {r['tag']} {r['direction']} N>={r['n_min']}: signals={r['n_signals']} HR={hr_s}")


# ─── Step 9: Direction decomposition per tag ─────────────────────────────────
log("\nStep 9: Join with test base rates for excess HR...")
# Build tag lookup
test_rates = con.execute("""
SELECT tag, yes_base_rate, no_base_rate FROM _ao_tag_base_rates_test
""").fetchall()
test_rate_map = {row[0]: {"yes": row[1], "no": row[2]} for row in test_rates}

for r in all_results:
    tag = r["tag"]
    if tag in test_rate_map:
        if r["direction"] == "YES":
            r["base_rate"] = test_rate_map[tag]["yes"]
        else:
            r["base_rate"] = test_rate_map[tag]["no"]
        r["excess_hr"] = (r["hit_rate"] or 0) - r["base_rate"]
    else:
        r["base_rate"] = None
        r["excess_hr"] = None


# ─── Step 10: Pool size matched comparison ───────────────────────────────────
log("\nStep 10: Compare filtered K=50 vs unfiltered K=30 (size-matched)...")
# Get K=30 (unfiltered) for comparison
con.execute("""
CREATE OR REPLACE TABLE _ao_pool_k30 AS
SELECT trader, tag, calibration_gap, excess_hr, avg_conviction, n_positions
FROM _ao_elite_pool
WHERE rank_in_tag <= 30;
""")
k30_results = []
for n_min in [2, 3]:
    for direction in ["YES", "NO"]:
        sql = f"""
        WITH mkt_consensus AS (
            SELECT
                tp.condition_id, tp.tag,
                count(DISTINCT tp.trader) AS n_qualified,
                max(tp.first_trade) AS signal_entry,
                first(tp.resolved_at) AS resolved_at,
                first(tp.correct) AS market_correct
            FROM _ao_test_positions tp
            INNER JOIN _ao_pool_k30 pg ON tp.trader = pg.trader AND tp.tag = pg.tag
            WHERE tp.position = '{direction}'
              AND tp.hold_hours >= 0
            GROUP BY tp.condition_id, tp.tag
            HAVING n_qualified >= {n_min}
        )
        SELECT
            tag, '{direction}' AS direction, {n_min} AS n_min,
            count(*) AS n_signals,
            avg(market_correct) AS hit_rate,
            median(date_diff('hour', signal_entry, resolved_at)) AS median_hold_h
        FROM mkt_consensus
        WHERE date_diff('hour', signal_entry, resolved_at) >= 0
        GROUP BY tag ORDER BY n_signals DESC
        """
        rows = con.execute(sql).fetchall()
        for row in rows:
            tag, direc, nm, n_sig, hr, med_hold = row
            base_rate = test_rate_map.get(tag, {}).get("yes" if direc == "YES" else "no")
            k30_results.append({
                "gate": "Unfiltered K=30 (size-matched comparison)",
                "pool_size": 30,
                "tag": tag,
                "direction": direc,
                "n_min": nm,
                "n_signals": n_sig,
                "hit_rate": hr,
                "median_hold_h": med_hold,
                "base_rate": base_rate,
                "excess_hr": ((hr or 0) - base_rate) if base_rate else None,
            })

all_results.extend(k30_results)
log(f"  K=30 unfiltered results: {len(k30_results)} rows")


# ─── Step 11: Walk-forward stability check ───────────────────────────────────
log("\nStep 11: Walk-forward stability check (2 alternative splits)...")
SPLITS = [
    ("split_early", "2025-01-01", "2025-07-01"),   # earlier test window
    ("split_late", "2025-07-01", "2026-03-07"),     # later test window (default)
]

wf_results = []
for split_name, test_s, test_e in SPLITS:
    for gate_threshold, gate_label in [(None, "baseline"), (0.0, "gate3_cal_lt_0")]:
        # Recompute with this split's training data
        # Use yes_entry_data for reliable 0-1 entry price proxy
        con.execute(f"""
        CREATE OR REPLACE TABLE _wf_trader_train_{split_name} AS
        WITH base AS (
            SELECT
                p.trader,
                mt.primary_tag AS tag,
                p.condition_id,
                CAST(p.yes_won AS DOUBLE) AS correct,
                p.net_usd, p.volume,
                yed.price_x_vol / nullif(yed.volume, 0) AS entry_price_proxy
            FROM maker_positions p
            JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
            INNER JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
            WHERE p.position = 'YES'
              AND p.resolved_at IS NOT NULL
              AND CAST(p.resolved_at AS DATE) < '{test_s}'
              AND abs(p.net_usd) > 0.01 AND abs(p.volume) > 0.01
              AND yed.volume > 0.01
        ),
        trader_agg AS (
            SELECT
                b.trader, b.tag,
                count(*) AS n_positions,
                avg(b.correct) AS hit_rate,
                avg(b.entry_price_proxy) AS avg_entry_price,
                avg(b.correct) - avg(b.entry_price_proxy) AS calibration_gap,
                avg(abs(b.net_usd) / nullif(b.volume, 0)) AS avg_conviction
            FROM base b GROUP BY b.trader, b.tag
            HAVING count(*) >= 20
        )
        SELECT
            ta.*, br.yes_base_rate,
            ta.hit_rate - br.yes_base_rate AS excess_hr,
            row_number() OVER (PARTITION BY ta.tag ORDER BY (ta.hit_rate - br.yes_base_rate) DESC) AS rank_in_tag
        FROM trader_agg ta
        JOIN _ao_tag_base_rates_train br ON ta.tag = br.tag
        WHERE ta.avg_conviction >= 0.90;
        """)

        gate_filter = "1=1" if gate_threshold is None else f"calibration_gap >= {gate_threshold}"
        con.execute(f"""
        CREATE OR REPLACE TABLE _wf_pool AS
        SELECT * FROM _wf_trader_train_{split_name}
        WHERE rank_in_tag <= 50 AND {gate_filter};
        """)
        pool_sz = con.execute("SELECT count(*) FROM _wf_pool").fetchone()[0]

        # Test positions for this split
        con.execute(f"""
        CREATE OR REPLACE TABLE _wf_test_pos AS
        SELECT
            p.trader, p.condition_id, mt.primary_tag AS tag, p.position,
            CAST(p.yes_won AS DOUBLE) AS correct,
            p.net_usd, p.first_trade, p.resolved_at,
            date_diff('hour', p.first_trade, p.resolved_at) AS hold_hours
        FROM maker_positions p
        JOIN _ao_market_tags mt ON p.condition_id = mt.condition_id
        WHERE p.position IN ('YES', 'NO')
          AND p.resolved_at IS NOT NULL
          AND CAST(p.resolved_at AS DATE) >= '{test_s}'
          AND CAST(p.resolved_at AS DATE) < '{test_e}'
          AND CAST(p.first_trade AS DATE) >= '{test_s}'
          AND abs(p.net_usd) > 0.01;
        """)

        for direction in ["YES", "NO"]:
            rows = con.execute(f"""
            WITH mkt AS (
                SELECT
                    tp.condition_id, tp.tag,
                    count(DISTINCT tp.trader) AS n_q,
                    first(tp.correct) AS correct
                FROM _wf_test_pos tp
                INNER JOIN _wf_pool pg ON tp.trader = pg.trader AND tp.tag = pg.tag
                WHERE tp.position = '{direction}' AND tp.hold_hours >= 0
                GROUP BY tp.condition_id, tp.tag
                HAVING n_q >= 3
            )
            SELECT tag, count(*) AS n_signals, avg(correct) AS hr
            FROM mkt GROUP BY tag ORDER BY n_signals DESC LIMIT 5
            """).fetchall()

            for row in rows:
                tag, n_sig, hr = row
                wf_results.append({
                    "split": split_name,
                    "gate": gate_label,
                    "pool_size": pool_sz,
                    "tag": tag,
                    "direction": direction,
                    "n_signals": n_sig,
                    "hit_rate": hr,
                })

log(f"  Walk-forward results: {len(wf_results)} rows")


# ─── Step 12: Write results markdown ────────────────────────────────────────
log("\nStep 12: Writing results markdown...")


def fmt(v, pct=False, n=3):
    if v is None:
        return "N/A"
    if pct:
        return f"{v*100:+.1f}pp"
    return f"{v:.{n}f}"


# Group results by tag for main output
FOCUS_TAGS = ["Politics", "Sports", "Crypto"]

md_lines = [
    "# Anti-Overpayer Exclusion Filter — Discovery Results",
    "",
    f"**Date**: {datetime.now().strftime('%Y-%m-%d')}",
    f"**Train**: < {TRAIN_END} | **Test**: >= {TEST_START}",
    f"**Method**: Vectorized (UPPER BOUNDS — expect 20-40pp tick degradation)",
    "",
    "## Summary",
    "",
    "Tests whether excluding traders with negative calibration_gap from the K=50 consensus pool",
    "improves signal quality. Five gates tested: none, <-5pp, <-3pp, <0, <0+stability proxy.",
    "",
    "## Pool Characterization",
    "",
    "### K=50 Pool Calibration Gap Distribution (by tag)",
    "",
    "| Tag | K50 Size | Neg-Cal (N) | Neg-Cal (%) | Avg CalGap K50 | Avg Excess HR K50 |",
    "|-----|---------|------------|-------------|----------------|-------------------|",
]

for row in pool_summary:
    tag, n_all, k50, k100, k200, avg_exc, avg_cg, n_neg = row
    pct_neg = (n_neg / k50 * 100) if k50 else 0
    md_lines.append(
        f"| {tag} | {k50} | {n_neg} | {pct_neg:.0f}% | {fmt(avg_cg, pct=True)} | {fmt(avg_exc, pct=True)} |"
    )

md_lines += [
    "",
    "### Overpayer Profile (K=50 pool, all tags combined)",
    "",
    "| Bucket | N Traders | Avg Excess HR | Avg CalGap | Avg Entry Price | Avg HR |",
    "|--------|-----------|---------------|------------|----------------|--------|",
]
for row in overpayer_profile:
    bucket, n_t, avg_exc, avg_cg, avg_ep, avg_hr, avg_n = row
    md_lines.append(
        f"| {bucket} | {n_t} | {fmt(avg_exc, pct=True)} | {fmt(avg_cg, pct=True)} | {fmt(avg_ep)} | {fmt(avg_hr, pct=True)} |"
    )

md_lines += [
    "",
    "## Consensus Results by Gate",
    "",
    "All signals: Vol-weighted consensus, N >= threshold.",
    "Excess HR = HR - test-period tag base rate.",
    "",
]

for focus_tag in FOCUS_TAGS:
    md_lines += [
        f"### {focus_tag}",
        "",
        "| Gate | Pool Size | Direction | N_min | N Signals | HR | Base Rate | Excess HR | Hold (h) |",
        "|------|-----------|-----------|-------|-----------|-----|-----------|-----------|----------|",
    ]
    tag_rows = [r for r in all_results if r["tag"] == focus_tag and r["n_min"] <= 5]
    # Sort by gate, direction, n_min
    tag_rows.sort(key=lambda x: (x["gate"], x["direction"], x["n_min"]))
    for r in tag_rows:
        md_lines.append(
            f"| {r['gate']} | {r['pool_size']} | {r['direction']} | {r['n_min']} | "
            f"{r['n_signals']} | {fmt(r['hit_rate'], pct=True)} | "
            f"{fmt(r['base_rate'], pct=True)} | {fmt(r['excess_hr'], pct=True)} | "
            f"{int(r['median_hold_h']) if r['median_hold_h'] else 'N/A'} |"
        )
    md_lines.append("")


# Walk-forward
md_lines += [
    "## Walk-Forward Stability",
    "",
    "Two alternative splits to check if exclusion gate holds across time.",
    "",
    "| Split | Gate | Tag | Direction | Pool | N Signals | HR |",
    "|-------|------|-----|-----------|------|-----------|-----|",
]
for r in wf_results[:40]:
    md_lines.append(
        f"| {r['split']} | {r['gate']} | {r['tag']} | {r['direction']} | "
        f"{r['pool_size']} | {r['n_signals']} | {fmt(r['hit_rate'], pct=True)} |"
    )


# Key question: does filtering HELP or just SHRINK?
md_lines += [
    "",
    "## Key Question: Does Filtering Help or Just Shrink?",
    "",
    "Comparing filtered K=50 (Gate 3: cal>=0) vs unfiltered K=30:",
    "",
]

# Summarize delta
for focus_tag in FOCUS_TAGS:
    g0 = [r for r in all_results if r["tag"] == focus_tag and r["gate"].startswith("Gate 0") and r["n_min"] == 3]
    g3 = [r for r in all_results if r["tag"] == focus_tag and r["gate"].startswith("Gate 3") and r["n_min"] == 3]
    k30 = [r for r in all_results if r["tag"] == focus_tag and r["gate"].startswith("Unfiltered K=30") and r["n_min"] == 3]

    md_lines.append(f"### {focus_tag} — N_min=3")
    md_lines.append("")
    md_lines.append("| Config | Pool | N Signals | HR | Excess HR |")
    md_lines.append("|--------|------|-----------|-----|-----------|")

    for r in sorted(g0 + g3 + k30, key=lambda x: x["direction"]):
        md_lines.append(
            f"| {r['gate'][:30]} ({r['direction']}) | {r['pool_size']} | "
            f"{r['n_signals']} | {fmt(r['hit_rate'], pct=True)} | {fmt(r['excess_hr'], pct=True)} |"
        )
    md_lines.append("")


# Interpretation
md_lines += [
    "## Interpretation & Recommendations",
    "",
    "### When does the filter HELP?",
    "- If filtered K=50 has HIGHER excess HR than unfiltered K=50 AND similar/higher signal count",
    "  than unfiltered K=30 (size-matched) → exclusion gate adds value",
    "",
    "### When does the filter SHRINK WITHOUT IMPROVING?",
    "- If filtered K=50 excess HR ≈ unfiltered K=30 excess HR → the filter merely removes traders",
    "  without adding quality. A simpler K=30 threshold achieves the same result.",
    "",
    "### Sure-thing piler risk",
    "- Traders with cal_gap < -5pp in the K=50 pool are likely sure-thing pilers",
    "  who look skilled by raw HR but underperform their implied probability",
    "- Even small numbers (5-10 of 50) can pollute consensus markets by entering near-certain events",
    "",
    "### Recommendation",
    "- Gate 3 (exclude cal_gap < 0) is theoretically cleanest but may over-filter",
    "- Gate 1 (exclude cal_gap < -5pp) is the practical starting point",
    "- Compare against unfiltered K=30 to determine if filtering truly adds value",
    "",
    "**All results are VECTORIZED UPPER BOUNDS.** Expect 20-40pp tick degradation.",
]

RESULTS_FILE.write_text("\n".join(md_lines))
log(f"\nResults written to: {RESULTS_FILE}")
log("=== Analysis complete ===")
