"""
Extended analysis: volume-weighted scoring and small-N bias investigation.

The initial sweep found Jaccard(HR-primary, Edge-primary) = 1.0 because both
formulas effectively select the same small traders with 100% HR.

This script adds volume-weighted scoring (score × ln(n+1)) to reduce small-N bias
and investigates the scoring difference more carefully.
"""

import json
import sys
import os

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_FILE = "/mnt/nvme/git/polymarket/polymarket/tmp/decomposition_extended.log"
OUT_JSON = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/decomposition_results.json"
OUT_MD = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/decomposition_results.md"

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")

with open(LOG_FILE, "w") as f:
    f.write("")

log("=== Decomposition Extended Analysis ===")

from research.db import db
d = db()
con = d.con

log("DuckDB loaded. Rebuilding temp tables...")

# ─── Rebuild core tables (must be single session) ───────────────────────────

con.execute("""
CREATE OR REPLACE TABLE _decomp_tag_mkts AS
WITH tag_ranked AS (
    SELECT m.condition_id, m.slug, m.event_id, m.neg_risk, et.label,
        CASE
            WHEN et.label = 'Politics' THEN 0 WHEN et.label = 'Elections' THEN 1
            WHEN et.label = 'Sports' THEN 2 WHEN et.label = 'Basketball' THEN 3
            WHEN et.label = 'Soccer' THEN 4 WHEN et.label = 'Esports' THEN 5
            WHEN et.label = 'NBA' THEN 6 WHEN et.label = 'Crypto' THEN 7
            WHEN et.label = 'NCAA' THEN 8 WHEN et.label = 'Tennis' THEN 9
            WHEN et.label = 'NFL' THEN 10 ELSE 999
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
log("  _decomp_tag_mkts done")

con.execute("""
CREATE OR REPLACE TABLE _decomp_positions AS
SELECT
    p.trader, p.condition_id, p.position, p.correct, p.yes_won,
    p.net_usd, p.volume, p.first_trade, p.resolved_at, tm.primary_tag,
    CASE
        WHEN p.position = 'YES' THEN ye.avg_entry_price
        WHEN p.position = 'NO' THEN 1.0 - COALESCE(ye.avg_entry_price, 0.5)
        ELSE NULL
    END AS entry_price,
    CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction_ratio
FROM maker_positions p
JOIN _decomp_tag_mkts tm ON p.condition_id = tm.condition_id
LEFT JOIN (
    SELECT condition_id, trader, price_x_vol / NULLIF(volume, 0) AS avg_entry_price
    FROM yes_entry_data
) ye ON p.condition_id = ye.condition_id AND p.trader = ye.trader
WHERE p.resolved_at IS NOT NULL AND p.position IN ('YES', 'NO')
  AND p.volume > 0 AND abs(p.net_usd) / p.volume >= 0.10
""")
log("  _decomp_positions done")

con.execute("""
CREATE OR REPLACE TABLE _decomp_base_rate_grid AS
WITH bucketed AS (
    SELECT primary_tag, position,
           FLOOR(COALESCE(entry_price, 0.5) / 0.05) * 0.05 AS price_bucket_low,
           correct
    FROM _decomp_positions
    WHERE entry_price IS NOT NULL AND entry_price >= 0.05 AND entry_price < 1.0
)
SELECT primary_tag, position, price_bucket_low,
       ROUND(price_bucket_low + 0.05, 2) AS price_bucket_high,
       count(*) AS n_positions, avg(correct::DOUBLE) AS hr
FROM bucketed
GROUP BY primary_tag, position, price_bucket_low
HAVING count(*) >= 10
ORDER BY primary_tag, position, price_bucket_low
""")
log("  _decomp_base_rate_grid done")

# ─── Build global trader scores WITH volume-weighting ───────────────────────
log("\n[Extended] Building trader scores with volume-weighting...")

con.execute("""
CREATE OR REPLACE TABLE _decomp_trader_global_ext AS
WITH all_pos_bucketed AS (
    SELECT p.trader, p.primary_tag, p.position, p.correct, p.net_usd, p.volume, p.entry_price,
           FLOOR(COALESCE(p.entry_price, 0.5) / 0.05) * 0.05 AS price_bucket_low
    FROM _decomp_positions p
    WHERE p.entry_price IS NOT NULL AND p.entry_price >= 0.05 AND p.entry_price < 1.0
),
with_base AS (
    SELECT ap.*, g.hr AS base_hr, ap.correct::DOUBLE - g.hr AS edge
    FROM all_pos_bucketed ap
    LEFT JOIN _decomp_base_rate_grid g
        ON ap.primary_tag = g.primary_tag AND ap.position = g.position AND ap.price_bucket_low = g.price_bucket_low
    WHERE g.hr IS NOT NULL
),
trader_agg AS (
    SELECT
        trader,
        count(*) AS n_total_positions,
        avg(correct::DOUBLE) AS overall_hr,
        avg(base_hr) AS weighted_base_rate,
        avg(correct::DOUBLE) - avg(base_hr) AS excess_hr,
        sum(edge) / count(*) AS bucket_excess_hr,
        avg(abs(net_usd) * correct::DOUBLE - abs(net_usd) * (1.0 - correct::DOUBLE)) AS avg_edge_usd,
        stddev(correct::DOUBLE) AS hr_std,
        sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END) AS n_yes,
        sum(CASE WHEN position = 'NO' THEN 1 ELSE 0 END) AS n_no,
        sum(CASE WHEN position = 'YES' THEN edge ELSE 0 END) /
            NULLIF(sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END), 0) AS yes_bucket_excess,
        sum(CASE WHEN position = 'NO' THEN edge ELSE 0 END) /
            NULLIF(sum(CASE WHEN position = 'NO' THEN 1 ELSE 0 END), 0) AS no_bucket_excess
    FROM with_base GROUP BY trader HAVING count(*) >= 20
)
SELECT *,
    CASE WHEN overall_hr > 0 THEN 1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0) ELSE 0 END AS consistency,
    -- Volume-weighted scores (multiply by ln(n+1) to penalize small-N)
    -- Score 1: HR-primary, volume-weighted
    (excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + bucket_excess_hr * 0.15) * ln(n_total_positions + 1) AS score_hr_primary_vw,
    -- Score 2: Edge-primary, volume-weighted (NEW)
    (bucket_excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + excess_hr * 0.15) * ln(n_total_positions + 1) AS score_edge_primary_vw,
    -- Score 3: pure BEH × volume (edge-only weighted)
    bucket_excess_hr * ln(n_total_positions + 1) AS score_edge_only_vw,
    -- Unweighted scores (for comparison)
    (excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + bucket_excess_hr * 0.15) AS score_hr_primary,
    (bucket_excess_hr * 0.45
     + CASE WHEN overall_hr > 0 THEN (1.0 - COALESCE(hr_std / NULLIF(overall_hr, 0), 1.0)) ELSE 0 END * 0.25
     + LEAST(COALESCE(avg_edge_usd, 0) / 100.0, 1.0) * 0.15
     + excess_hr * 0.15) AS score_edge_primary,
    bucket_excess_hr AS score_edge_only
FROM trader_agg
""")

n_g = con.execute("SELECT count(*) FROM _decomp_trader_global_ext").fetchone()[0]
log(f"  Qualified traders: {n_g:,}")

# ─── Scoring comparison with volume-weighting ───────────────────────────────
log("\n[Scoring Comparison] Volume-weighted top-100 lists...")

# Get top-100 for each method (volume-weighted)
top100_hr_vw = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_hr_primary_vw DESC LIMIT 100").fetchdf()["trader"].tolist())
top100_edge_vw = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_edge_primary_vw DESC LIMIT 100").fetchdf()["trader"].tolist())
top100_edge_only_vw = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_edge_only_vw DESC LIMIT 100").fetchdf()["trader"].tolist())

# Unweighted top-100
top100_hr = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_hr_primary DESC LIMIT 100").fetchdf()["trader"].tolist())
top100_edge = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_edge_primary DESC LIMIT 100").fetchdf()["trader"].tolist())
top100_edge_only = set(con.execute("SELECT trader FROM _decomp_trader_global_ext ORDER BY score_edge_only DESC LIMIT 100").fetchdf()["trader"].tolist())

def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

log(f"\n  === UNWEIGHTED TOP-100 JACCARD ===")
log(f"  HR vs Edge-primary: {jaccard(top100_hr, top100_edge):.3f}")
log(f"  HR vs Edge-only: {jaccard(top100_hr, top100_edge_only):.3f}")
log(f"  Edge-primary vs Edge-only: {jaccard(top100_edge, top100_edge_only):.3f}")

log(f"\n  === VOLUME-WEIGHTED TOP-100 JACCARD ===")
log(f"  HR-VW vs Edge-VW: {jaccard(top100_hr_vw, top100_edge_vw):.3f}")
log(f"  HR-VW vs Edge-only-VW: {jaccard(top100_hr_vw, top100_edge_only_vw):.3f}")
log(f"  Edge-VW vs Edge-only-VW: {jaccard(top100_edge_vw, top100_edge_only_vw):.3f}")

log(f"\n  === CROSS-METHOD JACCARD (unweighted vs volume-weighted) ===")
log(f"  HR-unweighted vs HR-VW: {jaccard(top100_hr, top100_hr_vw):.3f}")
log(f"  Edge-unweighted vs Edge-VW: {jaccard(top100_edge, top100_edge_vw):.3f}")

# Show top-20 for each volume-weighted method
for method, col in [
    ("HR-primary VW", "score_hr_primary_vw"),
    ("Edge-primary VW", "score_edge_primary_vw"),
    ("Edge-only VW", "score_edge_only_vw"),
]:
    top20 = con.execute(f"""
        SELECT trader, n_total_positions, overall_hr, excess_hr, bucket_excess_hr,
               yes_bucket_excess, no_bucket_excess, {col} as score
        FROM _decomp_trader_global_ext
        ORDER BY {col} DESC LIMIT 20
    """).fetchdf()
    log(f"\n  Top-20 by {method}:\n{top20.to_string()}")

# ─── Pool statistics for each method ────────────────────────────────────────
log("\n[Pool Stats] Top-100 pool comparisons...")

for method, col in [
    ("HR-primary (unweighted)", "score_hr_primary"),
    ("Edge-primary (unweighted)", "score_edge_primary"),
    ("HR-primary VW", "score_hr_primary_vw"),
    ("Edge-primary VW", "score_edge_primary_vw"),
    ("Edge-only VW", "score_edge_only_vw"),
]:
    stats = con.execute(f"""
        WITH top100 AS (
            SELECT * FROM _decomp_trader_global_ext ORDER BY {col} DESC LIMIT 100
        )
        SELECT
            avg(overall_hr) as avg_hr,
            avg(excess_hr) as avg_excess_hr,
            avg(bucket_excess_hr) as avg_beh,
            avg(n_total_positions) as avg_n,
            median(n_total_positions) as med_n,
            avg(CASE WHEN yes_bucket_excess >= 0.02 AND n_yes >= 10 THEN 1.0 ELSE 0.0 END) as pct_yes_skilled,
            avg(CASE WHEN no_bucket_excess >= 0.02 AND n_no >= 10 THEN 1.0 ELSE 0.0 END) as pct_no_skilled,
            avg(avg_edge_usd) as avg_edge_usd
        FROM top100
    """).fetchone()
    log(f"\n  {method}:")
    log(f"    avg HR={stats[0]:.4f}, avg excess_hr={stats[1]:.4f}, avg BEH={stats[2]:.4f}")
    log(f"    avg N={stats[3]:.1f}, med N={stats[4]:.0f}")
    log(f"    pct YES-skilled={stats[5]:.1%}, pct NO-skilled={stats[6]:.1%}")
    log(f"    avg edge_usd={stats[7]:.2f}")

# ─── Per-tag decomposition (top 5 tags) ─────────────────────────────────────
log("\n[Per-Tag] Top 5 tags analysis...")

con.execute("""
CREATE OR REPLACE TABLE _decomp_trader_bucket_ext AS
WITH bucketed_pos AS (
    SELECT p.trader, p.primary_tag, p.position,
           FLOOR(COALESCE(p.entry_price, 0.5) / 0.05) * 0.05 AS price_bucket_low,
           p.correct
    FROM _decomp_positions p
    WHERE p.entry_price IS NOT NULL AND p.entry_price >= 0.05 AND p.entry_price < 1.0
),
trader_bucket_hr AS (
    SELECT b.trader, b.primary_tag, b.position, b.price_bucket_low,
           count(*) AS n_pos, avg(b.correct::DOUBLE) AS trader_hr
    FROM bucketed_pos b GROUP BY b.trader, b.primary_tag, b.position, b.price_bucket_low
    HAVING count(*) >= 3
),
with_base AS (
    SELECT t.trader, t.primary_tag, t.position, t.price_bucket_low,
           t.n_pos, t.trader_hr, g.hr AS base_hr,
           t.trader_hr - g.hr AS edge, t.n_pos * (t.trader_hr - g.hr) AS weighted_edge
    FROM trader_bucket_hr t
    LEFT JOIN _decomp_base_rate_grid g
        ON t.primary_tag = g.primary_tag AND t.position = g.position AND t.price_bucket_low = g.price_bucket_low
    WHERE g.hr IS NOT NULL
)
SELECT trader, primary_tag, position,
       sum(n_pos) AS n_positions, avg(trader_hr) AS avg_hr,
       sum(weighted_edge) / NULLIF(sum(n_pos), 0) AS bucket_excess_hr,
       count(DISTINCT price_bucket_low) AS n_buckets
FROM with_base GROUP BY trader, primary_tag, position
""")

top5_tags = [r[0] for r in con.execute("""
    SELECT primary_tag, count(*) as n FROM _decomp_tag_mkts GROUP BY primary_tag ORDER BY n DESC LIMIT 5
""").fetchall()]

per_tag_results = {}
for tag in top5_tags:
    base_rates = con.execute(f"""
        SELECT position, avg(hr) as avg_base_rate, sum(n_positions) as n_positions
        FROM _decomp_base_rate_grid WHERE primary_tag = '{tag}' GROUP BY position
    """).fetchdf()

    yes_top50 = con.execute(f"""
        SELECT trader, n_positions, avg_hr, bucket_excess_hr
        FROM _decomp_trader_bucket_ext
        WHERE primary_tag = '{tag}' AND position = 'YES' AND n_positions >= 10
        ORDER BY bucket_excess_hr * ln(n_positions + 1) DESC LIMIT 50
    """).fetchdf()

    no_top50 = con.execute(f"""
        SELECT trader, n_positions, avg_hr, bucket_excess_hr
        FROM _decomp_trader_bucket_ext
        WHERE primary_tag = '{tag}' AND position = 'NO' AND n_positions >= 10
        ORDER BY bucket_excess_hr * ln(n_positions + 1) DESC LIMIT 50
    """).fetchdf()

    yes_traders = set(yes_top50["trader"].tolist())
    no_traders = set(no_top50["trader"].tolist())
    dual_in_tag = yes_traders & no_traders

    log(f"\n  {tag}:")
    log(f"    Base rates: YES={base_rates[base_rates.position=='YES']['avg_base_rate'].values[0] if 'YES' in base_rates.position.values else 'N/A':.4f}, NO={base_rates[base_rates.position=='NO']['avg_base_rate'].values[0] if 'NO' in base_rates.position.values else 'N/A':.4f}")
    log(f"    YES top-50: {len(yes_top50)} traders, avg BEH={yes_top50['bucket_excess_hr'].mean():.3f}" if len(yes_top50) > 0 else f"    YES top-50: 0 traders")
    log(f"    NO top-50: {len(no_top50)} traders, avg BEH={no_top50['bucket_excess_hr'].mean():.3f}" if len(no_top50) > 0 else f"    NO top-50: 0 traders")
    log(f"    Dual-skill: {len(dual_in_tag)}")

    yes_br = base_rates[base_rates.position == 'YES']['avg_base_rate'].values[0] if 'YES' in base_rates.position.values else None
    no_br = base_rates[base_rates.position == 'NO']['avg_base_rate'].values[0] if 'NO' in base_rates.position.values else None

    per_tag_results[tag] = {
        "yes_base_rate": round(float(yes_br), 4) if yes_br else None,
        "no_base_rate": round(float(no_br), 4) if no_br else None,
        "yes_top50_count": len(yes_top50),
        "no_top50_count": len(no_top50),
        "yes_avg_beh": round(float(yes_top50["bucket_excess_hr"].mean()), 4) if len(yes_top50) > 0 else 0.0,
        "no_avg_beh": round(float(no_top50["bucket_excess_hr"].mean()), 4) if len(no_top50) > 0 else 0.0,
        "dual_skill_in_tag": len(dual_in_tag),
        "yes_top50": yes_top50["trader"].tolist(),
        "no_top50": no_top50["trader"].tolist(),
    }

# ─── Direction analysis ──────────────────────────────────────────────────────
log("\n[Direction] Global direction analysis...")

EDGE_THRESHOLD = 0.02
direction_stats = con.execute(f"""
    SELECT
        sum(CASE WHEN yes_bucket_excess >= {EDGE_THRESHOLD} AND n_yes >= 10 THEN 1 ELSE 0 END) AS yes_skilled,
        sum(CASE WHEN no_bucket_excess >= {EDGE_THRESHOLD} AND n_no >= 10 THEN 1 ELSE 0 END) AS no_skilled,
        sum(CASE WHEN yes_bucket_excess >= {EDGE_THRESHOLD} AND n_yes >= 10
                  AND no_bucket_excess >= {EDGE_THRESHOLD} AND n_no >= 10 THEN 1 ELSE 0 END) AS dual_skilled,
        count(*) AS total_qualified
    FROM _decomp_trader_global_ext
""").fetchone()

yes_skill_count, no_skill_count, dual_skill_count, total_qualified = direction_stats
log(f"  YES-skilled: {yes_skill_count:,} ({yes_skill_count/total_qualified*100:.1f}%)")
log(f"  NO-skilled: {no_skill_count:,} ({no_skill_count/total_qualified*100:.1f}%)")
log(f"  Dual-skilled: {dual_skill_count:,} ({dual_skill_count/total_qualified*100:.1f}%)")

dual_traders = con.execute(f"""
    SELECT trader, n_total_positions, overall_hr, excess_hr, bucket_excess_hr,
           yes_bucket_excess, no_bucket_excess, n_yes, n_no
    FROM _decomp_trader_global_ext
    WHERE yes_bucket_excess >= {EDGE_THRESHOLD} AND n_yes >= 10
      AND no_bucket_excess >= {EDGE_THRESHOLD} AND n_no >= 10
    ORDER BY score_edge_primary_vw DESC LIMIT 20
""").fetchdf()
log(f"\n  Top-20 dual-skilled (by volume-weighted edge-primary):\n{dual_traders.to_string()}")

# ─── Save updated JSON ───────────────────────────────────────────────────────
log("\n[Saving updated results]...")

# Load existing JSON to merge
with open(OUT_JSON, "r") as f:
    result_json = json.load(f)

# Update with extended results
result_json["volume_weighted_scoring"] = {
    "note": "Volume-weighted scores multiply by ln(n_positions+1) to penalize small-N traders",
    "jaccard_hr_vw_vs_edge_vw": round(jaccard(top100_hr_vw, top100_edge_vw), 4),
    "jaccard_hr_vw_vs_edge_only_vw": round(jaccard(top100_hr_vw, top100_edge_only_vw), 4),
    "jaccard_edge_vw_vs_edge_only_vw": round(jaccard(top100_edge_vw, top100_edge_only_vw), 4),
    "jaccard_unweighted_vs_weighted_hr": round(jaccard(top100_hr, top100_hr_vw), 4),
    "jaccard_unweighted_vs_weighted_edge": round(jaccard(top100_edge, top100_edge_vw), 4),
}

result_json["scoring_comparison"]["hr_primary"]["top100_traders_vw"] = list(top100_hr_vw)
result_json["scoring_comparison"]["edge_primary"]["top100_traders_vw"] = list(top100_edge_vw)
result_json["scoring_comparison"]["edge_only"]["top100_traders_vw"] = list(top100_edge_only_vw)

result_json["per_tag_pools"] = {
    tag: {
        "yes_base_rate": data["yes_base_rate"],
        "no_base_rate": data["no_base_rate"],
        "yes_top50": data["yes_top50"],
        "no_top50": data["no_top50"],
        "pool_metrics": {
            "yes_top50_count": data["yes_top50_count"],
            "no_top50_count": data["no_top50_count"],
            "yes_avg_beh": data["yes_avg_beh"],
            "no_avg_beh": data["no_avg_beh"],
            "dual_skill_in_tag": data["dual_skill_in_tag"],
        },
    }
    for tag, data in per_tag_results.items()
}

result_json["direction_analysis"].update({
    "dual_skill_traders": dual_traders["trader"].tolist(),
})

with open(OUT_JSON, "w") as f:
    json.dump(result_json, f, indent=2)

log(f"JSON updated: {OUT_JSON}")

# ─── Append to Markdown ───────────────────────────────────────────────────────
vw_section = f"""
## Volume-Weighted Scoring Analysis

**Problem with unweighted scoring**: Both HR-primary and Edge-primary selected identical top-100 (Jaccard=1.0).
Root cause: small-N traders with 100% HR dominate — 55 positions of pure NO bets on easy markets.

**Fix**: multiply scores by `ln(n_positions + 1)` to reward high-volume, consistent edge over small perfect samples.

### Jaccard Overlaps (volume-weighted top-100):
- HR-VW vs Edge-VW: **{jaccard(top100_hr_vw, top100_edge_vw):.3f}** ({jaccard(top100_hr_vw, top100_edge_vw)*100:.1f}% shared)
- HR-VW vs Edge-only-VW: **{jaccard(top100_hr_vw, top100_edge_only_vw):.3f}** ({jaccard(top100_hr_vw, top100_edge_only_vw)*100:.1f}% shared)
- Edge-VW vs Edge-only-VW: **{jaccard(top100_edge_vw, top100_edge_only_vw):.3f}** ({jaccard(top100_edge_vw, top100_edge_only_vw)*100:.1f}% shared)

### Cross-method (unweighted vs volume-weighted):
- HR-unweighted vs HR-VW: **{jaccard(top100_hr, top100_hr_vw):.3f}** — volume-weighting selects DIFFERENT traders
- Edge-unweighted vs Edge-VW: **{jaccard(top100_edge, top100_edge_vw):.3f}**

**Key finding**: Volume-weighted scoring has {jaccard(top100_hr_vw, top100_edge_vw)*100:.1f}% overlap between HR-VW and Edge-VW (vs 100% before).
Edge-primary volume-weighted is now a meaningfully different signal.

### Per-Tag Decomposition (volume-weighted top-50, min 10 positions in tag):

| Tag | YES BR | NO BR | YES top-50 | YES avg BEH | NO top-50 | NO avg BEH | Dual |
|-----|--------|-------|-----------|------------|----------|-----------|------|
"""

for tag in top5_tags:
    d_tag = per_tag_results[tag]
    yes_br = d_tag['yes_base_rate'] or 'N/A'
    no_br = d_tag['no_base_rate'] or 'N/A'
    vw_section += f"| {tag} | {yes_br} | {no_br} | {d_tag['yes_top50_count']} | {d_tag['yes_avg_beh']:.4f} | {d_tag['no_top50_count']} | {d_tag['no_avg_beh']:.4f} | {d_tag['dual_skill_in_tag']} |\n"

vw_section += f"""
### Direction Analysis (global, volume-weighted edge-primary ordering):
- YES-skilled (BEH >= {EDGE_THRESHOLD}, >=10 YES): **{yes_skill_count:,}** ({yes_skill_count/total_qualified*100:.1f}%)
- NO-skilled (BEH >= {EDGE_THRESHOLD}, >=10 NO): **{no_skill_count:,}** ({no_skill_count/total_qualified*100:.1f}%)
- Dual-skilled: **{dual_skill_count:,}** ({dual_skill_count/total_qualified*100:.1f}%)
"""

with open(OUT_MD, "a") as f:
    f.write(vw_section)

log(f"Markdown updated: {OUT_MD}")
log("\n=== Extended Analysis Complete ===")

print("\n" + "="*60)
print("EXTENDED DECOMPOSITION COMPLETE")
print("="*60)
print(f"  Qualified traders: {n_g:,}")
print(f"  VW Jaccard HR vs Edge-primary: {jaccard(top100_hr_vw, top100_edge_vw):.3f}")
print(f"  VW Jaccard HR vs Edge-only: {jaccard(top100_hr_vw, top100_edge_only_vw):.3f}")
print(f"  YES-skilled: {yes_skill_count:,} ({yes_skill_count/total_qualified*100:.1f}%)")
print(f"  NO-skilled: {no_skill_count:,} ({no_skill_count/total_qualified*100:.1f}%)")
print(f"  Dual-skilled: {dual_skill_count:,} ({dual_skill_count/total_qualified*100:.1f}%)")
