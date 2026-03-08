"""Quick test: deeper consensus + base rate regime gate.

Checks HR at N=2..6 consensus thresholds for each fold, with regime flag.
"""
from __future__ import annotations
from research.db import db

FOLDS = [
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01"),
]

CONFIGS = {
    "Esports": (10, 0.80),
    "Tennis": (20, 0.90),
}


def main():
    d = db()

    for tag, (meh_pp, mpe) in CONFIGS.items():
        meh_thresh = meh_pp / 100.0

        d.execute("DROP TABLE IF EXISTS tag_mkts")
        d.execute("""
            CREATE TEMP TABLE tag_mkts AS
            SELECT DISTINCT m.condition_id FROM markets m
            JOIN event_tags et ON m.event_id = et.event_id
            WHERE et.label = $1
        """, [tag])

        print(f"\n{'='*60}")
        print(f"{tag}  (meh={meh_pp}pp, mpe={mpe})")
        print(f"{'='*60}")

        for ts, te, xs, xe in FOLDS:
            br = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base
                FROM (SELECT condition_id, first(yes_won) AS yes_won FROM maker_positions
                      WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                        AND CAST(resolved_at AS DATE) >= '{ts}' AND CAST(resolved_at AS DATE) < '{te}'
                      GROUP BY condition_id)
            """)
            train_base = br["base"]

            d.execute("DROP TABLE IF EXISTS _ep_tmp2")
            d.execute(f"""
                CREATE TEMP TABLE _ep_tmp2 AS
                SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
                FROM yes_entry_data y
                WHERE y.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND CAST(y.first_trade AS DATE) >= '{ts}' AND CAST(y.first_trade AS DATE) < '{te}'
                GROUP BY y.trader
            """)

            pool_r = d.fetchall(f"""
                SELECT p.trader FROM maker_positions p
                INNER JOIN _ep_tmp2 ep ON p.trader = ep.trader
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{ts}' AND CAST(p.resolved_at AS DATE) < '{te}'
                GROUP BY p.trader
                HAVING count() >= 5 AND count() < 10000
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
                  AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} >= {meh_thresh}
                  AND first(ep.avg_ep) <= {mpe}
            """)
            pool = {r["trader"] for r in pool_r}
            if len(pool) < 2:
                print(f"  fold {xs}: pool={len(pool)} SKIP")
                continue

            placeholders = ", ".join(f"'{t}'" for t in pool)
            d.execute("DROP TABLE IF EXISTS tp2")
            d.execute(f"""
                CREATE TEMP TABLE tp2 AS
                SELECT p.condition_id, p.trader, p.first_trade, p.yes_won, p.resolved_at
                FROM maker_positions p
                WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '{xs}' AND CAST(p.resolved_at AS DATE) < '{xe}'
                  AND CAST(p.first_trade AS DATE) >= '{xs}'
                  AND p.trader IN ({placeholders})
            """)

            test_br = d.fetchone(f"""
                SELECT sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / count() AS base
                FROM (SELECT condition_id, first(yes_won) AS yes_won FROM maker_positions
                      WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
                        AND CAST(resolved_at AS DATE) >= '{xs}' AND CAST(resolved_at AS DATE) < '{xe}'
                      GROUP BY condition_id)
            """)
            test_base = test_br["base"]

            regime = " [HIGH BASE]" if train_base > 0.55 else ""
            print(f"  fold {xs}: pool={len(pool)} train_base={train_base:.3f} test_base={test_base:.3f}{regime}")

            for n in [2, 3, 4, 5, 6]:
                rows = d.fetchall(f"""
                    WITH ranked AS (
                        SELECT condition_id, trader, min(first_trade) AS ft, first(yes_won) AS yes_won,
                               ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY min(first_trade)) AS entry_order
                        FROM tp2 GROUP BY condition_id, trader
                    ),
                    consensus_mkts AS (
                        SELECT condition_id, first(yes_won) AS yes_won, count() AS n_entries
                        FROM ranked WHERE entry_order <= {n}
                        GROUP BY condition_id HAVING n_entries >= {n}
                    )
                    SELECT count() AS n_mkts,
                           sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS wins,
                           round(sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::DOUBLE / greatest(count(), 1), 4) AS hr
                    FROM consensus_mkts
                """)
                if rows and rows[0]["n_mkts"] > 0:
                    r = rows[0]
                    exc = round((r["hr"] - test_base) * 100, 1)
                    print(f"    N>={n}: HR={r['hr']} excess={exc:+.1f}pp (n={r['n_mkts']})")

            # Also: what if we skip this fold due to regime gate (train_base > 0.50)?
            if train_base > 0.50:
                print(f"    >> REGIME GATE: would skip this fold (train_base={train_base:.3f} > 0.50)")


if __name__ == "__main__":
    main()
