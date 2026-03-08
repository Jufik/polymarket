"""Tag-HR-Copy Sweep — DuckDB port of sweep_r3.py.

Same logic, same folds, same params. Uses Parquet snapshot via DuckDB
instead of CH with FINAL.

Key differences from sweep_r3.py:
- No _tmp_* Memory tables in CH — uses DuckDB temp tables
- No arrayExists — uses pre-aggregated market stats + EXISTS filter
- All 100 combos batched into a single SQL query per fold (vs 200 queries)

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-copy/scripts/sweep_duckdb.py
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-copy/scripts/sweep_duckdb.py --tags Esports
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from research.db import db

TAGS = ["Esports", "Tennis", "1H"]
MT = [10, 20, 30, 50]
EP = [3, 5, 8, 10, 15]
PRICE_CEIL = [0.75, 0.80, 0.85, 0.90, 1.0]
MAX_H = 48

FOLDS = [
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-02-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-05-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]


def _build_combos_values(base: float) -> str:
    """Build VALUES clause for all (min_trades, min_hr, max_ep, combo_key) combos."""
    rows = []
    for mt in MT:
        for ep_t in EP:
            thr = round(base + ep_t / 100.0, 4)
            for pc in PRICE_CEIL:
                key = f"{mt}_{ep_t}_{pc}"
                rows.append(f"('{key}', {mt}, {thr}, {pc})")
    return ", ".join(rows)


def _batch_combo_query(stats_table: str, pairs_table: str, win_col: str, base: float) -> str:
    """Build a single SQL that computes all 100 combos at once.

    Uses pre-aggregated market stats + EXISTS to check if any trader
    in that market meets the combo threshold (matching CH arrayExists semantics).
    """
    values = _build_combos_values(base)
    return f"""
        WITH combos(combo_key, min_trades, min_hr, max_ep) AS (
            VALUES {values}
        )
        SELECT c.combo_key,
               count() as ns,
               sum(CASE WHEN s.{win_col} THEN 1 ELSE 0 END)::INT as nc,
               round(sum(CASE WHEN s.{win_col} THEN 1 ELSE 0 END)::DOUBLE
                     / greatest(count(), 1), 4) as hr,
               round(median(s.median_pnl), 4) as mp,
               round(avg(s.median_pnl), 4) as ap,
               round(median(s.hold_hours), 2) as mh
        FROM combos c, {stats_table} s
        WHERE EXISTS (
            SELECT 1 FROM {pairs_table} p
            WHERE p.condition_id = s.condition_id
              AND p.q_hr >= c.min_hr
              AND p.q_n >= c.min_trades
              AND p.q_avg_ep <= c.max_ep
        )
        GROUP BY c.combo_key
    """


def run_sweep(tags: list[str] | None = None) -> dict:
    d = db()
    tags = tags or TAGS

    results = {
        "hypothesis": "tag-hr-copy",
        "round": "3-duckdb",
        "timestamp": datetime.now().isoformat(),
        "max_hold_hours": MAX_H,
        "cs_formula": "excess_hr * median_pnl_usd / median_hold_days",
        "fix": "first_trade >= test_start: only test-period entries generate signals",
        "verdict": "pending",
        "tags": {},
    }

    for tag in tags:
        t_tag = time.time()
        print(f"\n=== {tag} ===", flush=True)

        # Tag markets
        d.execute("DROP TABLE IF EXISTS tag_mkts")
        d.execute("""
            CREATE TEMP TABLE tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])
        n_mkts = d.fetchone("SELECT count() as n FROM tag_mkts")["n"]
        print(f"  {n_mkts:,} markets in tag", flush=True)

        all_fold_data = []

        for ts, te, xs, xe in FOLDS:
            t_fold = time.time()
            timings: dict[str, float] = {}

            # Base rate
            t0 = time.time()
            br = d.fetchone(f"""
                SELECT
                    sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT as yw,
                    count() as tot
                FROM (
                    SELECT condition_id, first(yes_won) as yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{ts}'
                      AND CAST(resolved_at AS DATE) < '{te}'
                    GROUP BY condition_id
                )
            """)
            timings["base_rate"] = time.time() - t0

            yw, tot = br["yw"], br["tot"]
            if tot < 20:
                print(f"  {xs}: SKIP {tot}", flush=True)
                continue
            base = round(yw / tot, 4)

            # Entry price — uses pre-joined yes_entry_data (condition_id sorted, 1.3 GB)
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS entry_price")
            d.execute(f"""
                CREATE TEMP TABLE entry_price AS
                SELECT trader, sum(price_x_vol) / sum(volume) AS avg_ep
                FROM yes_entry_data
                WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND CAST(first_trade AS DATE) >= '{ts}'
                  AND CAST(first_trade AS DATE) < '{te}'
                GROUP BY trader
            """)
            timings["entry_price"] = time.time() - t0

            # Qualified BUY traders
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS qual_buy")
            d.execute(f"""
                CREATE TEMP TABLE qual_buy AS
                SELECT p.trader, count() AS n,
                       sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS hr,
                       coalesce(first(ep.avg_ep), 1.0) AS avg_ep
                FROM maker_positions p
                LEFT JOIN entry_price ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{ts}'
                  AND CAST(p.resolved_at AS DATE) < '{te}'
                GROUP BY p.trader HAVING n >= 5
            """)
            timings["qual_buy"] = time.time() - t0

            # Qualified DIR traders
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS qual_dir")
            d.execute(f"""
                CREATE TEMP TABLE qual_dir AS
                SELECT p.trader, count() AS n,
                       sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS hr,
                       coalesce(first(ep.avg_ep), 1.0) AS avg_ep
                FROM maker_positions p
                LEFT JOIN entry_price ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND CAST(p.resolved_at AS DATE) >= '{ts}'
                  AND CAST(p.resolved_at AS DATE) < '{te}'
                GROUP BY p.trader HAVING n >= 5
            """)
            timings["qual_dir"] = time.time() - t0

            # Market-trader pairs (BUY)
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS mkt_buy_pairs")
            d.execute(f"""
                CREATE TEMP TABLE mkt_buy_pairs AS
                SELECT t.condition_id, t.yes_won::BOOLEAN as yes_won,
                       t.realized_pnl, t.first_trade, t.resolved_at,
                       q.hr AS q_hr, q.n AS q_n, q.avg_ep AS q_avg_ep
                FROM maker_positions t
                JOIN qual_buy q ON t.trader = q.trader
                WHERE t.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND t.position = 'YES'
                  AND CAST(t.resolved_at AS DATE) >= '{xs}'
                  AND CAST(t.resolved_at AS DATE) < '{xe}'
                  AND CAST(t.first_trade AS DATE) >= '{xs}'
            """)
            timings["pairs_buy"] = time.time() - t0

            # Market-trader pairs (DIR)
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS mkt_dir_pairs")
            d.execute(f"""
                CREATE TEMP TABLE mkt_dir_pairs AS
                SELECT t.condition_id, (t.correct = 1) as correct_dir,
                       t.realized_pnl, t.first_trade, t.resolved_at,
                       q.hr AS q_hr, q.n AS q_n, q.avg_ep AS q_avg_ep
                FROM maker_positions t
                JOIN qual_dir q ON t.trader = q.trader
                WHERE t.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND CAST(t.resolved_at AS DATE) >= '{xs}'
                  AND CAST(t.resolved_at AS DATE) < '{xe}'
                  AND CAST(t.first_trade AS DATE) >= '{xs}'
            """)
            timings["pairs_dir"] = time.time() - t0

            # Pre-aggregate market-level stats (same for ALL combos)
            # This matches the original CH semantics: market stats are computed
            # across ALL qualified traders, then combos filter which markets qualify.
            t0 = time.time()
            d.execute("DROP TABLE IF EXISTS mkt_buy_stats")
            d.execute(f"""
                CREATE TEMP TABLE mkt_buy_stats AS
                SELECT condition_id,
                       first(yes_won) as yes_won,
                       date_diff('hour', max(first_trade), first(resolved_at)) as hold_hours,
                       median(realized_pnl) as median_pnl
                FROM mkt_buy_pairs
                GROUP BY condition_id
                HAVING hold_hours >= 0 AND hold_hours <= {MAX_H}
            """)
            d.execute("DROP TABLE IF EXISTS mkt_dir_stats")
            d.execute(f"""
                CREATE TEMP TABLE mkt_dir_stats AS
                SELECT condition_id,
                       first(correct_dir) as correct_dir,
                       date_diff('hour', max(first_trade), first(resolved_at)) as hold_hours,
                       median(realized_pnl) as median_pnl
                FROM mkt_dir_pairs
                GROUP BY condition_id
                HAVING hold_hours >= 0 AND hold_hours <= {MAX_H}
            """)
            timings["market_stats"] = time.time() - t0

            # All 100 combos in ONE query each for buy and dir
            t0 = time.time()
            buy_sql = _batch_combo_query("mkt_buy_stats", "mkt_buy_pairs", "yes_won", base)
            dir_sql = _batch_combo_query("mkt_dir_stats", "mkt_dir_pairs", "correct_dir", base)
            buy_rows = d.fetchall(buy_sql)
            dir_rows = d.fetchall(dir_sql)
            timings["combos"] = time.time() - t0

            # Build combos dict
            buy_by_key = {r["combo_key"]: r for r in buy_rows}
            dir_by_key = {r["combo_key"]: r for r in dir_rows}

            agg_keys = ["ns", "nc", "hr", "mp", "ap", "mh"]
            combos = {}
            for mt in MT:
                for ep_t in EP:
                    for pc in PRICE_CEIL:
                        key = f"{mt}_{ep_t}_{pc}"
                        rb = buy_by_key.get(key)
                        rd = dir_by_key.get(key)
                        combos[key] = {
                            "buy": [0 if rb is None or rb.get(k) is None else rb[k] for k in agg_keys],
                            "dir": [0 if rd is None or rd.get(k) is None else rd[k] for k in agg_keys],
                            "base": base,
                        }

            fold_elapsed = time.time() - t_fold
            all_fold_data.append(
                {"base": base, "n": tot, "fold_test": xs, "combos": combos}
            )
            timing_str = " | ".join(f"{k}={v:.1f}s" for k, v in timings.items())
            print(
                f"  fold {xs}: base={base:.4f} n={tot}  "
                f"{len(combos)} combos, {fold_elapsed:.1f}s  [{timing_str}]",
                flush=True,
            )

        # Aggregate across folds (identical to sweep_r3.py)
        def agg(folds: list, vk: str) -> list:
            cf: dict[str, list] = defaultdict(list)
            for fd in folds:
                for ck, dat in fd["combos"].items():
                    row = dat[vk]
                    ns, nc, hr, mp, ap, mh = [
                        float(x) if x is not None else 0 for x in row
                    ]
                    if ns < 5:
                        continue
                    cf[ck].append({
                        "ns": ns, "hr": hr, "br": float(fd["base"]),
                        "exc": hr - float(fd["base"]),
                        "mp": mp, "ap": ap, "mh": mh, "fold": fd["fold_test"],
                    })
            out = []
            for ck, fs in cf.items():
                if len(fs) < 2:
                    continue
                parts = ck.split("_")
                mt2, ep2, pc2 = int(parts[0]), int(parts[1]), float(parts[2])
                n = len(fs)
                ah = sum(f["hr"] for f in fs) / n
                ae = sum(f["exc"] for f in fs) / n
                amp = sum(f["mp"] for f in fs) / n
                aap = sum(f["ap"] for f in fs) / n
                ah2 = sum(f["mh"] for f in fs) / n
                ts2 = int(sum(f["ns"] for f in fs))
                hd = ah2 / 24 if ah2 > 0 else 1.0
                cs = round(ae * amp / hd, 4) if amp > 0 else 0
                out.append({
                    "params": {
                        "min_trades": mt2,
                        "excess_hr_pp": ep2,
                        "max_avg_entry_price": pc2,
                    },
                    "n_folds": n, "avg_hr": round(ah, 4),
                    "avg_excess_hr_pp": round(ae * 100, 2),
                    "median_pnl_usd": round(amp, 2),
                    "avg_pnl_usd": round(aap, 2),
                    "avg_hold_hours": round(ah2, 2),
                    "total_signals": ts2, "signals_per_fold": round(ts2 / n, 1),
                    "compounding_score": cs,
                })
            out.sort(key=lambda x: x["compounding_score"], reverse=True)
            return out[:15]

        tb = agg(all_fold_data, "buy")
        td = agg(all_fold_data, "dir")
        tag_elapsed = time.time() - t_tag
        results["tags"][tag] = {
            "buy_only_top15": tb,
            "directional_top15": td,
            "n_folds": len(all_fold_data),
            "fold_base_rates": [
                {"fold": fd["fold_test"], "base": fd["base"], "n": fd["n"]}
                for fd in all_fold_data
            ],
        }

        print(f"  BUY best: {tb[0] if tb else 'NONE'}", flush=True)
        print(f"  DIR best: {td[0] if td else 'NONE'}", flush=True)
        print(f"  Tag total: {tag_elapsed:.1f}s", flush=True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tags", nargs="+", help="Override tag list")
    args = parser.parse_args()

    t0 = time.time()
    results = run_sweep(args.tags)
    elapsed = time.time() - t0

    out_path = Path(
        "research/hypotheses/tag-hr-copy/discovery/results_duckdb.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDone in {elapsed:.0f}s -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
