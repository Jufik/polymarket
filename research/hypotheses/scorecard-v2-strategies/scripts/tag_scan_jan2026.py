"""Tag scanner for January 2026 test window.

Extended scan with:
  - Train cutoff: 2026-01-01 (6 more months vs previous July 2025 scan)
  - Test period: January 2026 (resolved 2026-01-01 to 2026-02-01)
  - Thresholds: >= 30 resolved test markets AND >= 50 training markets
  - Pool: Top-K=25 composite score
  - Consensus: N=2 distinct pool traders

Key improvements vs previous scan:
  - More training data for tags that grew in H2 2025 (Esports, Entertainment, Music, etc.)
  - Tighter test window (1 month) gives cleaner out-of-sample test
  - Lower threshold (30 vs 50) for test markets to catch smaller but viable tags

Results are VECTORIZED UPPER BOUNDS — expect 20-40pp degradation in tick validation.
Only tags with vectorized excess_hr >= 25pp AND n_signals >= 10 proceed to tick validation.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/tag_scan_jan2026.py
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

TRAIN_CUTOFF = "2026-01-01"
TEST_START = "2026-01-01"
TEST_END = "2026-02-01"
MIN_TEST_MARKETS = 30
MIN_TRAIN_MARKETS = 50
TOP_K = 25
N_CONSENSUS = 2
TICK_VALIDATE_EXCESS_HR = 0.25  # 25pp vectorized threshold for tick validation
TICK_VALIDATE_MIN_SIGNALS = 10  # min signals for tick validation

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/tag_scan_jan2026.log")
RESULTS_JSON_PATH = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/"
    "scorecard-v2-strategies/validation/tag_scan_jan2026_results.json"
)
RESULTS_MD_PATH = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/"
    "scorecard-v2-strategies/validation/tag_scan_jan2026_results.md"
)


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def get_db():
    from research.db import db as get_db_fn
    return get_db_fn().con


# ── Shared setup ─────────────────────────────────────────────────────────────

GAMBLING_MACRO_SQL = """
CREATE OR REPLACE MACRO is_gambling_market_jan(slug) AS (
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
CREATE OR REPLACE TABLE _tsj_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag,
    first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market_jan(m.slug)
GROUP BY m.condition_id, m.slug;
"""


def setup_shared_tables(con) -> None:
    log("Setting up shared tables...")
    con.execute(GAMBLING_MACRO_SQL)
    con.execute(MARKET_TAGS_SQL)
    log("  Done.")


# ── Tag overview ──────────────────────────────────────────────────────────────

def get_all_tags_overview(con) -> list[dict]:
    """Get all tags with market/trader stats split by train/test windows."""
    log(f"Fetching all-tags overview (train<{TRAIN_CUTOFF}, test={TEST_START}..{TEST_END})...")
    rows = con.execute(f"""
    WITH test_markets AS (
        SELECT
            mt.primary_tag AS tag,
            count(DISTINCT p.condition_id) AS n_test_markets,
            avg(CAST(p.yes_won AS DOUBLE)) AS test_yes_hr
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        WHERE p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND p.volume > 0
        GROUP BY mt.primary_tag
    ),
    train_markets AS (
        SELECT
            mt.primary_tag AS tag,
            count(DISTINCT p.condition_id) AS n_train_markets,
            count(DISTINCT p.trader) AS n_train_traders,
            avg(CAST(p.yes_won AS DOUBLE)) AS train_yes_hr
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
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


# ── Pool building ─────────────────────────────────────────────────────────────

def build_composite_pool_for_tag(con, tag: str, k: int = TOP_K) -> set[str]:
    """Build composite pool for a given tag using training data only.

    Composite weights: 0.45 * excess_hr + 0.25 * consistency_sharpe
                     + 0.15 * avg_edge_usd + 0.15 * bucket_excess_hr

    Min qualifications: >= 10 markets in training, conviction >= 0.90, < 10000 trades.
    Returns top-K traders by composite score.
    """
    safe_tag = tag.lower().replace(" ", "_").replace("/", "_").replace("-", "_")

    # Step 1: Base training stats
    con.execute(f"""
    CREATE OR REPLACE TABLE _tsj_train_{safe_tag} AS
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
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
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

    pool_size = con.execute(f"SELECT count(*) FROM _tsj_train_{safe_tag}").fetchone()[0]
    if pool_size == 0:
        return set()

    # If fewer traders than k, return all (no cutoff)
    if pool_size < k:
        rows = con.execute(f"SELECT trader FROM _tsj_train_{safe_tag}").fetchall()
        return {r[0].lower() for r in rows}

    # Step 2: Consistency Sharpe (monthly HR)
    con.execute(f"""
    CREATE OR REPLACE TABLE _tsj_consistency_{safe_tag} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg(CAST(p.yes_won AS DOUBLE)) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        JOIN _tsj_train_{safe_tag} t ON p.trader = t.trader
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

    # Step 3: Bucket excess HR (per 10pp price bucket)
    con.execute(f"""
    CREATE OR REPLACE TABLE _tsj_bucket_{safe_tag} AS
    WITH price_data AS (
        SELECT
            p.trader,
            p.condition_id,
            CAST(p.yes_won AS DOUBLE) AS correct,
            CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _tsj_train_{safe_tag} t ON p.trader = t.trader
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
    CREATE OR REPLACE TABLE _tsj_composite_{safe_tag} AS
    WITH base AS (
        SELECT
            t.trader,
            t.excess_hr,
            t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _tsj_train_{safe_tag} t
        LEFT JOIN _tsj_consistency_{safe_tag} c ON t.trader = c.trader
        LEFT JOIN _tsj_bucket_{safe_tag} b ON t.trader = b.trader
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
        SELECT trader FROM _tsj_composite_{safe_tag}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


# ── Vectorized backtest ───────────────────────────────────────────────────────

def run_vectorized_backtest(con, tag: str, pool: set[str], n_consensus: int = N_CONSENSUS) -> dict:
    """Run vectorized backtest at MARKET level for January 2026 test window.

    Signal = N distinct pool traders entered YES in same market during test period.
    signal_entry = max(first_trade) = time Nth trader entered (consensus trigger).
    Aggregated at market level (not trader level) per vectorized_counting_unit rule.
    """
    if not pool:
        return {
            "n_signals": 0, "hr": 0.0, "base_rate": 0.5,
            "excess_hr": 0.0, "median_hold_days": 0.0,
            "avg_edge_usd": 0.0, "compounding_score": 0.0,
        }

    safe_tag = tag.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    pool_table = f"_tsj_pool_{safe_tag}"

    # Insert pool traders into temp table
    pool_values = ", ".join(f"('{t}')" for t in pool)
    con.execute(f"""
    CREATE OR REPLACE TABLE {pool_table} AS
    SELECT lower(trader) AS trader FROM (VALUES {pool_values}) t(trader);
    """)

    # Get tag base rate in test period (January 2026)
    base_rate_row = con.execute(f"""
        SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND CAST(p.first_trade AS DATE) >= '{TEST_START}'
          AND p.volume > 0
    """).fetchone()
    base_rate = base_rate_row[0] if base_rate_row[0] is not None else 0.5

    # MARKET-level consensus aggregation (vectorized counting unit rule):
    # - n_pool_traders = count(DISTINCT trader) per market (not count(*))
    # - signal_entry = max(first_trade) = when Nth trader entered
    # - market_correct = single YES/NO outcome per market
    result = con.execute(f"""
    WITH consensus_markets AS (
        SELECT
            p.condition_id,
            count(DISTINCT p.trader) AS n_pool_traders,
            max(CAST(p.first_trade AS TIMESTAMP)) AS signal_entry,
            first(CAST(p.yes_won AS INTEGER)) AS market_correct,
            first(CAST(p.resolved_at AS TIMESTAMP)) AS resolved_at,
            median(abs(p.net_usd)) AS sample_net_usd
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        JOIN {pool_table} pool ON lower(p.trader) = pool.trader
        WHERE mt.primary_tag = '{tag}'
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
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
        median(sample_net_usd) AS avg_edge_usd
    FROM consensus_markets
    """).fetchone()

    n_signals = result[0] if result[0] else 0
    hr = result[1] if result[1] is not None else 0.0
    median_hold_days = result[2] if result[2] is not None else 1.0
    avg_edge_usd = result[3] if result[3] is not None else 0.0

    excess_hr = hr - base_rate
    hold_days = max(median_hold_days, 0.5)
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
    return excess_hr * math.sqrt(max(n_signals, 0))


# ── Tick-by-tick validation ───────────────────────────────────────────────────

def run_tick_validation(
    con, tag: str, pool: set[str], metrics: dict, tag_info: dict
) -> dict:
    """Run full tick-by-tick validation for a tag via SyncReplayRunner.

    Uses January 2026 universe only (resolved in test window).
    Returns tick-validated summary metrics.
    """
    log(f"  [TICK] Running tick-by-tick validation for {tag}...")
    t0 = time.time()

    from research.strategies.consensus_v2 import TokenMapStrategy
    from research.fast_replay import load_replay_resolutions
    from research.harness import run_fast_backtest
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    # Get January 2026 test markets for this tag
    tag_markets_rows = con.execute(f"""
        SELECT DISTINCT p.condition_id
        FROM maker_positions p
        JOIN _tsj_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
    """).fetchall()
    tag_markets = {r[0] for r in tag_markets_rows}

    gambling_rows = con.execute("""
        SELECT condition_id FROM markets WHERE is_gambling_market_jan(slug)
    """).fetchall()
    gambling_markets = {r[0] for r in gambling_rows}

    log(f"    tag_markets={len(tag_markets)}, gambling_excl={len(gambling_markets)}")

    # Load resolutions and token_map
    resolutions, token_map_raw = load_replay_resolutions()
    # Fix: load_replay_resolutions returns mixed-case keys ("Yes"/"No") — need uppercase
    token_map = {
        cid: {k.upper(): v for k, v in outcomes.items()}
        for cid, outcomes in token_map_raw.items()
    }
    log(f"    token_map loaded: {len(token_map)} markets")

    safe_name = tag.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    strategy = TokenMapStrategy(
        name=f"jan2026_{safe_name}_k{TOP_K}_n{N_CONSENSUS}",
        pool=pool,
        tag_markets=tag_markets,
        gambling_markets=gambling_markets,
        n_threshold=N_CONSENSUS,
        token_map=token_map,
        direction_filter="YES",
        size_usd=100.0,
    )

    config = StrategyConfig(
        name=f"jan2026_{safe_name}",
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=5000.0,
        max_position_usd=100.0,
        max_open_positions=50,
        cooldown_s=0,
    )

    try:
        result, summary = run_fast_backtest(
            strategy,
            config,
            universe=tag_markets,
        )
        elapsed = time.time() - t0

        if summary is None:
            log(f"    No settled positions after {elapsed:.1f}s")
            return {
                "tick_fills": result.total_fills,
                "tick_hr": None,
                "tick_excess_hr": None,
                "tick_pnl": None,
                "tick_sharpe": None,
                "tick_elapsed_s": round(elapsed, 1),
                "tick_error": "no_settled_positions",
            }

        base_rate = tag_info["test_base_rate"]
        tick_excess_hr = summary.hit_rate - base_rate
        log(f"    fills={result.total_fills}, HR={summary.hit_rate:.1%}, "
            f"excess={tick_excess_hr:+.1%}, PnL=${summary.total_pnl_net:,.2f} "
            f"({elapsed:.1f}s)")

        return {
            "tick_fills": result.total_fills,
            "tick_hr": round(summary.hit_rate, 4),
            "tick_excess_hr": round(tick_excess_hr, 4),
            "tick_pnl": round(summary.total_pnl_net, 2),
            "tick_sharpe": round(summary.sharpe, 3),
            "tick_elapsed_s": round(elapsed, 1),
            "tick_error": None,
        }
    except Exception as e:
        log(f"    ERROR in tick validation: {e}")
        return {
            "tick_fills": 0,
            "tick_hr": None,
            "tick_excess_hr": None,
            "tick_pnl": None,
            "tick_sharpe": None,
            "tick_elapsed_s": round(time.time() - t0, 1),
            "tick_error": str(e)[:200],
        }


# ── Main scan ─────────────────────────────────────────────────────────────────

def scan_all_tags(con, all_overview: list[dict]) -> list[dict]:
    """Main scan loop: all tags with enough data."""
    viable_tags = [
        t for t in all_overview
        if t["n_test_markets"] >= MIN_TEST_MARKETS and t["n_train_markets"] >= MIN_TRAIN_MARKETS
    ]
    log(f"\nViable tags for scan (>={MIN_TEST_MARKETS} test markets, "
        f">={MIN_TRAIN_MARKETS} train markets): {len(viable_tags)}")
    for t in viable_tags:
        log(f"  {t['tag']:30s} train={t['n_train_markets']:6d} test={t['n_test_markets']:5d} "
            f"train_base={t['train_base_rate']:.3f} test_base={t['test_base_rate']:.3f}")

    results = []
    for i, tag_info in enumerate(viable_tags):
        tag = tag_info["tag"]
        log(f"\n[{i+1}/{len(viable_tags)}] Processing: {tag}")
        t0 = time.time()

        try:
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
                    "tick": None,
                })
                continue

            metrics = run_vectorized_backtest(con, tag, pool, n_consensus=N_CONSENSUS)
            elapsed = time.time() - t0
            rscore = rank_score(metrics["excess_hr"], metrics["n_signals"])

            log(f"  n_signals={metrics['n_signals']} hr={metrics['hr']:.3f} "
                f"base={metrics['base_rate']:.3f} excess={metrics['excess_hr']:+.3f} "
                f"hold={metrics['median_hold_days']:.1f}d "
                f"rank_score={rscore:.3f} ({elapsed:.1f}s)")

            # Decide tick validation
            tick_result = None
            should_tick = (
                metrics["excess_hr"] >= TICK_VALIDATE_EXCESS_HR
                and metrics["n_signals"] >= TICK_VALIDATE_MIN_SIGNALS
            )
            if should_tick:
                log(f"  -> PROCEEDING TO TICK VALIDATION (excess={metrics['excess_hr']:+.1%}, "
                    f"n_signals={metrics['n_signals']})")
                tick_result = run_tick_validation(con, tag, pool, metrics, tag_info)
            else:
                reason = []
                if metrics["excess_hr"] < TICK_VALIDATE_EXCESS_HR:
                    reason.append(f"excess_hr={metrics['excess_hr']:+.1%} < {TICK_VALIDATE_EXCESS_HR:.0%}")
                if metrics["n_signals"] < TICK_VALIDATE_MIN_SIGNALS:
                    reason.append(f"n_signals={metrics['n_signals']} < {TICK_VALIDATE_MIN_SIGNALS}")
                log(f"  -> SKIP tick validation ({', '.join(reason)})")

            results.append({
                **tag_info,
                "pool_size": len(pool),
                **metrics,
                "rank_score": round(rscore, 4),
                "skip_reason": None,
                "tick": tick_result,
            })

        except Exception as e:
            log(f"  ERROR: {e}")
            import traceback
            log(f"  Traceback: {traceback.format_exc()[:500]}")
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
                "skip_reason": f"error: {str(e)[:100]}",
                "tick": None,
            })

    return results


# ── Results writer ────────────────────────────────────────────────────────────

def write_results_md(results: list[dict], all_overview: list[dict]) -> None:
    """Write full results to markdown report."""

    # Sort by rank_score descending
    ranked = sorted(results, key=lambda r: r.get("rank_score", 0), reverse=True)

    # Categorize
    tick_validated = [
        r for r in ranked
        if r.get("tick") is not None and r["tick"].get("tick_excess_hr") is not None
    ]
    viable = [
        r for r in ranked
        if r["excess_hr"] >= 0.25 and r["n_signals"] >= TICK_VALIDATE_MIN_SIGNALS
        and r.get("tick") is None  # vectorized only — tick validated ones handled separately
    ]
    marginal = [
        r for r in ranked
        if 0.10 <= r["excess_hr"] < 0.25 and r["n_signals"] >= 5
        and r not in tick_validated
    ]
    weak = [r for r in ranked if r not in tick_validated and r not in viable and r not in marginal]

    lines = [
        "# Tag Scan Results — January 2026 Extended Window",
        "",
        f"**Date**: 2026-03-07",
        f"**Method**: Vectorized UPPER BOUNDS (expect 20-40pp tick degradation)",
        f"**Train cutoff**: {TRAIN_CUTOFF} | **Test period**: {TEST_START} to {TEST_END}",
        f"**Pool**: Top-K={TOP_K} composite score | **Consensus**: N={N_CONSENSUS}",
        f"**Filter**: >={MIN_TEST_MARKETS} test markets, >={MIN_TRAIN_MARKETS} train markets",
        f"**Tick validate threshold**: excess_hr >= {TICK_VALIDATE_EXCESS_HR:.0%} AND n_signals >= {TICK_VALIDATE_MIN_SIGNALS}",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- Total tags scanned: {len(results)}",
        f"- Tick validated: **{len(tick_validated)}**",
        f"- Vectorized viable (>= 25pp, no tick run): {len(viable)}",
        f"- Marginal (10-25pp): {len(marginal)}",
        f"- Weak / skip: {len(weak)}",
        "",
        "> [!CRITICAL]",
        "> These are VECTORIZED upper bounds. Real tick-validated results will be 20-40pp lower.",
        "> Tags with excess_hr >= 25pp AND n_signals >= 10 were advanced to tick-by-tick validation.",
        "> Tick-validated results are ground truth — vectorized numbers are upper bounds only.",
        "",
        "---",
        "",
    ]

    # ── Tick-validated results ─────────────────────────────────────────────────
    if tick_validated:
        lines.extend([
            "## Tick-Validated Results",
            "",
            "| Tag | Test Mkts | Pool | Signals | Vec HR | Vec Excess | Tick HR | Tick Excess | Tick PnL | Sharpe | Rec |",
            "|-----|----------|------|---------|--------|-----------|---------|------------|----------|--------|-----|",
        ])
        for r in tick_validated:
            t = r["tick"]
            tick_hr_s = f"{t['tick_hr']:.1%}" if t["tick_hr"] is not None else "N/A"
            tick_exc_s = (f"+{t['tick_excess_hr']:.1%}" if t["tick_excess_hr"] is not None and t["tick_excess_hr"] >= 0
                          else f"{t['tick_excess_hr']:.1%}") if t["tick_excess_hr"] is not None else "N/A"
            tick_pnl_s = f"${t['tick_pnl']:,.0f}" if t["tick_pnl"] is not None else "N/A"
            tick_sharpe_s = f"{t['tick_sharpe']:.2f}" if t["tick_sharpe"] is not None else "N/A"

            # Recommendation logic
            tick_excess = t.get("tick_excess_hr") or 0.0
            if tick_excess >= 0.10 and (t["tick_pnl"] or 0) > 0:
                rec = "**DEPLOY**"
            elif tick_excess >= 0.05:
                rec = "monitor"
            else:
                rec = "skip"

            lines.append(
                f"| **{r['tag']}** | {r['n_test_markets']} | {r['pool_size']} | "
                f"{r['n_signals']} | {r['hr']:.1%} | +{r['excess_hr']:.1%} | "
                f"{tick_hr_s} | {tick_exc_s} | {tick_pnl_s} | {tick_sharpe_s} | {rec} |"
            )

        lines.extend(["", "---", ""])

    # ── Vectorized viable (not tick validated) ────────────────────────────────
    if viable:
        lines.extend([
            "## Vectorized Viable (>= 25pp, tick validation pending)",
            "",
            "| Rank | Tag | Test Mkts | Pool | Signals | HR | Base | Excess | Hold (d) | Rank Score |",
            "|------|-----|----------|------|---------|-----|------|--------|----------|------------|",
        ])
        for i, r in enumerate(viable, 1):
            lines.append(
                f"| {i} | **{r['tag']}** | {r['n_test_markets']} | {r['pool_size']} | "
                f"{r['n_signals']} | {r['hr']:.1%} | {r['base_rate']:.1%} | "
                f"**+{r['excess_hr']:.1%}** | {r['median_hold_days']:.1f} | {r['rank_score']:.2f} |"
            )
        lines.extend(["", "---", ""])

    # ── Marginal tags ──────────────────────────────────────────────────────────
    lines.extend([
        "## Marginal Tags (10-25pp excess, vectorized)",
        "",
        "| Tag | Test Mkts | Pool | Signals | HR | Base | Excess | Hold (d) |",
        "|-----|----------|------|---------|-----|------|--------|----------|",
    ])
    for r in marginal:
        lines.append(
            f"| {r['tag']} | {r['n_test_markets']} | {r['pool_size']} | "
            f"{r['n_signals']} | {r['hr']:.1%} | {r['base_rate']:.1%} | "
            f"+{r['excess_hr']:.1%} | {r['median_hold_days']:.1f} |"
        )

    # ── Full tag overview ──────────────────────────────────────────────────────
    lines.extend([
        "",
        "---",
        "",
        "## Full Tag Universe Overview (January 2026)",
        "",
        "All tags with market counts in training and test periods:",
        "",
        "| Tag | Train Mkts | Train Traders | Train Base | Test Mkts | Test Base | Signals | Excess HR | Status |",
        "|-----|-----------|---------------|-----------|----------|----------|---------|-----------|--------|",
    ])

    # Show all scanned tags sorted by test market count
    by_test = sorted(results, key=lambda r: r["n_test_markets"], reverse=True)
    for r in by_test:
        t = r.get("tick")
        if t is not None and t.get("tick_excess_hr") is not None:
            tick_exc = t["tick_excess_hr"]
            if tick_exc >= 0.10 and (t["tick_pnl"] or 0) > 0:
                status = "TICK:DEPLOY"
            elif tick_exc >= 0.05:
                status = "TICK:monitor"
            else:
                status = "TICK:skip"
        elif r["excess_hr"] >= 0.25 and r["n_signals"] >= TICK_VALIDATE_MIN_SIGNALS:
            status = "VEC:viable"
        elif 0.10 <= r["excess_hr"] < 0.25 and r["n_signals"] >= 5:
            status = "marginal"
        elif r.get("skip_reason"):
            status = f"skip ({r['skip_reason'][:25]})"
        else:
            status = "weak"

        lines.append(
            f"| {r['tag']} | {r['n_train_markets']} | {r['n_train_traders']} | "
            f"{r['train_base_rate']:.3f} | {r['n_test_markets']} | {r['test_base_rate']:.3f} | "
            f"{r['n_signals']} | {r['excess_hr']:+.3f} | {status} |"
        )

    # ── All tags universe including below-threshold ────────────────────────────
    lines.extend([
        "",
        "---",
        "",
        "## Complete Tag Universe (all tags, sorted by Jan 2026 market count)",
        "",
        "| Tag | Train Mkts | Jan 2026 Mkts | Jan 2026 Base HR | Note |",
        "|-----|-----------|--------------|----------------|------|",
    ])

    all_sorted = sorted(all_overview, key=lambda r: r["n_test_markets"], reverse=True)
    for r in all_sorted[:100]:
        was_scanned = any(x["tag"] == r["tag"] for x in results)
        note = "scanned" if was_scanned else (
            "too few train" if r["n_train_markets"] < MIN_TRAIN_MARKETS else
            "too few test" if r["n_test_markets"] < MIN_TEST_MARKETS else "other"
        )
        lines.append(
            f"| {r['tag']} | {r['n_train_markets']} | {r['n_test_markets']} | "
            f"{r['test_base_rate']:.3f} | {note} |"
        )

    # ── Recommendations ────────────────────────────────────────────────────────
    lines.extend([
        "",
        "---",
        "",
        "## Ranked Recommendations",
        "",
    ])

    # Combine tick-validated and vectorized viable for final recommendation table
    all_recommended = []
    for r in tick_validated:
        t = r["tick"]
        tick_excess = t.get("tick_excess_hr") or 0
        tick_pnl = t.get("tick_pnl") or 0
        all_recommended.append({
            "tag": r["tag"],
            "n_signals": r["n_signals"],
            "vec_excess": r["excess_hr"],
            "tick_excess": tick_excess,
            "tick_pnl": tick_pnl,
            "median_hold_days": r["median_hold_days"],
            "status": "tick_validated",
            "rec": "DEPLOY" if tick_excess >= 0.10 and tick_pnl > 0 else ("monitor" if tick_excess >= 0.05 else "skip"),
        })
    for r in viable:
        all_recommended.append({
            "tag": r["tag"],
            "n_signals": r["n_signals"],
            "vec_excess": r["excess_hr"],
            "tick_excess": None,
            "tick_pnl": None,
            "median_hold_days": r["median_hold_days"],
            "status": "vectorized_only",
            "rec": "tick-validate",
        })

    if all_recommended:
        lines.extend([
            "| Rank | Tag | Signals (Jan 26) | Vec Excess (UB) | Tick Excess | Tick PnL | Hold (d) | Recommendation |",
            "|------|-----|----------------|----------------|------------|----------|----------|----------------|",
        ])
        for i, r in enumerate(all_recommended, 1):
            vec_exc_s = f"+{r['vec_excess']:.1%}"
            tick_exc_s = f"+{r['tick_excess']:.1%}" if r["tick_excess"] is not None else "(not run)"
            tick_pnl_s = f"${r['tick_pnl']:,.0f}" if r["tick_pnl"] is not None else "(not run)"
            lines.append(
                f"| {i} | **{r['tag']}** | {r['n_signals']} | {vec_exc_s} | "
                f"{tick_exc_s} | {tick_pnl_s} | {r['median_hold_days']:.1f} | **{r['rec']}** |"
            )
    else:
        lines.append("No tags met the recommendation threshold in January 2026.")

    # ── Delta vs previous scan ─────────────────────────────────────────────────
    lines.extend([
        "",
        "---",
        "",
        "## Delta vs Previous Scan (July 2025 cutoff)",
        "",
        "Previous scan (train<2025-07-01, test=2025-07-01+) found:",
        "- 14 tags with >= 50 test markets AND >= 100 train markets",
        "- 3 genuine viable: Sports, Politics, Crypto",
        "- Notable near-misses: Esports (42 train markets, too few for pool building)",
        "",
        "This scan (train<2026-01-01, test=Jan 2026):",
    ])

    # Identify newly qualifying tags
    prev_tags = {"Sports", "Politics", "Crypto", "Weather", "Awards", "Movies",
                 "Culture", "Music", "Business", "Science", "Trump", "Elon Musk",
                 "box office", "NFL"}
    new_tags = [r for r in results if r["tag"] not in prev_tags]
    if new_tags:
        lines.append(f"- **New tags qualifying** (not in previous scan): {', '.join(r['tag'] for r in new_tags)}")
    else:
        lines.append("- No new tags entered the scan threshold")

    lines.extend([
        "",
        "---",
        "",
        "## Methodology Notes",
        "",
        "- **Pool building**: Top-K=25 by composite score (0.45*excess_hr + 0.25*consistency_sharpe + 0.15*avg_edge_usd + 0.15*bucket_excess_hr)",
        "- **Min trader qualifications**: >= 10 markets in training, conviction >= 0.90, < 10000 trades (bot filter)",
        "- **Signal**: N=2 distinct pool traders enter YES in the same market during January 2026",
        "- **Test window filter**: first_trade >= 2026-01-01 (only copyable entries, prevents training leakage)",
        "- **Hold time**: date_diff(day, max(first_trade), resolved_at) — market level, not trader level",
        "- **Counting**: MARKET level (distinct condition_ids), not trader-position level",
        "- **Rank score**: excess_hr * sqrt(n_signals) — balances edge with volume",
        "- **Vectorized bias**: ~20-40pp excess HR lost in tick-by-tick validation (consensus gap)",
        "- **Tick validation threshold**: excess_hr >= 25pp AND n_signals >= 10",
        "- **Direction filter in tick**: YES-only (BUY-entry consensus)",
        "",
        "---",
        "",
        "*Results are UPPER BOUNDS from vectorized backtesting. Tick-validated results are ground truth.*",
    ])

    RESULTS_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_MD_PATH.write_text("\n".join(lines) + "\n")
    log(f"\nResults written to: {RESULTS_MD_PATH}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("")  # clear log

    log("=" * 70)
    log("TAG SCAN — January 2026 Extended Window")
    log(f"Train cutoff: {TRAIN_CUTOFF} | Test: {TEST_START} to {TEST_END}")
    log(f"Pool K={TOP_K} | Consensus N={N_CONSENSUS}")
    log(f"Min test markets: {MIN_TEST_MARKETS} | Min train markets: {MIN_TRAIN_MARKETS}")
    log(f"Tick validate: excess_hr >= {TICK_VALIDATE_EXCESS_HR:.0%} AND n_signals >= {TICK_VALIDATE_MIN_SIGNALS}")
    log("=" * 70)

    t_total = time.time()
    con = get_db()

    setup_shared_tables(con)
    all_overview = get_all_tags_overview(con)
    results = scan_all_tags(con, all_overview)

    elapsed_total = time.time() - t_total
    log(f"\nTotal scan time: {elapsed_total:.1f}s")

    # Save raw JSON
    RESULTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON_PATH.write_text(json.dumps(results, indent=2, default=str))
    log(f"Raw results: {RESULTS_JSON_PATH}")

    # Write markdown
    write_results_md(results, all_overview)

    # Print summary
    tick_validated = [r for r in results if r.get("tick") and r["tick"].get("tick_excess_hr") is not None]
    viable = [r for r in results if r["excess_hr"] >= 0.25 and r["n_signals"] >= TICK_VALIDATE_MIN_SIGNALS]

    print("\n" + "=" * 70)
    print("TICK-VALIDATED TAGS:")
    print("=" * 70)
    for r in tick_validated:
        t = r["tick"]
        tick_exc = t.get("tick_excess_hr") or 0
        print(f"  {r['tag']:30s} vec_excess={r['excess_hr']:+.1%} "
              f"tick_excess={tick_exc:+.1%} "
              f"PnL=${t.get('tick_pnl',0):,.0f} fills={t.get('tick_fills',0)}")

    if not tick_validated:
        print("  (none)")

    print("\nVECTORIZED VIABLE (excess >= 25pp, n_signals >= 10):")
    viable_sorted = sorted(viable, key=lambda r: r["rank_score"], reverse=True)
    for r in viable_sorted:
        print(f"  {r['tag']:30s} signals={r['n_signals']:4d} "
              f"HR={r['hr']:.1%} base={r['base_rate']:.1%} "
              f"excess={r['excess_hr']:+.1%} hold={r['median_hold_days']:.1f}d "
              f"rank={r['rank_score']:.2f}")
    if not viable_sorted:
        print("  (none)")


if __name__ == "__main__":
    main()
