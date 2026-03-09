"""Tag-HR-Consensus Sweep — vectorized discovery for consensus-based signals.

This fixes the tag-hr-copy failure: instead of copying individual trades,
we measure market-level consensus where N distinct qualified traders entered
the same market. The Nth trader's chronological entry price is used as fill.

Tags: Esports, Tennis (1H excluded — near-base-rate in prior tick validation)

Pool construction: scorecard-v3 composite scoring with BEH gate.
  composite = 0.45 * excess_hr + 0.25 * consistency + 0.15 * avg_edge + 0.15 * beh
  BEH gate >= 0.02
  BUY-only trades (SELL is exit, not signal)
  Gambling markets excluded

Sweep parameters:
  K (pool size): 25, 50, 100
  N (consensus threshold): 1, 2, 3, 4, 5
  Direction: YES-only, NO-only, combined (vol-weighted majority)

Walk-forward folds:
  Train on [ts, te), test on [xs, xe)
  5 folds, 6-month training windows

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/scripts/sweep_consensus.py
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/scripts/sweep_consensus.py --tags Esports
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from research.db import db

TAGS = ["Esports", "Tennis"]

POOL_SIZES = [25, 50, 100]
CONSENSUS_THRESHOLDS = [1, 2, 3, 4, 5]
DIRECTIONS = ["YES", "NO", "combined"]

FOLDS = [
    # (train_start, train_end, test_start, test_end)
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-04-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-07-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-10-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2026-01-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-03-01"),
]

# Gambling market macro (same as scorecard-v3)
GAMBLING_MACRO_SQL = """
CREATE OR REPLACE MACRO is_gambling_market_v3(slug) AS (
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
"""


def build_pool_for_tag(d, tag: str, k: int, train_start: str, train_end: str,
                       direction: str) -> set[str]:
    """Build a composite-scored pool for a tag+direction using scorecard-v3 logic.

    Parameters:
        direction: "YES" or "NO" — determines which position side to evaluate
    """
    tag_lower = tag.lower()

    if direction == "YES":
        pos_filter = "p.position = 'YES'"
        correct_expr = "CAST(p.yes_won AS DOUBLE)"
    else:
        pos_filter = "p.position = 'NO'"
        correct_expr = "p.correct::DOUBLE"

    # Step 1: base training stats
    d.execute(f"""
    CREATE OR REPLACE TABLE _thc_train_{tag_lower}_{direction.lower()} AS
    WITH base AS (
        SELECT
            p.trader, p.condition_id,
            {correct_expr} AS correct,
            abs(p.net_usd) AS abs_net_usd,
            p.volume, p.net_usd
        FROM maker_positions p
        JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
          AND p.net_usd IS NOT NULL
    ),
    tag_base AS (
        SELECT avg(correct) AS base_rate FROM base
    ),
    trader_stats AS (
        SELECT
            b.trader,
            count(DISTINCT b.condition_id) AS n_markets,
            avg(b.correct) AS hit_rate,
            median(b.net_usd) AS avg_edge_usd
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 20
           AND count(*) < 10000
    )
    SELECT
        ts.trader, ts.n_markets, ts.hit_rate, ts.avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    # Step 2: consistency_sharpe
    d.execute(f"""
    CREATE OR REPLACE TABLE _thc_consistency_{tag_lower}_{direction.lower()} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg({correct_expr}) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN _thc_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    ),
    sharpe AS (
        SELECT trader, count(*) AS n_months,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0 END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
        HAVING count(*) >= 3
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: bucket_excess_hr
    if direction == "YES":
        price_expr = "yed.price_x_vol / yed.volume"
        join_clause = f"""
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _thc_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
        """
    else:
        price_expr = "COALESCE(1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)), abs(p.net_usd) / NULLIF(p.net_no, 0))"
        join_clause = f"""
        JOIN _thc_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
          AND {price_expr} BETWEEN 0.05 AND 0.95
        """

    d.execute(f"""
    CREATE OR REPLACE TABLE _thc_bucket_{tag_lower}_{direction.lower()} AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            {correct_expr} AS correct,
            CAST(floor({price_expr} * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
        {join_clause}
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr, count(*) AS n_pop
        FROM price_data GROUP BY price_bucket HAVING count(*) >= 10
    ),
    trader_bucket AS (
        SELECT pd.trader, pd.price_bucket,
               avg(pd.correct) AS bucket_hr, bp.pop_hr
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    ),
    trader_bucket_excess AS (
        SELECT trader, count(*) AS n_buckets,
               avg(bucket_hr - pop_hr) AS bucket_excess_hr
        FROM trader_bucket
        GROUP BY trader
        HAVING count(*) >= 2
    )
    SELECT * FROM trader_bucket_excess;
    """)

    # Step 4: composite score with BEH gate
    d.execute(f"""
    CREATE OR REPLACE TABLE _thc_composite_{tag_lower}_{direction.lower()} AS
    WITH base AS (
        SELECT
            t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _thc_train_{tag_lower}_{direction.lower()} t
        LEFT JOIN _thc_consistency_{tag_lower}_{direction.lower()} c ON t.trader = c.trader
        LEFT JOIN _thc_bucket_{tag_lower}_{direction.lower()} b ON t.trader = b.trader
        WHERE coalesce(b.bucket_excess_hr, 0.0) >= 0.02
    ),
    ranked AS (
        SELECT *,
            percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
            percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
            percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge,
            percent_rank() OVER (ORDER BY bucket_excess_hr) AS pr_bucket
        FROM base
    )
    SELECT
        trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd, bucket_excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) DESC
        ) AS rank
    FROM ranked;
    """)

    rows = d.fetchall(f"""
        SELECT trader FROM _thc_composite_{tag_lower}_{direction.lower()}
        WHERE rank <= {k}
        ORDER BY rank
    """)
    pool = {r["trader"].lower() for r in rows}

    # Get count of qualified traders for logging
    n_qualified = d.fetchone(f"SELECT count() AS n FROM _thc_composite_{tag_lower}_{direction.lower()}")["n"]

    return pool


def compute_consensus_signals(
    d,
    tag: str,
    pool_yes: set[str],
    pool_no: set[str],
    test_start: str,
    test_end: str,
    consensus_n: int,
    direction_mode: str,
) -> list[dict]:
    """Compute consensus signals for a tag+pool+N.

    For each market in the tag that resolves in [test_start, test_end):
    - Count distinct pool traders who BUY that market's YES or NO token
    - Only count traders whose first_trade >= test_start (test-period entry)
    - If count >= consensus_n, it's a signal
    - Use Nth trader's chronological entry price as fill price

    direction_mode: "YES" | "NO" | "combined"
      YES: only YES pool, only YES entries, fire YES
      NO: only NO pool, only NO entries, fire NO
      combined: both pools, fire in vol-weighted majority direction
    """

    if direction_mode == "YES":
        d.execute("DROP TABLE IF EXISTS _thc_signal_pool")
        vals = ", ".join(f"('{t}')" for t in pool_yes)
        if not pool_yes:
            return []
        d.execute(f"CREATE TEMP TABLE _thc_signal_pool AS SELECT * FROM (VALUES {vals}) AS t(trader)")

        rows = d.fetchall(f"""
            WITH entries AS (
                SELECT
                    p.condition_id,
                    p.trader,
                    p.yes_won,
                    p.correct,
                    p.realized_pnl,
                    p.first_trade,
                    p.resolved_at,
                    p.net_usd,
                    p.volume
                FROM maker_positions p
                JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
                JOIN _thc_signal_pool sp ON p.trader = sp.trader
                WHERE p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                  AND CAST(p.resolved_at AS DATE) < '{test_end}'
                  AND CAST(p.first_trade AS DATE) >= '{test_start}'
                  AND p.volume > 0
            ),
            ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_rank,
                    COUNT(*) OVER (PARTITION BY condition_id) AS n_traders
                FROM entries
            )
            SELECT
                condition_id,
                n_traders,
                -- Use Nth trader's entry time
                MAX(CASE WHEN entry_rank = {consensus_n} THEN first_trade END) AS signal_time,
                first(resolved_at) AS resolved_at,
                first(yes_won) AS yes_won,
                -- Market-level correctness for YES direction
                first(yes_won) AS signal_correct,
                median(realized_pnl) AS median_pnl,
                avg(realized_pnl) AS avg_pnl,
                'YES' AS signal_direction
            FROM ranked
            WHERE n_traders >= {consensus_n}
            GROUP BY condition_id, n_traders
            HAVING signal_time IS NOT NULL
        """)
        return rows

    elif direction_mode == "NO":
        d.execute("DROP TABLE IF EXISTS _thc_signal_pool")
        vals = ", ".join(f"('{t}')" for t in pool_no)
        if not pool_no:
            return []
        d.execute(f"CREATE TEMP TABLE _thc_signal_pool AS SELECT * FROM (VALUES {vals}) AS t(trader)")

        rows = d.fetchall(f"""
            WITH entries AS (
                SELECT
                    p.condition_id,
                    p.trader,
                    p.yes_won,
                    p.correct,
                    p.realized_pnl,
                    p.first_trade,
                    p.resolved_at,
                    p.net_usd,
                    p.volume
                FROM maker_positions p
                JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
                JOIN _thc_signal_pool sp ON p.trader = sp.trader
                WHERE p.position = 'NO'
                  AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                  AND CAST(p.resolved_at AS DATE) < '{test_end}'
                  AND CAST(p.first_trade AS DATE) >= '{test_start}'
                  AND p.volume > 0
            ),
            ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_rank,
                    COUNT(*) OVER (PARTITION BY condition_id) AS n_traders
                FROM entries
            )
            SELECT
                condition_id,
                n_traders,
                MAX(CASE WHEN entry_rank = {consensus_n} THEN first_trade END) AS signal_time,
                first(resolved_at) AS resolved_at,
                first(yes_won) AS yes_won,
                -- NO is correct when yes_won = 0
                (1 - first(yes_won))::INT AS signal_correct,
                median(realized_pnl) AS median_pnl,
                avg(realized_pnl) AS avg_pnl,
                'NO' AS signal_direction
            FROM ranked
            WHERE n_traders >= {consensus_n}
            GROUP BY condition_id, n_traders
            HAVING signal_time IS NOT NULL
        """)
        return rows

    else:  # combined
        # Get YES and NO signals separately, then pick vol-weighted majority
        d.execute("DROP TABLE IF EXISTS _thc_signal_pool_yes")
        d.execute("DROP TABLE IF EXISTS _thc_signal_pool_no")

        if pool_yes:
            vals_y = ", ".join(f"('{t}')" for t in pool_yes)
            d.execute(f"CREATE TEMP TABLE _thc_signal_pool_yes AS SELECT * FROM (VALUES {vals_y}) AS t(trader)")
        else:
            d.execute("CREATE TEMP TABLE _thc_signal_pool_yes(trader VARCHAR)")

        if pool_no:
            vals_n = ", ".join(f"('{t}')" for t in pool_no)
            d.execute(f"CREATE TEMP TABLE _thc_signal_pool_no AS SELECT * FROM (VALUES {vals_n}) AS t(trader)")
        else:
            d.execute("CREATE TEMP TABLE _thc_signal_pool_no(trader VARCHAR)")

        rows = d.fetchall(f"""
            WITH yes_entries AS (
                SELECT p.condition_id, p.trader, p.first_trade, p.volume,
                       'YES' AS dir
                FROM maker_positions p
                JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
                JOIN _thc_signal_pool_yes sp ON p.trader = sp.trader
                WHERE p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                  AND CAST(p.resolved_at AS DATE) < '{test_end}'
                  AND CAST(p.first_trade AS DATE) >= '{test_start}'
                  AND p.volume > 0
            ),
            no_entries AS (
                SELECT p.condition_id, p.trader, p.first_trade, p.volume,
                       'NO' AS dir
                FROM maker_positions p
                JOIN _thc_tag_mkts tm ON p.condition_id = tm.condition_id
                JOIN _thc_signal_pool_no sp ON p.trader = sp.trader
                WHERE p.position = 'NO'
                  AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                  AND CAST(p.resolved_at AS DATE) < '{test_end}'
                  AND CAST(p.first_trade AS DATE) >= '{test_start}'
                  AND p.volume > 0
            ),
            all_entries AS (
                SELECT * FROM yes_entries
                UNION ALL
                SELECT * FROM no_entries
            ),
            per_market AS (
                SELECT condition_id,
                    COUNT(DISTINCT trader) AS n_traders,
                    SUM(CASE WHEN dir = 'YES' THEN volume ELSE 0 END) AS yes_vol,
                    SUM(CASE WHEN dir = 'NO' THEN volume ELSE 0 END) AS no_vol,
                    COUNT(DISTINCT CASE WHEN dir = 'YES' THEN trader END) AS n_yes,
                    COUNT(DISTINCT CASE WHEN dir = 'NO' THEN trader END) AS n_no
                FROM all_entries
                GROUP BY condition_id
                HAVING COUNT(DISTINCT trader) >= {consensus_n}
            ),
            market_resolution AS (
                SELECT condition_id,
                       first(yes_won) AS yes_won,
                       first(resolved_at) AS resolved_at,
                       median(realized_pnl) AS median_pnl,
                       avg(realized_pnl) AS avg_pnl
                FROM maker_positions
                WHERE condition_id IN (SELECT condition_id FROM per_market)
                GROUP BY condition_id
            )
            SELECT
                pm.condition_id,
                pm.n_traders,
                mr.resolved_at,
                mr.yes_won,
                CASE WHEN pm.yes_vol >= pm.no_vol THEN 'YES' ELSE 'NO' END AS signal_direction,
                CASE
                    WHEN pm.yes_vol >= pm.no_vol THEN mr.yes_won
                    ELSE (1 - mr.yes_won)
                END AS signal_correct,
                mr.median_pnl,
                mr.avg_pnl,
                pm.n_yes,
                pm.n_no
            FROM per_market pm
            JOIN market_resolution mr ON pm.condition_id = mr.condition_id
        """)
        return rows


def run_sweep(tags: list[str] | None = None) -> dict:
    d = db()
    tags = tags or TAGS

    # Setup gambling macro
    d.execute(GAMBLING_MACRO_SQL)

    results = {
        "hypothesis": "tag-hr-consensus",
        "timestamp": datetime.now().isoformat(),
        "description": "Consensus-based signals: fire when N distinct qualified traders enter same market",
        "fix_from_tag_hr_copy": "Individual trade copying replaced with N-trader consensus threshold",
        "verdict": "pending",
        "tags": {},
    }

    for tag in tags:
        t_tag = time.time()
        print(f"\n{'='*70}")
        print(f"  TAG: {tag}")
        print(f"{'='*70}", flush=True)

        # Tag markets (excluding gambling)
        d.execute("DROP TABLE IF EXISTS _thc_tag_mkts")
        d.execute(f"""
            CREATE TABLE _thc_tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = '{tag}'
              AND NOT is_gambling_market_v3(m.slug)
        """)
        n_mkts = d.fetchone("SELECT count() as n FROM _thc_tag_mkts")["n"]
        print(f"  {n_mkts:,} non-gambling markets in tag", flush=True)

        tag_results: dict = {"n_markets": n_mkts, "folds": [], "summary": {}}

        for fold_idx, (ts, te, xs, xe) in enumerate(FOLDS):
            t_fold = time.time()
            print(f"\n  --- Fold {fold_idx+1}: train=[{ts},{te}) test=[{xs},{xe}) ---", flush=True)

            # Compute per-fold base rates
            br_yes = d.fetchone(f"""
                SELECT avg(CAST(yes_won AS DOUBLE)) AS br
                FROM (
                    SELECT condition_id, first(yes_won) as yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _thc_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{xs}'
                      AND CAST(resolved_at AS DATE) < '{xe}'
                    GROUP BY condition_id
                )
            """)
            base_rate_yes = round(br_yes["br"], 4) if br_yes and br_yes["br"] is not None else 0.5
            base_rate_no = round(1.0 - base_rate_yes, 4)
            print(f"    Base rates: YES={base_rate_yes:.4f}, NO={base_rate_no:.4f}", flush=True)

            fold_combos = []

            for k in POOL_SIZES:
                # Build pools for YES and NO directions
                t_pool = time.time()
                pool_yes = build_pool_for_tag(d, tag, k, ts, te, "YES")
                pool_no = build_pool_for_tag(d, tag, k, ts, te, "NO")
                pool_time = time.time() - t_pool
                print(f"    K={k}: YES pool={len(pool_yes)}, NO pool={len(pool_no)} ({pool_time:.1f}s)", flush=True)

                for n in CONSENSUS_THRESHOLDS:
                    for direction_mode in DIRECTIONS:
                        t_sig = time.time()

                        if direction_mode == "YES":
                            base_rate = base_rate_yes
                        elif direction_mode == "NO":
                            base_rate = base_rate_no
                        else:
                            base_rate = base_rate_yes  # combined uses YES base for YES dir signals

                        signals = compute_consensus_signals(
                            d, tag, pool_yes, pool_no, xs, xe, n, direction_mode,
                        )

                        n_signals = len(signals)
                        if n_signals == 0:
                            continue

                        n_correct = sum(1 for s in signals if s.get("signal_correct", 0) == 1)
                        hr = n_correct / n_signals if n_signals > 0 else 0
                        med_pnl = sorted([s.get("median_pnl", 0) or 0 for s in signals])[n_signals // 2] if n_signals > 0 else 0

                        # Compute hold time where available
                        hold_hours_list = []
                        for s in signals:
                            if s.get("signal_time") and s.get("resolved_at"):
                                try:
                                    st = s["signal_time"]
                                    rt = s["resolved_at"]
                                    # Handle both datetime and timestamp types
                                    if hasattr(st, 'timestamp') and hasattr(rt, 'timestamp'):
                                        h = (rt.timestamp() - st.timestamp()) / 3600
                                    else:
                                        h = 0
                                    if h >= 0:
                                        hold_hours_list.append(h)
                                except Exception:
                                    pass

                        med_hold_h = sorted(hold_hours_list)[len(hold_hours_list) // 2] if hold_hours_list else 24.0

                        if direction_mode == "combined":
                            # For combined, compute HR separately by direction
                            yes_sigs = [s for s in signals if s.get("signal_direction") == "YES"]
                            no_sigs = [s for s in signals if s.get("signal_direction") == "NO"]
                            n_yes = len(yes_sigs)
                            n_no = len(no_sigs)
                            hr_yes = sum(1 for s in yes_sigs if s.get("signal_correct") == 1) / n_yes if n_yes else 0
                            hr_no = sum(1 for s in no_sigs if s.get("signal_correct") == 1) / n_no if n_no else 0
                            # Overall excess: weighted by signal count
                            excess = (n_yes * (hr_yes - base_rate_yes) + n_no * (hr_no - base_rate_no)) / n_signals if n_signals else 0
                        else:
                            excess = hr - base_rate

                        combo_key = f"K{k}_N{n}_{direction_mode}"
                        sig_time = time.time() - t_sig

                        fold_combos.append({
                            "combo_key": combo_key,
                            "k": k,
                            "n": n,
                            "direction": direction_mode,
                            "n_signals": n_signals,
                            "n_correct": n_correct,
                            "hr": round(hr, 4),
                            "base_rate": round(base_rate if direction_mode != "combined" else base_rate_yes, 4),
                            "base_rate_yes": base_rate_yes,
                            "base_rate_no": base_rate_no,
                            "excess_hr": round(excess, 4),
                            "median_pnl": round(float(med_pnl), 2),
                            "median_hold_hours": round(med_hold_h, 2),
                            "fold": xs,
                            "time_s": round(sig_time, 2),
                        })

            fold_elapsed = time.time() - t_fold
            tag_results["folds"].append({
                "fold_test": xs,
                "base_rate_yes": base_rate_yes,
                "base_rate_no": base_rate_no,
                "n_combos": len(fold_combos),
                "elapsed_s": round(fold_elapsed, 1),
                "combos": fold_combos,
            })
            print(f"    Fold done: {len(fold_combos)} combos, {fold_elapsed:.1f}s", flush=True)

        # Aggregate across folds
        combo_agg: dict[str, list] = defaultdict(list)
        for fold in tag_results["folds"]:
            for combo in fold["combos"]:
                combo_agg[combo["combo_key"]].append(combo)

        summary = []
        for combo_key, folds_data in combo_agg.items():
            if len(folds_data) < 2:
                continue

            n_folds = len(folds_data)
            avg_hr = sum(f["hr"] for f in folds_data) / n_folds
            avg_excess = sum(f["excess_hr"] for f in folds_data) / n_folds
            avg_med_pnl = sum(f["median_pnl"] for f in folds_data) / n_folds
            avg_hold_h = sum(f["median_hold_hours"] for f in folds_data) / n_folds
            total_signals = sum(f["n_signals"] for f in folds_data)
            total_correct = sum(f["n_correct"] for f in folds_data)

            # Pool HR (aggregate, not avg of avgs)
            pooled_hr = total_correct / total_signals if total_signals > 0 else 0

            # Compounding score
            hold_days = avg_hold_h / 24 if avg_hold_h > 0 else 1.0
            cs = round(avg_excess * avg_med_pnl / hold_days, 4) if avg_med_pnl > 0 else 0

            parts = combo_key.split("_")
            summary.append({
                "combo_key": combo_key,
                "k": int(parts[0][1:]),
                "n_consensus": int(parts[1][1:]),
                "direction": "_".join(parts[2:]),
                "n_folds": n_folds,
                "avg_hr": round(avg_hr, 4),
                "pooled_hr": round(pooled_hr, 4),
                "avg_excess_hr_pp": round(avg_excess * 100, 2),
                "avg_median_pnl": round(avg_med_pnl, 2),
                "avg_hold_hours": round(avg_hold_h, 2),
                "total_signals": total_signals,
                "signals_per_fold": round(total_signals / n_folds, 1),
                "compounding_score": cs,
                "per_fold": [
                    {
                        "fold": f["fold"],
                        "n_signals": f["n_signals"],
                        "hr": f["hr"],
                        "excess": f["excess_hr"],
                        "base": f["base_rate"],
                    }
                    for f in folds_data
                ],
            })

        summary.sort(key=lambda x: x["avg_excess_hr_pp"], reverse=True)
        tag_results["summary"] = summary

        # Print top configs
        print(f"\n  === TOP 10 by excess HR (tag={tag}) ===")
        for i, s in enumerate(summary[:10]):
            print(
                f"  {i+1}. {s['combo_key']:<25} "
                f"HR={s['avg_hr']:.1%} excess={s['avg_excess_hr_pp']:+.1f}pp "
                f"sigs={s['total_signals']:>5} med_pnl=${s['avg_median_pnl']:.2f} "
                f"hold={s['avg_hold_hours']:.1f}h CS={s['compounding_score']:.2f} "
                f"folds={s['n_folds']}"
            )

        results["tags"][tag] = tag_results
        tag_elapsed = time.time() - t_tag
        print(f"\n  Tag {tag} total: {tag_elapsed:.1f}s", flush=True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Tag-HR-Consensus Sweep")
    parser.add_argument("--tags", nargs="+", help="Override tag list (default: Esports Tennis)")
    args = parser.parse_args()

    t0 = time.time()
    results = run_sweep(args.tags)
    elapsed = time.time() - t0

    out_path = Path("research/hypotheses/tag-hr-consensus/discovery/results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDone in {elapsed:.0f}s -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
