"""Tag scanner: find all tags viable for the composite scorecard methodology.

Scans ALL tags in the Polymarket dataset and identifies which ones have:
1. Enough resolved markets in the test period (>=50 resolved, test: 2025-07-01+)
2. A viable composite pool (top-K=25 trained before 2025-07-01)
3. Consensus signal (N=2 distinct pool traders agree)

Ranks by: excess_hr * sqrt(n_signals) — balancing edge with volume.

Results are VECTORIZED UPPER BOUNDS. Real tick-validated numbers will be 20-40pp lower.
Only tags with vectorized excess_hr >= 30pp are recommended (expecting ~10pp+ after degradation).

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/tag_scan.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

TRAIN_CUTOFF = "2025-07-01"
TEST_START = "2025-07-01"
MIN_TEST_MARKETS = 50
TOP_K = 25
N_CONSENSUS = 2

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/tag_scan.log")
RESULTS_PATH = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/"
    "scorecard-v2-strategies/validation/tag_scan_results.md"
)


def log(msg: str) -> None:
    print(msg, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(msg + "\n")


def get_db():
    from research.db import db as get_db_fn
    return get_db_fn().con


GAMBLING_MACRO_SQL = """
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
"""

MARKET_TAGS_SQL = """
CREATE OR REPLACE TABLE _ts_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag,
    first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
"""


def setup_shared_tables(con) -> None:
    log("Setting up shared tables...")
    con.execute(GAMBLING_MACRO_SQL)
    con.execute(MARKET_TAGS_SQL)
    log("  Done.")


def get_all_tags_overview(con) -> list[dict]:
    """Get all tags with market/trader stats split by train/test windows."""
    log("Fetching all-tags overview...")
    rows = con.execute(f"""
    WITH test_markets AS (
        -- Markets resolved in test period, with primary tag
        SELECT
            mt.primary_tag AS tag,
            count(DISTINCT p.condition_id) AS n_test_markets,
            avg(CAST(p.yes_won AS DOUBLE)) AS test_yes_hr
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND p.volume > 0
        GROUP BY mt.primary_tag
    ),
    train_markets AS (
        -- Markets in training window
        SELECT
            mt.primary_tag AS tag,
            count(DISTINCT p.condition_id) AS n_train_markets,
            count(DISTINCT p.trader) AS n_train_traders,
            avg(CAST(p.yes_won AS DOUBLE)) AS train_yes_hr
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
          AND p.volume > 0
        GROUP BY mt.primary_tag
    )
    SELECT
        COALESCE(tm.tag, tr.tag) AS tag,
        COALESCE(tr.n_train_markets, 0) AS n_train_markets,
        COALESCE(tr.n_train_traders, 0) AS n_train_traders,
        COALESCE(tr.train_yes_hr, 0.0) AS train_base_rate,
        COALESCE(tm.n_test_markets, 0) AS n_test_markets,
        COALESCE(tm.test_yes_hr, 0.0) AS test_base_rate
    FROM test_markets tm
    FULL OUTER JOIN train_markets tr ON tm.tag = tr.tag
    ORDER BY n_test_markets DESC
    """).fetchall()

    result = []
    for row in rows:
        result.append({
            "tag": row[0],
            "n_train_markets": row[1],
            "n_train_traders": row[2],
            "train_base_rate": round(row[3], 4),
            "n_test_markets": row[4],
            "test_base_rate": round(row[5], 4),
        })
    log(f"  Found {len(result)} tags total.")
    return result


def build_composite_pool_for_tag(con, tag: str, k: int = TOP_K) -> set[str]:
    """Build composite pool for a given tag using training data only.

    Returns top-K traders by composite score (percentile-normalized blend).
    """
    safe_tag = tag.lower().replace(" ", "_").replace("/", "_").replace("-", "_")

    # Step 1: Base training stats with conviction filter
    con.execute(f"""
    CREATE OR REPLACE TABLE _ts_train_{safe_tag} AS
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
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
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
            avg(b.abs_net_usd) AS avg_pos_size,
            avg(b.conviction) AS avg_conviction,
            median(b.net_usd) AS avg_edge_usd
        FROM base b
        GROUP BY b.trader
        HAVING count(DISTINCT b.condition_id) >= 10
           AND avg(CASE WHEN b.volume > 0 THEN abs(b.net_usd) / b.volume ELSE 0 END) >= 0.90
           AND count(*) < 10000
    )
    SELECT
        ts.trader,
        ts.n_markets,
        ts.hit_rate,
        ts.avg_pos_size,
        ts.avg_conviction,
        ts.avg_edge_usd,
        (ts.hit_rate - tb.base_rate) AS excess_hr,
        tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0;
    """)

    pool_size = con.execute(f"SELECT count(*) FROM _ts_train_{safe_tag}").fetchone()[0]
    if pool_size < k:
        # Not enough qualified traders — return empty or all available
        if pool_size == 0:
            return set()
        # Return all traders with positive excess_hr (no cutoff)
        rows = con.execute(f"SELECT trader FROM _ts_train_{safe_tag}").fetchall()
        return {r[0].lower() for r in rows}

    # Step 2: Consistency Sharpe (monthly HR)
    con.execute(f"""
    CREATE OR REPLACE TABLE _ts_consistency_{safe_tag} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _ts_train_{safe_tag} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 3
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
        HAVING count(*) >= 4
    )
    SELECT trader, n_months, consistency_sharpe FROM sharpe;
    """)

    # Step 3: Bucket excess HR
    con.execute(f"""
    CREATE OR REPLACE TABLE _ts_bucket_{safe_tag} AS
    WITH price_data AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _ts_train_{safe_tag} t ON p.trader = t.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
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

    # Step 4: Composite score with percentile normalization
    con.execute(f"""
    CREATE OR REPLACE TABLE _ts_composite_{safe_tag} AS
    WITH base AS (
        SELECT
            t.trader,
            t.excess_hr,
            t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _ts_train_{safe_tag} t
        LEFT JOIN _ts_consistency_{safe_tag} c ON t.trader = c.trader
        LEFT JOIN _ts_bucket_{safe_tag} b ON t.trader = b.trader
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
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) AS composite_score,
        ROW_NUMBER() OVER (
            ORDER BY (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) DESC
        ) AS rank
    FROM ranked;
    """)

    rows = con.execute(f"""
        SELECT trader FROM _ts_composite_{safe_tag}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


def run_vectorized_backtest(con, tag: str, pool: set[str], n_consensus: int = N_CONSENSUS) -> dict:
    """Run vectorized backtest: N-trader consensus in test period, YES positions only.

    Returns metrics computed at MARKET level (not trader level).
    """
    if not pool:
        return {
            "n_signals": 0, "hr": 0.0, "excess_hr": 0.0,
            "median_hold_days": 0.0, "avg_edge_usd": 0.0, "compounding_score": 0.0,
        }

    # Create pool table
    pool_values = ", ".join(f"('{t}')" for t in pool)
    con.execute(f"""
    CREATE OR REPLACE TABLE _ts_pool_{tag.lower().replace(' ', '_').replace('/', '_').replace('-', '_')} AS
    SELECT lower(trader) AS trader FROM (VALUES {pool_values}) t(trader);
    """)

    safe_tag = tag.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    pool_table = f"_ts_pool_{safe_tag}"

    # Get tag base rate in test period
    base_rate_row = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND p.volume > 0
    """).fetchone()
    base_rate = base_rate_row[0] if base_rate_row[0] is not None else 0.5

    # Vectorized consensus: markets where >= N pool traders entered YES in test window
    # Aggregating to MARKET level (not trader level) per the counting unit rule.
    # signal_entry = max(first_trade) across consensus traders = time when Nth trader entered
    result = con.execute(f"""
    WITH consensus_markets AS (
        SELECT
            p.condition_id,
            count(DISTINCT p.trader) AS n_pool_traders,
            max(CAST(p.first_trade AS TIMESTAMP)) AS signal_entry,
            first(CAST(p.yes_won AS INTEGER)) AS market_correct,
            first(p.net_usd) AS sample_net_usd,
            first(CAST(p.resolved_at AS TIMESTAMP)) AS resolved_at
        FROM maker_positions p
        JOIN _ts_market_tags mt ON p.condition_id = mt.condition_id
        JOIN {pool_table} pool ON lower(p.trader) = pool.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND p.volume > 0
        GROUP BY p.condition_id
        HAVING count(DISTINCT p.trader) >= {n_consensus}
    )
    SELECT
        count(*) AS n_signals,
        avg(CAST(market_correct AS DOUBLE)) AS hr,
        median(
            CAST(date_diff('day', signal_entry, resolved_at) AS DOUBLE)
        ) AS median_hold_days,
        median(abs(sample_net_usd)) AS avg_edge_usd
    FROM consensus_markets
    """).fetchone()

    n_signals = result[0] if result[0] else 0
    hr = result[1] if result[1] is not None else 0.0
    median_hold_days = result[2] if result[2] is not None else 1.0
    avg_edge_usd = result[3] if result[3] is not None else 0.0

    excess_hr = hr - base_rate
    hold_days = max(median_hold_days, 0.5)  # avoid divide-by-zero
    compounding_score = (excess_hr * avg_edge_usd / hold_days) if n_signals > 0 else 0.0

    return {
        "n_signals": n_signals,
        "hr": round(hr, 4),
        "base_rate": round(base_rate, 4),
        "excess_hr": round(excess_hr, 4),
        "median_hold_days": round(median_hold_days, 2),
        "avg_edge_usd": round(avg_edge_usd, 4),
        "compounding_score": round(compounding_score, 4),
    }


def rank_score(excess_hr: float, n_signals: int) -> float:
    """Rank score: excess_hr * sqrt(n_signals) — balances edge and volume."""
    import math
    return excess_hr * math.sqrt(max(n_signals, 0))


def scan_all_tags(con) -> list[dict]:
    """Main scan: all tags with >= MIN_TEST_MARKETS markets in test window."""
    setup_shared_tables(con)
    overview = get_all_tags_overview(con)

    # Filter to tags with enough test-period data and training data
    viable_tags = [
        t for t in overview
        if t["n_test_markets"] >= MIN_TEST_MARKETS and t["n_train_markets"] >= 100
    ]
    log(f"\nViable tags for scan (>={MIN_TEST_MARKETS} test markets, >=100 train markets): "
        f"{len(viable_tags)}")
    for t in viable_tags:
        log(f"  {t['tag']:30s} train={t['n_train_markets']:5d} test={t['n_test_markets']:5d} "
            f"base_rate={t['train_base_rate']:.3f}")

    results = []
    for i, tag_info in enumerate(viable_tags):
        tag = tag_info["tag"]
        log(f"\n[{i+1}/{len(viable_tags)}] Processing: {tag}")
        t0 = time.time()

        try:
            # Build composite pool (train window only)
            pool = build_composite_pool_for_tag(con, tag, k=TOP_K)
            log(f"  Pool size: {len(pool)} traders")

            if len(pool) == 0:
                log(f"  SKIP: no qualified traders")
                results.append({
                    **tag_info,
                    "pool_size": 0,
                    "n_signals": 0,
                    "hr": 0.0,
                    "base_rate": tag_info["test_base_rate"],
                    "excess_hr": 0.0,
                    "median_hold_days": 0.0,
                    "avg_edge_usd": 0.0,
                    "compounding_score": 0.0,
                    "rank_score": 0.0,
                    "skip_reason": "no_qualified_traders",
                })
                continue

            # Run vectorized backtest
            metrics = run_vectorized_backtest(con, tag, pool, n_consensus=N_CONSENSUS)

            elapsed = time.time() - t0
            rank = rank_score(metrics["excess_hr"], metrics["n_signals"])

            log(f"  n_signals={metrics['n_signals']} hr={metrics['hr']:.3f} "
                f"base={metrics['base_rate']:.3f} excess={metrics['excess_hr']:+.3f} "
                f"hold={metrics['median_hold_days']:.1f}d "
                f"rank_score={rank:.3f} ({elapsed:.1f}s)")

            results.append({
                **tag_info,
                "pool_size": len(pool),
                **metrics,
                "rank_score": round(rank, 4),
                "skip_reason": None,
            })

        except Exception as e:
            log(f"  ERROR: {e}")
            results.append({
                **tag_info,
                "pool_size": 0,
                "n_signals": 0,
                "hr": 0.0,
                "base_rate": tag_info.get("test_base_rate", 0.0),
                "excess_hr": 0.0,
                "median_hold_days": 0.0,
                "avg_edge_usd": 0.0,
                "compounding_score": 0.0,
                "rank_score": 0.0,
                "skip_reason": f"error: {e}",
            })

    return results


def write_results_md(results: list[dict], all_overview: list[dict]) -> None:
    """Write results to markdown file."""
    import math

    # Sort by rank_score descending
    ranked = sorted(results, key=lambda r: r["rank_score"], reverse=True)

    # Separate viable vs not
    viable = [r for r in ranked if r["excess_hr"] >= 0.30 and r["n_signals"] >= 5]
    marginal = [r for r in ranked if 0.10 <= r["excess_hr"] < 0.30 and r["n_signals"] >= 5]
    weak = [r for r in ranked if r not in viable and r not in marginal]

    lines = [
        "# Tag Scan Results — All Polymarket Tags",
        "",
        f"**Date**: 2026-03-07",
        f"**Method**: Vectorized UPPER BOUNDS (expect 20-40pp tick degradation)",
        f"**Train cutoff**: {TRAIN_CUTOFF} | **Test start**: {TEST_START}",
        f"**Pool**: Top-K={TOP_K} composite score | **Consensus**: N={N_CONSENSUS}",
        f"**Filter**: ≥{MIN_TEST_MARKETS} test markets, ≥100 train markets",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- Total tags scanned: {len(results)}",
        f"- VIABLE (excess_hr ≥ 30pp, n_signals ≥ 5): **{len(viable)}**",
        f"- MARGINAL (10pp ≤ excess_hr < 30pp): {len(marginal)}",
        f"- WEAK / SKIP: {len(weak)}",
        "",
        "> [!CRITICAL]",
        "> These are VECTORIZED upper bounds. Real tick-validated results will be 20-40pp lower.",
        "> Only tags with excess_hr ≥ 30pp (vectorized) are expected to yield ≥10pp after tick validation.",
        "> Known validated: Sports +39.8pp, Politics +41pp, Crypto +37.4pp.",
        "",
        "---",
        "",
        "## VIABLE Tags (excess_hr >= 30pp, vectorized UB)",
        "",
        "| Rank | Tag | Test Markets | Pool | Signals | HR | Base | Excess | Hold (d) | Rank Score |",
        "|------|-----|-------------|------|---------|-----|------|--------|----------|------------|",
    ]

    for i, r in enumerate(viable, 1):
        lines.append(
            f"| {i} | **{r['tag']}** | {r['n_test_markets']} | {r['pool_size']} | "
            f"{r['n_signals']} | {r['hr']:.1%} | {r['base_rate']:.1%} | "
            f"**+{r['excess_hr']:.1%}** | {r['median_hold_days']:.1f} | {r['rank_score']:.2f} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## MARGINAL Tags (10pp ≤ excess_hr < 30pp)",
        "",
        "| Rank | Tag | Test Markets | Pool | Signals | HR | Base | Excess | Hold (d) |",
        "|------|-----|-------------|------|---------|-----|------|--------|----------|",
    ])

    for i, r in enumerate(marginal, 1):
        lines.append(
            f"| {i} | {r['tag']} | {r['n_test_markets']} | {r['pool_size']} | "
            f"{r['n_signals']} | {r['hr']:.1%} | {r['base_rate']:.1%} | "
            f"+{r['excess_hr']:.1%} | {r['median_hold_days']:.1f} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Full Tag Universe Overview",
        "",
        "All tags with ≥50 test markets, sorted by test market count:",
        "",
        "| Tag | Train Mkts | Train Traders | Train Base | Test Mkts | Test Base | n_signals | Excess HR | Status |",
        "|-----|-----------|---------------|-----------|----------|----------|----------|-----------|--------|",
    ])

    # Full results sorted by test market count
    by_test = sorted(results, key=lambda r: r["n_test_markets"], reverse=True)
    for r in by_test:
        if r["excess_hr"] >= 0.30 and r["n_signals"] >= 5:
            status = "VIABLE"
        elif 0.10 <= r["excess_hr"] < 0.30 and r["n_signals"] >= 5:
            status = "marginal"
        elif r.get("skip_reason"):
            status = f"skip ({r['skip_reason'][:20]})"
        else:
            status = "weak"
        lines.append(
            f"| {r['tag']} | {r['n_train_markets']} | {r['n_train_traders']} | "
            f"{r['train_base_rate']:.3f} | {r['n_test_markets']} | {r['test_base_rate']:.3f} | "
            f"{r['n_signals']} | {r['excess_hr']:+.3f} | {status} |"
        )

    # All-tags overview (including those below threshold)
    lines.extend([
        "",
        "---",
        "",
        "## All Tags Universe (including below threshold)",
        "",
        "| Tag | Train Mkts | Test Mkts | Test Base HR |",
        "|-----|-----------|----------|--------------|",
    ])

    all_sorted = sorted(all_overview, key=lambda r: r["n_test_markets"], reverse=True)
    for r in all_sorted[:80]:  # top 80 by test markets
        lines.append(
            f"| {r['tag']} | {r['n_train_markets']} | {r['n_test_markets']} | "
            f"{r['test_base_rate']:.3f} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Recommendations",
        "",
    ])

    if viable:
        lines.extend([
            f"### Top candidates for tick validation (beyond Sports/Politics/Crypto)",
            "",
        ])
        # Exclude already validated
        already_done = {"Sports", "Politics", "Crypto"}
        new_viable = [r for r in viable if r["tag"] not in already_done]
        if new_viable:
            for r in new_viable[:5]:
                signals_per_month = r["n_signals"] / 8  # ~8 months test window
                lines.extend([
                    f"**{r['tag']}**: {r['n_signals']} signals (~{signals_per_month:.0f}/month), "
                    f"+{r['excess_hr']:.1%} excess HR (vectorized UB), "
                    f"{r['median_hold_days']:.1f}d median hold",
                    "",
                ])
        else:
            lines.extend([
                "No new viable tags found beyond the already-validated Sports/Politics/Crypto.",
                "",
            ])

    lines.extend([
        "",
        "---",
        "",
        "## Methodology Notes",
        "",
        "- **Pool building**: Top-K=25 by composite score (0.45*excess_hr + 0.25*consistency_sharpe + 0.15*avg_edge_usd + 0.15*bucket_excess_hr)",
        "- **Min trader qualifications**: ≥10 markets, conviction ≥0.90, <10000 trades (bot filter)",
        "- **Signal**: N=2 distinct pool traders enter YES in the same market during test period",
        "- **Hold time**: date_diff(day, max(first_trade), resolved_at) — market level, not trader level",
        "- **Counting**: MARKET level (distinct condition_ids), not trader-position level",
        "- **Rank score**: excess_hr × sqrt(n_signals) — balances edge with volume",
        "- **Vectorized bias**: ~20-40pp excess HR will be lost in tick-by-tick validation",
        "- **Tick validation threshold**: Only recommend if excess_hr ≥ 30pp (vectorized)",
    ])

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines) + "\n")
    log(f"\nResults written to: {RESULTS_PATH}")


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("")  # clear log

    log("=" * 70)
    log("TAG SCAN — All Polymarket Tags")
    log(f"Train cutoff: {TRAIN_CUTOFF} | Test start: {TEST_START}")
    log(f"Pool K={TOP_K} | Consensus N={N_CONSENSUS} | Min test markets: {MIN_TEST_MARKETS}")
    log("=" * 70)

    t_total = time.time()
    con = get_db()

    # First get all-tags overview (for the full universe table)
    setup_shared_tables(con)
    all_overview = get_all_tags_overview(con)

    # Run full scan
    results = scan_all_tags(con)

    elapsed_total = time.time() - t_total
    log(f"\nTotal scan time: {elapsed_total:.1f}s")

    # Save raw results as JSON for debugging
    json_path = RESULTS_PATH.parent / "tag_scan_results.json"
    json_path.write_text(json.dumps(results, indent=2))
    log(f"Raw results: {json_path}")

    # Write markdown report
    write_results_md(results, all_overview)

    # Print summary to stdout
    viable = [r for r in results if r["excess_hr"] >= 0.30 and r["n_signals"] >= 5]
    viable_sorted = sorted(viable, key=lambda r: r["rank_score"], reverse=True)

    print("\n" + "=" * 70)
    print("VIABLE TAGS (excess_hr >= 30pp, n_signals >= 5):")
    print("=" * 70)
    for r in viable_sorted:
        print(f"  {r['tag']:30s} signals={r['n_signals']:4d} "
              f"HR={r['hr']:.1%} base={r['base_rate']:.1%} "
              f"excess={r['excess_hr']:+.1%} hold={r['median_hold_days']:.1f}d "
              f"rank={r['rank_score']:.2f}")


if __name__ == "__main__":
    main()
