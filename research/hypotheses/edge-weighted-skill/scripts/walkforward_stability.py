"""Walk-forward stability analysis: edge-weighted vs HR-only trader pool rankings.

Tests whether edge-weighted (bucket_excess_hr-primary) scorer produces more stable
out-of-sample pools than HR-only rankings. Three scoring methods compared:

  1. HR-only: rank by excess_hr descending
  2. Composite (existing): 0.45*excess_hr + 0.25*consistency + 0.15*avg_edge + 0.15*bucket_excess
  3. Edge-primary (NEW):   0.45*bucket_excess + 0.25*consistency + 0.15*avg_edge + 0.15*excess_hr

Walk-forward folds (12m train, 3m test):
  Fold 1: train 2024-07-01 – 2025-06-30, test 2025-07-01 – 2025-09-30
  Fold 2: train 2024-10-01 – 2025-09-30, test 2025-10-01 – 2025-12-31
  Fold 3: train 2025-01-01 – 2025-12-31, test 2026-01-01 – 2026-03-09

CRITICAL RULES enforced:
  - train_end = test_start (no overlap)
  - first_trade >= test_start to filter phantom signals (not copyable)
  - Aggregate to MARKET level before computing metrics
  - Gambling markets excluded (updown / up-or-down / crypto above-below patterns)
  - All results are UPPER BOUNDS (vectorized, not tick-by-tick)

Usage:
    PYTHONPATH=/mnt/nvme/git/polymarket/polymarket uv run python \
        research/hypotheses/edge-weighted-skill/scripts/walkforward_stability.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_PATH = "/mnt/nvme/git/polymarket/polymarket/tmp/walkforward_stability.log"
OUT_JSON = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/walkforward_results.json"
OUT_MD = "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/edge-weighted-skill/discovery/walkforward_results.md"

# -------------------------------------------------------------------
# Walk-forward fold definitions
# CRITICAL: train_end = test_start (strict, no overlap)
# -------------------------------------------------------------------
FOLDS = [
    {
        "fold": 1,
        "train_start": "2024-07-01",
        "train_end": "2025-07-01",   # exclusive upper bound
        "test_start": "2025-07-01",
        "test_end":   "2025-10-01",  # exclusive
    },
    {
        "fold": 2,
        "train_start": "2024-10-01",
        "train_end": "2025-10-01",
        "test_start": "2025-10-01",
        "test_end":   "2026-01-01",
    },
    {
        "fold": 3,
        "train_start": "2025-01-01",
        "train_end": "2026-01-01",
        "test_start": "2026-01-01",
        "test_end":   "2026-04-01",  # open-ended for current data
    },
]

TAGS = ["Politics", "Sports", "Crypto", "Elections"]
K_VALUES = [25, 50, 100]
MIN_TRAIN_POSITIONS = 20  # minimum resolved non-gambling positions in training window

GAMBLING_MACRO_SQL = """
CREATE OR REPLACE MACRO is_gambling_market_wf(slug) AS (
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


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def ensure_base_tables(con) -> None:
    """Create shared market_tags lookup (gambling-excluded)."""
    con.execute(GAMBLING_MACRO_SQL)
    log("Created gambling macro.")

    # Priority-based canonical tag assignment (Elections > Crypto > Politics > Sports > ...)
    con.execute("""
    CREATE OR REPLACE TABLE _wf_market_tags AS
    WITH tagged AS (
        SELECT
            m.condition_id,
            m.slug,
            et.label,
            CASE
                WHEN et.label = 'Elections'  THEN 0
                WHEN et.label = 'Crypto'     THEN 1
                WHEN et.label = 'Politics'   THEN 2
                WHEN et.label = 'Sports'     THEN 3
                WHEN et.label = 'Soccer'     THEN 4
                WHEN et.label = 'Basketball' THEN 5
                WHEN et.label = 'Tech'       THEN 6
                ELSE 999
            END AS tag_priority
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE NOT is_gambling_market_wf(m.slug)
    ),
    primary_tag_cte AS (
        SELECT condition_id, slug,
               first(label ORDER BY tag_priority, label) AS primary_tag
        FROM tagged
        GROUP BY condition_id, slug
    )
    SELECT * FROM primary_tag_cte;
    """)
    log("Created _wf_market_tags.")


def build_fold_pool(
    con,
    fold: dict,
    tag: str,
    k: int,
    method: str,  # 'hr_only' | 'composite' | 'edge_primary'
) -> dict:
    """Score traders using training data only, select top-K pool.

    Returns dict with:
        pool: list of trader addresses
        pool_details: list of dicts with scorer components
        train_base_rate: float
        n_qualified_train: int
    """
    train_start = fold["train_start"]
    train_end = fold["train_end"]  # exclusive
    fold_id = fold["fold"]
    suffix = f"f{fold_id}_{tag.lower()}_{method}"

    # Step 1: Base training stats per trader
    con.execute(f"""
    CREATE OR REPLACE TABLE _wf_train_{suffix} AS
    WITH base AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            abs(p.net_usd) AS abs_net_usd,
            p.volume,
            CASE WHEN p.volume > 0 THEN abs(p.net_usd) / p.volume ELSE 0 END AS conviction,
            p.net_usd
        FROM maker_positions p
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
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
            avg(b.conviction) AS avg_conviction,
            median(b.net_usd) AS avg_edge_usd
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= {MIN_TRAIN_POSITIONS}
           AND avg(b.conviction) >= 0.90
           AND count(*) < 10000
    )
    SELECT
        ts.trader,
        ts.n_markets,
        ts.hit_rate,
        ts.avg_conviction,
        ts.avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    n_qualified = con.execute(f"SELECT count(*) FROM _wf_train_{suffix}").fetchone()[0]
    base_rate_row = con.execute(
        f"SELECT avg(tag_base_rate) FROM _wf_train_{suffix}"
    ).fetchone()
    train_base_rate = base_rate_row[0] if base_rate_row and base_rate_row[0] is not None else 0.0

    if n_qualified < k:
        log(f"  [WARN] fold={fold_id} tag={tag} method={method}: only {n_qualified} qualified traders, need {k}")

    # Step 2: consistency_sharpe (monthly Sharpe on HR, >=6 months >=5 positions each)
    con.execute(f"""
    CREATE OR REPLACE TABLE _wf_consistency_{suffix} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _wf_train_{suffix} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    ),
    sharpe AS (
        SELECT
            trader,
            count(*) AS n_months,
            avg(month_hr) AS mean_hr,
            CASE WHEN count(*) >= 2
                 THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
                 ELSE 0.0
            END AS consistency_sharpe
        FROM monthly
        GROUP BY trader
        HAVING count(*) >= 6
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: bucket_excess_hr (per 10pp price bucket, >=2 buckets required)
    con.execute(f"""
    CREATE OR REPLACE TABLE _wf_bucket_{suffix} AS
    WITH price_data AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _wf_train_{suffix} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
          AND yed.volume > 0
    ),
    bucket_pop AS (
        SELECT price_bucket, avg(correct) AS pop_hr
        FROM price_data
        GROUP BY price_bucket
    ),
    trader_bucket AS (
        SELECT
            pd.trader,
            pd.price_bucket,
            avg(pd.correct) AS bucket_hr,
            bp.pop_hr
        FROM price_data pd
        JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
        GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
    ),
    trader_bucket_excess AS (
        SELECT
            trader,
            count(*) AS n_buckets,
            avg(bucket_hr - pop_hr) AS bucket_excess_hr
        FROM trader_bucket
        GROUP BY trader
        HAVING count(*) >= 2
    )
    SELECT * FROM trader_bucket_excess;
    """)

    # Step 4: Build composite score and rank by chosen method
    if method == "hr_only":
        score_expr = "excess_hr"
    elif method == "composite":
        score_expr = "(0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket)"
    elif method == "edge_primary":
        score_expr = "(0.45 * pr_bucket + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_excess_hr)"
    else:
        raise ValueError(f"Unknown method: {method}")

    if method == "hr_only":
        con.execute(f"""
        CREATE OR REPLACE TABLE _wf_scored_{suffix} AS
        SELECT
            t.trader,
            t.excess_hr,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            t.avg_edge_usd,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr,
            t.excess_hr AS score,
            ROW_NUMBER() OVER (ORDER BY t.excess_hr DESC) AS rank
        FROM _wf_train_{suffix} t
        LEFT JOIN _wf_consistency_{suffix} c ON t.trader = c.trader
        LEFT JOIN _wf_bucket_{suffix} b ON t.trader = b.trader;
        """)
    else:
        con.execute(f"""
        CREATE OR REPLACE TABLE _wf_scored_{suffix} AS
        WITH base AS (
            SELECT
                t.trader,
                t.excess_hr,
                t.avg_edge_usd,
                coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
                coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
            FROM _wf_train_{suffix} t
            LEFT JOIN _wf_consistency_{suffix} c ON t.trader = c.trader
            LEFT JOIN _wf_bucket_{suffix} b ON t.trader = b.trader
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
            trader,
            excess_hr,
            consistency_sharpe,
            avg_edge_usd,
            bucket_excess_hr,
            {score_expr} AS score,
            ROW_NUMBER() OVER (ORDER BY {score_expr} DESC) AS rank
        FROM ranked;
        """)

    rows = con.execute(f"""
        SELECT trader, excess_hr, consistency_sharpe, avg_edge_usd, bucket_excess_hr, score, rank
        FROM _wf_scored_{suffix}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()

    pool_traders = [r[0].lower() for r in rows]
    pool_details = [
        {
            "trader": r[0].lower(),
            "excess_hr": round(r[1], 4) if r[1] is not None else None,
            "consistency_sharpe": round(r[2], 4) if r[2] is not None else None,
            "avg_edge_usd": round(r[3], 2) if r[3] is not None else None,
            "bucket_excess_hr": round(r[4], 4) if r[4] is not None else None,
            "score": round(r[5], 4) if r[5] is not None else None,
            "train_rank": r[6],
        }
        for r in rows
    ]

    return {
        "pool": pool_traders,
        "pool_details": pool_details,
        "train_base_rate": round(train_base_rate, 4),
        "n_qualified_train": n_qualified,
    }


def evaluate_oos(
    con,
    fold: dict,
    tag: str,
    pool_traders: list[str],
    suffix: str,
) -> dict:
    """Evaluate pool traders on test-period data.

    CRITICAL filters:
      - resolved_at in [test_start, test_end)
      - first_trade >= test_start (phantom signal filter — only copyable entries)
      - Aggregate to MARKET level before computing HR (counting_unit rule)

    Returns dict with oos metrics.
    """
    test_start = fold["test_start"]
    test_end = fold["test_end"]

    if not pool_traders:
        return {
            "n_signals": 0,
            "active_traders": 0,
            "test_hr": None,
            "test_excess_hr": None,
            "test_base_rate": None,
            "median_hold_days": None,
        }

    # Build a temp table for the pool
    traders_list = ", ".join(f"'{t}'" for t in pool_traders)
    con.execute(f"""
    CREATE OR REPLACE TABLE _wf_pool_{suffix} AS
    SELECT unnest([{traders_list}]) AS trader;
    """)

    # Test-period base rate for this tag (all qualified traders, NOT just pool)
    base_rate_row = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
        FROM maker_positions p
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
          AND CAST(p.resolved_at AS DATE) < '{test_end}'
          AND CAST(p.first_trade AS DATE) >= '{test_start}'
          AND p.volume > 0
    """).fetchone()
    test_base_rate = base_rate_row[0] if base_rate_row and base_rate_row[0] is not None else 0.0

    # Market-level aggregation (counting_unit rule): one row per condition_id
    # Use max(first_trade) as the signal entry time (last qualified trader to enter)
    # CRITICAL: first_trade >= test_start ensures only copyable signals
    markets_result = con.execute(f"""
    WITH pool_positions AS (
        SELECT
            p.condition_id,
            p.yes_won,
            p.resolved_at,
            p.first_trade
        FROM maker_positions p
        JOIN _wf_pool_{suffix} pool ON lower(p.trader) = pool.trader
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
          AND CAST(p.resolved_at AS DATE) < '{test_end}'
          AND CAST(p.first_trade AS DATE) >= '{test_start}'
          AND p.volume > 0
          AND p.net_usd IS NOT NULL
    ),
    market_agg AS (
        SELECT
            condition_id,
            first(yes_won) AS yes_won,
            count(DISTINCT (SELECT trader FROM maker_positions WHERE condition_id = pool_positions.condition_id LIMIT 1)) AS n_pool_traders,
            max(first_trade) AS last_entry,
            first(resolved_at) AS resolved_at,
            date_diff('day', max(first_trade), first(resolved_at)) AS hold_days
        FROM pool_positions
        GROUP BY condition_id
    )
    SELECT
        count(*) AS n_signals,
        avg(CAST(yes_won AS DOUBLE)) AS test_hr,
        median(hold_days) AS median_hold_days
    FROM market_agg
    WHERE hold_days >= 0
    """).fetchone()

    # Count active traders in test period
    active_traders_row = con.execute(f"""
        SELECT count(DISTINCT lower(p.trader)) AS n_active
        FROM maker_positions p
        JOIN _wf_pool_{suffix} pool ON lower(p.trader) = pool.trader
        JOIN _wf_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
          AND CAST(p.resolved_at AS DATE) < '{test_end}'
          AND CAST(p.first_trade AS DATE) >= '{test_start}'
          AND p.volume > 0
    """).fetchone()

    n_signals = markets_result[0] if markets_result else 0
    test_hr = markets_result[1] if markets_result and markets_result[0] > 0 else None
    median_hold_days = markets_result[2] if markets_result else None
    active_traders = active_traders_row[0] if active_traders_row else 0

    test_excess_hr = None
    if test_hr is not None and test_base_rate is not None:
        test_excess_hr = round(test_hr - test_base_rate, 4)

    return {
        "n_signals": int(n_signals),
        "active_traders": int(active_traders),
        "test_hr": round(test_hr, 4) if test_hr is not None else None,
        "test_excess_hr": test_excess_hr,
        "test_base_rate": round(test_base_rate, 4),
        "median_hold_days": int(median_hold_days) if median_hold_days is not None else None,
    }


def compute_spearman_rank_correlation(ranks_a: list[str], ranks_b: list[str]) -> float | None:
    """Compute Spearman rank correlation between two ranked trader lists.

    Both lists should be ordered by rank (index 0 = rank 1).
    Computes correlation between rank positions of traders in the intersection.
    """
    set_a = set(ranks_a)
    set_b = set(ranks_b)
    common = set_a & set_b
    if len(common) < 3:
        return None

    # Map trader -> rank in each list (1-indexed)
    rank_a = {t: i + 1 for i, t in enumerate(ranks_a)}
    rank_b = {t: i + 1 for i, t in enumerate(ranks_b)}

    # Compute Spearman (by formula: 1 - 6*sum(d^2) / (n*(n^2-1)))
    n = len(common)
    d_sq_sum = sum((rank_a[t] - rank_b[t]) ** 2 for t in common)
    rho = 1.0 - (6.0 * d_sq_sum) / (n * (n * n - 1)) if n > 1 else None
    return round(rho, 4) if rho is not None else None


def main() -> None:
    import os
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

    log("=" * 60)
    log("Walk-forward stability analysis starting")
    log(f"Tags: {TAGS}, K values: {K_VALUES}, Folds: {len(FOLDS)}")

    from research.db import db as get_db
    con = get_db().con

    t0 = time.time()
    ensure_base_tables(con)

    methods = ["hr_only", "composite", "edge_primary"]
    all_results: dict = {}

    # Store training pools per fold per tag per method for rank correlation
    # Structure: training_pools[fold][tag][method][k] = list of traders (ordered by rank)
    training_pools: dict = {}

    for fold in FOLDS:
        fold_id = fold["fold"]
        log(f"\n--- Fold {fold_id}: train {fold['train_start']} to {fold['train_end']}, "
            f"test {fold['test_start']} to {fold['test_end']} ---")
        training_pools[fold_id] = {}

        for tag in TAGS:
            log(f"  Tag: {tag}")
            training_pools[fold_id][tag] = {}

            for method in methods:
                log(f"    Method: {method}")
                training_pools[fold_id][tag][method] = {}

                # Build pool with max K (we'll slice for smaller K)
                max_k = max(K_VALUES)
                t_pool = time.time()
                pool_result = build_fold_pool(con, fold, tag, max_k, method)
                log(f"      Pool built: {len(pool_result['pool'])} traders, "
                    f"n_qualified={pool_result['n_qualified_train']}, "
                    f"base_rate={pool_result['train_base_rate']:.3f} "
                    f"({time.time() - t_pool:.1f}s)")

                # Store ordered pool for rank correlation
                training_pools[fold_id][tag][method]["all"] = pool_result["pool"]

                for k in K_VALUES:
                    pool_k = pool_result["pool"][:k]
                    training_pools[fold_id][tag][method][k] = pool_k

                    suffix = f"f{fold_id}_{tag.lower()}_{method}_k{k}"
                    t_oos = time.time()
                    oos = evaluate_oos(con, fold, tag, pool_k, suffix)
                    log(f"      K={k}: n_signals={oos['n_signals']}, "
                        f"active={oos['active_traders']}, "
                        f"test_hr={oos['test_hr']}, "
                        f"excess={oos['test_excess_hr']} "
                        f"({time.time() - t_oos:.1f}s)")

                    key = f"fold{fold_id}_{tag}_{method}_k{k}"
                    all_results[key] = {
                        "fold": fold_id,
                        "tag": tag,
                        "method": method,
                        "k": k,
                        "train_start": fold["train_start"],
                        "train_end": fold["train_end"],
                        "test_start": fold["test_start"],
                        "test_end": fold["test_end"],
                        "train_base_rate": pool_result["train_base_rate"],
                        "n_qualified_train": pool_result["n_qualified_train"],
                        "pool_size": len(pool_k),
                        **oos,
                    }

    log(f"\nTotal sweep time: {time.time() - t0:.1f}s")

    # -------------------------------------------------------------------
    # Stability metrics
    # -------------------------------------------------------------------
    log("\nComputing stability metrics...")
    stability_metrics: list[dict] = []

    for tag in TAGS:
        for method in methods:
            for k in K_VALUES:
                # HR variance across folds (OOS)
                hrs = [
                    all_results[f"fold{f['fold']}_{tag}_{method}_k{k}"]["test_hr"]
                    for f in FOLDS
                ]
                valid_hrs = [h for h in hrs if h is not None]
                hr_std = None
                if len(valid_hrs) >= 2:
                    mean_hr = sum(valid_hrs) / len(valid_hrs)
                    hr_std = round((sum((h - mean_hr) ** 2 for h in valid_hrs) / len(valid_hrs)) ** 0.5, 4)

                excess_hrs = [
                    all_results[f"fold{f['fold']}_{tag}_{method}_k{k}"]["test_excess_hr"]
                    for f in FOLDS
                ]
                valid_excess = [e for e in excess_hrs if e is not None]
                all_positive = all(e is not None and e > 0 for e in excess_hrs)

                signal_counts = [
                    all_results[f"fold{f['fold']}_{tag}_{method}_k{k}"]["n_signals"]
                    for f in FOLDS
                ]

                # Pool retention: fraction of fold1 pool retained in fold2
                # (uses the full ordered list up to K, not just top-K)
                retention_f1f2 = None
                retention_f2f3 = None
                if len(FOLDS) >= 2:
                    pool_f1 = set(training_pools[1][tag][method].get(k, []))
                    pool_f2 = set(training_pools[2][tag][method].get(k, []))
                    pool_f3 = set(training_pools[3][tag][method].get(k, []))

                    if pool_f1:
                        retention_f1f2 = round(len(pool_f1 & pool_f2) / len(pool_f1), 3)
                    if pool_f2:
                        retention_f2f3 = round(len(pool_f2 & pool_f3) / len(pool_f2), 3)

                # Spearman rank correlation across fold pairs
                spearman_f1f2 = None
                spearman_f2f3 = None
                if len(FOLDS) >= 2:
                    all_f1 = training_pools[1][tag][method].get("all", [])
                    all_f2 = training_pools[2][tag][method].get("all", [])
                    all_f3 = training_pools[3][tag][method].get("all", [])
                    # Limit to top-K for correlation
                    spearman_f1f2 = compute_spearman_rank_correlation(
                        all_f1[:k], all_f2[:k]
                    )
                    spearman_f2f3 = compute_spearman_rank_correlation(
                        all_f2[:k], all_f3[:k]
                    )

                stability_metrics.append({
                    "tag": tag,
                    "method": method,
                    "k": k,
                    "fold_hrs": hrs,
                    "fold_excess_hrs": excess_hrs,
                    "fold_signals": signal_counts,
                    "hr_std": hr_std,
                    "all_positive_excess": all_positive,
                    "pool_retention_f1f2": retention_f1f2,
                    "pool_retention_f2f3": retention_f2f3,
                    "spearman_f1f2": spearman_f1f2,
                    "spearman_f2f3": spearman_f2f3,
                    "avg_signals": round(sum(signal_counts) / len(signal_counts), 1),
                    "min_signals": min(signal_counts),
                })

    # -------------------------------------------------------------------
    # Save results
    # -------------------------------------------------------------------
    output = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "folds": FOLDS,
            "tags": TAGS,
            "k_values": K_VALUES,
            "methods": methods,
            "note": "ALL RESULTS ARE UPPER BOUNDS (vectorized, not tick-by-tick)",
        },
        "fold_results": all_results,
        "stability_metrics": stability_metrics,
    }

    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    log(f"Saved JSON: {OUT_JSON}")

    # -------------------------------------------------------------------
    # Markdown summary
    # -------------------------------------------------------------------
    write_markdown(stability_metrics, all_results, output["meta"])
    log(f"Saved Markdown: {OUT_MD}")
    log("DONE.")


def write_markdown(
    stability_metrics: list[dict],
    all_results: dict,
    meta: dict,
) -> None:
    lines = [
        "# Walk-Forward Stability Analysis: Edge-Weighted vs HR-Only",
        "",
        "> **ALL RESULTS ARE UPPER BOUNDS** (vectorized, not tick-by-tick). Expect 20-40pp degradation in tick validation.",
        "",
        f"Generated: {meta['generated_at']}",
        "",
        "## Fold Definitions",
        "| Fold | Train Start | Train End | Test Start | Test End |",
        "|------|-------------|-----------|------------|----------|",
    ]
    for f in meta["folds"]:
        lines.append(
            f"| {f['fold']} | {f['train_start']} | {f['train_end']} | "
            f"{f['test_start']} | {f['test_end']} |"
        )

    lines += [
        "",
        "## Scoring Methods",
        "1. **hr_only**: rank by `excess_hr` descending",
        "2. **composite**: `0.45*excess_hr + 0.25*consistency + 0.15*avg_edge + 0.15*bucket_excess`",
        "3. **edge_primary** (NEW): `0.45*bucket_excess + 0.25*consistency + 0.15*avg_edge + 0.15*excess_hr`",
        "",
        "All composite/edge_primary components are percentile-rank normalized to [0,1] within fold-tag.",
        "",
    ]

    folds = meta["folds"]
    methods = meta["methods"]
    k_values = meta["k_values"]

    for tag in meta["tags"]:
        lines.append(f"## Tag: {tag}")
        lines.append("")

        # OOS HR table
        lines.append("### OOS Hit Rate by Method × K × Fold")
        header = "| Method | K |"
        sep = "|--------|---|"
        for f in folds:
            header += f" F{f['fold']} HR | F{f['fold']} Excess | F{f['fold']} Signals |"
            sep += "--------|-----------|----------|"
        header += " HR σ | Retention F1→F2 | Retention F2→F3 | Spearman F1-F2 | Spearman F2-F3 |"
        sep += "------|-----------------|-----------------|----------------|----------------|"
        lines.append(header)
        lines.append(sep)

        for method in methods:
            for k in k_values:
                row = f"| {method} | {k} |"
                for f in folds:
                    key = f"fold{f['fold']}_{tag}_{method}_k{k}"
                    r = all_results.get(key, {})
                    hr = f"{r.get('test_hr', None):.3f}" if r.get("test_hr") is not None else "N/A"
                    ex = f"{r.get('test_excess_hr', None):+.3f}" if r.get("test_excess_hr") is not None else "N/A"
                    sigs = r.get("n_signals", 0)
                    row += f" {hr} | {ex} | {sigs} |"

                # Stability metrics
                sm = next(
                    (s for s in stability_metrics
                     if s["tag"] == tag and s["method"] == method and s["k"] == k),
                    {}
                )
                hr_std = f"{sm.get('hr_std', None):.3f}" if sm.get("hr_std") is not None else "N/A"
                ret12 = f"{sm.get('pool_retention_f1f2', None):.2f}" if sm.get("pool_retention_f1f2") is not None else "N/A"
                ret23 = f"{sm.get('pool_retention_f2f3', None):.2f}" if sm.get("pool_retention_f2f3") is not None else "N/A"
                sp12 = f"{sm.get('spearman_f1f2', None):.3f}" if sm.get("spearman_f1f2") is not None else "N/A"
                sp23 = f"{sm.get('spearman_f2f3', None):.3f}" if sm.get("spearman_f2f3") is not None else "N/A"
                row += f" {hr_std} | {ret12} | {ret23} | {sp12} | {sp23} |"
                lines.append(row)

        lines.append("")

    # -------------------------------------------------------------------
    # Stability summary table (best method per tag×K by combined score)
    # -------------------------------------------------------------------
    lines += [
        "## Stability Summary: Best Method per Tag × K",
        "",
        "Ranking criteria: lowest HR σ + highest retention + highest Spearman.",
        "",
        "| Tag | K | Best Method | HR σ | Avg Excess HR | Retention F1→F2 | Spearman F1-F2 | All Positive Excess |",
        "|-----|---|-------------|------|---------------|-----------------|----------------|---------------------|",
    ]

    for tag in meta["tags"]:
        for k in k_values:
            candidates = [
                s for s in stability_metrics
                if s["tag"] == tag and s["k"] == k and s.get("hr_std") is not None
            ]
            if not candidates:
                continue
            # Score: lower HR σ is better, higher retention is better
            def stability_score(s: dict) -> float:
                sigma_score = -(s.get("hr_std") or 0.5)  # lower is better
                retain_score = (s.get("pool_retention_f1f2") or 0.0) * 0.3
                spearman_score = (s.get("spearman_f1f2") or 0.0) * 0.2
                return sigma_score + retain_score + spearman_score

            best = max(candidates, key=stability_score)
            avg_excess = None
            valid = [e for e in best["fold_excess_hrs"] if e is not None]
            if valid:
                avg_excess = round(sum(valid) / len(valid), 4)

            lines.append(
                f"| {tag} | {k} | {best['method']} | "
                f"{best.get('hr_std', 'N/A')} | "
                f"{avg_excess if avg_excess is not None else 'N/A'} | "
                f"{best.get('pool_retention_f1f2', 'N/A')} | "
                f"{best.get('spearman_f1f2', 'N/A')} | "
                f"{best.get('all_positive_excess', 'N/A')} |"
            )

    lines += [
        "",
        "## Key Findings",
        "",
        "_(auto-generated analysis — verify manually)_",
        "",
    ]

    # Auto-generate key findings
    for tag in meta["tags"]:
        for k in k_values:
            candidates = {
                method: next(
                    (s for s in stability_metrics if s["tag"] == tag and s["method"] == method and s["k"] == k),
                    None
                )
                for method in methods
            }
            composite_sm = candidates.get("composite")
            edge_sm = candidates.get("edge_primary")
            hr_sm = candidates.get("hr_only")

            if composite_sm and edge_sm and hr_sm:
                # Compare HR std
                stds = {m: candidates[m].get("hr_std") for m in methods if candidates[m]}
                best_m = min((m for m in stds if stds[m] is not None), key=lambda m: stds[m] or 9999, default=None)
                lines.append(
                    f"- **{tag} K={k}**: Best stability = {best_m} "
                    f"(σ={stds.get(best_m, 'N/A')}). "
                    f"HR-only σ={stds.get('hr_only', 'N/A')}, "
                    f"composite σ={stds.get('composite', 'N/A')}, "
                    f"edge_primary σ={stds.get('edge_primary', 'N/A')}."
                )

    lines.append("")
    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
