"""Copy-trader contamination analysis for tag-hr-consensus.

Investigates whether qualified pool growth (47 -> 774 traders) is driven by
copy-traders who follow original insiders and inherit their HR, creating
fake consensus signals.

Five analyses:
1. Entry time clustering — gap distribution between consecutive qualified entries
2. Leader-follower detection — who enters first vs who follows
3. First-mover consensus — HR by entry order (first K entrants only)
4. Temporal independence filter — HR for markets with independent vs clustered entries
5. Pool composition over time — overlap and turnover across folds

Uses DuckDB + Parquet snapshot. Runs for Esports and Tennis across 3 validation folds.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/exploration/copy_trader_analysis.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from research.db import db

# Same folds as validation
FOLDS = [
    # (train_start, train_end, test_start, test_end)
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

# Use validated params from R2 (best tick-by-tick results)
CONFIGS = {
    "Esports": {"meh_pp": 10, "mpe": 0.80, "consensus_n": 4},
    "Tennis":  {"meh_pp": 20, "mpe": 0.90, "consensus_n": 3},
}

MIN_TRADES = 5  # Matching validation (R2 used 5)
BOT_GUARD = 10_000
OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/exploration")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def setup_tag_mkts(d, tag: str) -> int:
    d.execute("DROP TABLE IF EXISTS tag_mkts")
    d.execute("""
        CREATE TEMP TABLE tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = $1
    """, [tag])
    n = d.fetchone("SELECT count() AS n FROM tag_mkts")["n"]
    return n


def get_base_rate(d, start: str, end: str) -> tuple[float, int]:
    r = d.fetchone(f"""
        SELECT
            sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS yw,
            count() AS tot
        FROM (
            SELECT condition_id, first(yes_won) AS yes_won
            FROM maker_positions
            WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{start}'
              AND CAST(resolved_at AS DATE) < '{end}'
            GROUP BY condition_id
        )
    """)
    yw, tot = r["yw"], r["tot"]
    return (round(yw / tot, 4) if tot > 0 else 0.5), tot


def build_qualified_pool(d, ts: str, te: str, base: float,
                         meh_pp: int, mpe: float) -> set[str]:
    """Build qualified pool matching validation logic."""
    meh_thresh = meh_pp / 100.0

    d.execute("DROP TABLE IF EXISTS _ep_tmp")
    d.execute(f"""
        CREATE TEMP TABLE _ep_tmp AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{ts}'
          AND CAST(y.first_trade AS DATE) < '{te}'
        GROUP BY y.trader
    """)

    result = d.fetchall(f"""
        SELECT p.trader
        FROM maker_positions p
        INNER JOIN _ep_tmp ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{ts}'
          AND CAST(p.resolved_at AS DATE) < '{te}'
        GROUP BY p.trader
        HAVING count() >= {MIN_TRADES}
          AND count() < {BOT_GUARD}
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {base} >= {meh_thresh}
          AND first(ep.avg_ep) <= {mpe}
    """)
    return {r["trader"] for r in result}


def build_test_positions(d, qualified: set[str], xs: str, xe: str) -> None:
    """Build test_positions temp table: qualified YES entries in test window."""
    if not qualified:
        d.execute("DROP TABLE IF EXISTS test_positions")
        d.execute("CREATE TEMP TABLE test_positions (condition_id VARCHAR, trader VARCHAR, first_trade TIMESTAMPTZ, yes_won UTINYINT, realized_pnl DOUBLE)")
        return

    placeholders = ", ".join(f"'{t}'" for t in qualified)
    d.execute("DROP TABLE IF EXISTS test_positions")
    d.execute(f"""
        CREATE TEMP TABLE test_positions AS
        SELECT
            p.condition_id,
            p.trader,
            p.first_trade,
            p.yes_won,
            p.realized_pnl,
            p.resolved_at
        FROM maker_positions p
        WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{xs}'
          AND CAST(p.resolved_at AS DATE) < '{xe}'
          AND CAST(p.first_trade AS DATE) >= '{xs}'
          AND p.trader IN ({placeholders})
    """)


# ---------------------------------------------------------------------------
# Analysis 1: Entry time clustering
# ---------------------------------------------------------------------------
def analysis_entry_clustering(d) -> dict:
    """For markets with >=2 qualified entries, compute gap distribution."""
    rows = d.fetchall("""
        WITH entries AS (
            SELECT
                condition_id, trader, first_trade,
                ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_order,
                LAG(first_trade) OVER (PARTITION BY condition_id ORDER BY first_trade) AS prev_entry
            FROM test_positions
        ),
        gaps AS (
            SELECT
                condition_id,
                entry_order,
                date_diff('minute', prev_entry, first_trade) AS gap_minutes
            FROM entries
            WHERE entry_order >= 2
        )
        SELECT
            CASE
                WHEN gap_minutes < 5 THEN '1_instant_copy_lt5min'
                WHEN gap_minutes < 30 THEN '2_fast_copy_5_30min'
                WHEN gap_minutes < 120 THEN '3_delayed_30min_2h'
                WHEN gap_minutes < 1440 THEN '4_same_day_2h_24h'
                ELSE '5_independent_gt24h'
            END AS gap_type,
            count() AS n_entries,
            round(count() * 100.0 / sum(count()) OVER (), 1) AS pct,
            round(avg(gap_minutes), 1) AS avg_gap_min,
            round(median(gap_minutes), 1) AS median_gap_min
        FROM gaps
        GROUP BY 1
        ORDER BY 1
    """)
    return {
        "description": "Gap distribution between consecutive qualified trader entries per market",
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Analysis 2: Leader-follower detection
# ---------------------------------------------------------------------------
def analysis_leader_follower(d, base_rate: float) -> dict:
    """Identify leaders (enter first) vs followers. Compare individual HR."""
    rows = d.fetchall("""
        WITH entries AS (
            SELECT
                condition_id, trader, first_trade, yes_won,
                ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_order
            FROM test_positions
        ),
        trader_stats AS (
            SELECT
                trader,
                count() AS n_markets,
                round(avg(entry_order), 2) AS avg_entry_order,
                sum(CASE WHEN entry_order = 1 THEN 1 ELSE 0 END) AS times_first,
                sum(CASE WHEN entry_order <= 2 THEN 1 ELSE 0 END) AS times_top2,
                round(sum(CASE WHEN entry_order <= 2 THEN 1 ELSE 0 END)::DOUBLE / count(), 3) AS leader_score,
                round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count(), 4) AS individual_hr
            FROM entries
            GROUP BY trader
            HAVING n_markets >= 3
        )
        SELECT * FROM trader_stats
        ORDER BY leader_score DESC
    """)

    # Aggregate: leaders (score > 0.5) vs followers (score <= 0.5)
    leaders = [r for r in rows if r["leader_score"] > 0.5]
    followers = [r for r in rows if r["leader_score"] <= 0.5]

    def agg_group(group: list) -> dict:
        if not group:
            return {"n_traders": 0}
        n = len(group)
        avg_hr = sum(r["individual_hr"] for r in group) / n
        avg_markets = sum(r["n_markets"] for r in group) / n
        avg_order = sum(r["avg_entry_order"] for r in group) / n
        return {
            "n_traders": n,
            "avg_individual_hr": round(avg_hr, 4),
            "avg_markets_per_trader": round(avg_markets, 1),
            "avg_entry_order": round(avg_order, 2),
            "excess_hr_over_base": round((avg_hr - base_rate) * 100, 2),
        }

    return {
        "description": "Leaders (entry_order<=2 in >50% of markets) vs followers",
        "leaders_summary": agg_group(leaders),
        "followers_summary": agg_group(followers),
        "base_rate": base_rate,
        "top_10_leaders": rows[:10],
        "bottom_10_followers": [r for r in sorted(rows, key=lambda x: x["leader_score"])][:10],
    }


# ---------------------------------------------------------------------------
# Analysis 3: HR by entry order (first-mover consensus)
# ---------------------------------------------------------------------------
def analysis_first_mover_hr(d, base_rate: float) -> dict:
    """Compare HR when counting only first K entrants vs all."""
    results = []
    for k in [1, 2, 3, 4, 5]:
        for min_n in [1, 2, 3]:
            if min_n > k:
                continue
            rows = d.fetchall(f"""
                WITH ranked AS (
                    SELECT
                        condition_id, trader, first_trade, yes_won,
                        ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_order
                    FROM test_positions
                ),
                first_k AS (
                    SELECT condition_id, first(yes_won) AS yes_won, count(DISTINCT trader) AS n_traders
                    FROM ranked
                    WHERE entry_order <= {k}
                    GROUP BY condition_id
                    HAVING n_traders >= {min_n}
                )
                SELECT
                    count() AS n_markets,
                    sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS n_wins,
                    round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(), 1), 4) AS hr
                FROM first_k
            """)
            if rows and rows[0]["n_markets"] > 0:
                r = rows[0]
                results.append({
                    "first_k": k,
                    "min_consensus_n": min_n,
                    "n_markets": r["n_markets"],
                    "n_wins": r["n_wins"],
                    "hr": r["hr"],
                    "excess_hr_pp": round((r["hr"] - base_rate) * 100, 2),
                })

    return {
        "description": "HR when only counting first K qualified entrants per market",
        "base_rate": base_rate,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Analysis 4: Temporal independence filter
# ---------------------------------------------------------------------------
def analysis_temporal_independence(d, base_rate: float) -> dict:
    """Compare HR for markets with independent vs clustered entries."""
    rows = d.fetchall("""
        WITH entries AS (
            SELECT
                condition_id, trader, first_trade, yes_won,
                ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_order,
                LAG(first_trade) OVER (PARTITION BY condition_id ORDER BY first_trade) AS prev_entry
            FROM test_positions
        ),
        market_gaps AS (
            SELECT
                condition_id,
                first(yes_won) AS yes_won,
                count() AS n_entries,
                min(CASE WHEN entry_order >= 2
                    THEN date_diff('minute', prev_entry, first_trade)
                    ELSE NULL END) AS min_gap_min,
                avg(CASE WHEN entry_order >= 2
                    THEN date_diff('minute', prev_entry, first_trade)
                    ELSE NULL END) AS avg_gap_min,
                max(CASE WHEN entry_order >= 2
                    THEN date_diff('minute', prev_entry, first_trade)
                    ELSE NULL END) AS max_gap_min
            FROM entries
            GROUP BY condition_id
            HAVING n_entries >= 2
        )
        SELECT
            CASE
                WHEN min_gap_min < 5 THEN '1_has_instant_copy_lt5min'
                WHEN min_gap_min < 30 THEN '2_has_fast_copy_5_30min'
                WHEN min_gap_min < 120 THEN '3_all_delayed_30min_2h'
                ELSE '4_all_independent_gt2h'
            END AS independence_level,
            count() AS n_markets,
            sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS n_wins,
            round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(), 1), 4) AS hr,
            round(avg(n_entries), 1) AS avg_n_entries,
            round(avg(min_gap_min), 1) AS avg_min_gap,
            round(avg(avg_gap_min), 1) AS avg_avg_gap
        FROM market_gaps
        GROUP BY 1
        ORDER BY 1
    """)

    return {
        "description": "HR by temporal independence of entries (min gap between any consecutive pair)",
        "base_rate": base_rate,
        "rows": [dict(r, excess_hr_pp=round((r["hr"] - base_rate) * 100, 2)) for r in rows],
    }


# ---------------------------------------------------------------------------
# Analysis 5: Pool composition over time
# ---------------------------------------------------------------------------
def run_pool_composition(d, tag: str, cfg: dict) -> dict:
    """Track pool overlap and turnover across folds."""
    pools = {}
    pool_hrs = {}

    setup_tag_mkts(d, tag)

    for ts, te, xs, xe in FOLDS:
        base, _ = get_base_rate(d, ts, te)
        pool = build_qualified_pool(d, ts, te, base, cfg["meh_pp"], cfg["mpe"])
        pools[xs] = pool

        # Individual HR for pool members in this training window
        if pool:
            placeholders = ", ".join(f"'{t}'" for t in pool)
            hrs = d.fetchall(f"""
                SELECT
                    p.trader,
                    sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS hr
                FROM maker_positions p
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{ts}'
                  AND CAST(p.resolved_at AS DATE) < '{te}'
                  AND p.trader IN ({placeholders})
                GROUP BY p.trader
            """)
            pool_hrs[xs] = {r["trader"]: r["hr"] for r in hrs}

    # Compute overlaps
    fold_keys = [xs for _, _, xs, _ in FOLDS]
    composition = []
    for i, fk in enumerate(fold_keys):
        entry = {
            "fold": fk,
            "pool_size": len(pools[fk]),
            "avg_hr": round(sum(pool_hrs.get(fk, {}).values()) / max(len(pool_hrs.get(fk, {})), 1), 4),
        }
        if i > 0:
            prev_fk = fold_keys[i - 1]
            prev_pool = pools[prev_fk]
            curr_pool = pools[fk]
            overlap = prev_pool & curr_pool
            new_traders = curr_pool - prev_pool
            lost_traders = prev_pool - curr_pool

            entry["returning_from_prev"] = len(overlap)
            entry["new_traders"] = len(new_traders)
            entry["lost_from_prev"] = len(lost_traders)
            entry["overlap_pct"] = round(len(overlap) / max(len(curr_pool), 1) * 100, 1)

            # HR comparison: returning vs new
            curr_hrs = pool_hrs.get(fk, {})
            if overlap:
                returning_hr = [curr_hrs[t] for t in overlap if t in curr_hrs]
                entry["returning_avg_hr"] = round(sum(returning_hr) / max(len(returning_hr), 1), 4)
            if new_traders:
                new_hr = [curr_hrs[t] for t in new_traders if t in curr_hrs]
                entry["new_trader_avg_hr"] = round(sum(new_hr) / max(len(new_hr), 1), 4)

        composition.append(entry)

    return {
        "description": "Pool composition and turnover across folds",
        "tag": tag,
        "composition": composition,
    }


# ---------------------------------------------------------------------------
# Analysis 6 (bonus): Entry order vs outcome correlation
# ---------------------------------------------------------------------------
def analysis_entry_order_outcome(d, base_rate: float) -> dict:
    """Per entry_order position, what's the individual position HR?"""
    rows = d.fetchall("""
        WITH ranked AS (
            SELECT
                condition_id, trader, first_trade, yes_won, realized_pnl,
                ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_order
            FROM test_positions
        )
        SELECT
            entry_order,
            count() AS n_positions,
            count(DISTINCT condition_id) AS n_markets,
            round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(), 1), 4) AS hr,
            round(avg(realized_pnl), 2) AS avg_pnl
        FROM ranked
        WHERE entry_order <= 10
        GROUP BY entry_order
        ORDER BY entry_order
    """)
    return {
        "description": "HR and PnL by entry_order (1=first mover, 2=second, etc.)",
        "base_rate": base_rate,
        "rows": [dict(r, excess_hr_pp=round((r["hr"] - base_rate) * 100, 2)) for r in rows],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_tag_fold(d, tag: str, cfg: dict, ts: str, te: str, xs: str, xe: str) -> dict:
    """Run all analyses for one tag/fold combination."""
    base, n_mkts = get_base_rate(d, xs, xe)
    train_base, _ = get_base_rate(d, ts, te)

    qualified = build_qualified_pool(d, ts, te, train_base, cfg["meh_pp"], cfg["mpe"])
    build_test_positions(d, qualified, xs, xe)

    n_test_pos = d.fetchone("SELECT count() AS n FROM test_positions")["n"]
    n_markets = d.fetchone("SELECT count(DISTINCT condition_id) AS n FROM test_positions")["n"]
    n_traders = d.fetchone("SELECT count(DISTINCT trader) AS n FROM test_positions")["n"]

    log(f"    fold {xs}: pool={len(qualified)} test_pos={n_test_pos} markets={n_markets} traders={n_traders}")

    if n_test_pos < 10:
        log(f"    fold {xs}: SKIP (too few test positions)")
        return {"fold": xs, "skip": True, "n_test_positions": n_test_pos}

    return {
        "fold": xs,
        "test_base_rate": base,
        "train_base_rate": train_base,
        "n_test_markets": n_mkts,
        "pool_size": len(qualified),
        "n_test_positions": n_test_pos,
        "n_active_markets": n_markets,
        "n_active_traders": n_traders,
        "entry_clustering": analysis_entry_clustering(d),
        "leader_follower": analysis_leader_follower(d, base),
        "first_mover_hr": analysis_first_mover_hr(d, base),
        "temporal_independence": analysis_temporal_independence(d, base),
        "entry_order_outcome": analysis_entry_order_outcome(d, base),
    }


def main() -> None:
    d = db()
    results = {
        "hypothesis": "copy-trader-contamination",
        "timestamp": datetime.now().isoformat(),
        "note": "Investigates whether pool growth is driven by copy-traders. "
                "Uses same pool qualification as validation R2.",
        "tags": {},
    }

    for tag, cfg in CONFIGS.items():
        log(f"\n=== {tag} ===")
        n_mkts = setup_tag_mkts(d, tag)
        log(f"  {n_mkts:,} markets in tag")

        fold_results = []
        for ts, te, xs, xe in FOLDS:
            t0 = time.time()
            fold_data = run_tag_fold(d, tag, cfg, ts, te, xs, xe)
            elapsed = time.time() - t0
            fold_data["elapsed_s"] = round(elapsed, 1)
            fold_results.append(fold_data)
            log(f"    fold {xs}: done in {elapsed:.1f}s")

        # Pool composition analysis (across all folds)
        pool_comp = run_pool_composition(d, tag, cfg)

        results["tags"][tag] = {
            "config": cfg,
            "folds": fold_results,
            "pool_composition": pool_comp,
        }

    out_path = OUTPUT_DIR / "copy_trader_analysis.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
