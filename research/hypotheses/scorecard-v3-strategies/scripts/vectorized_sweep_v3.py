"""Vectorized sweep of all v3 strategy legs.

Test period: 2025-07-01 to 2026-03-01 (8 months).
All results are UPPER BOUNDS — vectorized backtests are 20-40pp optimistic vs tick.

Legs:
  YES:
    Sports YES K=25 N={1,2,3,5}   — v2 vs v3 (BEH gate impact)
    Politics YES K=100 N={1,2,3,5} max_price=0.80 — v2 vs v3
    Crypto YES K=50 N={1,2,3}     max_price=0.65 — v2 vs v3
  NO (hold>=24h filter):
    Politics NO K=100 N={1,2,3}
    Sports NO K=50 N={1,2,3}
  In-play:
    Sports YES K=25 N=1 hold<4h  (dedicated in-play track)

Fill price = Nth trader's chronological entry price (causal, no look-ahead).
Signal count = count(DISTINCT condition_id) at market level.

Output:
  research/hypotheses/scorecard-v3-strategies/discovery/sweep_results.md
  research/hypotheses/scorecard-v3-strategies/discovery/sweep_results.json
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

REPO = "/mnt/nvme/git/polymarket/polymarket"
TRAIN_CUTOFF = "2025-07-01"
TEST_START   = "2025-07-01"
TEST_END     = "2026-03-01"
LOG_FILE     = f"{REPO}/tmp/sweep_v3.log"

os.makedirs(f"{REPO}/tmp", exist_ok=True)

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ─── Load pool builders ───────────────────────────────────────────────────────

def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


log("Loading pool builders...")
v2_mod = _load_module(
    f"{REPO}/research/hypotheses/scorecard-v2-strategies/scripts/build_pools.py",
    "build_pools_v2",
)
v3_mod = _load_module(
    f"{REPO}/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py",
    "build_pools_v3",
)

from research.db import db as get_db
con = get_db().con
log("DuckDB singleton ready.")


# ─── Shared setup ─────────────────────────────────────────────────────────────

v3_mod._ensure_shared_tables(con)
log("Shared tables created (_v3_market_tags, gambling macro).")


# ─── Build all pools ──────────────────────────────────────────────────────────

log("Building YES pools (v2)...")
# v2 shared tables: creates _bp_market_tags, is_gambling_market macro
v2_mod._ensure_shared_tables(con)
# Use internal builders to share the same DuckDB connection
sports_yes_v2 = v2_mod._build_composite_pool(con, "Sports", k=25)
politics_yes_v2 = v2_mod._build_composite_pool(con, "Politics", k=100)
crypto_yes_v2 = v2_mod._build_hr_only_pool(con, "Crypto", k=50)
log(f"  Sports YES v2 K=25: {len(sports_yes_v2)} traders")
log(f"  Politics YES v2 K=100: {len(politics_yes_v2)} traders")
log(f"  Crypto YES v2 K=50: {len(crypto_yes_v2)} traders")

log("Building YES pools (v3)...")
sports_yes_v3 = v3_mod._build_yes_composite_pool(con, "Sports", k=25, train_cutoff=TRAIN_CUTOFF)
politics_yes_v3 = v3_mod._build_yes_composite_pool(con, "Politics", k=100, train_cutoff=TRAIN_CUTOFF)
crypto_yes_v3 = v3_mod._build_yes_composite_pool(con, "Crypto", k=50, train_cutoff=TRAIN_CUTOFF)
log(f"  Sports YES v3 K=25: {len(sports_yes_v3)} traders")
log(f"  Politics YES v3 K=100: {len(politics_yes_v3)} traders")
log(f"  Crypto YES v3 K=50: {len(crypto_yes_v3)} traders")

log("Building NO pools (v3)...")
politics_no_v3 = v3_mod._build_no_composite_pool(con, "Politics", k=100, train_cutoff=TRAIN_CUTOFF)
sports_no_v3 = v3_mod._build_no_composite_pool(con, "Sports", k=50, train_cutoff=TRAIN_CUTOFF)
log(f"  Politics NO v3 K=100: {len(politics_no_v3)} traders")
log(f"  Sports NO v3 K=50: {len(sports_no_v3)} traders")

# Jaccard overlap: v2 vs v3
def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

log(f"Jaccard v2/v3 overlap — Sports: {jaccard(sports_yes_v2, sports_yes_v3):.2f}, "
    f"Politics: {jaccard(politics_yes_v2, politics_yes_v3):.2f}, "
    f"Crypto: {jaccard(crypto_yes_v2, crypto_yes_v3):.2f}")


# ─── Core simulation function ──────────────────────────────────────────────────

def simulate_leg(
    pool: set[str],
    tag_label: str,
    direction: str,          # 'YES' or 'NO'
    n_thresh: int,
    test_start: str = TEST_START,
    test_end: str   = TEST_END,
    max_price: float | None = None,   # price ceiling on signal_price
    min_hold_hours: float | None = None,  # hold >= N hours (for NO legs)
    max_hold_hours: float | None = None,  # hold < N hours (for in-play)
    monthly_breakdown: bool = True,
) -> dict:
    """
    Market-level vectorized simulation.

    Signal = condition_id where exactly N-th pool trader (chronologically) entered.
    Fill price = N-th trader's actual entry price (causal, no look-ahead).
    PnL = (1 - signal_price) if correct, else -signal_price.

    Returns dict with n_signals, hr, excess_hr, avg_pnl_usd, base_rate,
    avg_hold_hours, monthly_breakdown (list of dicts).
    """
    if not pool:
        return {"error": "empty pool"}

    pool_lower = {t.lower() for t in pool}
    pool_list  = ", ".join(f"'{t}'" for t in pool_lower)
    tag        = tag_label.lower()
    dir_col    = "p.yes_won" if direction == "YES" else "(NOT p.yes_won)"  # correct for direction
    position_filter = f"p.position = '{direction}'"
    label      = f"{tag}_{direction.lower()}"

    # Step 1: test positions (market-level, causal filter)
    # Entry price: YES positions use yes_entry_data (price_x_vol/volume).
    # NO positions use 1 - YES price from yes_entry_data where available.
    # Rows without valid price data (yed join fails or price out of 0-1 range) are excluded.
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3sw_test_{label}_{n_thresh} AS
    SELECT
        p.condition_id, p.trader, p.first_trade,
        CAST(({dir_col}) AS DOUBLE) AS correct,
        CASE
            WHEN p.position = 'YES' THEN yed.price_x_vol / yed.volume
            ELSE 1.0 - (yed.price_x_vol / yed.volume)
        END AS entry_price,
        p.resolved_at,
        (epoch(p.resolved_at) - epoch(p.first_trade)) / 3600.0 AS hold_hours
    FROM maker_positions p
    JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
    JOIN yes_entry_data yed
         ON p.trader = yed.trader AND p.condition_id = yed.condition_id
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'  -- causal: no training entries
      AND p.correct IS NOT NULL
      AND p.volume > 0
      AND yed.volume > 0
      AND yed.price_x_vol / yed.volume BETWEEN 0.01 AND 0.99
      AND lower(p.trader) IN ({pool_list})
    """)

    cnt = con.execute(f"SELECT count(*) FROM _v3sw_test_{label}_{n_thresh}").fetchone()[0]
    if cnt == 0:
        return {"error": "no test positions"}

    # Step 2: ranked entries per market (chronological order)
    con.execute(f"""
    CREATE OR REPLACE TABLE _v3sw_ranked_{label}_{n_thresh} AS
    SELECT
        condition_id, trader, first_trade, entry_price, correct, resolved_at, hold_hours,
        ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade ASC) AS entry_rank,
        COUNT(*) OVER (PARTITION BY condition_id) AS total_pool_entries
    FROM _v3sw_test_{label}_{n_thresh}
    WHERE entry_price IS NOT NULL
      AND entry_price > 0
    """)

    # Step 3: Nth entry = signal (causal fill price)
    price_filter = f"AND entry_price <= {max_price}" if max_price is not None else ""
    min_hold_filter = f"AND hold_hours >= {min_hold_hours}" if min_hold_hours is not None else ""
    max_hold_filter = f"AND hold_hours < {max_hold_hours}" if max_hold_hours is not None else ""

    con.execute(f"""
    CREATE OR REPLACE TABLE _v3sw_signals_{label}_{n_thresh} AS
    SELECT
        condition_id,
        entry_price AS signal_price,
        first_trade AS signal_time,
        correct,
        resolved_at,
        hold_hours,
        CAST(resolved_at AS DATE) AS resolved_month
    FROM _v3sw_ranked_{label}_{n_thresh}
    WHERE entry_rank = {n_thresh}
      AND total_pool_entries >= {n_thresh}
      {price_filter}
      {min_hold_filter}
      {max_hold_filter}
    """)

    n_signals = con.execute(
        f"SELECT count(*) FROM _v3sw_signals_{label}_{n_thresh}"
    ).fetchone()[0]

    if n_signals == 0:
        return {"error": "no signals after filters"}

    # Step 4: aggregate metrics
    row = con.execute(f"""
    SELECT
        count(*) AS n_signals,
        avg(correct) AS hit_rate,
        avg(CASE WHEN correct = 1 THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl,
        median(hold_hours) AS med_hold_hours,
        avg(signal_price) AS avg_signal_price
    FROM _v3sw_signals_{label}_{n_thresh}
    """).fetchone()

    n_signals, hr, avg_pnl, med_hold, avg_price = row

    # Base rate (direction-specific, computed from all test positions for this tag/direction)
    base_row = con.execute(f"""
    SELECT avg(CAST(({dir_col}) AS DOUBLE))
    FROM maker_positions p
    JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = '{tag_label}'
      AND {position_filter}
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND p.correct IS NOT NULL
    """).fetchone()
    base_rate = base_row[0] if base_row and base_row[0] is not None else 0.5

    result = {
        "n_signals": int(n_signals),
        "hit_rate": round(float(hr), 4),
        "excess_hr": round(float(hr) - float(base_rate), 4),
        "base_rate": round(float(base_rate), 4),
        "avg_pnl_usd": round(float(avg_pnl), 4),
        "med_hold_hours": round(float(med_hold), 1) if med_hold else None,
        "avg_signal_price": round(float(avg_price), 4) if avg_price else None,
    }

    # Monthly breakdown
    if monthly_breakdown:
        months = con.execute(f"""
        SELECT
            date_trunc('month', CAST(resolved_at AS TIMESTAMP)) AS month,
            count(*) AS n_signals,
            avg(correct) AS hit_rate,
            avg(CASE WHEN correct = 1 THEN 1.0 - signal_price ELSE -signal_price END) AS avg_pnl
        FROM _v3sw_signals_{label}_{n_thresh}
        GROUP BY date_trunc('month', CAST(resolved_at AS TIMESTAMP))
        ORDER BY month
        """).fetchall()
        result["monthly"] = [
            {
                "month": str(m[0])[:7],
                "n_signals": int(m[1]),
                "hit_rate": round(float(m[2]), 4),
                "avg_pnl": round(float(m[3]), 4),
            }
            for m in months
        ]

    return result


# ─── Run all sweeps ────────────────────────────────────────────────────────────

results = {}
t0_all = time.time()


def run_sweep(name: str, pool: set, tag: str, direction: str,
              n_values: list[int], **kwargs) -> dict:
    log(f"Running {name}...")
    sweep = {}
    for n in n_values:
        key = f"n{n}"
        r = simulate_leg(pool, tag, direction, n, **kwargs)
        sweep[key] = r
        if "error" not in r:
            log(f"  N={n}: {r['n_signals']} signals, HR={r['hit_rate']:.1%}, "
                f"excess={r['excess_hr']:+.1%}, PnL={r['avg_pnl_usd']:+.4f}/trade")
        else:
            log(f"  N={n}: {r['error']}")
    return sweep


# ── Sports YES: v2 vs v3 ──────────────────────────────────────────────────────
log("=== Sports YES ===")
results["sports_yes_v2"] = run_sweep(
    "Sports YES v2", sports_yes_v2, "Sports", "YES", [1, 2, 3, 5],
)
results["sports_yes_v3"] = run_sweep(
    "Sports YES v3 (BEH gate)", sports_yes_v3, "Sports", "YES", [1, 2, 3, 5],
)

# ── Politics YES: v2 vs v3, max_price=0.80 ────────────────────────────────────
log("=== Politics YES ===")
results["politics_yes_v2"] = run_sweep(
    "Politics YES v2", politics_yes_v2, "Politics", "YES", [1, 2, 3, 5],
    max_price=0.80,
)
results["politics_yes_v3"] = run_sweep(
    "Politics YES v3 (BEH gate)", politics_yes_v3, "Politics", "YES", [1, 2, 3, 5],
    max_price=0.80,
)

# ── Crypto YES: v2 vs v3, max_price=0.65 ─────────────────────────────────────
log("=== Crypto YES ===")
results["crypto_yes_v2"] = run_sweep(
    "Crypto YES v2", crypto_yes_v2, "Crypto", "YES", [1, 2, 3],
    max_price=0.65,
)
results["crypto_yes_v3"] = run_sweep(
    "Crypto YES v3 (BEH gate)", crypto_yes_v3, "Crypto", "YES", [1, 2, 3],
    max_price=0.65,
)

# ── Politics NO: hold>=24h ────────────────────────────────────────────────────
log("=== Politics NO (hold>=24h) ===")
results["politics_no_v3"] = run_sweep(
    "Politics NO v3", politics_no_v3, "Politics", "NO", [1, 2, 3],
    min_hold_hours=24.0,
)

# ── Sports NO: hold>=24h ──────────────────────────────────────────────────────
log("=== Sports NO (hold>=24h) ===")
results["sports_no_v3"] = run_sweep(
    "Sports NO v3", sports_no_v3, "Sports", "NO", [1, 2, 3],
    min_hold_hours=24.0,
)

# ── In-Play Track: Sports YES K=25 N=1 hold<4h ────────────────────────────────
log("=== Sports YES In-Play (hold<4h) ===")
results["sports_inplay_v3"] = run_sweep(
    "Sports YES In-Play v3", sports_yes_v3, "Sports", "YES", [1],
    max_hold_hours=4.0,
)

log(f"All sweeps done in {time.time()-t0_all:.1f}s")


# ─── Save JSON ────────────────────────────────────────────────────────────────

out_dir = Path(f"{REPO}/research/hypotheses/scorecard-v3-strategies/discovery")
out_dir.mkdir(parents=True, exist_ok=True)

# Add metadata
output = {
    "metadata": {
        "train_cutoff": TRAIN_CUTOFF,
        "test_start": TEST_START,
        "test_end": TEST_END,
        "pool_sizes": {
            "sports_yes_v2": len(sports_yes_v2),
            "sports_yes_v3": len(sports_yes_v3),
            "politics_yes_v2": len(politics_yes_v2),
            "politics_yes_v3": len(politics_yes_v3),
            "crypto_yes_v2": len(crypto_yes_v2),
            "crypto_yes_v3": len(crypto_yes_v3),
            "politics_no_v3": len(politics_no_v3),
            "sports_no_v3": len(sports_no_v3),
        },
        "jaccard_v2_v3": {
            "sports_yes": round(jaccard(sports_yes_v2, sports_yes_v3), 3),
            "politics_yes": round(jaccard(politics_yes_v2, politics_yes_v3), 3),
            "crypto_yes": round(jaccard(crypto_yes_v2, crypto_yes_v3), 3),
        },
        "note": "ALL RESULTS ARE UPPER BOUNDS. Vectorized backtests 20-40pp optimistic vs tick.",
    },
    "results": results,
}

json_path = out_dir / "sweep_results.json"
with open(json_path, "w") as f:
    json.dump(output, f, indent=2, default=str)
log(f"Saved JSON: {json_path}")


# ─── Generate markdown report ─────────────────────────────────────────────────

def fmt_monthly(monthly: list[dict]) -> str:
    if not monthly:
        return "  _no data_"
    lines = ["  | Month | Signals | HR | PnL/trade |",
             "  |-------|---------|----|-----------| "]
    for m in monthly:
        lines.append(
            f"  | {m['month']} | {m['n_signals']} | {m['hit_rate']:.1%} | {m['avg_pnl']:+.4f} |"
        )
    return "\n".join(lines)


def fmt_leg(leg: dict, n: str) -> str:
    r = leg.get(n, {})
    if "error" in r:
        return f"  {n}: {r['error']}"
    return (
        f"  {n}: {r['n_signals']} signals | HR={r['hit_rate']:.1%} "
        f"(+{r['excess_hr']:+.1%} vs {r['base_rate']:.1%} base) | "
        f"PnL={r['avg_pnl_usd']:+.4f}/trade | hold={r.get('med_hold_hours', '?')}h | "
        f"price={r.get('avg_signal_price', '?'):.2f}"
    )


md_lines = [
    "# v3 Vectorized Sweep Results",
    "",
    "> **ALL VALUES ARE UPPER BOUNDS** — vectorized backtests 20-40pp optimistic vs tick.",
    "",
    f"- Train: resolved < {TRAIN_CUTOFF}",
    f"- Test: {TEST_START} → {TEST_END} (8 months)",
    "",
    "## Pool Sizes & v2/v3 Overlap",
    "",
    "| Leg | v2 Pool | v3 Pool (BEH gate) | Jaccard |",
    "|-----|---------|-------------------|---------|",
    f"| Sports YES K=25 | {len(sports_yes_v2)} | {len(sports_yes_v3)} | {jaccard(sports_yes_v2, sports_yes_v3):.2f} |",
    f"| Politics YES K=100 | {len(politics_yes_v2)} | {len(politics_yes_v3)} | {jaccard(politics_yes_v2, politics_yes_v3):.2f} |",
    f"| Crypto YES K=50 | {len(crypto_yes_v2)} | {len(crypto_yes_v3)} | {jaccard(crypto_yes_v2, crypto_yes_v3):.2f} |",
    f"| Politics NO K=100 | — | {len(politics_no_v3)} | — |",
    f"| Sports NO K=50 | — | {len(sports_no_v3)} | — |",
    "",
]

# YES legs: v2 vs v3 side-by-side
for tag, v2_key, v3_key, label, n_vals, note in [
    ("Sports", "sports_yes_v2", "sports_yes_v3", "Sports YES K=25", [1, 2, 3, 5], ""),
    ("Politics", "politics_yes_v2", "politics_yes_v3", "Politics YES K=100 (max_price=0.80)", [1, 2, 3, 5], ""),
    ("Crypto", "crypto_yes_v2", "crypto_yes_v3", "Crypto YES K=50 (max_price=0.65)", [1, 2, 3], ""),
]:
    md_lines += [f"## {label}", ""]
    if note:
        md_lines += [f"_{note}_", ""]

    for n in n_vals:
        nk = f"n{n}"
        r_v2 = results.get(v2_key, {}).get(nk, {})
        r_v3 = results.get(v3_key, {}).get(nk, {})

        md_lines.append(f"### N={n}")
        md_lines.append("")

        if "error" not in r_v2 and "error" not in r_v3:
            md_lines += [
                "| Metric | v2 (no BEH) | v3 (BEH gate) | Delta |",
                "|--------|------------|--------------|-------|",
                f"| Signals | {r_v2.get('n_signals','?')} | {r_v3.get('n_signals','?')} | — |",
                f"| Hit Rate | {r_v2.get('hit_rate',0):.1%} | {r_v3.get('hit_rate',0):.1%} | {r_v3.get('hit_rate',0)-r_v2.get('hit_rate',0):+.1%} |",
                f"| Excess HR | {r_v2.get('excess_hr',0):+.1%} | {r_v3.get('excess_hr',0):+.1%} | {r_v3.get('excess_hr',0)-r_v2.get('excess_hr',0):+.1%} |",
                f"| Base Rate | {r_v2.get('base_rate',0):.1%} | {r_v3.get('base_rate',0):.1%} | — |",
                f"| Avg PnL/trade | {r_v2.get('avg_pnl_usd',0):+.4f} | {r_v3.get('avg_pnl_usd',0):+.4f} | {r_v3.get('avg_pnl_usd',0)-r_v2.get('avg_pnl_usd',0):+.4f} |",
                f"| Med hold (h) | {r_v2.get('med_hold_hours','?')} | {r_v3.get('med_hold_hours','?')} | — |",
                f"| Avg signal price | {r_v2.get('avg_signal_price','?'):.2f} | {r_v3.get('avg_signal_price','?'):.2f} | — |",
            ]
        elif "error" not in r_v2:
            md_lines.append(f"v2: {fmt_leg(results[v2_key], nk)}")
            md_lines.append(f"v3: error — {r_v3.get('error', '?')}")
        elif "error" not in r_v3:
            md_lines.append(f"v2: error — {r_v2.get('error', '?')}")
            md_lines.append(f"v3: {fmt_leg(results[v3_key], nk)}")
        else:
            md_lines.append(f"v2: {r_v2.get('error','?')}, v3: {r_v3.get('error','?')}")

        md_lines.append("")

        # Monthly for v3
        if "monthly" in r_v3:
            md_lines.append("**v3 Monthly Breakdown:**")
            md_lines.append("")
            md_lines.append(fmt_monthly(r_v3["monthly"]))
            md_lines.append("")


# NO legs
for key, label, n_vals, note in [
    ("politics_no_v3", "Politics NO K=100 (hold>=24h)", [1, 2, 3], ""),
    ("sports_no_v3", "Sports NO K=50 (hold>=24h)", [1, 2, 3], "In-play dominated — thin signal count expected"),
]:
    md_lines += [f"## {label}", ""]
    if note:
        md_lines += [f"_{note}_", ""]

    for n in n_vals:
        nk = f"n{n}"
        r = results.get(key, {}).get(nk, {})
        md_lines.append(f"### N={n}")
        md_lines.append("")
        if "error" not in r:
            md_lines += [
                f"- Signals: {r['n_signals']}",
                f"- HR: {r['hit_rate']:.1%} (+{r['excess_hr']:+.1%} vs {r['base_rate']:.1%} base)",
                f"- PnL: {r['avg_pnl_usd']:+.4f}/trade",
                f"- Med hold: {r.get('med_hold_hours','?')}h",
                f"- Avg signal price: {r.get('avg_signal_price', '?'):.2f}",
                "",
            ]
            if "monthly" in r:
                md_lines.append("Monthly:")
                md_lines.append("")
                md_lines.append(fmt_monthly(r["monthly"]))
                md_lines.append("")
        else:
            md_lines.append(f"Error: {r.get('error')}")
            md_lines.append("")


# In-Play track
md_lines += [
    "## In-Play Track: Sports YES K=25 N=1 (hold<4h)",
    "",
    "_Dedicated in-play sub-track. Sub-second WebSocket delivery means no latency penalty in production._",
    "_These traders enter during live events — high HR but fill model gap may be significant._",
    "",
]
r = results.get("sports_inplay_v3", {}).get("n1", {})
if "error" not in r:
    md_lines += [
        f"- Signals: {r['n_signals']}",
        f"- HR: {r['hit_rate']:.1%} (+{r['excess_hr']:+.1%} vs {r['base_rate']:.1%} base)",
        f"- PnL: {r['avg_pnl_usd']:+.4f}/trade",
        f"- Med hold: {r.get('med_hold_hours','?')}h",
        f"- Avg signal price: {r.get('avg_signal_price', '?'):.2f}",
        "",
    ]
    if "monthly" in r:
        md_lines.append("Monthly:")
        md_lines.append("")
        md_lines.append(fmt_monthly(r["monthly"]))
        md_lines.append("")
else:
    md_lines.append(f"Error: {r.get('error')}")
    md_lines.append("")


# Summary table
md_lines += [
    "## Summary — Best Configs (v3)",
    "",
    "| Leg | N | Signals/8mo | HR | Excess HR | PnL/trade |",
    "|-----|---|------------|----|-----------|-----------| ",
]

def summary_row(leg_name: str, key: str, n: int, label: str) -> str:
    r = results.get(key, {}).get(f"n{n}", {})
    if "error" in r:
        return f"| {label} | {n} | error | — | — | — |"
    return (
        f"| {label} | {n} | {r['n_signals']} | {r['hit_rate']:.1%} | "
        f"{r['excess_hr']:+.1%} | {r['avg_pnl_usd']:+.4f} |"
    )

# Best N: highest PnL with >= 10 signals total (practical minimum)
for key, label, ns in [
    ("sports_yes_v3", "Sports YES v3 K=25", [1, 2, 3, 5]),
    ("politics_yes_v3", "Politics YES v3 K=100 p≤0.80", [1, 2, 3, 5]),
    ("crypto_yes_v3", "Crypto YES v3 K=50 p≤0.65", [1, 2, 3]),
    ("politics_no_v3", "Politics NO v3 K=100 h≥24h", [1, 2, 3]),
    ("sports_no_v3", "Sports NO v3 K=50 h≥24h", [1, 2, 3]),
]:
    best_n, best_r = None, None
    for n in ns:
        r = results.get(key, {}).get(f"n{n}", {})
        if "error" not in r and r.get("n_signals", 0) >= 10:
            if best_r is None or r["avg_pnl_usd"] > best_r["avg_pnl_usd"]:
                best_n, best_r = n, r
    if best_n is not None:
        md_lines.append(summary_row(key, key, best_n, label))
    else:
        # Fall back to N=1 even if thin
        md_lines.append(summary_row(key, key, 1, label))
r_ip = results.get("sports_inplay_v3", {}).get("n1", {})
if "error" not in r_ip:
    md_lines.append(
        f"| Sports YES In-Play v3 | 1 | {r_ip['n_signals']} | {r_ip['hit_rate']:.1%} | "
        f"{r_ip['excess_hr']:+.1%} | {r_ip['avg_pnl_usd']:+.4f} |"
    )

md_lines += [
    "",
    "## Notes",
    "",
    "- BEH gate (`bucket_excess_hr >= 0.02`): removes traders whose 'skill' is near-certainty bets",
    "- Causal fill price: Nth trader's chronological entry price (NOT avg across pool)",
    "- Phantom test signal filter: `first_trade >= test_start` (causal)",
    "- NO direction hold filter: `hold >= 24h` removes in-play contamination",
    "- In-play track: sub-second WebSocket delivery in production (no latency penalty)",
    "- v2 tick reference: Sports YES K=25 N=3 → +39.8pp tick, Sharpe=11.94 (6-month window)",
    "",
]

md_path = out_dir / "sweep_results.md"
with open(md_path, "w") as f:
    f.write("\n".join(md_lines))
log(f"Saved MD: {md_path}")

log("Done.")
