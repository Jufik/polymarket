"""Task #12: Sharp pool + Tracks 3 & 4 — DuckDB vectorized exploration.

Three analyses:

1. SHARP POOL (top-K): Instead of threshold-based pool qualification (excess_hr >= X%),
   select the top-K traders per tag/fold by excess_hr. K = {10, 20, 30, 50}.
   This caps pool size to prevent the pool explosion problem (Esports 2026-01: 536-774 traders).

2. TRACK 4: Signal-time volume (causal).
   Volume from the first N qualified traders only (those with first_trade <= Nth entry time).
   Compare YES HR by signal-time volume bucket. Tests whether the +45pp volume uplift
   (found in discovery) survives when computed causally.

3. TRACK 3: Dissent filter.
   Markets where qualified traders split YES/NO. Compute dissent_ratio = n_qual_yes / total_qual.
   Compare YES HR by dissent bucket. Tests whether YES consensus is weaker when NO traders present.

All analyses: Esports + Tennis, 3 folds, BUY-only positions.
Results written to: research/hypotheses/tag-hr-consensus/exploration/results_sharp_pool.json

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/exploration/sharp_pool_and_tracks.py
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
LOG_PATH = Path("tmp/exploration_sharp_pool.log")

FOLDS = [
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

TAGS = ["Esports", "Tennis"]
TOP_K_VALUES = [10, 20, 30, 50]
CONSENSUS_N = 3  # use N=3 for all sharp pool analyses
MIN_TRADES = 5
BOT_GUARD = 10_000


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Analysis 1: Sharp Pool (top-K by excess_hr)
# ---------------------------------------------------------------------------

def run_sharp_pool_analysis(d) -> dict:
    """For each tag/fold/K, select top-K traders by excess_hr, then count
    markets where N=CONSENSUS_N of those traders converge YES. Compare HR.
    """
    log("\n=== ANALYSIS 1: SHARP POOL (top-K) ===")
    results = {}

    for tag in TAGS:
        log(f"\n--- {tag} ---")
        d.execute("DROP TABLE IF EXISTS _exp_tag_mkts")
        d.execute("""
            CREATE TEMP TABLE _exp_tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])

        tag_results = []

        for train_start, train_end, test_start, test_end in FOLDS:
            log(f"  fold {test_start}")

            # Training-window base rate
            r = d.fetchone(f"""
                SELECT
                    sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate,
                    count() AS n
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{train_start}'
                      AND CAST(resolved_at AS DATE) < '{train_end}'
                    GROUP BY condition_id
                )
            """)
            train_base = r["base_rate"] or 0.5

            # Test-window base rate
            r = d.fetchone(f"""
                SELECT
                    sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate,
                    count() AS n
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{test_start}'
                      AND CAST(resolved_at AS DATE) < '{test_end}'
                    GROUP BY condition_id
                )
            """)
            test_base = r["base_rate"] or 0.5
            n_test = r["n"] or 0

            if n_test < 5:
                log(f"    SKIP: only {n_test} test markets")
                continue

            # Build full ranked pool (training window)
            # Rank by excess_hr descending; top-K is just a LIMIT in the final query
            d.execute("DROP TABLE IF EXISTS _exp_ranked_pool")
            d.execute(f"""
                CREATE TEMP TABLE _exp_ranked_pool AS
                SELECT
                    p.trader,
                    count() AS n_mkts,
                    sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
                    sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} AS excess_hr,
                    ROW_NUMBER() OVER (ORDER BY excess_hr DESC) AS rank_hr
                FROM maker_positions p
                WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{train_start}'
                  AND CAST(p.resolved_at AS DATE) < '{train_end}'
                GROUP BY p.trader
                HAVING n_mkts >= {MIN_TRADES}
                  AND n_mkts < {BOT_GUARD}
                  AND raw_hr < 0.99
                  AND excess_hr > 0
            """)

            r = d.fetchone("SELECT count() AS n FROM _exp_ranked_pool")
            pool_total = r["n"]
            log(f"    pool_total={pool_total}, train_base={train_base:.3f}, test_base={test_base:.3f}")

            fold_k_results = []
            for k in TOP_K_VALUES:
                if k > pool_total:
                    log(f"    K={k}: SKIP (pool_total={pool_total} < K)")
                    fold_k_results.append({
                        "k": k, "skip": True, "reason": f"pool_total={pool_total} < K"
                    })
                    continue

                # Markets where top-K traders have N=CONSENSUS_N YES entries in test window
                r2 = d.fetchone(f"""
                    WITH top_k AS (
                        SELECT trader FROM _exp_ranked_pool WHERE rank_hr <= {k}
                    ),
                    consensus_mkts AS (
                        SELECT
                            p.condition_id,
                            first(p.yes_won) AS yes_won,
                            count(DISTINCT p.trader) AS n_qual
                        FROM maker_positions p
                        INNER JOIN top_k q ON p.trader = q.trader
                        WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                          AND p.position = 'YES'
                          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                          AND CAST(p.resolved_at AS DATE) < '{test_end}'
                          AND CAST(p.first_trade AS DATE) >= '{test_start}'
                        GROUP BY p.condition_id
                        HAVING n_qual >= {CONSENSUS_N}
                    )
                    SELECT
                        count() AS n_signals,
                        sum(yes_won::INT) AS n_wins,
                        sum(yes_won::INT)::DOUBLE / count() AS hr,
                        sum(yes_won::INT)::DOUBLE / count() - {test_base} AS excess_hr
                    FROM consensus_mkts
                """)

                n_sigs = r2["n_signals"] or 0
                n_wins = r2["n_wins"] or 0
                hr = r2["hr"]
                excess = r2["excess_hr"]

                log(
                    f"    K={k}: n_signals={n_sigs} HR={hr:.1%} excess={excess:+.1f}pp"
                    if hr is not None else f"    K={k}: n_signals={n_sigs} (no HR)"
                )

                fold_k_results.append({
                    "k": k,
                    "n_signals": n_sigs,
                    "n_wins": n_wins,
                    "hit_rate": round(hr, 4) if hr is not None else None,
                    "excess_hr_pp": round(excess * 100, 2) if excess is not None else None,
                })

            tag_results.append({
                "fold": test_start,
                "train_base_rate": round(train_base, 4),
                "test_base_rate": round(test_base, 4),
                "pool_total": pool_total,
                "k_results": fold_k_results,
            })

        results[tag] = tag_results

    return results


# ---------------------------------------------------------------------------
# Analysis 2: Track 4 — Signal-time volume (causal)
# ---------------------------------------------------------------------------

def run_signal_time_volume_analysis(d) -> dict:
    """Compute signal-time volume = sum(net_usd) over first N traders only.

    Signal-time = max(first_trade) of the Nth qualified trader.
    Only volume from traders with first_trade <= signal_time is counted.
    """
    log("\n=== ANALYSIS 2: TRACK 4 — Signal-time volume (causal) ===")
    results = {}

    CONSENSUS_N_FOR_VOL = 3
    MEH_THRESH = 0.10   # use 10pp excess as baseline pool
    MPE_THRESH = 0.80   # max pool entry price

    for tag in TAGS:
        log(f"\n--- {tag} ---")
        d.execute("DROP TABLE IF EXISTS _exp_tag_mkts")
        d.execute("""
            CREATE TEMP TABLE _exp_tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])

        tag_results = []

        for train_start, train_end, test_start, test_end in FOLDS:
            log(f"  fold {test_start}")

            # Base rates
            r = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{train_start}'
                      AND CAST(resolved_at AS DATE) < '{train_end}'
                    GROUP BY condition_id
                )
            """)
            train_base = r["base_rate"] or 0.5

            r = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate,
                       count() AS n
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{test_start}'
                      AND CAST(resolved_at AS DATE) < '{test_end}'
                    GROUP BY condition_id
                )
            """)
            test_base = r["base_rate"] or 0.5
            n_test = r["n"] or 0
            if n_test < 5:
                log(f"    SKIP: n_test={n_test}")
                continue

            # Build pool (training window, mpe INNER JOIN)
            d.execute("DROP TABLE IF EXISTS _exp_ep_tmp")
            d.execute(f"""
                CREATE TEMP TABLE _exp_ep_tmp AS
                SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
                FROM yes_entry_data y
                WHERE y.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                  AND CAST(y.first_trade AS DATE) >= '{train_start}'
                  AND CAST(y.first_trade AS DATE) < '{train_end}'
                GROUP BY y.trader
            """)

            d.execute("DROP TABLE IF EXISTS _exp_pool")
            d.execute(f"""
                CREATE TEMP TABLE _exp_pool AS
                SELECT p.trader
                FROM maker_positions p
                INNER JOIN _exp_ep_tmp ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{train_start}'
                  AND CAST(p.resolved_at AS DATE) < '{train_end}'
                GROUP BY p.trader
                HAVING count() >= {MIN_TRADES}
                  AND count() < {BOT_GUARD}
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} >= {MEH_THRESH}
                  AND first(ep.avg_ep) <= {MPE_THRESH}
            """)

            r = d.fetchone("SELECT count() AS n FROM _exp_pool")
            pool_size = r["n"]
            log(f"    pool={pool_size}")

            if pool_size < CONSENSUS_N_FOR_VOL:
                log(f"    SKIP: pool_size={pool_size} < N={CONSENSUS_N_FOR_VOL}")
                tag_results.append({"fold": test_start, "skip": True})
                continue

            # Compute signal-time volume per consensus market
            # Signal-time = max(first_trade) of the Nth qualified trader
            # Volume = sum(net_usd) of traders with first_trade <= signal_time
            vol_results = d.fetchall(f"""
                WITH qual_test_positions AS (
                    SELECT
                        p.condition_id,
                        p.trader,
                        p.first_trade,
                        p.net_usd,
                        first(p.yes_won) OVER (PARTITION BY p.condition_id) AS yes_won,
                        ROW_NUMBER() OVER (
                            PARTITION BY p.condition_id
                            ORDER BY p.first_trade
                        ) AS entry_rank
                    FROM maker_positions p
                    INNER JOIN _exp_pool q ON p.trader = q.trader
                    WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND p.position = 'YES'
                      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                      AND CAST(p.resolved_at AS DATE) < '{test_end}'
                      AND CAST(p.first_trade AS DATE) >= '{test_start}'
                ),
                signal_points AS (
                    -- The Nth trader's entry time = signal_time
                    SELECT
                        condition_id,
                        max(first_trade) AS signal_time,
                        first(yes_won) AS yes_won,
                        count(DISTINCT trader) AS n_qual
                    FROM qual_test_positions
                    WHERE entry_rank <= {CONSENSUS_N_FOR_VOL}
                    GROUP BY condition_id
                    HAVING n_qual >= {CONSENSUS_N_FOR_VOL}
                ),
                signal_vol AS (
                    -- Volume from ALL qualified traders who entered at or before signal_time
                    SELECT
                        sp.condition_id,
                        sp.yes_won,
                        sp.signal_time,
                        sum(abs(qtp.net_usd)) AS signal_time_vol
                    FROM signal_points sp
                    JOIN qual_test_positions qtp ON sp.condition_id = qtp.condition_id
                      AND qtp.first_trade <= sp.signal_time
                    GROUP BY sp.condition_id, sp.yes_won, sp.signal_time
                )
                SELECT
                    CASE
                        WHEN signal_time_vol < 50   THEN 'micro (<$50)'
                        WHEN signal_time_vol < 200  THEN 'small ($50-200)'
                        WHEN signal_time_vol < 500  THEN 'medium ($200-500)'
                        WHEN signal_time_vol < 1000 THEN 'large ($500-1k)'
                        ELSE 'xlarge (>$1k)'
                    END AS vol_bucket,
                    count() AS n_markets,
                    sum(yes_won::INT)::DOUBLE / count() AS hr,
                    sum(yes_won::INT)::DOUBLE / count() - {test_base} AS excess_hr,
                    median(signal_time_vol) AS median_vol
                FROM signal_vol
                GROUP BY 1
                ORDER BY median(signal_time_vol)
            """)

            log(f"    signal-time vol results: {len(vol_results)} buckets")
            for row in vol_results:
                log(f"      {row['vol_bucket']}: n={row['n_markets']} HR={row['hr']:.1%} excess={row['excess_hr']:+.1f}pp")

            tag_results.append({
                "fold": test_start,
                "train_base_rate": round(train_base, 4),
                "test_base_rate": round(test_base, 4),
                "pool_size": pool_size,
                "vol_buckets": [
                    {
                        "bucket": r["vol_bucket"],
                        "n_markets": r["n_markets"],
                        "hit_rate": round(r["hr"], 4) if r["hr"] is not None else None,
                        "excess_hr_pp": round(r["excess_hr"] * 100, 2) if r["excess_hr"] is not None else None,
                        "median_vol": round(r["median_vol"], 2) if r["median_vol"] is not None else None,
                    }
                    for r in vol_results
                ],
            })

        results[tag] = tag_results

    return results


# ---------------------------------------------------------------------------
# Analysis 3: Track 3 — Dissent filter
# ---------------------------------------------------------------------------

def run_dissent_analysis(d) -> dict:
    """Check whether YES consensus HR drops when qualified traders also hold NO positions.

    Dissent ratio = n_qual_yes / (n_qual_yes + n_qual_no) per market.
    Compare YES HR by dissent bucket.
    """
    log("\n=== ANALYSIS 3: TRACK 3 — Dissent filter ===")
    results = {}

    CONSENSUS_N_FOR_DISSENT = 3
    MEH_THRESH = 0.10
    MPE_THRESH = 0.80

    for tag in TAGS:
        log(f"\n--- {tag} ---")
        d.execute("DROP TABLE IF EXISTS _exp_tag_mkts")
        d.execute("""
            CREATE TEMP TABLE _exp_tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])

        tag_results = []

        for train_start, train_end, test_start, test_end in FOLDS:
            log(f"  fold {test_start}")

            # Base rates
            r = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{train_start}'
                      AND CAST(resolved_at AS DATE) < '{train_end}'
                    GROUP BY condition_id
                )
            """)
            train_base = r["base_rate"] or 0.5

            r = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base_rate,
                       count() AS n
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{test_start}'
                      AND CAST(resolved_at AS DATE) < '{test_end}'
                    GROUP BY condition_id
                )
            """)
            test_base = r["base_rate"] or 0.5
            n_test = r["n"] or 0
            if n_test < 5:
                log(f"    SKIP: n_test={n_test}")
                continue

            # Build pool (training, mpe inner join)
            d.execute("DROP TABLE IF EXISTS _exp_ep_tmp")
            d.execute(f"""
                CREATE TEMP TABLE _exp_ep_tmp AS
                SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
                FROM yes_entry_data y
                WHERE y.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                  AND CAST(y.first_trade AS DATE) >= '{train_start}'
                  AND CAST(y.first_trade AS DATE) < '{train_end}'
                GROUP BY y.trader
            """)

            d.execute("DROP TABLE IF EXISTS _exp_pool")
            d.execute(f"""
                CREATE TEMP TABLE _exp_pool AS
                SELECT p.trader
                FROM maker_positions p
                INNER JOIN _exp_ep_tmp ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{train_start}'
                  AND CAST(p.resolved_at AS DATE) < '{train_end}'
                GROUP BY p.trader
                HAVING count() >= {MIN_TRADES}
                  AND count() < {BOT_GUARD}
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} >= {MEH_THRESH}
                  AND first(ep.avg_ep) <= {MPE_THRESH}
            """)

            r = d.fetchone("SELECT count() AS n FROM _exp_pool")
            pool_size = r["n"]
            log(f"    pool={pool_size}")

            if pool_size < CONSENSUS_N_FOR_DISSENT:
                log(f"    SKIP: pool={pool_size} < N={CONSENSUS_N_FOR_DISSENT}")
                tag_results.append({"fold": test_start, "skip": True})
                continue

            # Dissent analysis: YES vs NO qual traders per market
            dissent_results = d.fetchall(f"""
                WITH qual_positions AS (
                    SELECT
                        p.condition_id,
                        p.trader,
                        p.position,
                        first(p.yes_won) OVER (PARTITION BY p.condition_id) AS yes_won
                    FROM maker_positions p
                    INNER JOIN _exp_pool q ON p.trader = q.trader
                    WHERE p.condition_id IN (SELECT condition_id FROM _exp_tag_mkts)
                      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
                      AND CAST(p.resolved_at AS DATE) < '{test_end}'
                      AND CAST(p.first_trade AS DATE) >= '{test_start}'
                ),
                mkt_dissent AS (
                    SELECT
                        condition_id,
                        first(yes_won) AS yes_won,
                        sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END) AS n_yes,
                        sum(CASE WHEN position = 'NO' THEN 1 ELSE 0 END) AS n_no,
                        sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END)::DOUBLE /
                            (sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END) +
                             sum(CASE WHEN position = 'NO' THEN 1 ELSE 0 END)) AS dissent_ratio
                    FROM qual_positions
                    GROUP BY condition_id
                    HAVING n_yes >= {CONSENSUS_N_FOR_DISSENT}
                )
                SELECT
                    CASE
                        WHEN dissent_ratio >= 1.0   THEN 'pure YES (1.0)'
                        WHEN dissent_ratio >= 0.90  THEN 'strong (0.90-1.0)'
                        WHEN dissent_ratio >= 0.70  THEN 'moderate (0.70-0.90)'
                        ELSE 'split (<0.70)'
                    END AS dissent_bucket,
                    count() AS n_markets,
                    sum(yes_won::INT)::DOUBLE / count() AS hr,
                    sum(yes_won::INT)::DOUBLE / count() - {test_base} AS excess_hr,
                    median(dissent_ratio) AS median_dissent
                FROM mkt_dissent
                GROUP BY 1
                ORDER BY median(dissent_ratio) DESC
            """)

            log(f"    dissent results: {len(dissent_results)} buckets")
            for row in dissent_results:
                log(
                    f"      {row['dissent_bucket']}: n={row['n_markets']} "
                    f"HR={row['hr']:.1%} excess={row['excess_hr']:+.1f}pp"
                )

            tag_results.append({
                "fold": test_start,
                "train_base_rate": round(train_base, 4),
                "test_base_rate": round(test_base, 4),
                "pool_size": pool_size,
                "dissent_buckets": [
                    {
                        "bucket": r["dissent_bucket"],
                        "n_markets": r["n_markets"],
                        "hit_rate": round(r["hr"], 4) if r["hr"] is not None else None,
                        "excess_hr_pp": round(r["excess_hr"] * 100, 2) if r["excess_hr"] is not None else None,
                        "median_dissent": round(r["median_dissent"], 3) if r["median_dissent"] is not None else None,
                    }
                    for r in dissent_results
                ],
            })

        results[tag] = tag_results

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== Task #12: Sharp pool + Tracks 3-4 DuckDB exploration ===")
    t0 = time.time()

    d = db()

    sharp_pool_results = run_sharp_pool_analysis(d)
    signal_vol_results = run_signal_time_volume_analysis(d)
    dissent_results = run_dissent_analysis(d)

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    output = {
        "timestamp": datetime.now().isoformat(),
        "consensus_n": CONSENSUS_N,
        "sharp_pool": {
            "description": "Top-K traders by excess_hr (training window) — vectorized HR in test window",
            "k_values": TOP_K_VALUES,
            "consensus_n": CONSENSUS_N,
            "results_by_tag": sharp_pool_results,
        },
        "signal_time_volume": {
            "description": "Signal-time volume (causal: only vol from first N traders). Track 4.",
            "consensus_n": CONSENSUS_N,
            "meh_thresh_pp": 10,
            "mpe_thresh": 0.80,
            "results_by_tag": signal_vol_results,
        },
        "dissent_filter": {
            "description": "Dissent ratio = n_qual_yes / (n_qual_yes + n_qual_no). Track 3.",
            "consensus_n": CONSENSUS_N,
            "meh_thresh_pp": 10,
            "mpe_thresh": 0.80,
            "results_by_tag": dissent_results,
        },
    }

    out_path = OUTPUT_DIR / "results_sharp_pool.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log(f"Written: {out_path}")


if __name__ == "__main__":
    main()
