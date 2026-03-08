"""
tag-hr-consensus DuckDB Sweep — Round 3

Signal: Fire entry when N distinct qualified traders (high-HR within tag) have
entered YES positions in the SAME market.
Entry fires at max(first_trade) across the N consensus traders.
Hold to resolved_at.

Qualification criteria per fold (training window):
1. Tag match
2. Min positions >= MIN_TRADES (20) in tag during training
3. Raw HR - tag_base_rate >= min_excess_hr  [SWEPT: 0.05, 0.10, 0.15, 0.20]
4. Avg YES entry price <= max_pool_entry_price  [SWEPT: 0.70, 0.80, 0.90]
5. Raw HR < 0.99 (hard bot guard)
6. Position count < BOT_GUARD (10,000) in training window

Sweep dimensions:
- consensus_n: {2, 3, 4, 5}
- min_excess_hr (pool filter): {0.05, 0.10, 0.15, 0.20}
- max_pool_entry_price (pool filter): {0.70, 0.80, 0.90}
- price_ceil (signal filter): {0.75, 1.00}   [reduced for runtime]

Walk-forward: 5 folds, 6-month train, 1-month test.
Per fold: ONE qual table per (min_excess_hr, max_pool_entry_price) pair.
Batch: all (consensus_n × price_ceil) combos in one SQL per qual variant.

UPPER BOUNDS — vectorized.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/scripts/sweep_duckdb.py
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/scripts/sweep_duckdb.py --tags Esports
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

# Signal sweep parameters
CONSENSUS_N = [2, 3, 4, 5]
PRICE_CEIL = [0.75, 1.00]              # signal-level: max avg consensus entry price (reduced)

# Pool qualification parameters — all swept
MIN_EXCESS_HR = [0.05, 0.10, 0.15, 0.20]   # min raw_hr - base_rate for pool membership
MAX_POOL_EP = [0.70, 0.80, 0.90]            # max avg entry price for pool membership
MIN_TRADES = 20                              # min positions in tag during training
BOT_GUARD = 10_000                           # exclude traders with >= this many positions

MAX_HOLD_HOURS = 48

# Walk-forward folds: (train_start, train_end, test_start, test_end)
FOLDS = [
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-02-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-05-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]


def _combo_key(n: int, pc: float, meh: float, mpe: float) -> str:
    """Key: {n}_{pc}_{min_excess_hr_pp}_{mpe}  — e.g. '3_0.75_10_0.8'"""
    meh_pp = int(round(meh * 100))
    return f"{n}_{pc}_{meh_pp}_{mpe}"


def _batch_combo_query(mkt_stats_table: str, win_col: str) -> str:
    """Compute all (N × price_ceil) combos over pre-aggregated market stats.

    mkt_stats_table has one row per (condition_id, meh_thresh, mpe_thresh):
        condition_id, yes_won, n_traders, consensus_window_hours, hold_hours,
        median_pnl, avg_ep, meh_thresh, mpe_thresh

    For each combo:
    - n_traders >= consensus_n
    - avg_ep <= price_ceil  (signal-level)
    - hold_hours in [0, MAX_HOLD_HOURS]
    - meh_thresh and mpe_thresh identify the pool variant
    """
    n_pc_rows = []
    for n in CONSENSUS_N:
        for pc in PRICE_CEIL:
            n_pc_rows.append(f"({n}, {pc})")
    n_pc_values = ", ".join(n_pc_rows)

    return f"""
        WITH np_combos(consensus_n, price_ceil) AS (
            VALUES {n_pc_values}
        )
        SELECT
            concat(
                c.consensus_n::VARCHAR, '_',
                c.price_ceil::VARCHAR, '_',
                (s.meh_thresh * 100)::INT::VARCHAR, '_',
                s.mpe_thresh::VARCHAR
            ) AS combo_key,
            count() AS ns,
            sum(CASE WHEN s.{win_col} THEN 1 ELSE 0 END)::INT AS nc,
            round(
                sum(CASE WHEN s.{win_col} THEN 1 ELSE 0 END)::DOUBLE / greatest(count(), 1),
                4
            ) AS hr,
            round(median(s.median_pnl), 4) AS mp,
            round(avg(s.median_pnl), 4) AS ap,
            round(median(s.hold_hours), 2) AS mh
        FROM np_combos c
        JOIN {mkt_stats_table} s
            ON s.n_traders >= c.consensus_n
            AND s.avg_ep <= c.price_ceil
        WHERE s.hold_hours >= 0 AND s.hold_hours <= {MAX_HOLD_HOURS}
        GROUP BY combo_key
    """


def _build_qual_table(d, ts: str, te: str, base: float,
                      meh_thresh: float, mpe_thresh: float, variant: str) -> int:
    """Build qualified trader table for one (meh_thresh, mpe_thresh) combination.

    Returns number of qualified traders. Table name: qual_{variant}.
    variant: 'buy' or 'dir'

    Pool qualification:
    - min_trades >= MIN_TRADES
    - raw_hr - base >= meh_thresh  (min_excess_hr)
    - avg_entry_price <= mpe_thresh  (max_pool_entry_price)
    - raw_hr < 0.99  (hard bot guard)
    - position_count < BOT_GUARD

    Two-step to avoid DuckDB GROUP BY conflict with LEFT JOIN:
      1. Pre-compute per-trader avg entry price.
      2. Join into HR aggregation.
    """
    pos_filter = "AND p.position = 'YES'" if variant == "buy" else ""

    # Step 1: per-trader avg entry price in training window
    d.execute("DROP TABLE IF EXISTS _qf_ep_tmp")
    d.execute(f"""
        CREATE TEMP TABLE _qf_ep_tmp AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{ts}'
          AND CAST(y.first_trade AS DATE) < '{te}'
        GROUP BY y.trader
    """)

    # Step 2: qualify traders
    d.execute(f"DROP TABLE IF EXISTS qual_{variant}")
    d.execute(f"""
        CREATE TEMP TABLE qual_{variant} AS
        SELECT
            p.trader,
            sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
            sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {base} AS excess_hr,
            coalesce(first(ep.avg_ep), 0.75) AS avg_pool_ep
        FROM maker_positions p
        LEFT JOIN _qf_ep_tmp ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
          {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{ts}'
          AND CAST(p.resolved_at AS DATE) < '{te}'
        GROUP BY p.trader
        HAVING count() >= {MIN_TRADES}
          AND count() < {BOT_GUARD}
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {base} >= {meh_thresh}
          AND coalesce(first(ep.avg_ep), 0.75) <= {mpe_thresh}
    """)
    r = d.fetchone(f"SELECT count() AS n FROM qual_{variant}")
    return r["n"]


def _build_mkt_stats(d, variant: str, xs: str, xe: str,
                     meh_thresh: float, mpe_thresh: float) -> None:
    """Build market-level stats for one qual variant in the test window.

    Appends rows to mkt_stats_{variant}. Each row = one market, tagged with
    meh_thresh and mpe_thresh so the batch query can separate them.
    """
    pos_filter = "AND p.position = 'YES'" if variant == "buy" else ""

    # Test-window positions for qualified traders (phantom signal guard included)
    d.execute(f"DROP TABLE IF EXISTS tp_{variant}")
    d.execute(f"""
        CREATE TEMP TABLE tp_{variant} AS
        SELECT
            p.condition_id,
            p.trader,
            p.yes_won,
            p.realized_pnl,
            p.first_trade,
            p.resolved_at
        FROM maker_positions p
        JOIN qual_{variant} q ON p.trader = q.trader
        WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
          {pos_filter}
          AND CAST(p.resolved_at AS DATE) >= '{xs}'
          AND CAST(p.resolved_at AS DATE) < '{xe}'
          AND CAST(p.first_trade AS DATE) >= '{xs}'
    """)

    # Entry price at signal time (from yes_entry_data)
    d.execute(f"DROP TABLE IF EXISTS ep_{variant}")
    d.execute(f"""
        CREATE TEMP TABLE ep_{variant} AS
        SELECT
            y.condition_id,
            sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        JOIN tp_{variant} tp ON y.trader = tp.trader AND y.condition_id = tp.condition_id
        WHERE CAST(y.first_trade AS DATE) >= '{xs}'
        GROUP BY y.condition_id
    """)

    # Market-level aggregate: one row per market
    d.execute(f"""
        INSERT INTO mkt_stats_{variant}
        SELECT
            c.condition_id,
            first(c.yes_won)::BOOLEAN AS yes_won,
            count(DISTINCT c.trader) AS n_traders,
            date_diff('hour', min(c.first_trade), max(c.first_trade)) AS consensus_window_hours,
            date_diff('hour', max(c.first_trade), first(c.resolved_at)) AS hold_hours,
            median(c.realized_pnl) AS median_pnl,
            coalesce(first(e.avg_ep), 0.75) AS avg_ep,
            {meh_thresh} AS meh_thresh,
            {mpe_thresh} AS mpe_thresh
        FROM tp_{variant} c
        LEFT JOIN ep_{variant} e ON c.condition_id = e.condition_id
        GROUP BY c.condition_id
    """)


def run_fold(d, ts: str, te: str, xs: str, xe: str, base: float) -> tuple[list, list]:
    """Run one fold: build all qual tables, accumulate market stats, batch query."""
    for variant in ["buy", "dir"]:
        d.execute(f"DROP TABLE IF EXISTS mkt_stats_{variant}")
        d.execute(f"""
            CREATE TEMP TABLE mkt_stats_{variant} (
                condition_id VARCHAR,
                yes_won BOOLEAN,
                n_traders BIGINT,
                consensus_window_hours BIGINT,
                hold_hours BIGINT,
                median_pnl DOUBLE,
                avg_ep DOUBLE,
                meh_thresh DOUBLE,
                mpe_thresh DOUBLE
            )
        """)

    # Build one qual table per (meh, mpe) pair and accumulate market stats
    for meh in MIN_EXCESS_HR:
        for mpe in MAX_POOL_EP:
            for variant in ["buy", "dir"]:
                n_qual = _build_qual_table(d, ts, te, base, meh, mpe, variant)
                if n_qual < 2:
                    continue
                _build_mkt_stats(d, variant, xs, xe, meh, mpe)

    buy_sql = _batch_combo_query("mkt_stats_buy", "yes_won")
    dir_sql = _batch_combo_query("mkt_stats_dir", "yes_won")

    buy_rows = d.fetchall(buy_sql)
    dir_rows = d.fetchall(dir_sql)
    return buy_rows, dir_rows


def run_sweep(tags: list[str] | None = None) -> dict:
    d = db()
    tags = tags or TAGS

    n_pool_combos = len(MIN_EXCESS_HR) * len(MAX_POOL_EP)
    n_signal_combos = len(CONSENSUS_N) * len(PRICE_CEIL)
    n_total = n_pool_combos * n_signal_combos
    print(
        f"Pool combos: {n_pool_combos} (meh={MIN_EXCESS_HR} × mpe={MAX_POOL_EP})"
    )
    print(
        f"Signal combos: {n_signal_combos} (N={CONSENSUS_N} × pc={PRICE_CEIL})"
    )
    print(f"Total: {n_total} combos × {len(FOLDS)} folds × 2 variants × {len(tags)} tags")
    print(f"min_trades={MIN_TRADES}, bot_guard<{BOT_GUARD}, raw_hr<0.99")

    results: dict = {
        "hypothesis": "tag-hr-consensus",
        "round": "3-duckdb",
        "timestamp": datetime.now().isoformat(),
        "max_hold_hours": MAX_HOLD_HOURS,
        "cs_formula": "excess_hr * median_pnl_usd / median_hold_days",
        "note": "UPPER BOUNDS — vectorized. Pool filtered by min_excess_hr (swept) and max_pool_entry_price (swept).",
        "params": {
            "consensus_n": CONSENSUS_N,
            "price_ceil": PRICE_CEIL,
            "min_excess_hr": MIN_EXCESS_HR,
            "max_pool_entry_price": MAX_POOL_EP,
            "min_trades": MIN_TRADES,
            "bot_guard": BOT_GUARD,
        },
        "verdict": "pending",
        "tags": {},
    }

    for tag in tags:
        t_tag = time.time()
        print(f"\n=== {tag} ===", flush=True)

        d.execute("DROP TABLE IF EXISTS tag_mkts")
        d.execute("""
            CREATE TEMP TABLE tag_mkts AS
            SELECT DISTINCT m.condition_id
            FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])
        n_mkts = d.fetchone("SELECT count() AS n FROM tag_mkts")["n"]
        print(f"  {n_mkts:,} markets in tag", flush=True)

        all_fold_data = []

        for ts, te, xs, xe in FOLDS:
            t_fold = time.time()

            # Base rate for test window (deduplicated per condition_id)
            br = d.fetchone(f"""
                SELECT
                    sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS yw,
                    count() AS tot
                FROM (
                    SELECT condition_id, first(yes_won) AS yes_won
                    FROM maker_positions
                    WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                      AND CAST(resolved_at AS DATE) >= '{xs}'
                      AND CAST(resolved_at AS DATE) < '{xe}'
                    GROUP BY condition_id
                )
            """)
            yw, tot = br["yw"], br["tot"]
            if tot < 10:
                print(f"  fold {xs}: SKIP (n={tot})", flush=True)
                continue
            base = round(yw / tot, 4)

            buy_rows, dir_rows = run_fold(d, ts, te, xs, xe, base)

            buy_by_key = {r["combo_key"]: r for r in buy_rows}
            dir_by_key = {r["combo_key"]: r for r in dir_rows}

            agg_keys = ["ns", "nc", "hr", "mp", "ap", "mh"]
            combos: dict = {}
            for n in CONSENSUS_N:
                for pc in PRICE_CEIL:
                    for meh in MIN_EXCESS_HR:
                        for mpe in MAX_POOL_EP:
                            key = _combo_key(n, pc, meh, mpe)
                            rb = buy_by_key.get(key)
                            rd = dir_by_key.get(key)
                            combos[key] = {
                                "buy": [0 if rb is None or rb.get(k) is None else rb[k] for k in agg_keys],
                                "dir": [0 if rd is None or rd.get(k) is None else rd[k] for k in agg_keys],
                                "base": base,
                            }

            fold_elapsed = time.time() - t_fold
            all_fold_data.append({
                "base": base, "n": tot, "fold_test": xs, "combos": combos,
            })
            print(
                f"  fold {xs}: base={base:.3f} n={tot}  "
                f"buy={len(buy_rows)} dir={len(dir_rows)} combos  {fold_elapsed:.1f}s",
                flush=True,
            )

        if not all_fold_data:
            print(f"  {tag}: no folds — skipping", flush=True)
            continue

        # Aggregate across folds
        def agg(folds: list, vk: str) -> list:
            cf: dict[str, list] = defaultdict(list)
            for fd in folds:
                for ck, dat in fd["combos"].items():
                    row = dat[vk]
                    ns, nc, hr, mp, ap, mh = [float(x) if x is not None else 0 for x in row]
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
                # parse key: n_pc_meh_pp_mpe
                parts = ck.split("_")
                n_val = int(parts[0])
                pc_val = float(parts[1])
                meh_pp = int(parts[2])       # already in pp (e.g. "5", "10", "15", "20")
                mpe_val = float(parts[3])
                n_folds = len(fs)
                avg_hr = sum(f["hr"] for f in fs) / n_folds
                avg_exc = sum(f["exc"] for f in fs) / n_folds
                avg_mp = sum(f["mp"] for f in fs) / n_folds
                avg_ap = sum(f["ap"] for f in fs) / n_folds
                avg_hold_h = sum(f["mh"] for f in fs) / n_folds
                total_sigs = int(sum(f["ns"] for f in fs))
                hold_days = avg_hold_h / 24 if avg_hold_h > 0 else 1.0
                cs = round(avg_exc * avg_mp / hold_days, 4) if avg_mp > 0 else 0
                out.append({
                    "params": {
                        "consensus_n": n_val,
                        "price_ceil": pc_val,
                        "min_excess_hr_pp": meh_pp,
                        "max_pool_entry_price": mpe_val,
                    },
                    "n_folds": n_folds,
                    "avg_hr": round(avg_hr, 4),
                    "avg_excess_hr_pp": round(avg_exc * 100, 2),
                    "median_pnl_usd": round(avg_mp, 2),
                    "avg_pnl_usd": round(avg_ap, 2),
                    "avg_hold_hours": round(avg_hold_h, 2),
                    "total_signals": total_sigs,
                    "signals_per_fold": round(total_sigs / n_folds, 1),
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
    parser.add_argument("--tags", nargs="+", help="Override tag list (Esports Tennis)")
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
