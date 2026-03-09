"""Esports Sub-Tag Decomposition + Consensus Robustness Analysis.

Part 1: Decompose Esports by game (CS2, Dota2, LoL, Valorant, etc.)
  - Per-game base rates and market counts
  - Per-game trader pools and signal quality
  - Identify if one game carries the whole signal

Part 2: Parameter robustness sweep
  - K = [10, 25, 50, 75, 100]
  - N = [1, 2, 3, 4, 5]
  - All with BEH gate >= 0.02, YES direction
  - Stability analysis across the K x N grid

Usage:
    PYTHONPATH=/mnt/nvme/git/polymarket/polymarket uv run python \
        research/hypotheses/esports-sub-tag/scripts/analyze.py
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from research.db import db

OUTPUT_DIR = Path("research/hypotheses/esports-sub-tag/discovery")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Game tag mappings (event_tags.label -> game)
GAME_TAG_MAP = {
    "counter strike 2": "CS2",
    "cs2": "CS2",
    "counter-strike": "CS2",
    "csgo": "CS2",
    "Dota 2": "Dota2",
    "dota": "Dota2",
    "league of legends": "LoL",
    "lol": "LoL",
    "Valorant": "Valorant",
    "Honor of Kings": "HoK",
    "Starcraft 2": "SC2",
    "Rocket League": "RocketLeague",
    "Mobile Legends: Bang Bang": "MLBB",
    "Rainbow Six Siege": "R6",
    "Call of Duty": "CoD",
    "Overwatch": "Overwatch",
}

# Walk-forward folds (same as tag-hr-consensus)
FOLDS = [
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-04-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-07-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-10-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2026-01-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-03-01"),
]

# Gambling macro
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


def setup_game_mapping(d) -> None:
    """Create a table mapping condition_id -> game."""
    d.execute(GAMBLING_MACRO_SQL)

    # Build the CASE expression for game classification
    cases = []
    for label, game in GAME_TAG_MAP.items():
        cases.append(f"WHEN et.label = '{label}' THEN '{game}'")
    case_sql = " ".join(cases)

    d.execute("""
    CREATE OR REPLACE TABLE _esg_esports_events AS
    SELECT DISTINCT event_id FROM event_tags WHERE label = 'Esports'
    """)

    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_event_game AS
    WITH game_labels AS (
        SELECT et.event_id,
            CASE {case_sql}
                 ELSE NULL
            END AS game
        FROM event_tags et
        JOIN _esg_esports_events ee ON et.event_id = ee.event_id
    ),
    -- Pick first non-null game per event
    event_game AS (
        SELECT event_id, first(game) AS game
        FROM game_labels
        WHERE game IS NOT NULL
        GROUP BY event_id
    )
    SELECT ee.event_id, COALESCE(eg.game, 'Other') AS game
    FROM _esg_esports_events ee
    LEFT JOIN event_game eg ON ee.event_id = eg.event_id
    """)

    d.execute("""
    CREATE OR REPLACE TABLE _esg_market_game AS
    SELECT m.condition_id, eg.game
    FROM markets m
    JOIN _esg_event_game eg ON m.event_id = eg.event_id
    WHERE NOT is_gambling_market_v3(m.slug)
    """)

    # Also create the full esports tag markets table (for pool building)
    d.execute("""
    CREATE OR REPLACE TABLE _esg_tag_mkts AS
    SELECT DISTINCT m.condition_id
    FROM markets m
    JOIN event_tags et ON m.event_id = et.event_id
    WHERE et.label = 'Esports'
      AND NOT is_gambling_market_v3(m.slug)
    """)

    n_mkts = d.fetchone("SELECT count() AS n FROM _esg_market_game")["n"]
    log(f"  Mapped {n_mkts:,} esports markets to games")

    # Print distribution
    rows = d.fetchall("""
        SELECT game, count(*) AS n FROM _esg_market_game GROUP BY game ORDER BY n DESC
    """)
    for r in rows:
        log(f"    {r['game']:<15} {r['n']:>6}")


def part1_game_decomposition(d) -> dict:
    """Part 1: Per-game base rates, trader pools, and signal quality."""
    log("\n" + "=" * 70)
    log("PART 1: ESPORTS SUB-TAG DECOMPOSITION")
    log("=" * 70)

    results = {}

    # 1a. Per-game base rates (market-level YES win rate)
    log("\n--- Per-game base rates (resolved markets in test period) ---")
    game_stats = d.fetchall("""
        WITH resolved AS (
            SELECT mg.game, mg.condition_id,
                   CAST(mr.token_won AS INT) AS yes_won
            FROM _esg_market_game mg
            JOIN markets_resolved mr ON mg.condition_id = mr.condition_id
            WHERE mr.outcome = 'YES'
              AND mr.token_won IS NOT NULL
        )
        SELECT game,
               count(*) AS n_resolved,
               sum(yes_won) AS n_yes_won,
               avg(CAST(yes_won AS DOUBLE)) AS yes_base_rate
        FROM resolved
        GROUP BY game
        ORDER BY n_resolved DESC
    """)

    log(f"\n  {'Game':<15} {'Resolved':>10} {'YES won':>10} {'YES base':>10}")
    log("  " + "-" * 50)
    for r in game_stats:
        log(f"  {r['game']:<15} {r['n_resolved']:>10} {r['n_yes_won']:>10} {r['yes_base_rate']:>9.1%}")
        results[r["game"]] = {
            "n_resolved": r["n_resolved"],
            "n_yes_won": r["n_yes_won"],
            "yes_base_rate": round(r["yes_base_rate"], 4),
        }

    # 1b. Per-game base rates in test period only (2025-07 to 2026-03)
    log("\n--- Per-game base rates (test period: 2025-07 to 2026-03) ---")
    game_stats_test = d.fetchall("""
        WITH resolved AS (
            SELECT mg.game, mg.condition_id,
                   CAST(mr.token_won AS INT) AS yes_won
            FROM _esg_market_game mg
            JOIN markets_resolved mr ON mg.condition_id = mr.condition_id
            WHERE mr.outcome = 'YES'
              AND mr.token_won IS NOT NULL
        ),
        with_resolve_time AS (
            SELECT r.*, p.resolved_at
            FROM resolved r
            JOIN (
                SELECT condition_id, first(resolved_at) AS resolved_at
                FROM maker_positions
                GROUP BY condition_id
            ) p ON r.condition_id = p.condition_id
            WHERE CAST(p.resolved_at AS DATE) >= '2025-07-01'
              AND CAST(p.resolved_at AS DATE) < '2026-03-01'
        )
        SELECT game,
               count(*) AS n_resolved,
               sum(yes_won) AS n_yes_won,
               avg(CAST(yes_won AS DOUBLE)) AS yes_base_rate
        FROM with_resolve_time
        GROUP BY game
        ORDER BY n_resolved DESC
    """)

    log(f"\n  {'Game':<15} {'Resolved':>10} {'YES won':>10} {'YES base':>10}")
    log("  " + "-" * 50)
    for r in game_stats_test:
        log(f"  {r['game']:<15} {r['n_resolved']:>10} {r['n_yes_won']:>10} {r['yes_base_rate']:>9.1%}")
        if r["game"] in results:
            results[r["game"]]["test_resolved"] = r["n_resolved"]
            results[r["game"]]["test_yes_base_rate"] = round(r["yes_base_rate"], 4)

    # 1c. Per-game qualified traders (using same v3 composite scoring)
    log("\n--- Per-game qualified trader pools (v3, BEH>=0.02, train < 2025-07) ---")

    for game, stats in results.items():
        if stats.get("test_resolved", 0) < 20:
            log(f"  {game}: skipping, only {stats.get('test_resolved', 0)} test markets")
            stats["pool_k50"] = 0
            stats["pool_qualified"] = 0
            continue

        # Build pool using ONLY this game's markets
        try:
            n_qualified = _build_game_pool(d, game, "YES", "2025-07-01")
            stats["pool_qualified"] = n_qualified
            stats["pool_k50"] = min(n_qualified, 50)
            log(f"  {game}: {n_qualified} qualified traders (pool capped at {stats['pool_k50']})")
        except Exception as e:
            log(f"  {game}: pool build failed: {e}")
            stats["pool_qualified"] = 0
            stats["pool_k50"] = 0

    # 1d. Per-game signal quality (vectorized, YES direction, K=50, N=3)
    log("\n--- Per-game vectorized signal quality (K=50, N=3, YES) ---")
    log("    Using FULL esports pool (not game-specific), then measuring per-game")

    # Build full Esports pool
    pool_full = _build_full_esports_pool(d, 50, "YES", "2025-07-01")
    log(f"    Full Esports YES pool: {len(pool_full)} traders")

    if pool_full:
        vals = ", ".join(f"('{t}')" for t in pool_full)
        d.execute("DROP TABLE IF EXISTS _esg_full_pool")
        d.execute(f"CREATE TEMP TABLE _esg_full_pool AS SELECT * FROM (VALUES {vals}) AS t(trader)")

        # Compute consensus signals per game
        game_signals = d.fetchall("""
            WITH entries AS (
                SELECT
                    p.condition_id,
                    mg.game,
                    p.trader,
                    p.yes_won,
                    p.realized_pnl,
                    p.first_trade,
                    p.resolved_at,
                    p.net_usd,
                    p.volume
                FROM maker_positions p
                JOIN _esg_market_game mg ON p.condition_id = mg.condition_id
                JOIN _esg_full_pool sp ON p.trader = sp.trader
                WHERE p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
                  AND CAST(p.resolved_at AS DATE) < '2026-03-01'
                  AND CAST(p.first_trade AS DATE) >= '2025-07-01'
                  AND p.volume > 0
            ),
            ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_rank,
                    COUNT(*) OVER (PARTITION BY condition_id) AS n_traders
                FROM entries
            ),
            market_signals AS (
                SELECT
                    condition_id,
                    first(game) AS game,
                    n_traders,
                    MAX(CASE WHEN entry_rank = 3 THEN first_trade END) AS signal_time,
                    first(yes_won) AS yes_won,
                    first(resolved_at) AS resolved_at,
                    median(realized_pnl) AS median_pnl
                FROM ranked
                WHERE n_traders >= 3
                GROUP BY condition_id, n_traders
                HAVING signal_time IS NOT NULL
            )
            SELECT
                game,
                count(*) AS n_signals,
                sum(CAST(yes_won AS INT)) AS n_correct,
                avg(CAST(yes_won AS DOUBLE)) AS hr,
                avg(median_pnl) AS avg_median_pnl,
                median(EXTRACT(EPOCH FROM (resolved_at - signal_time)) / 3600) AS med_hold_h
            FROM market_signals
            GROUP BY game
            ORDER BY n_signals DESC
        """)

        log(f"\n  {'Game':<15} {'Sigs':>6} {'HR':>8} {'Base':>8} {'Excess':>8} {'medPnL':>8} {'Hold':>6}")
        log("  " + "-" * 65)
        for r in game_signals:
            game = r["game"]
            base = results.get(game, {}).get("test_yes_base_rate", 0.463)
            excess = r["hr"] - base
            log(f"  {game:<15} {r['n_signals']:>6} {r['hr']:>7.1%} {base:>7.1%} "
                f"{excess:>+7.1%} ${r['avg_median_pnl']:>7.2f} {r['med_hold_h'] or 0:>5.1f}h")
            if game in results:
                results[game]["vec_signals_k50_n3"] = r["n_signals"]
                results[game]["vec_hr_k50_n3"] = round(r["hr"], 4)
                results[game]["vec_excess_k50_n3"] = round(excess, 4)
                results[game]["vec_med_pnl_k50_n3"] = round(float(r["avg_median_pnl"]), 2)
                results[game]["vec_hold_h_k50_n3"] = round(float(r["med_hold_h"] or 0), 1)

    return results


def _build_game_pool(d, game: str, direction: str, train_cutoff: str) -> int:
    """Build v3 pool for a specific game and return count of qualified traders."""
    game_lower = game.lower().replace(" ", "_")

    if direction == "YES":
        pos_filter = "p.position = 'YES'"
        correct_expr = "CAST(p.yes_won AS DOUBLE)"
    else:
        pos_filter = "p.position = 'NO'"
        correct_expr = "p.correct::DOUBLE"

    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_gtrain_{game_lower} AS
    WITH game_mkts AS (
        SELECT condition_id FROM _esg_market_game WHERE game = '{game}'
    ),
    base AS (
        SELECT p.trader, p.condition_id,
               {correct_expr} AS correct, p.net_usd
        FROM maker_positions p
        JOIN game_mkts gm ON p.condition_id = gm.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0 AND p.net_usd IS NOT NULL
    ),
    tag_base AS (SELECT avg(correct) AS base_rate FROM base),
    trader_stats AS (
        SELECT trader,
               count(DISTINCT condition_id) AS n_markets,
               avg(correct) AS hit_rate,
               median(net_usd) AS avg_edge_usd
        FROM base GROUP BY trader
        HAVING count(DISTINCT condition_id) >= 10
           AND count(*) < 10000
    )
    SELECT ts.trader, ts.n_markets, ts.hit_rate, ts.avg_edge_usd,
           (ts.hit_rate - tb.base_rate) AS excess_hr
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0
    """)

    row = d.fetchone(f"SELECT count() AS n FROM _esg_gtrain_{game_lower}")
    return row["n"] if row else 0


def _build_full_esports_pool(d, k: int, direction: str, train_cutoff: str) -> set[str]:
    """Build v3 composite pool for ALL Esports, return top-K traders."""
    if direction == "YES":
        pos_filter = "p.position = 'YES'"
        correct_expr = "CAST(p.yes_won AS DOUBLE)"
    else:
        pos_filter = "p.position = 'NO'"
        correct_expr = "p.correct::DOUBLE"

    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_train AS
    WITH base AS (
        SELECT p.trader, p.condition_id,
               {correct_expr} AS correct, p.volume, p.net_usd
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0 AND p.net_usd IS NOT NULL
    ),
    tag_base AS (SELECT avg(correct) AS base_rate FROM base),
    trader_stats AS (
        SELECT trader,
               count(DISTINCT condition_id) AS n_markets,
               avg(correct) AS hit_rate,
               median(net_usd) AS avg_edge_usd
        FROM base GROUP BY trader
        HAVING count(DISTINCT condition_id) >= 20
           AND count(*) < 10000
    )
    SELECT ts.trader, ts.n_markets, ts.hit_rate, ts.avg_edge_usd,
           (ts.hit_rate - tb.base_rate) AS excess_hr,
           tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0
    """)

    # Consistency
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_consistency AS
    WITH monthly AS (
        SELECT p.trader,
               date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
               avg({correct_expr}) AS month_hr,
               count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN _esg_train t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    )
    SELECT trader, count(*) AS n_months,
        CASE WHEN count(*) >= 2
             THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
             ELSE 0.0 END AS consistency_sharpe
    FROM monthly GROUP BY trader HAVING count(*) >= 3
    """)

    # BEH
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_bucket AS
    WITH price_data AS (
        SELECT p.trader, p.condition_id,
               {correct_expr} AS correct,
               CAST(floor((yed.price_x_vol / yed.volume) * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _esg_train t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) < '{train_cutoff}'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
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
    FROM trader_bucket GROUP BY trader HAVING count(*) >= 2
    """)

    # Composite
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_composite AS
    WITH base AS (
        SELECT t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
               coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
               coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _esg_train t
        LEFT JOIN _esg_consistency c ON t.trader = c.trader
        LEFT JOIN _esg_bucket b ON t.trader = b.trader
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
    SELECT trader, n_markets, excess_hr, consistency_sharpe, avg_edge_usd, bucket_excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) DESC
        ) AS rank
    FROM ranked
    """)

    rows = d.fetchall(f"""
        SELECT trader FROM _esg_composite WHERE rank <= {k} ORDER BY rank
    """)
    pool = {r["trader"].lower() for r in rows}
    return pool


def part2_parameter_robustness(d) -> dict:
    """Part 2: K x N sweep for Esports YES."""
    log("\n" + "=" * 70)
    log("PART 2: PARAMETER ROBUSTNESS (ESPORTS YES)")
    log("=" * 70)

    K_VALUES = [10, 25, 50, 75, 100]
    N_VALUES = [1, 2, 3, 4, 5]

    # Ensure tag markets table exists
    d.execute("""
    CREATE OR REPLACE TABLE _esg_tag_mkts AS
    SELECT DISTINCT m.condition_id
    FROM markets m
    JOIN event_tags et ON m.event_id = et.event_id
    WHERE et.label = 'Esports'
      AND NOT is_gambling_market_v3(m.slug)
    """)

    results = {}
    fold_results = defaultdict(list)

    for fold_idx, (ts, te, xs, xe) in enumerate(FOLDS):
        t_fold = time.time()
        log(f"\n  --- Fold {fold_idx+1}: train=[{ts},{te}) test=[{xs},{xe}) ---")

        # Per-fold market-level base rate
        br_row = d.fetchone(f"""
            SELECT avg(CAST(mr.token_won AS DOUBLE)) AS br
            FROM markets_resolved mr
            JOIN _esg_tag_mkts tm ON mr.condition_id = tm.condition_id
            JOIN (
                SELECT condition_id, first(resolved_at) AS resolved_at
                FROM maker_positions GROUP BY condition_id
            ) p ON mr.condition_id = p.condition_id
            WHERE mr.outcome = 'YES' AND mr.token_won IS NOT NULL
              AND CAST(p.resolved_at AS DATE) >= '{xs}'
              AND CAST(p.resolved_at AS DATE) < '{xe}'
        """)
        base_rate = round(br_row["br"], 4) if br_row and br_row["br"] is not None else 0.5
        log(f"    Market-level YES base rate: {base_rate:.1%}")

        for k in K_VALUES:
            # Build pool for this K
            pool = _build_full_esports_pool_fold(d, k, "YES", ts, te)
            log(f"    K={k}: pool={len(pool)} traders")

            if not pool:
                continue

            vals = ", ".join(f"('{t}')" for t in pool)
            d.execute("DROP TABLE IF EXISTS _esg_sweep_pool")
            d.execute(f"CREATE TEMP TABLE _esg_sweep_pool AS SELECT * FROM (VALUES {vals}) AS t(trader)")

            # Get all entries in one query, then compute signals for each N
            entries = d.fetchall(f"""
                WITH entries AS (
                    SELECT
                        p.condition_id, p.trader, p.yes_won,
                        p.realized_pnl, p.first_trade, p.resolved_at
                    FROM maker_positions p
                    JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
                    JOIN _esg_sweep_pool sp ON p.trader = sp.trader
                    WHERE p.position = 'YES'
                      AND CAST(p.resolved_at AS DATE) >= '{xs}'
                      AND CAST(p.resolved_at AS DATE) < '{xe}'
                      AND CAST(p.first_trade AS DATE) >= '{xs}'
                      AND p.volume > 0
                ),
                ranked AS (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY first_trade) AS entry_rank,
                        COUNT(*) OVER (PARTITION BY condition_id) AS n_traders
                    FROM entries
                )
                SELECT condition_id, n_traders, entry_rank,
                       first_trade, resolved_at, yes_won, realized_pnl
                FROM ranked
            """)

            # Group by condition_id
            market_data = defaultdict(list)
            for e in entries:
                market_data[e["condition_id"]].append(e)

            for n in N_VALUES:
                # Count markets with >= n traders
                n_signals = 0
                n_correct = 0
                pnl_list = []
                hold_h_list = []

                for cid, mdata in market_data.items():
                    max_traders = mdata[0]["n_traders"]
                    if max_traders < n:
                        continue
                    n_signals += 1
                    yes_won = mdata[0]["yes_won"]
                    if yes_won:
                        n_correct += 1

                    # Get Nth trader entry time
                    nth_entries = [e for e in mdata if e["entry_rank"] == n]
                    if nth_entries:
                        signal_time = nth_entries[0]["first_trade"]
                        resolved_at = nth_entries[0]["resolved_at"]
                        if signal_time and resolved_at:
                            try:
                                h = (resolved_at.timestamp() - signal_time.timestamp()) / 3600
                                if h >= 0:
                                    hold_h_list.append(h)
                            except Exception:
                                pass

                    # Median pnl across pool traders
                    pnls = [e["realized_pnl"] for e in mdata if e["realized_pnl"] is not None]
                    if pnls:
                        pnl_list.append(sorted(pnls)[len(pnls) // 2])

                if n_signals == 0:
                    continue

                hr = n_correct / n_signals
                excess = hr - base_rate
                med_pnl = sorted(pnl_list)[len(pnl_list) // 2] if pnl_list else 0
                med_hold = sorted(hold_h_list)[len(hold_h_list) // 2] if hold_h_list else 24

                combo_key = f"K{k}_N{n}"
                fold_results[combo_key].append({
                    "fold": xs,
                    "n_signals": n_signals,
                    "n_correct": n_correct,
                    "hr": round(hr, 4),
                    "base_rate": base_rate,
                    "excess_hr": round(excess, 4),
                    "median_pnl": round(float(med_pnl), 2),
                    "median_hold_h": round(float(med_hold), 1),
                    "pool_size": len(pool),
                })

        fold_elapsed = time.time() - t_fold
        log(f"    Fold done in {fold_elapsed:.1f}s")

    # Aggregate across folds
    summary = []
    for combo_key, folds in fold_results.items():
        if len(folds) < 2:
            continue

        n_folds = len(folds)
        total_signals = sum(f["n_signals"] for f in folds)
        total_correct = sum(f["n_correct"] for f in folds)
        pooled_hr = total_correct / total_signals if total_signals > 0 else 0
        avg_excess = sum(f["excess_hr"] for f in folds) / n_folds
        avg_med_pnl = sum(f["median_pnl"] for f in folds) / n_folds
        avg_hold_h = sum(f["median_hold_h"] for f in folds) / n_folds

        hold_days = avg_hold_h / 24 if avg_hold_h > 0 else 1.0
        cs = round(avg_excess * avg_med_pnl / hold_days, 4) if avg_med_pnl > 0 else 0

        parts = combo_key.split("_")
        k = int(parts[0][1:])
        n = int(parts[1][1:])

        summary.append({
            "combo_key": combo_key,
            "k": k,
            "n": n,
            "n_folds": n_folds,
            "pooled_hr": round(pooled_hr, 4),
            "avg_excess_hr_pp": round(avg_excess * 100, 2),
            "avg_median_pnl": round(avg_med_pnl, 2),
            "avg_hold_hours": round(avg_hold_h, 1),
            "total_signals": total_signals,
            "signals_per_fold": round(total_signals / n_folds, 1),
            "compounding_score": cs,
            "per_fold": folds,
        })

    summary.sort(key=lambda x: x["avg_excess_hr_pp"], reverse=True)

    # Print K x N grid
    log("\n  === K x N GRID: Excess HR (pp) over market-level YES base ===")
    log(f"  {'':>8}" + "".join(f"  N={n:<5}" for n in N_VALUES))
    log("  " + "-" * 50)
    for k in K_VALUES:
        row = f"  K={k:<5}"
        for n in N_VALUES:
            key = f"K{k}_N{n}"
            matches = [s for s in summary if s["combo_key"] == key]
            if matches:
                val = matches[0]["avg_excess_hr_pp"]
                sigs = matches[0]["total_signals"]
                row += f"  {val:>+5.1f}  "
            else:
                row += "    N/A  "
        log(row)

    # Print K x N grid: total signals
    log("\n  === K x N GRID: Total Signals ===")
    log(f"  {'':>8}" + "".join(f"  N={n:<5}" for n in N_VALUES))
    log("  " + "-" * 50)
    for k in K_VALUES:
        row = f"  K={k:<5}"
        for n in N_VALUES:
            key = f"K{k}_N{n}"
            matches = [s for s in summary if s["combo_key"] == key]
            if matches:
                sigs = matches[0]["total_signals"]
                row += f"  {sigs:>5}  "
            else:
                row += "    N/A  "
        log(row)

    # Print K x N grid: compounding score
    log("\n  === K x N GRID: Compounding Score ===")
    log(f"  {'':>8}" + "".join(f"  N={n:<5}" for n in N_VALUES))
    log("  " + "-" * 50)
    for k in K_VALUES:
        row = f"  K={k:<5}"
        for n in N_VALUES:
            key = f"K{k}_N{n}"
            matches = [s for s in summary if s["combo_key"] == key]
            if matches:
                cs_val = matches[0]["compounding_score"]
                row += f"  {cs_val:>5.1f}  "
            else:
                row += "    N/A  "
        log(row)

    # Print top 15
    log(f"\n  === TOP 15 BY EXCESS HR ===")
    log(f"  {'Config':<12} {'Folds':>5} {'Sigs':>6} {'HR':>7} {'Excess':>8} {'medPnL':>8} {'Hold':>6} {'CS':>6}")
    log("  " + "-" * 65)
    for s in summary[:15]:
        log(f"  {s['combo_key']:<12} {s['n_folds']:>5} {s['total_signals']:>6} "
            f"{s['pooled_hr']:>6.1%} {s['avg_excess_hr_pp']:>+7.1f}pp "
            f"${s['avg_median_pnl']:>7.2f} {s['avg_hold_hours']:>5.1f}h {s['compounding_score']:>5.1f}")

    results = {
        "summary": summary,
        "fold_results": {k: v for k, v in fold_results.items()},
    }
    return results


def _build_full_esports_pool_fold(d, k: int, direction: str,
                                   train_start: str, train_end: str) -> set[str]:
    """Build v3 pool for a specific fold training window."""
    if direction == "YES":
        pos_filter = "p.position = 'YES'"
        correct_expr = "CAST(p.yes_won AS DOUBLE)"
    else:
        pos_filter = "p.position = 'NO'"
        correct_expr = "p.correct::DOUBLE"

    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_ftrain AS
    WITH base AS (
        SELECT p.trader, p.condition_id,
               {correct_expr} AS correct, p.volume, p.net_usd
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0 AND p.net_usd IS NOT NULL
    ),
    tag_base AS (SELECT avg(correct) AS base_rate FROM base),
    trader_stats AS (
        SELECT trader,
               count(DISTINCT condition_id) AS n_markets,
               avg(correct) AS hit_rate,
               median(net_usd) AS avg_edge_usd
        FROM base GROUP BY trader
        HAVING count(DISTINCT condition_id) >= 20
           AND count(*) < 10000
    )
    SELECT ts.trader, ts.n_markets, ts.hit_rate, ts.avg_edge_usd,
           (ts.hit_rate - tb.base_rate) AS excess_hr,
           tb.base_rate AS tag_base_rate
    FROM trader_stats ts CROSS JOIN tag_base tb
    WHERE (ts.hit_rate - tb.base_rate) > 0
    """)

    # Consistency
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_fconsistency AS
    WITH monthly AS (
        SELECT p.trader,
               date_trunc('month', CAST(p.resolved_at AS TIMESTAMP)) AS month,
               avg({correct_expr}) AS month_hr,
               count(DISTINCT p.condition_id) AS n_pos
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN _esg_ftrain t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0
        GROUP BY p.trader, date_trunc('month', CAST(p.resolved_at AS TIMESTAMP))
        HAVING count(DISTINCT p.condition_id) >= 5
    )
    SELECT trader, count(*) AS n_months,
        CASE WHEN count(*) >= 2
             THEN avg(month_hr) / (coalesce(stddev(month_hr), 0.0) + 0.05)
             ELSE 0.0 END AS consistency_sharpe
    FROM monthly GROUP BY trader HAVING count(*) >= 3
    """)

    # BEH
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_fbucket AS
    WITH price_data AS (
        SELECT p.trader, p.condition_id,
               {correct_expr} AS correct,
               CAST(floor((yed.price_x_vol / yed.volume) * 10) * 0.10 AS DOUBLE) AS price_bucket
        FROM maker_positions p
        JOIN _esg_tag_mkts tm ON p.condition_id = tm.condition_id
        JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
        JOIN _esg_ftrain t ON p.trader = t.trader
        WHERE {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
          AND p.volume > 0 AND yed.volume > 0
          AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
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
    FROM trader_bucket GROUP BY trader HAVING count(*) >= 2
    """)

    # Composite
    d.execute(f"""
    CREATE OR REPLACE TABLE _esg_fcomposite AS
    WITH base AS (
        SELECT t.trader, t.n_markets, t.excess_hr, t.avg_edge_usd,
               coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe,
               coalesce(b.bucket_excess_hr, 0.0) AS bucket_excess_hr
        FROM _esg_ftrain t
        LEFT JOIN _esg_fconsistency c ON t.trader = c.trader
        LEFT JOIN _esg_fbucket b ON t.trader = b.trader
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
    SELECT trader, n_markets, excess_hr,
        (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) AS composite_score,
        ROW_NUMBER() OVER (ORDER BY
            (0.45 * pr_excess_hr + 0.25 * pr_consistency + 0.15 * pr_edge + 0.15 * pr_bucket) DESC
        ) AS rank
    FROM ranked
    """)

    rows = d.fetchall(f"SELECT trader FROM _esg_fcomposite WHERE rank <= {k} ORDER BY rank")
    return {r["trader"].lower() for r in rows}


def main() -> None:
    t0 = time.time()
    d = db()

    setup_game_mapping(d)
    game_results = part1_game_decomposition(d)
    param_results = part2_parameter_robustness(d)

    # Save all results
    output = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "description": "Esports sub-tag decomposition + parameter robustness",
        },
        "game_decomposition": game_results,
        "parameter_robustness": {
            "summary": param_results["summary"],
        },
    }

    json_path = OUTPUT_DIR / "results.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")
    log(f"Saved: {json_path}")


if __name__ == "__main__":
    main()
