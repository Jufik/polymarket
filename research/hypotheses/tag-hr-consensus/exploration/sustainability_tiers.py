"""Task #14: Trader sustainability tiers (consistency grading).

For each trader in the sharp pool (K=50), split the 6-month training window into
3x 2-month sub-windows. Compute per-trader excess_hr in each sub-window.

Tier definition (out of 3 sub-windows with positive excess_hr):
  Tier 1: 3/3 positive — consistent across all sub-windows
  Tier 2: 2/3 positive — mostly consistent
  Tier 3: 1/3 positive — occasionally consistent
  Tier 4: 0/3 positive — qualified in aggregate but never in a sub-window

Then: for each test-window consensus signal, compute the tier composition of the
N consensus traders. Does tier composition predict out-of-sample HR?

Also computes flat vs tier-weighted sizing PnL simulation.

Output: research/hypotheses/tag-hr-consensus/exploration/sustainability_tiers.json
Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/exploration/sustainability_tiers.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from research.db import db

OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/exploration")
LOG_PATH = Path("tmp/sustainability_tiers.log")

FOLDS = [
    # (train_start, train_end, test_start, test_end)
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

# Sub-window boundaries (months 0-2, 2-4, 4-6 relative to train_start)
# We'll compute these dynamically per fold
SUB_WINDOW_MONTHS = 2

TAGS = ["Esports", "Tennis"]
TOP_K = 50
CONSENSUS_N = 3
MIN_TRADES_PER_WINDOW = 2   # min trades in a sub-window to count it
MIN_TRADES_TOTAL = 5
BOT_GUARD = 10_000
MPE_THRESH = 0.80

# Tier weights for sizing simulation
TIER_WEIGHTS = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.5, 5: 0.25}
BASE_SIZE_USD = 100.0


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def months_offset(date_str: str, months: int) -> str:
    """Add N months to a YYYY-MM-DD string."""
    year, month, day = map(int, date_str.split("-"))
    month += months
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def setup_tag_mkts(d, tag: str) -> None:
    d.execute("DROP TABLE IF EXISTS _st_tag_mkts")
    d.execute("""
        CREATE TEMP TABLE _st_tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = $1
    """, [tag])


def get_base_rate(d, start: str, end: str) -> float:
    r = d.fetchone(f"""
        SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate
        FROM (
            SELECT condition_id, first(yes_won) AS yes_won
            FROM maker_positions
            WHERE condition_id IN (SELECT condition_id FROM _st_tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{start}'
              AND CAST(resolved_at AS DATE) < '{end}'
            GROUP BY condition_id
        )
    """)
    return r["base_rate"] or 0.5


def build_tiered_pool(d, train_start: str, train_end: str, train_base: float) -> dict[str, dict]:
    """Build top-K pool with per-trader tier based on sub-window consistency.

    Returns dict: trader -> {rank, aggregate_excess_hr, sub_window_excess_hrs, tier, n_positive_windows}
    """
    # Sub-window boundaries
    sw1_start = train_start
    sw1_end = months_offset(train_start, 2)
    sw2_start = sw1_end
    sw2_end = months_offset(train_start, 4)
    sw3_start = sw2_end
    sw3_end = train_end

    log(f"    sub-windows: {sw1_start}→{sw1_end}, {sw2_start}→{sw2_end}, {sw3_start}→{sw3_end}")

    # Step 1: avg entry price filter (INNER JOIN, training window)
    d.execute("DROP TABLE IF EXISTS _st_ep_tmp")
    d.execute(f"""
        CREATE TEMP TABLE _st_ep_tmp AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM _st_tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{train_start}'
          AND CAST(y.first_trade AS DATE) < '{train_end}'
        GROUP BY y.trader
    """)

    # Step 2: aggregate stats + per-sub-window excess_hr for each trader
    # Compute aggregate qualified pool (top-K) first
    agg_pool = d.fetchall(f"""
        WITH ranked AS (
            SELECT
                p.trader,
                count() AS n_total,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} AS excess_hr,
                first(ep.avg_ep) AS avg_ep,
                ROW_NUMBER() OVER (
                    ORDER BY (sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base}) DESC,
                             count() DESC
                ) AS rank_hr
            FROM maker_positions p
            INNER JOIN _st_ep_tmp ep ON p.trader = ep.trader
            WHERE p.condition_id IN (SELECT condition_id FROM _st_tag_mkts)
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{train_start}'
              AND CAST(p.resolved_at AS DATE) < '{train_end}'
            GROUP BY p.trader
            HAVING n_total >= {MIN_TRADES_TOTAL}
              AND n_total < {BOT_GUARD}
              AND raw_hr < 0.99
              AND excess_hr > 0
              AND avg_ep <= {MPE_THRESH}
        )
        SELECT trader, n_total, raw_hr, excess_hr, avg_ep, rank_hr
        FROM ranked
        WHERE rank_hr <= {TOP_K}
        ORDER BY rank_hr
    """)

    if not agg_pool:
        return {}

    trader_set = {r["trader"] for r in agg_pool}
    traders_joined = ", ".join(f"'{t}'" for t in trader_set)

    # Step 3: per-sub-window excess_hr for these traders
    sw_results = {}
    for sw_idx, (sw_start, sw_end) in enumerate([
        (sw1_start, sw1_end), (sw2_start, sw2_end), (sw3_start, sw3_end)
    ]):
        sw_base = get_base_rate(d, sw_start, sw_end)
        rows = d.fetchall(f"""
            SELECT
                p.trader,
                count() AS n_sw,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS sw_hr,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {sw_base} AS sw_excess_hr
            FROM maker_positions p
            WHERE p.trader IN ({traders_joined})
              AND p.condition_id IN (SELECT condition_id FROM _st_tag_mkts)
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{sw_start}'
              AND CAST(p.resolved_at AS DATE) < '{sw_end}'
            GROUP BY p.trader
            HAVING n_sw >= {MIN_TRADES_PER_WINDOW}
        """)
        sw_results[sw_idx] = {r["trader"]: r for r in rows}

    # Step 4: build tier per trader
    tiered_pool = {}
    for row in agg_pool:
        trader = row["trader"]
        sw_excesses = []
        for sw_idx in range(3):
            if trader in sw_results[sw_idx]:
                sw_excesses.append(sw_results[sw_idx][trader]["sw_excess_hr"])
            else:
                sw_excesses.append(None)  # insufficient data in this sub-window

        n_positive = sum(1 for e in sw_excesses if e is not None and e > 0)
        n_measured = sum(1 for e in sw_excesses if e is not None)

        # Tier: based on how consistently positive across measured windows
        if n_measured == 0:
            tier = 5  # no sub-window data — weakest
        elif n_positive == 3:
            tier = 1  # all 3 positive
        elif n_positive == 2:
            tier = 2
        elif n_positive == 1:
            tier = 3
        else:
            tier = 4  # 0 positive sub-windows (qualified in aggregate only)

        tiered_pool[trader] = {
            "rank": int(row["rank_hr"]),
            "aggregate_excess_hr": round(float(row["excess_hr"]), 4),
            "raw_hr": round(float(row["raw_hr"]), 4),
            "n_total": int(row["n_total"]),
            "avg_ep": round(float(row["avg_ep"]), 4),
            "sw_excess_hrs": [round(e, 4) if e is not None else None for e in sw_excesses],
            "n_positive_windows": n_positive,
            "n_measured_windows": n_measured,
            "tier": tier,
        }

    return tiered_pool


def analyze_tier_vs_hr(d, tiered_pool: dict, test_start: str, test_end: str, test_base: float) -> dict:
    """For each test-window consensus market, compute tier composition and flat vs tier-weighted PnL."""
    if not tiered_pool:
        return {}

    trader_tiers = {t: info["tier"] for t, info in tiered_pool.items()}
    traders_joined = ", ".join(f"'{t}'" for t in tiered_pool)

    # Get all test-window consensus markets and their qual traders
    consensus_mkts = d.fetchall(f"""
        SELECT
            p.condition_id,
            first(p.yes_won) AS yes_won,
            count(DISTINCT p.trader) AS n_qual,
            list(p.trader) AS traders
        FROM maker_positions p
        WHERE p.trader IN ({traders_joined})
          AND p.condition_id IN (SELECT condition_id FROM _st_tag_mkts)
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
          AND CAST(p.resolved_at AS DATE) < '{test_end}'
          AND CAST(p.first_trade AS DATE) >= '{test_start}'
        GROUP BY p.condition_id
        HAVING n_qual >= {CONSENSUS_N}
    """)

    if not consensus_mkts:
        return {"n_consensus_markets": 0}

    # For each market, compute avg tier of its consensus traders
    tier_buckets: dict[str, list[int]] = {
        "elite (avg_tier <= 1.5)": [],
        "strong (1.5 < avg_tier <= 2.5)": [],
        "moderate (2.5 < avg_tier <= 3.5)": [],
        "weak (avg_tier > 3.5)": [],
    }

    # Sizing simulation: flat vs tier-weighted
    flat_pnl = 0.0
    tier_weighted_pnl = 0.0
    flat_n = 0
    tier_w_n = 0

    composition_data = []

    for mkt in consensus_mkts:
        yes_won = int(mkt["yes_won"]) if mkt["yes_won"] is not None else None
        traders_in_mkt = mkt["traders"]

        tiers_in_mkt = [trader_tiers.get(t, 5) for t in traders_in_mkt if t in trader_tiers]
        if not tiers_in_mkt:
            continue

        avg_tier = sum(tiers_in_mkt) / len(tiers_in_mkt)
        min_tier = min(tiers_in_mkt)
        n_tier1 = sum(1 for t in tiers_in_mkt if t == 1)

        if avg_tier <= 1.5:
            bucket = "elite (avg_tier <= 1.5)"
        elif avg_tier <= 2.5:
            bucket = "strong (1.5 < avg_tier <= 2.5)"
        elif avg_tier <= 3.5:
            bucket = "moderate (2.5 < avg_tier <= 3.5)"
        else:
            bucket = "weak (avg_tier > 3.5)"

        if yes_won is not None:
            tier_buckets[bucket].append(yes_won)

            # Sizing simulation
            # Flat: $100, outcome: +$100*(1/ep - 1) if won (approx +$100 at 0.5 price), -$100 if lost
            # Use simplified: flat PnL = +100 if won, -100 if lost
            flat_pnl += BASE_SIZE_USD if yes_won else -BASE_SIZE_USD
            flat_n += 1

            # Tier-weighted: size = $100 * mean(tier_weight[tier]) for traders in consensus
            quality = sum(TIER_WEIGHTS.get(t, 0.25) for t in tiers_in_mkt) / len(tiers_in_mkt)
            tw_size = BASE_SIZE_USD * quality
            tier_weighted_pnl += tw_size if yes_won else -tw_size
            tier_w_n += 1

        composition_data.append({
            "yes_won": yes_won,
            "avg_tier": round(avg_tier, 2),
            "min_tier": min_tier,
            "n_tier1": n_tier1,
            "n_qual": mkt["n_qual"],
        })

    # Aggregate by bucket
    bucket_stats = {}
    for bucket, outcomes in tier_buckets.items():
        if not outcomes:
            bucket_stats[bucket] = {"n": 0, "hr": None, "excess_hr_pp": None}
        else:
            hr = sum(outcomes) / len(outcomes)
            bucket_stats[bucket] = {
                "n": len(outcomes),
                "hr": round(hr, 4),
                "excess_hr_pp": round((hr - test_base) * 100, 2),
            }

    # n_tier1 in consensus
    tier1_buckets: dict[str, list[int]] = {"0 Tier1": [], "1 Tier1": [], "2+ Tier1": []}
    for row in composition_data:
        if row["yes_won"] is None:
            continue
        n1 = row["n_tier1"]
        key = "0 Tier1" if n1 == 0 else ("1 Tier1" if n1 == 1 else "2+ Tier1")
        tier1_buckets[key].append(row["yes_won"])

    tier1_stats = {}
    for key, outcomes in tier1_buckets.items():
        if not outcomes:
            tier1_stats[key] = {"n": 0, "hr": None, "excess_hr_pp": None}
        else:
            hr = sum(outcomes) / len(outcomes)
            tier1_stats[key] = {
                "n": len(outcomes),
                "hr": round(hr, 4),
                "excess_hr_pp": round((hr - test_base) * 100, 2),
            }

    return {
        "n_consensus_markets": len(consensus_mkts),
        "avg_tier_bucket_hr": bucket_stats,
        "n_tier1_in_consensus_hr": tier1_stats,
        "sizing_sim": {
            "n_signals": flat_n,
            "flat_pnl": round(flat_pnl, 2),
            "tier_weighted_pnl": round(tier_weighted_pnl, 2),
            "flat_avg_size": BASE_SIZE_USD,
            "tier_weighted_uplift_pct": round(
                (tier_weighted_pnl - flat_pnl) / max(abs(flat_pnl), 1) * 100, 1
            ) if flat_n > 0 else None,
        },
    }


def analyze_fold(d, tag: str, train_start: str, train_end: str, test_start: str, test_end: str) -> dict:
    log(f"\n  fold {test_start}")

    train_base = get_base_rate(d, train_start, train_end)
    test_base = get_base_rate(d, test_start, test_end)

    r = d.fetchone(f"""
        SELECT count() AS n FROM (
            SELECT DISTINCT condition_id FROM maker_positions
            WHERE condition_id IN (SELECT condition_id FROM _st_tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{test_start}'
              AND CAST(resolved_at AS DATE) < '{test_end}'
        )
    """)
    n_test = r["n"]
    if n_test < 5:
        log(f"    SKIP: n_test={n_test}")
        return {"fold": test_start, "skip": True}

    log(f"    train_base={train_base:.3f} test_base={test_base:.3f} n_test={n_test}")

    tiered_pool = build_tiered_pool(d, train_start, train_end, train_base)
    log(f"    pool size (top-{TOP_K}): {len(tiered_pool)}")

    if not tiered_pool:
        return {"fold": test_start, "skip": True, "reason": "empty pool"}

    # Tier distribution
    tier_dist: dict[int, int] = {}
    for info in tiered_pool.values():
        t = info["tier"]
        tier_dist[t] = tier_dist.get(t, 0) + 1

    log(f"    tier distribution: " + ", ".join(f"T{k}={v}" for k, v in sorted(tier_dist.items())))

    # Per-tier aggregate stats
    tier_stats: dict[int, dict] = {}
    for tier in sorted(tier_dist.keys()):
        traders_in_tier = [info for info in tiered_pool.values() if info["tier"] == tier]
        avg_excess = sum(t["aggregate_excess_hr"] for t in traders_in_tier) / len(traders_in_tier)
        tier_stats[tier] = {
            "n_traders": len(traders_in_tier),
            "avg_aggregate_excess_hr": round(avg_excess, 4),
        }

    # Test-window: does tier composition predict HR? + sizing sim
    tier_hr_analysis = analyze_tier_vs_hr(d, tiered_pool, test_start, test_end, test_base)

    log(f"    n_consensus_markets: {tier_hr_analysis.get('n_consensus_markets', 0)}")
    if "avg_tier_bucket_hr" in tier_hr_analysis:
        for bucket, stats in tier_hr_analysis["avg_tier_bucket_hr"].items():
            if stats["n"] > 0:
                log(f"      {bucket}: n={stats['n']} HR={stats['hr']:.1%} excess={stats['excess_hr_pp']:+.1f}pp")
    if "sizing_sim" in tier_hr_analysis:
        sim = tier_hr_analysis["sizing_sim"]
        log(f"    sizing: flat=${sim['flat_pnl']:+.0f}, tier-weighted=${sim['tier_weighted_pnl']:+.0f} ({sim.get('tier_weighted_uplift_pct', 0):+.1f}%)")

    return {
        "fold": test_start,
        "train_base_rate": round(train_base, 4),
        "test_base_rate": round(test_base, 4),
        "n_test_markets": n_test,
        "pool_size": len(tiered_pool),
        "tier_distribution": {str(k): v for k, v in sorted(tier_dist.items())},
        "tier_aggregate_stats": {str(k): v for k, v in sorted(tier_stats.items())},
        "tier_hr_analysis": tier_hr_analysis,
        # Top-5 per tier for inspection
        "top_traders_by_tier": {
            str(tier): [
                {"trader": t[:12] + "...", "rank": info["rank"],
                 "agg_excess_hr": info["aggregate_excess_hr"],
                 "sw_excess_hrs": info["sw_excess_hrs"],
                 "n_positive_windows": info["n_positive_windows"]}
                for t, info in sorted(tiered_pool.items(), key=lambda x: x[1]["rank"])
                if info["tier"] == tier
            ][:5]
            for tier in sorted(tier_dist.keys())
        },
    }


def compute_summary(results: dict) -> dict:
    """Compute aggregate tier_distributions, tier_vs_hr, sizing_comparison, verdict."""
    tier_distributions = {}
    tier_vs_hr = {}
    sizing_comparison: dict[str, dict] = {}

    for tag, folds in results.items():
        # Aggregate tier distributions
        agg_dist: dict[str, list] = {}
        agg_bucket_hr: dict[str, list] = {}
        total_flat = 0.0
        total_tw = 0.0
        total_n = 0

        for fold in folds:
            if fold.get("skip"):
                continue
            dist = fold["tier_distribution"]
            for tier, n in dist.items():
                if tier not in agg_dist:
                    agg_dist[tier] = []
                agg_dist[tier].append(n)

            analysis = fold["tier_hr_analysis"]
            for bucket, stats in analysis.get("avg_tier_bucket_hr", {}).items():
                if stats["n"] > 0:
                    if bucket not in agg_bucket_hr:
                        agg_bucket_hr[bucket] = []
                    agg_bucket_hr[bucket].append({"n": stats["n"], "hr": stats["hr"], "excess": stats["excess_hr_pp"]})

            sim = analysis.get("sizing_sim", {})
            total_flat += sim.get("flat_pnl", 0.0)
            total_tw += sim.get("tier_weighted_pnl", 0.0)
            total_n += sim.get("n_signals", 0)

        tier_distributions[tag] = {
            k: {"avg_per_fold": round(sum(v) / len(v), 1), "total": sum(v)}
            for k, v in sorted(agg_dist.items())
        }

        tier_vs_hr[tag] = {}
        for bucket, fold_data in agg_bucket_hr.items():
            total_n_b = sum(d["n"] for d in fold_data)
            weighted_hr = sum(d["hr"] * d["n"] for d in fold_data) / total_n_b if total_n_b > 0 else None
            tier_vs_hr[tag][bucket] = {
                "total_n": total_n_b,
                "n_folds": len(fold_data),
                "weighted_hr": round(weighted_hr, 4) if weighted_hr is not None else None,
                "avg_excess_pp": round(sum(d["excess"] for d in fold_data) / len(fold_data), 2),
            }

        sizing_comparison[tag] = {
            "n_signals": total_n,
            "flat_pnl": round(total_flat, 2),
            "tier_weighted_pnl": round(total_tw, 2),
            "uplift_pct": round((total_tw - total_flat) / max(abs(total_flat), 1) * 100, 1) if total_n > 0 else None,
        }

    # Determine verdict
    # Check if tiers predict HR: strong bucket consistently better than moderate
    tier_signal = False
    for tag in results:
        tv = tier_vs_hr.get(tag, {})
        strong = tv.get("strong (1.5 < avg_tier <= 2.5)", {})
        moderate = tv.get("moderate (2.5 < avg_tier <= 3.5)", {})
        if (strong.get("weighted_hr") and moderate.get("weighted_hr") and
                strong["weighted_hr"] > moderate["weighted_hr"] + 0.05):
            tier_signal = True

    verdict = "tiers_predict_hr" if tier_signal else "tiers_no_signal"

    return {
        "tier_distributions": tier_distributions,
        "tier_vs_hr": tier_vs_hr,
        "sizing_comparison": sizing_comparison,
        "verdict": verdict,
    }


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== Task #14: Trader sustainability tiers (consistency grading) ===")
    t0 = time.time()

    d = db()
    results = {}

    for tag in TAGS:
        log(f"\n{'='*60}")
        log(f"TAG: {tag}")
        setup_tag_mkts(d, tag)

        tag_results = []
        for train_start, train_end, test_start, test_end in FOLDS:
            fold_result = analyze_fold(d, tag, train_start, train_end, test_start, test_end)
            tag_results.append(fold_result)

        results[tag] = tag_results

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    summary = compute_summary(results)

    output = {
        "timestamp": datetime.now().isoformat(),
        "description": (
            "Trader sustainability tiers: split 6-month training into 3x2-month sub-windows. "
            "Tier 1=positive excess in all 3, Tier 4=positive in aggregate only. "
            f"Top-K={TOP_K} pool, N={CONSENSUS_N} consensus. Tags: Esports, Tennis."
        ),
        "params": {
            "top_k": TOP_K,
            "consensus_n": CONSENSUS_N,
            "min_trades_total": MIN_TRADES_TOTAL,
            "min_trades_per_window": MIN_TRADES_PER_WINDOW,
            "mpe_thresh": MPE_THRESH,
            "sub_window_months": SUB_WINDOW_MONTHS,
            "tier_weights": TIER_WEIGHTS,
            "base_size_usd": BASE_SIZE_USD,
        },
        # Summary keys matching the task spec
        "tier_distributions": summary["tier_distributions"],
        "tier_vs_hr": summary["tier_vs_hr"],
        "sizing_comparison": summary["sizing_comparison"],
        "verdict": summary["verdict"],
        # Full per-fold detail
        "results_by_tag": results,
    }

    out_path = OUTPUT_DIR / "sustainability_tiers.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log(f"Written: {out_path}")

    # Print summary
    log("\n=== SUMMARY ===")
    log(f"Verdict: {summary['verdict']}")
    for tag in TAGS:
        log(f"\n{tag}:")
        log(f"  Tier distributions (avg per fold): {summary['tier_distributions'].get(tag, {})}")
        log("  Tier vs HR:")
        for bucket, stats in summary["tier_vs_hr"].get(tag, {}).items():
            if stats["total_n"] > 0:
                log(f"    {bucket}: n={stats['total_n']} HR={stats.get('weighted_hr', 0):.1%} excess={stats['avg_excess_pp']:+.1f}pp")
        sc = summary["sizing_comparison"].get(tag, {})
        if sc.get("n_signals", 0) > 0:
            log(f"  Sizing: n={sc['n_signals']} flat=${sc['flat_pnl']:+.0f} tier-weighted=${sc['tier_weighted_pnl']:+.0f} ({sc.get('uplift_pct', 0):+.1f}%)")


if __name__ == "__main__":
    main()
