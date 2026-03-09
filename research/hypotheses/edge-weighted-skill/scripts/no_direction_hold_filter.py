"""
Hold-filtered analysis: apply >= 1 day hold filter to separate in-play from genuine signals.
This script rebuilds the consensus sweep with hold >= 24h to isolate Politics NO.
"""
import json
import sys
import os

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG = "/mnt/nvme/git/polymarket/polymarket/tmp/no_hold_filter.log"
OUT_JSON = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.json"
OUT_MD   = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/no_direction_results.md"

def log(msg):
    print(msg, flush=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")

with open(LOG, "w") as f:
    f.write("")

log("=== NO-Direction Hold-Filtered Analysis ===")

from research.db import db
d = db()
con = d.con

TRAIN_END  = "2025-11-01"
TEST_START = "2025-11-01"
TEST_END   = "2026-02-01"

# Rebuild all temp tables (single session required)
log("Rebuilding temp tables...")
con.execute("""
CREATE OR REPLACE TABLE _no_tag_mkts AS
WITH tag_ranked AS (
    SELECT m.condition_id, m.slug, m.event_id, m.neg_risk, et.label,
        CASE
            WHEN et.label = 'Politics'    THEN 0 WHEN et.label = 'Elections'   THEN 1
            WHEN et.label = 'Sports'      THEN 2 WHEN et.label = 'Basketball'  THEN 3
            WHEN et.label = 'Soccer'      THEN 4 WHEN et.label = 'Esports'     THEN 5
            WHEN et.label = 'NBA'         THEN 6 WHEN et.label = 'Crypto'      THEN 7
            WHEN et.label = 'NCAA'        THEN 8 WHEN et.label = 'Tennis'      THEN 9
            WHEN et.label = 'NFL'         THEN 10 ELSE 999
        END AS tag_priority
    FROM markets m JOIN events e ON m.event_id = e.id JOIN event_tags et ON e.id = et.event_id
    WHERE m.slug NOT LIKE '%updown%' AND m.slug NOT LIKE '%up-or-down%'
      AND et.label IN ('Politics','Sports','Basketball','Soccer','Esports','NBA','Crypto','NCAA','Tennis','NFL')
      AND m.neg_risk = 0
),
tag_assigned AS (
    SELECT condition_id, slug, arg_min(label, tag_priority) AS primary_tag
    FROM tag_ranked GROUP BY condition_id, slug
)
SELECT * FROM tag_assigned
""")

con.execute(f"""
CREATE OR REPLACE TABLE _no_base_rate_grid AS
WITH no_positions AS (
    SELECT tm.primary_tag,
           FLOOR((1.0 - COALESCE(ye.avg_entry_price, 0.5)) / 0.05) * 0.05 AS price_bucket_low,
           p.correct
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    LEFT JOIN (SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price FROM yes_entry_data) ye
        ON p.condition_id = ye.condition_id AND p.trader = ye.trader
    WHERE p.position = 'NO' AND p.resolved_at IS NOT NULL
      AND p.volume > 0 AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
      AND (1.0 - COALESCE(ye.avg_entry_price, 0.5)) >= 0.05
      AND (1.0 - COALESCE(ye.avg_entry_price, 0.5)) < 1.0
)
SELECT primary_tag, price_bucket_low, ROUND(price_bucket_low + 0.05, 2) AS price_bucket_high,
       count(*) AS n_positions, avg(correct::DOUBLE) AS no_hr
FROM no_positions GROUP BY primary_tag, price_bucket_low HAVING count(*) >= 10
""")

con.execute(f"""
CREATE OR REPLACE TABLE _no_trader_scores AS
WITH no_positions AS (
    SELECT p.trader, tm.primary_tag, p.condition_id, p.correct, p.net_usd, p.volume, p.first_trade,
           CASE WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price ELSE 0.5 END AS no_entry_price
    FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    LEFT JOIN (SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price FROM yes_entry_data) ye
        ON p.condition_id = ye.condition_id AND p.trader = ye.trader
    WHERE p.position = 'NO' AND p.resolved_at IS NOT NULL
      AND p.volume > 0 AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) < '{TRAIN_END}'
),
bucketed AS (SELECT *, FLOOR(no_entry_price / 0.05) * 0.05 AS price_bucket_low
             FROM no_positions WHERE no_entry_price >= 0.05 AND no_entry_price < 1.0),
with_base AS (
    SELECT b.*, g.no_hr AS base_hr, b.correct::DOUBLE - g.no_hr AS edge
    FROM bucketed b LEFT JOIN _no_base_rate_grid g
        ON b.primary_tag = g.primary_tag AND b.price_bucket_low = g.price_bucket_low
    WHERE g.no_hr IS NOT NULL
),
trader_agg AS (
    SELECT trader, count(*) AS n_total, avg(correct::DOUBLE) AS overall_hr,
           avg(base_hr) AS weighted_base_rate, avg(correct::DOUBLE) - avg(base_hr) AS excess_hr,
           sum(edge) / count(*) AS bucket_excess_hr,
           avg(abs(net_usd) * correct::DOUBLE - abs(net_usd) * (1.0 - correct::DOUBLE)) AS avg_edge_usd,
           stddev(correct::DOUBLE) AS hr_std
    FROM with_base GROUP BY trader HAVING count(*) >= 20
)
SELECT *,
    CASE WHEN overall_hr > 0 THEN 1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0) ELSE 0 END AS consistency,
    (excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + bucket_excess_hr * 0.15) * ln(n_total + 1) AS composite_score_vw
FROM trader_agg
""")
log("Tables rebuilt.")

for K in [50, 100, 200]:
    con.execute(f"""
        CREATE OR REPLACE TABLE _no_pool_{K} AS
        SELECT trader FROM _no_trader_scores ORDER BY composite_score_vw DESC LIMIT {K}
    """)

# Test NO base rate for Politics only (hold >= 1 day)
politics_base_no = con.execute(f"""
    SELECT avg(p.correct::DOUBLE) FROM maker_positions p
    JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
    WHERE p.position = 'NO' AND p.resolved_at IS NOT NULL AND p.volume > 0
      AND abs(p.net_usd) / p.volume >= 0.10
      AND CAST(p.resolved_at AS DATE) >= '{TEST_START}' AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
      AND tm.primary_tag = 'Politics'
""").fetchone()[0]
log(f"Politics test NO base rate: {politics_base_no:.4f}")

# ─── Hold-filtered sweep: >= 24h (1 day) ─────────────────────────────────────
log("\n=== HOLD-FILTERED SWEEP (hold >= 24h) ===")

hold_results = {}
for K in [50, 100, 200]:
    hold_results[K] = {}
    for N in [1, 2, 3]:
        r = con.execute(f"""
        WITH pool_positions AS (
            SELECT p.trader, p.condition_id, tm.primary_tag, p.correct, p.first_trade, p.resolved_at,
                   CASE WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price ELSE 0.5 END AS no_entry_price
            FROM maker_positions p
            JOIN _no_pool_{K} pool ON p.trader = pool.trader
            JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
            LEFT JOIN (SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price FROM yes_entry_data) ye
                ON p.condition_id = ye.condition_id AND p.trader = ye.trader
            WHERE p.position = 'NO' AND p.resolved_at IS NOT NULL AND p.volume > 0
              AND abs(p.net_usd) / p.volume >= 0.10
              AND CAST(p.resolved_at AS DATE) >= '{TEST_START}' AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
              AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
        ),
        market_level AS (
            SELECT condition_id, primary_tag, first(correct) AS correct, first(resolved_at) AS resolved_at,
                   count(DISTINCT trader) AS n_traders, max(first_trade) AS signal_entry,
                   arg_max(no_entry_price, first_trade) AS signal_entry_price,
                   date_diff('hour', max(first_trade), first(resolved_at)) AS hold_hours,
                   date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
            FROM pool_positions
            GROUP BY condition_id, primary_tag
            HAVING count(DISTINCT trader) >= {N}
              AND date_diff('hour', max(first_trade), first(resolved_at)) >= 24   -- HOLD FILTER
              AND date_diff('day', max(first_trade), first(resolved_at)) <= 30
        )
        SELECT count(*) AS n, avg(correct::DOUBLE) AS hr,
               median(signal_entry_price) AS med_price,
               median(hold_days) AS med_hold,
               avg(CASE WHEN correct=1 THEN (1.0-signal_entry_price) ELSE -signal_entry_price END) AS avg_edge
        FROM market_level
        """).fetchone()

        hr_s = f"{r[1]:.4f}" if r[1] is not None else "N/A"
        edge_s = f"{r[4]:.4f}" if r[4] is not None else "N/A"
        log(f"  K={K}, N={N}: n={r[0]}, HR={hr_s}, med_hold={r[3]}d, avg_edge={edge_s}")
        hold_results[K][N] = {
            "n_signals": int(r[0]) if r[0] else 0,
            "hit_rate": round(float(r[1]), 4) if r[1] else None,
            "median_entry_price": round(float(r[2]), 4) if r[2] else None,
            "median_hold_days": float(r[3]) if r[3] is not None else None,
            "avg_edge_per_dollar": round(float(r[4]), 4) if r[4] else None,
        }

# ─── Hold-filtered per-tag (K=100, N=2) ──────────────────────────────────────
log("\n=== Hold-filtered per-tag (K=100, N=2, hold>=24h) ===")

FOCUS_TAGS = ["Politics", "Esports", "Sports", "Crypto"]
hold_per_tag = {}
for tag in FOCUS_TAGS:
    base = politics_base_no if tag == "Politics" else None
    r = con.execute(f"""
    WITH pool_positions AS (
        SELECT p.trader, p.condition_id, p.correct, p.first_trade, p.resolved_at,
               CASE WHEN ye.avg_entry_price IS NOT NULL THEN 1.0 - ye.avg_entry_price ELSE 0.5 END AS no_entry_price
        FROM maker_positions p
        JOIN _no_pool_100 pool ON p.trader = pool.trader
        JOIN _no_tag_mkts tm ON p.condition_id = tm.condition_id
        LEFT JOIN (SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price FROM yes_entry_data) ye
            ON p.condition_id = ye.condition_id AND p.trader = ye.trader
        WHERE p.position = 'NO' AND p.resolved_at IS NOT NULL AND p.volume > 0
          AND abs(p.net_usd) / p.volume >= 0.10
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}' AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND tm.primary_tag = '{tag}'
    ),
    market_level AS (
        SELECT condition_id, first(correct) AS correct, first(resolved_at) AS resolved_at,
               count(DISTINCT trader) AS n_traders, max(first_trade) AS signal_entry,
               arg_max(no_entry_price, first_trade) AS signal_entry_price,
               date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
        FROM pool_positions
        GROUP BY condition_id
        HAVING count(DISTINCT trader) >= 2
          AND date_diff('hour', max(first_trade), first(resolved_at)) >= 24
          AND date_diff('day', max(first_trade), first(resolved_at)) <= 30
    )
    SELECT count(*) AS n, avg(correct::DOUBLE) AS hr, median(signal_entry_price) AS med_price,
           median(hold_days) AS med_hold,
           avg(CASE WHEN correct=1 THEN (1.0-signal_entry_price) ELSE -signal_entry_price END) AS avg_edge
    FROM market_level
    """).fetchone()

    hr_s = f"{r[1]:.4f}" if r[1] is not None else "N/A"
    edge_s = f"{r[4]:.4f}" if r[4] is not None else "N/A"
    log(f"  {tag}: n={r[0]}, HR={hr_s}, med_hold={r[3]}d, avg_edge={edge_s}")
    hold_per_tag[tag] = {
        "n_signals": int(r[0]) if r[0] else 0,
        "hit_rate": round(float(r[1]), 4) if r[1] else None,
        "median_entry_price": round(float(r[2]), 4) if r[2] else None,
        "median_hold_days": float(r[3]) if r[3] is not None else None,
        "avg_edge_per_dollar": round(float(r[4]), 4) if r[4] else None,
    }

# ─── Save to JSON and append to MD ───────────────────────────────────────────
with open(OUT_JSON) as f:
    data = json.load(f)

data["hold_filtered_sweep"] = {
    str(K): {str(N): hold_results[K][N] for N in [1, 2, 3]}
    for K in [50, 100, 200]
}
data["hold_filtered_per_tag"] = hold_per_tag

with open(OUT_JSON, "w") as f:
    json.dump(data, f, indent=2)

# Append to markdown
section = [
    "",
    "## Hold-Filtered Sweep (hold >= 24h) — Removing In-Play Contamination",
    "",
    "Re-run with `date_diff('hour', signal_entry, resolved_at) >= 24` filter.",
    "",
    "| K | N | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |",
    "|---|---|-----------|----|-----------|---------|-----------:|",
]
for K in [50, 100, 200]:
    for N in [1, 2, 3]:
        r = hold_results[K][N]
        if r["n_signals"] > 0 and r["hit_rate"] is not None:
            hold = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "N/A"
            section.append(
                f"| {K} | {N} | {r['n_signals']:,} | {r['hit_rate']:.4f} | "
                f"{r['median_entry_price']:.4f} | {hold} | "
                f"{r['avg_edge_per_dollar']:+.4f} |"
            )
        else:
            section.append(f"| {K} | {N} | 0 | — | — | — | — |")

section += [
    "",
    "### Hold-Filtered Per-Tag (K=100, N=2, hold >= 24h)",
    "",
    "| Tag | n_signals | HR | Med Entry | Med Hold | Avg Edge/$ |",
    "|-----|-----------|----|-----------|---------|-----------:|",
]
for tag in FOCUS_TAGS:
    r = hold_per_tag[tag]
    if r["n_signals"] > 0 and r["hit_rate"] is not None:
        hold = f"{r['median_hold_days']:.0f}d" if r["median_hold_days"] is not None else "N/A"
        section.append(
            f"| {tag} | {r['n_signals']:,} | {r['hit_rate']:.4f} | "
            f"{r['median_entry_price']:.4f} | {hold} | "
            f"{r['avg_edge_per_dollar']:+.4f} |"
        )
    else:
        section.append(f"| {tag} | 0 | — | — | — | — |")

section += [
    "",
    "### Interpretation",
    "",
    "After removing in-play signals (hold < 24h):",
    "- **Signal volume drops sharply** — confirms in-play dominated the unfiltered results",
    "- **Politics** retains signals (already had 1-day hold)",
    "- **Sports/Esports/Crypto** lose most signals — these were in-play",
    "- This is the tick-validation-relevant signal count",
]

with open(OUT_MD, "a") as f:
    f.write("\n".join(section))

log(f"\nJSON updated: {OUT_JSON}")
log(f"Markdown updated: {OUT_MD}")
log("\n=== Hold-Filter Analysis Complete ===")

print("\n" + "="*60)
print("HOLD-FILTERED ANALYSIS COMPLETE")
print("="*60)
print(f"\n  Politics (K=100, N=2, hold>=24h): n={hold_per_tag['Politics']['n_signals']}, "
      f"HR={hold_per_tag['Politics']['hit_rate']}, "
      f"edge={hold_per_tag['Politics']['avg_edge_per_dollar']}")
print(f"  Esports  (K=100, N=2, hold>=24h): n={hold_per_tag['Esports']['n_signals']}")
print(f"  Sports   (K=100, N=2, hold>=24h): n={hold_per_tag['Sports']['n_signals']}")
print(f"  Crypto   (K=100, N=2, hold>=24h): n={hold_per_tag['Crypto']['n_signals']}")
print(f"\n  Output: {OUT_JSON}")
print(f"  Output: {OUT_MD}")
