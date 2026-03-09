"""Tick-by-tick validation for tag-hr-consensus hypothesis.

Validates the top configs from vectorized sweep using SyncReplayRunner.
Key difference from tag-hr-copy: strategy fires on N-trader consensus,
not individual trades. This should produce much smaller vec-to-tick gap.

Configs to validate:
  1. Esports YES K=50 N=3 (vec: 67.3% HR, +19.6pp, 283 sigs)
  2. Esports YES K=100 N=4 (vec: 66.9% HR, +19.2pp, 288 sigs)
  3. Tennis NO K=50 N=2 (vec: 74.0% HR, +12.0pp, 296 sigs)

Test period: 2025-07-01 to 2026-03-01 (8 months)
Train cutoff: 2025-07-01

Usage:
    PYTHONPATH=/mnt/nvme/git/polymarket/polymarket uv run python \
        research/hypotheses/tag-hr-consensus/scripts/tick_validate.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.harness import run_fast_backtest, print_summary
from research.fast_replay import load_replay_resolutions
from research.strategies.consensus_v2 import TokenMapStrategy

OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
VALIDATION_DIR = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/tag-hr-consensus/discovery"
)

TEST_START = "2025-07-01"
TEST_END = "2026-03-01"
TRAIN_CUTOFF = "2025-07-01"

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


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def build_token_map_from_db(con) -> dict[str, dict[str, str]]:
    """Build token_map: {condition_id: {'YES': asset_id, 'NO': asset_id}}."""
    rows = con.execute(
        "SELECT condition_id, asset_id, outcome FROM token_market_map"
    ).fetchall()
    token_map: dict[str, dict[str, str]] = {}
    for cid, aid, outcome in rows:
        token_map.setdefault(cid, {})[outcome] = aid
    return token_map


def build_pool_for_tag(con, tag: str, k: int, direction: str) -> set[str]:
    """Build v3-style composite pool for tag+direction using train data before TRAIN_CUTOFF."""
    tag_lower = tag.lower()

    if direction == "YES":
        pos_filter = "p.position = 'YES'"
        correct_expr = "CAST(p.yes_won AS DOUBLE)"
    else:
        pos_filter = "p.position = 'NO'"
        correct_expr = "p.correct::DOUBLE"

    # Base training stats
    con.execute(f"""
    CREATE OR REPLACE TABLE _thcv_train_{tag_lower}_{direction.lower()} AS
    WITH base AS (
        SELECT
            p.trader, p.condition_id,
            {correct_expr} AS correct,
            abs(p.net_usd) AS abs_net_usd,
            p.volume, p.net_usd
        FROM maker_positions p
        JOIN _thcv_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE {pos_filter}
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

    # Consistency
    con.execute(f"""
    CREATE OR REPLACE TABLE _thcv_consistency_{tag_lower}_{direction.lower()} AS
    WITH monthly AS (
        SELECT
            p.trader,
            date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
            avg({correct_expr}) AS month_hr,
            count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _thcv_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN _thcv_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    )
    SELECT trader, count(*) AS n_months,
        CASE WHEN count(*) >= 2
             THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
             ELSE 0.0 END AS consistency_sharpe
    FROM monthly
    GROUP BY trader
    HAVING count(*) >= 3;
    """)

    # BEH
    if direction == "YES":
        price_expr = "yed.price_x_vol / yed.volume"
        join_clause = f"""
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _thcv_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
        """
    else:
        price_expr = "COALESCE(1.0 - (yed.price_x_vol / NULLIF(yed.volume, 0)), abs(p.net_usd) / NULLIF(p.net_no, 0))"
        join_clause = f"""
        JOIN _thcv_train_{tag_lower}_{direction.lower()} t ON p.trader = t.trader
        LEFT JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{TRAIN_CUTOFF}'
          AND p.volume > 0
          AND {price_expr} BETWEEN 0.05 AND 0.95
        """

    con.execute(f"""
    CREATE OR REPLACE TABLE _thcv_bucket_{tag_lower}_{direction.lower()} AS
    WITH price_data AS (
        SELECT
            p.trader, p.condition_id,
            {correct_expr} AS correct,
            CAST(floor({price_expr} * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _thcv_tag_mkts tm ON p.condition_id = tm.condition_id
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
    )
    SELECT trader, count(*) AS n_buckets,
           avg(bucket_hr - pop_hr) AS bucket_excess_hr
    FROM trader_bucket
    GROUP BY trader
    HAVING count(*) >= 2;
    """)

    # Composite with BEH gate
    con.execute(f"""
    CREATE OR REPLACE TABLE _thcv_composite_{tag_lower}_{direction.lower()} AS
    WITH base AS (
        SELECT
            t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
            coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
            coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _thcv_train_{tag_lower}_{direction.lower()} t
        LEFT JOIN _thcv_consistency_{tag_lower}_{direction.lower()} c ON t.trader = c.trader
        LEFT JOIN _thcv_bucket_{tag_lower}_{direction.lower()} b ON t.trader = b.trader
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

    rows = con.execute(f"""
        SELECT trader FROM _thcv_composite_{tag_lower}_{direction.lower()}
        WHERE rank <= {k}
        ORDER BY rank
    """).fetchall()
    return {r[0].lower() for r in rows}


def get_tag_markets(con, tag: str) -> set[str]:
    """Get all non-gambling markets for a tag that resolved in/after test period."""
    rows = con.execute(f"""
        SELECT DISTINCT p.condition_id
        FROM maker_positions p
        JOIN _thcv_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE CAST(p.resolved_at AS DATE) >= '{TEST_START}'
    """).fetchall()
    return {r[0] for r in rows}


def get_gambling_markets(con) -> set[str]:
    rows = con.execute(
        "SELECT condition_id FROM markets WHERE is_gambling_market_v3(slug)"
    ).fetchall()
    return {r[0] for r in rows}


def compute_base_rate(con, tag: str, direction: str) -> float:
    if direction == "YES":
        correct_expr = "avg(CAST(p.yes_won AS DOUBLE))"
    else:
        correct_expr = "avg(p.correct::DOUBLE)"

    row = con.execute(f"""
        SELECT {correct_expr} AS base_rate
        FROM maker_positions p
        JOIN _thcv_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE p.position = '{direction}'
          AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
          AND CAST(p.resolved_at AS DATE) < '{TEST_END}'
          AND p.volume > 0
    """).fetchone()
    return round(row[0], 4) if row and row[0] is not None else 0.0


def analyze_ledger(ledger_path: Path, base_rate: float, label: str) -> dict:
    if not ledger_path.exists():
        log(f"  [WARN] Ledger not found: {ledger_path}")
        return {}

    df = pl.read_parquet(ledger_path)
    settled = df.filter(pl.col("resolution").is_not_null())
    log(f"  Settled: {len(settled)} / {len(df)} total")

    if len(settled) == 0:
        return {"settled": 0}

    hr = (settled["resolution"] == "WON").mean()
    excess = hr - base_rate

    hold_h = settled.with_columns(
        (pl.col("hold_duration_s") / 3600).alias("hold_h")
    )
    median_hold = hold_h["hold_h"].median()
    total_pnl = settled["pnl_net"].sum() if "pnl_net" in settled.columns else 0

    # Monthly breakdown
    if "signal_time" in settled.columns:
        monthly = settled.with_columns(
            (pl.col("signal_time") * 1e6).cast(pl.Datetime("us"))
              .dt.truncate("1mo").alias("month")
        )
        monthly_stats = (
            monthly.group_by("month")
              .agg([
                  pl.len().alias("n_fills"),
                  (pl.col("resolution") == "WON").mean().alias("hr"),
                  pl.col("pnl_net").sum().alias("pnl_net"),
              ])
              .sort("month")
        )
        log(f"  Monthly:\n{monthly_stats}")

    log(f"  HR: {hr:.1%}, base: {base_rate:.1%}, excess: {excess:+.1%}")
    log(f"  PnL: ${total_pnl:,.2f}, median hold: {median_hold:.1f}h")

    return {
        "settled": len(settled),
        "hr": round(float(hr), 4),
        "excess_hr": round(float(excess), 4),
        "base_rate": base_rate,
        "total_pnl": round(float(total_pnl), 2),
        "median_hold_h": round(float(median_hold), 1) if median_hold else None,
    }


def make_config(name: str, capital: float = 5000.0) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital,
        max_position_usd=100.0,
        max_open_positions=50,
        cooldown_s=0,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("Tag-HR-Consensus: Tick-by-Tick Validation")
    log(f"Test period: {TEST_START} to {TEST_END}")
    log("=" * 70)

    # Load token_map
    log("\nLoading token_map...")
    t0 = time.time()
    _, token_map_replay = load_replay_resolutions()
    log(f"  token_map (replay): {len(token_map_replay)} markets ({time.time()-t0:.1f}s)")

    from research.db import db as get_db
    con = get_db().con

    token_map_db = build_token_map_from_db(con)
    token_map = {**token_map_db, **token_map_replay}
    log(f"  Combined token_map: {len(token_map)} markets")

    # Setup shared tables
    con.execute(GAMBLING_MACRO_SQL)
    gambling = get_gambling_markets(con)

    all_results = {}

    # ═════════════════════════════════════════════════════════════════════
    # LEG 1: Esports YES K=50 N=3
    # Vectorized UB: 67.3% HR, +19.6pp excess, 283 sigs
    # ═════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 1: Esports YES K=50 N=3")
    log("Vectorized UB: 67.3% HR, +19.6pp excess, 283 sigs/3 folds")
    log("=" * 70)

    # Build tag markets
    con.execute("DROP TABLE IF EXISTS _thcv_tag_mkts")
    con.execute("""
        CREATE TABLE _thcv_tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = 'Esports'
          AND NOT is_gambling_market_v3(m.slug)
    """)

    t0 = time.time()
    pool_esports_yes = build_pool_for_tag(con, "Esports", 50, "YES")
    log(f"  Pool: {len(pool_esports_yes)} traders ({time.time()-t0:.1f}s)")

    tag_markets_esports = get_tag_markets(con, "Esports")
    universe_esports = tag_markets_esports - gambling
    log(f"  Universe: {len(universe_esports)} markets (excl gambling)")

    base_rate_esports_yes = compute_base_rate(con, "Esports", "YES")
    log(f"  Base rate (Esports YES, test): {base_rate_esports_yes:.1%}")

    strategy1 = TokenMapStrategy(
        name="thc_esports_yes_k50_n3",
        pool=pool_esports_yes,
        tag_markets=tag_markets_esports,
        gambling_markets=gambling,
        n_threshold=3,
        token_map=token_map,
        direction_filter="YES",
        size_usd=100.0,
    )

    config1 = make_config("thc_esports_yes_k50_n3")

    log("  Running tick replay...")
    t0 = time.time()
    result1, summary1 = run_fast_backtest(
        strategy1, config1,
        universe=universe_esports,
        output_dir=OUTPUT_DIR,
    )
    elapsed1 = time.time() - t0
    log(f"  Elapsed: {elapsed1:.1f}s | trades: {result1.total_trades:,} | "
        f"fills: {result1.total_fills} | rejected: {len(result1.rejected_intents)}")

    if summary1:
        print_summary(summary1, "Esports_YES_K50_N3")

    leg1 = analyze_ledger(
        OUTPUT_DIR / "ledger_thc_esports_yes_k50_n3.parquet",
        base_rate_esports_yes, "Esports YES K50 N3",
    )
    leg1.update({
        "name": "thc_esports_yes_k50_n3",
        "tag": "Esports", "direction": "YES", "k": 50, "n": 3,
        "vec_hr": 0.673, "vec_excess": 0.196,
        "pool_size": len(pool_esports_yes),
        "total_fills": result1.total_fills,
    })
    if summary1:
        leg1.update({
            "tick_hr": round(summary1.hit_rate, 4),
            "tick_pnl_net": round(summary1.total_pnl_net, 2),
            "tick_sharpe": round(summary1.sharpe, 4),
            "tick_excess": round(summary1.hit_rate - base_rate_esports_yes, 4),
        })
    all_results["leg1_esports_yes_k50_n3"] = leg1

    # ═════════════════════════════════════════════════════════════════════
    # LEG 2: Esports YES K=100 N=4
    # Vectorized UB: 66.9% HR, +19.2pp excess, 288 sigs
    # ═════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 2: Esports YES K=100 N=4")
    log("Vectorized UB: 66.9% HR, +19.2pp excess, 288 sigs/3 folds")
    log("=" * 70)

    t0 = time.time()
    pool_esports_yes_100 = build_pool_for_tag(con, "Esports", 100, "YES")
    log(f"  Pool: {len(pool_esports_yes_100)} traders ({time.time()-t0:.1f}s)")

    strategy2 = TokenMapStrategy(
        name="thc_esports_yes_k100_n4",
        pool=pool_esports_yes_100,
        tag_markets=tag_markets_esports,
        gambling_markets=gambling,
        n_threshold=4,
        token_map=token_map,
        direction_filter="YES",
        size_usd=100.0,
    )

    config2 = make_config("thc_esports_yes_k100_n4")

    log("  Running tick replay...")
    t0 = time.time()
    result2, summary2 = run_fast_backtest(
        strategy2, config2,
        universe=universe_esports,
        output_dir=OUTPUT_DIR,
    )
    elapsed2 = time.time() - t0
    log(f"  Elapsed: {elapsed2:.1f}s | trades: {result2.total_trades:,} | "
        f"fills: {result2.total_fills} | rejected: {len(result2.rejected_intents)}")

    if summary2:
        print_summary(summary2, "Esports_YES_K100_N4")

    leg2 = analyze_ledger(
        OUTPUT_DIR / "ledger_thc_esports_yes_k100_n4.parquet",
        base_rate_esports_yes, "Esports YES K100 N4",
    )
    leg2.update({
        "name": "thc_esports_yes_k100_n4",
        "tag": "Esports", "direction": "YES", "k": 100, "n": 4,
        "vec_hr": 0.669, "vec_excess": 0.192,
        "pool_size": len(pool_esports_yes_100),
        "total_fills": result2.total_fills,
    })
    if summary2:
        leg2.update({
            "tick_hr": round(summary2.hit_rate, 4),
            "tick_pnl_net": round(summary2.total_pnl_net, 2),
            "tick_sharpe": round(summary2.sharpe, 4),
            "tick_excess": round(summary2.hit_rate - base_rate_esports_yes, 4),
        })
    all_results["leg2_esports_yes_k100_n4"] = leg2

    # ═════════════════════════════════════════════════════════════════════
    # LEG 3: Tennis NO K=50 N=2
    # Vectorized UB: 74.0% HR, +12.0pp excess, 296 sigs
    # ═════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 3: Tennis NO K=50 N=2")
    log("Vectorized UB: 74.0% HR, +12.0pp excess, 296 sigs/3 folds")
    log("=" * 70)

    # Build Tennis tag markets
    con.execute("DROP TABLE IF EXISTS _thcv_tag_mkts")
    con.execute("""
        CREATE TABLE _thcv_tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = 'Tennis'
          AND NOT is_gambling_market_v3(m.slug)
    """)

    t0 = time.time()
    pool_tennis_no = build_pool_for_tag(con, "Tennis", 50, "NO")
    log(f"  Pool: {len(pool_tennis_no)} traders ({time.time()-t0:.1f}s)")

    tag_markets_tennis = get_tag_markets(con, "Tennis")
    universe_tennis = tag_markets_tennis - gambling
    log(f"  Universe: {len(universe_tennis)} markets (excl gambling)")

    base_rate_tennis_no = compute_base_rate(con, "Tennis", "NO")
    log(f"  Base rate (Tennis NO, test): {base_rate_tennis_no:.1%}")

    strategy3 = TokenMapStrategy(
        name="thc_tennis_no_k50_n2",
        pool=pool_tennis_no,
        tag_markets=tag_markets_tennis,
        gambling_markets=gambling,
        n_threshold=2,
        token_map=token_map,
        direction_filter="NO",
        size_usd=100.0,
    )

    config3 = make_config("thc_tennis_no_k50_n2")

    log("  Running tick replay...")
    t0 = time.time()
    result3, summary3 = run_fast_backtest(
        strategy3, config3,
        universe=universe_tennis,
        output_dir=OUTPUT_DIR,
    )
    elapsed3 = time.time() - t0
    log(f"  Elapsed: {elapsed3:.1f}s | trades: {result3.total_trades:,} | "
        f"fills: {result3.total_fills} | rejected: {len(result3.rejected_intents)}")

    if summary3:
        print_summary(summary3, "Tennis_NO_K50_N2")

    leg3 = analyze_ledger(
        OUTPUT_DIR / "ledger_thc_tennis_no_k50_n2.parquet",
        base_rate_tennis_no, "Tennis NO K50 N2",
    )
    leg3.update({
        "name": "thc_tennis_no_k50_n2",
        "tag": "Tennis", "direction": "NO", "k": 50, "n": 2,
        "vec_hr": 0.740, "vec_excess": 0.120,
        "pool_size": len(pool_tennis_no),
        "total_fills": result3.total_fills,
    })
    if summary3:
        leg3.update({
            "tick_hr": round(summary3.hit_rate, 4),
            "tick_pnl_net": round(summary3.total_pnl_net, 2),
            "tick_sharpe": round(summary3.sharpe, 4),
            "tick_excess": round(summary3.hit_rate - base_rate_tennis_no, 4),
        })
    all_results["leg3_tennis_no_k50_n2"] = leg3

    # ── Save results ──────────────────────────────────────────────────
    output = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "test_start": TEST_START,
            "test_end": TEST_END,
            "train_cutoff": TRAIN_CUTOFF,
            "note": "Tick-validated via SyncReplayRunner. NOT upper bounds.",
        },
        "results": all_results,
    }
    json_path = VALIDATION_DIR / "tick_results.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log(f"\nSaved: {json_path}")

    # ── Print comparison table ──────────────────────────────────────
    log("\n" + "=" * 70)
    log("VECTORIZED vs TICK-BY-TICK COMPARISON")
    log("=" * 70)
    log(f"{'Config':<30} {'Vec HR':>8} {'Tick HR':>8} {'Gap':>8} {'Vec exc':>8} {'Tick exc':>9} {'PnL':>10} {'Fills':>6}")
    for key, r in all_results.items():
        vec_hr = r.get("vec_hr", 0)
        tick_hr = r.get("tick_hr", 0)
        gap = tick_hr - vec_hr if tick_hr else None
        vec_exc = r.get("vec_excess", 0)
        tick_exc = r.get("tick_excess", 0)
        pnl = r.get("tick_pnl_net", 0)
        fills = r.get("total_fills", 0)
        gap_str = f"{gap:+.1%}" if gap is not None else "N/A"
        log(f"{r.get('name',''):<30} {vec_hr:>7.1%} {tick_hr:>7.1%} {gap_str:>8} {vec_exc:>+7.1%} {tick_exc:>+8.1%} ${pnl:>9,.2f} {fills:>6}")

    log("\n" + "=" * 70)
    log("VALIDATION COMPLETE")
    log("=" * 70)


if __name__ == "__main__":
    main()
