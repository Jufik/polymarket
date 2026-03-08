"""
Tick-by-tick validation — corrected candidate parameters.

Based on debug findings:
  A) Esports K=50 is too thin (15 test entries). Use ALL excess>0 pool (880 traders)
     with N=3, conf=0.80 → vectorized HR=92% (matches strategy2 doc)
  B) Crypto all excess>0, N=5, conf=0.90 → vectorized HR=73.2%, 127 signals
  C) Politics K=50, N=3, NO, >=24h → vectorized HR=89.9%, 932 signals

Approach: semi-tick using positions table ordered by first_trade.
  Signal time = max(first_trade) when Nth pool trader confirms threshold.
  This is the CAUSAL signal time (what we'd see in real-time).

For each candidate:
  - Vectorized HR (positions table, correct pool)
  - Tick HR (same data, but signal fires at causal N-entry time)
  - Degradation = vectorized HR - tick HR
"""

import json
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

LOG_PATH = "/mnt/nvme/git/polymarket/polymarket/tmp/tick_validate2.log"
RESULTS_PATH = "/mnt/nvme/git/polymarket/polymarket/tmp/tick_validate2_results.json"

TRAIN_END = "2025-12-05"
TEST_START = "2025-12-05"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# Clear log
with open(LOG_PATH, "w") as f:
    pass

log("=" * 70)
log("Tick-by-Tick Validation — Corrected Parameters")
log("=" * 70)

from research.db import db as get_db
t0 = time.time()
con = get_db().con
log(f"DuckDB ready ({time.time()-t0:.1f}s)")

# ── Shared infrastructure ─────────────────────────────────────────────────────
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
        )
    )
);
""")
con.execute("""
CREATE OR REPLACE TABLE _tv_market_tags AS
SELECT m.condition_id, m.slug,
       first(et.label ORDER BY et.tag_id) AS primary_tag,
       first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
""")


def build_pool(tag: str, min_markets: int, min_conviction: float, k_cap: int | None,
               composite_score: bool = False, position_filter: str | None = None) -> tuple[set, dict]:
    """Build qualified pool from training data. Returns (pool_traders, base_rate_info)."""

    pos_filter_sql = ""
    if position_filter:
        pos_filter_sql = f"AND p.position = '{position_filter}'"

    hr_expr = """
        avg(CASE
            WHEN p.position = 'YES' THEN CAST(p.yes_won AS DOUBLE)
            WHEN p.position = 'NO' THEN 1.0 - CAST(p.yes_won AS DOUBLE)
            ELSE 0.5
        END)"""

    score_expr = "(t.hit_rate - b.br)" if not composite_score else \
                 "(t.hit_rate - b.br) * ln(t.n_positions + 1.0)"

    k_where = f"WHERE hr_rank <= {k_cap}" if k_cap else "WHERE 1=1"

    sql = f"""
    CREATE OR REPLACE TABLE _tv_pool_tmp AS
    WITH train_pos AS (
        SELECT p.trader,
               count(DISTINCT p.condition_id) AS n_positions,
               {hr_expr} AS hit_rate,
               avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) AS avg_conviction
        FROM maker_positions p
        JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
          AND p.volume > 0
          AND p.net_usd IS NOT NULL
          {pos_filter_sql}
        GROUP BY p.trader
        HAVING count(DISTINCT p.condition_id) >= {min_markets}
           AND avg(CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END) >= {min_conviction}
           AND count(*) < 10000
    ),
    base_rate AS (
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS br
        FROM maker_positions p
        JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
    ),
    scored AS (
        SELECT t.trader, t.n_positions, t.hit_rate, t.avg_conviction,
               {score_expr} AS score,
               (t.hit_rate - b.br) AS excess_hr,
               ROW_NUMBER() OVER (ORDER BY {score_expr} DESC) AS hr_rank
        FROM train_pos t CROSS JOIN base_rate b
        WHERE (t.hit_rate - b.br) > 0
    )
    SELECT trader, n_positions, hit_rate, excess_hr, hr_rank
    FROM scored {k_where};
    """
    con.execute(sql)

    pool_traders = set(r[0] for r in con.execute("SELECT trader FROM _tv_pool_tmp").fetchall())
    pool_hr = con.execute("SELECT avg(hit_rate), avg(excess_hr), count(*) FROM _tv_pool_tmp").fetchone()
    br = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE))
        FROM maker_positions p
        JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}' AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
    """).fetchone()[0]
    info = {
        "n_traders": len(pool_traders),
        "avg_hr": pool_hr[0],
        "avg_excess_hr": pool_hr[1],
        "train_base_rate": br,
    }
    return pool_traders, info


def vectorized_hr(tag: str, pool_table: str, position: str | None, min_n: int,
                  min_conf: float, min_hold_hours: float = 0.0) -> dict:
    """Compute vectorized HR using positions table (market-level aggregation)."""

    pos_filter = f"AND p.position = '{position}'" if position else ""
    if position == "NO":
        hr_expr = "avg(1.0 - yes_won) AS hr"
        dir_correct = "avg(1.0 - yes_won)"
        dir_signal = "'NO'"
    else:
        # vol-weighted: use sign of vol_dir
        hr_expr = """avg(CASE WHEN (trade_yes = 1.0 AND yes_won = 1.0)
                             OR (trade_yes = 0.0 AND yes_won = 0.0)
                        THEN 1.0 ELSE 0.0 END) AS hr"""
        dir_correct = """avg(CASE WHEN (trade_yes = 1.0 AND yes_won = 1.0)
                             OR (trade_yes = 0.0 AND yes_won = 0.0)
                        THEN 1.0 ELSE 0.0 END)"""
        dir_signal = "CASE WHEN vol_dir > 0 THEN 1.0 ELSE 0.0 END"

    if position == "NO":
        conf_expr = "1.0"  # all NO traders → confidence always 1.0 for that direction
        having_conf = ""
    else:
        conf_expr = "greatest(n_yes, n_no) * 1.0 / n_qual"
        having_conf = f"AND {conf_expr} >= {min_conf}"

    sql = f"""
    WITH test_pos AS (
        SELECT p.trader, p.condition_id, p.position, p.yes_won, p.net_usd, p.first_trade, p.resolved_at
        FROM maker_positions p
        JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
        JOIN {pool_table} ep ON p.trader = ep.trader
        WHERE mt.primary_tag = '{tag}'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND p.volume > 0
          {pos_filter}
    ),
    market_signals AS (
        SELECT
            condition_id,
            first(yes_won) AS yes_won,
            count(DISTINCT trader) AS n_qual,
            count(DISTINCT CASE WHEN position='YES' THEN trader END) AS n_yes,
            count(DISTINCT CASE WHEN position='NO' THEN trader END) AS n_no,
            sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir,
            date_diff('hour', max(first_trade), first(resolved_at)) AS hold_hours,
            max(first_trade) AS signal_entry
        FROM test_pos
        GROUP BY condition_id
        HAVING n_qual >= {min_n}
           AND date_diff('hour', max(first_trade), first(resolved_at)) >= {min_hold_hours}
    )
    SELECT
        count(*) AS n_signals,
        avg(CASE WHEN yes_won = 1.0 THEN 1.0 ELSE 0.0 END) AS yes_win_rate,
        avg(1.0 - CAST(yes_won AS DOUBLE)) AS no_win_rate,
        median(hold_hours) AS med_hold_hours,
        avg(n_qual) AS avg_n_traders
    FROM market_signals
    WHERE n_qual >= {min_n}
    """

    if position == "NO":
        r = con.execute(sql).fetchone()
        return {
            "n_signals": r[0],
            "hit_rate": r[2],  # NO direction: correct when yes_won=0
            "median_hold_hours": r[3],
            "avg_n_traders": r[4],
        }
    else:
        # vol-weighted needs confidence filter — more complex
        sql2 = f"""
        WITH test_pos AS (
            SELECT p.trader, p.condition_id, p.position, p.yes_won, p.net_usd, p.first_trade, p.resolved_at
            FROM maker_positions p
            JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
            JOIN {pool_table} ep ON p.trader = ep.trader
            WHERE mt.primary_tag = '{tag}'
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
              AND p.volume > 0
        ),
        market_signals AS (
            SELECT
                condition_id,
                first(yes_won) AS yes_won,
                count(DISTINCT trader) AS n_qual,
                count(DISTINCT CASE WHEN position='YES' THEN trader END) AS n_yes,
                count(DISTINCT CASE WHEN position='NO' THEN trader END) AS n_no,
                sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir,
                date_diff('hour', max(first_trade), first(resolved_at)) AS hold_hours
            FROM test_pos
            GROUP BY condition_id
            HAVING n_qual >= {min_n}
               AND date_diff('hour', max(first_trade), first(resolved_at)) >= {min_hold_hours}
        ),
        with_dir AS (
            SELECT *,
                   greatest(n_yes, n_no) * 1.0 / n_qual AS confidence,
                   CASE WHEN vol_dir > 0 THEN 1.0 ELSE 0.0 END AS trade_yes
            FROM market_signals
            WHERE confidence >= {min_conf}
        )
        SELECT
            count(*) AS n_signals,
            avg(CASE WHEN (trade_yes = 1.0 AND yes_won = 1.0) OR (trade_yes = 0.0 AND yes_won = 0.0)
                     THEN 1.0 ELSE 0.0 END) AS hr,
            median(hold_hours) AS med_hold_hours,
            avg(n_qual) AS avg_n_traders
        FROM with_dir
        """
        r = con.execute(sql2).fetchone()
        return {
            "n_signals": r[0],
            "hit_rate": r[1],
            "median_hold_hours": r[2],
            "avg_n_traders": r[3],
        }


def tick_hr(tag: str, pool_traders: set, position: str | None, min_n: int,
            min_conf: float, min_hold_hours: float = 0.0, label: str = "") -> dict:
    """
    Tick-level HR using positions table ordered by first_trade.

    Simulates real-time consensus detection: signal fires when the Nth pool
    trader's first_trade is observed. Only trades with first_trade >= TEST_START
    count as copyable entries.
    """
    log(f"  [{label}] Building temp pool table ({len(pool_traders)} traders)...")
    t0 = time.time()

    # Use temp tables for large pools
    con.execute("CREATE OR REPLACE TABLE _tv_tick_pool (trader VARCHAR);")
    pool_data = [(t,) for t in pool_traders]
    con.executemany("INSERT INTO _tv_tick_pool VALUES (?)", pool_data)

    pos_filter = f"AND p.position = '{position}'" if position else ""

    log(f"  [{label}] Loading pool entries from positions table...")
    # Get all pool entries in test window, ordered by condition_id + first_trade
    entries = con.execute(f"""
    SELECT
        p.condition_id,
        p.trader,
        p.position,
        abs(p.net_usd) AS abs_usd,
        epoch(CAST(p.first_trade AS TIMESTAMP)) AS first_trade_ts,
        CAST(p.yes_won AS DOUBLE) AS yes_won,
        epoch(CAST(p.resolved_at AS TIMESTAMP)) AS resolved_at_ts
    FROM maker_positions p
    JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
    JOIN _tv_tick_pool tp ON p.trader = tp.trader
    WHERE mt.primary_tag = '{tag}'
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
      AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
      AND p.volume > 0
      {pos_filter}
    ORDER BY p.condition_id, epoch(CAST(p.first_trade AS TIMESTAMP))
    """).fetchall()

    load_time = time.time() - t0
    log(f"  [{label}] Loaded {len(entries):,} pool entries in {load_time:.2f}s")

    # Group by condition_id
    by_market: dict = defaultdict(list)
    meta: dict = {}
    for row in entries:
        cid, trader, pos, abs_usd, ft_ts, yes_won, res_ts = row
        by_market[cid].append({
            "trader": trader,
            "position": pos,
            "abs_usd": abs_usd or 0.0,
            "first_trade_ts": ft_ts,
        })
        if cid not in meta:
            meta[cid] = {"yes_won": yes_won, "resolved_at_ts": res_ts}

    log(f"  [{label}] Markets with pool activity: {len(by_market):,}")

    # Simulate tick-by-tick per market
    signals = []
    for cid, entries_list in by_market.items():
        m = meta.get(cid, {})
        yes_won = m.get("yes_won", None)
        resolved_at_ts = m.get("resolved_at_ts", None)
        if yes_won is None or resolved_at_ts is None:
            continue

        # Sort by first_trade_ts (causal order)
        sorted_entries = sorted(entries_list, key=lambda x: x["first_trade_ts"])

        seen: set = set()
        n_yes: set = set()
        n_no: set = set()
        yes_vol = 0.0
        no_vol = 0.0

        signal_fired = False
        for entry in sorted_entries:
            t = entry["trader"]
            if t in seen:
                continue
            seen.add(t)

            if entry["position"] == "YES":
                n_yes.add(t)
                yes_vol += entry["abs_usd"]
            else:
                n_no.add(t)
                no_vol += entry["abs_usd"]

            n_total = len(seen)
            if n_total < min_n:
                continue

            # Compute confidence at this point
            if position == "NO":
                # All entries are NO (filtered above), so conf = 1.0 always
                conf = 1.0
                trade_dir = "NO"
            else:
                # Vol-weighted direction
                n_dom = max(len(n_yes), len(n_no))
                conf = n_dom / n_total
                trade_dir = "YES" if yes_vol >= no_vol else "NO"

            if conf < min_conf:
                continue

            # Signal fires here
            signal_ts = entry["first_trade_ts"]
            hold_hours = (resolved_at_ts - signal_ts) / 3600.0

            if hold_hours < min_hold_hours or hold_hours < 0:
                continue

            # Was signal correct?
            if trade_dir == "YES":
                correct = (yes_won == 1.0)
            else:
                correct = (yes_won == 0.0)

            signals.append({
                "condition_id": cid,
                "signal_ts": signal_ts,
                "resolved_at_ts": resolved_at_ts,
                "hold_hours": hold_hours,
                "trade_dir": trade_dir,
                "correct": correct,
                "n_traders_at_signal": n_total,
                "confidence": conf,
            })
            signal_fired = True
            break  # Only one signal per market

    n_sigs = len(signals)
    if n_sigs == 0:
        log(f"  [{label}] No signals fired!")
        return {"n_signals": 0, "hit_rate": None}

    n_correct = sum(1 for s in signals if s["correct"])
    hr = n_correct / n_sigs
    hold_hours = sorted(s["hold_hours"] for s in signals)
    med_hold = hold_hours[len(hold_hours) // 2]
    avg_n = sum(s["n_traders_at_signal"] for s in signals) / n_sigs

    log(f"  [{label}] TICK RESULTS: N={n_sigs}, HR={hr:.3f}, med_hold={med_hold:.0f}h, avg_n={avg_n:.1f}")
    return {
        "n_signals": n_sigs,
        "n_correct": n_correct,
        "hit_rate": round(hr, 4),
        "median_hold_hours": round(med_hold, 1),
        "avg_n_traders": round(avg_n, 1),
        "signals": signals,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE A: Smart Money Pool — Esports ALL pool, N=3, conf>=0.80
# ══════════════════════════════════════════════════════════════════════════════
log("")
log("=" * 60)
log("CANDIDATE A: Smart Money Pool — Esports (all excess>0), N=3, conf>=0.80")
log("=" * 60)

pool_a, pool_a_info = build_pool("Esports", min_markets=10, min_conviction=0.90,
                                  k_cap=None, composite_score=False)
log(f"  Pool: {pool_a_info['n_traders']} traders, avg HR={pool_a_info['avg_hr']:.3f}, excess={pool_a_info['avg_excess_hr']:+.3f}")

# Vectorized baseline
log("  Computing vectorized baseline...")
vec_a = vectorized_hr("Esports", "_tv_pool_tmp", position=None, min_n=3, min_conf=0.80)
log(f"  Vectorized: N={vec_a['n_signals']}, HR={vec_a['hit_rate']:.3f}, hold={vec_a['median_hold_hours']:.0f}h")

# Tick-level
log("  Running tick-by-tick simulation...")
tick_a = tick_hr("Esports", pool_a, position=None, min_n=3, min_conf=0.80, label="Esports-all-N3-C80")

# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE B: Smart Money Pool — Crypto ALL pool, N=5, conf>=0.90
# ══════════════════════════════════════════════════════════════════════════════
log("")
log("=" * 60)
log("CANDIDATE B: Smart Money Pool — Crypto (all excess>0), N=5, conf>=0.90")
log("=" * 60)

pool_b, pool_b_info = build_pool("Crypto", min_markets=10, min_conviction=0.90,
                                  k_cap=None, composite_score=False)
log(f"  Pool: {pool_b_info['n_traders']} traders, avg HR={pool_b_info['avg_hr']:.3f}, excess={pool_b_info['avg_excess_hr']:+.3f}")

log("  Computing vectorized baseline...")
vec_b = vectorized_hr("Crypto", "_tv_pool_tmp", position=None, min_n=5, min_conf=0.90)
log(f"  Vectorized: N={vec_b['n_signals']}, HR={vec_b['hit_rate']:.3f}, hold={vec_b['median_hold_hours']:.0f}h")

log("  Running tick-by-tick simulation...")
tick_b = tick_hr("Crypto", pool_b, position=None, min_n=5, min_conf=0.90, label="Crypto-all-N5-C90")

# ══════════════════════════════════════════════════════════════════════════════
# CANDIDATE C: Tag-Expert Consensus — Politics NO, K=50, N=3, >=24h hold
# ══════════════════════════════════════════════════════════════════════════════
log("")
log("=" * 60)
log("CANDIDATE C: Tag-Expert Consensus — Politics NO, K=50, N=3, >=24h hold")
log("=" * 60)

pool_c, pool_c_info = build_pool("Politics", min_markets=20, min_conviction=0.90,
                                  k_cap=50, composite_score=True)
log(f"  Pool: {pool_c_info['n_traders']} traders, avg HR={pool_c_info['avg_hr']:.3f}, excess={pool_c_info['avg_excess_hr']:+.3f}")

log("  Computing vectorized baseline...")
vec_c = vectorized_hr("Politics", "_tv_pool_tmp", position="NO", min_n=3,
                       min_conf=0.60, min_hold_hours=24.0)
log(f"  Vectorized: N={vec_c['n_signals']}, HR={vec_c['hit_rate']:.3f}, hold={vec_c['median_hold_hours']:.0f}h")

log("  Running tick-by-tick simulation...")
tick_c = tick_hr("Politics", pool_c, position="NO", min_n=3, min_conf=0.60,
                  min_hold_hours=24.0, label="Politics-NO-K50-N3-24h")

# ══════════════════════════════════════════════════════════════════════════════
# SUPPLEMENTAL: Politics NO, larger pool (all excess>0), N=3, >=24h
# ══════════════════════════════════════════════════════════════════════════════
log("")
log("=" * 60)
log("SUPPLEMENTAL: Politics NO, all excess>0 pool, N=3, >=24h hold")
log("=" * 60)

pool_c2, pool_c2_info = build_pool("Politics", min_markets=20, min_conviction=0.90,
                                    k_cap=None, composite_score=True)
log(f"  Pool: {pool_c2_info['n_traders']} traders, avg HR={pool_c2_info['avg_hr']:.3f}, excess={pool_c2_info['avg_excess_hr']:+.3f}")

log("  Computing vectorized baseline (all pool, NO, N>=3, >=24h)...")
vec_c2 = vectorized_hr("Politics", "_tv_pool_tmp", position="NO", min_n=3,
                        min_conf=0.60, min_hold_hours=24.0)
log(f"  Vectorized: N={vec_c2['n_signals']}, HR={vec_c2['hit_rate']:.3f}, hold={vec_c2['median_hold_hours']:.0f}h")

log("  Running tick-by-tick simulation (all pool)...")
tick_c2 = tick_hr("Politics", pool_c2, position="NO", min_n=3, min_conf=0.60,
                   min_hold_hours=24.0, label="Politics-NO-all-N3-24h")

# ══════════════════════════════════════════════════════════════════════════════
# Base rates (test period)
# ══════════════════════════════════════════════════════════════════════════════
test_base_rates = {}
for tag in ["Esports", "Crypto", "Politics"]:
    br = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE))
        FROM maker_positions p
        JOIN _tv_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    """).fetchone()[0]
    test_base_rates[tag] = br

log(f"\nTest base rates: {test_base_rates}")

# ══════════════════════════════════════════════════════════════════════════════
# Results compilation
# ══════════════════════════════════════════════════════════════════════════════
log("")
log("=" * 70)
log("FINAL RESULTS")
log("=" * 70)

esports_br = test_base_rates.get("Esports", 0.477)
crypto_br = test_base_rates.get("Crypto", 0.479)
politics_yes_br = test_base_rates.get("Politics", 0.279)
politics_no_br = 1.0 - politics_yes_br

def summary_row(name: str, vec: dict, tick: dict, base_rate: float) -> str:
    tick_n = tick.get("n_signals", 0)
    tick_hr_val = tick.get("hit_rate", None)
    vec_hr = vec.get("hit_rate", 0) or 0
    vec_exc = (vec_hr - base_rate) if vec_hr else 0
    tick_exc = (tick_hr_val - base_rate) if tick_hr_val else None
    degrad = (vec_hr - tick_hr_val) if tick_hr_val else None
    return (f"  {name:<35} Vec: N={vec.get('n_signals',0):4d} HR={vec_hr:.3f} ex={vec_exc:+.3f} | "
            f"Tick: N={tick_n:4d} HR={f'{tick_hr_val:.3f}' if tick_hr_val else 'N/A':>6} "
            f"ex={f'{tick_exc:+.3f}' if tick_exc is not None else 'N/A':>7} "
            f"deg={f'{degrad:+.3f}' if degrad is not None else 'N/A':>7}")

log(summary_row("Esports all-pool N=3 C=0.80", vec_a, tick_a, esports_br))
log(summary_row("Crypto all-pool N=5 C=0.90", vec_b, tick_b, crypto_br))
log(summary_row("Politics NO K50 N=3 >=24h", vec_c, tick_c, politics_no_br))
log(summary_row("Politics NO all N=3 >=24h (supp)", vec_c2, tick_c2, politics_no_br))

# ── Save JSON ─────────────────────────────────────────────────────────────────
results = {
    "generated_at": datetime.now().isoformat(),
    "test_start": TEST_START,
    "test_base_rates": {k: round(v, 4) for k, v in test_base_rates.items()},
    "candidate_A_esports": {
        "label": "Smart Money Pool — Esports all excess>0 pool, N=3, conf>=0.80",
        "pool": pool_a_info,
        "vectorized": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vec_a.items()},
        "tick": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tick_a.items() if k != "signals"},
        "tick_excess_hr": round((tick_a.get("hit_rate") or 0) - esports_br, 4),
        "vectorized_excess_hr": round((vec_a.get("hit_rate") or 0) - esports_br, 4),
        "degradation_pp": round((vec_a.get("hit_rate") or 0) - (tick_a.get("hit_rate") or 0), 4) if tick_a.get("hit_rate") else None,
    },
    "candidate_B_crypto": {
        "label": "Smart Money Pool — Crypto all excess>0 pool, N=5, conf>=0.90",
        "pool": pool_b_info,
        "vectorized": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vec_b.items()},
        "tick": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tick_b.items() if k != "signals"},
        "tick_excess_hr": round((tick_b.get("hit_rate") or 0) - crypto_br, 4),
        "vectorized_excess_hr": round((vec_b.get("hit_rate") or 0) - crypto_br, 4),
        "degradation_pp": round((vec_b.get("hit_rate") or 0) - (tick_b.get("hit_rate") or 0), 4) if tick_b.get("hit_rate") else None,
    },
    "candidate_C_politics_K50": {
        "label": "Tag-Expert Consensus — Politics NO, K=50, N=3, >=24h hold",
        "pool": pool_c_info,
        "vectorized": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vec_c.items()},
        "tick": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tick_c.items() if k != "signals"},
        "tick_excess_hr": round((tick_c.get("hit_rate") or 0) - politics_no_br, 4),
        "vectorized_excess_hr": round((vec_c.get("hit_rate") or 0) - politics_no_br, 4),
        "degradation_pp": round((vec_c.get("hit_rate") or 0) - (tick_c.get("hit_rate") or 0), 4) if tick_c.get("hit_rate") else None,
    },
    "supplemental_C_politics_all": {
        "label": "Politics NO — all excess>0 pool, N=3, >=24h hold",
        "pool": pool_c2_info,
        "vectorized": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vec_c2.items()},
        "tick": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tick_c2.items() if k != "signals"},
        "tick_excess_hr": round((tick_c2.get("hit_rate") or 0) - politics_no_br, 4),
        "vectorized_excess_hr": round((vec_c2.get("hit_rate") or 0) - politics_no_br, 4),
        "degradation_pp": round((vec_c2.get("hit_rate") or 0) - (tick_c2.get("hit_rate") or 0), 4) if tick_c2.get("hit_rate") else None,
    },
}

with open(RESULTS_PATH, "w") as f:
    json.dump(results, f, indent=2, default=str)
log(f"\nResults saved to {RESULTS_PATH}")
log("Done!")
